# 1. DEPENDENCIAS Y CONSTANTES
# Importar librerías (fitz, re, etc.)
import fitz  # PyMuPDF
import logging
import re

from utils.base_extractor import get_report_metadata

# Definir umbrales de tolerancia (EPSILON_Y = 4.0, etc.)
EPSILON_Y = 4.0

# Compilar expresiones regulares (patrones de rango, números, flags)
VALUE_RE = re.compile(r"\d+(?:[\.,]\d+)?")
RANGE_RE = re.compile(r"\d+(?:[\.,]\d+)?\s*-\s*\d+(?:[\.,]\d+)?")
FLAG_RE = re.compile(r"[↑↓*]")
RESIDUAL_DOT_RE = re.compile(r"^\.+$")
WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+")
LOWERCASE_CONNECTORS = {"i", "y", "e", "de", "del", "la", "el", "d"}


def is_heading_upper_like(text: str) -> bool:
    """
    Considera una línea como "mayúsculas de encabezado" si sus palabras están
    en mayúsculas, permitiendo conectores cortos en minúscula (ej. "i", "de").
    """
    words = WORD_RE.findall(text)
    if not words:
        return False

    has_upper_word = False
    for word in words:
        if word.lower() in LOWERCASE_CONNECTORS:
            continue
        if word == word.upper():
            has_upper_word = True
            continue
        return False

    return has_upper_word


# 2. FUNCIONES DE EXTRACCIÓN Y GEOMETRÍA
def get_page_spans(page) -> list:
    """Extrae todos los spans de la página usando get_text('dict')."""
    spans = []
    text_dict = page.get_text("dict", sort=True)

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text and text.strip():
                    spans.append(span)

    return spans


def calculate_y_mid(span: dict) -> float:
    """Calcula el centroide vertical de un span."""
    y0 = span["bbox"][1]
    y1 = span["bbox"][3]
    return (y0 + y1) / 2.0


def cluster_spans_into_lines(spans: list, epsilon: float) -> list[list]:
    """
    Ordena spans por y_mid.
    Agrupa en 'Líneas Lógicas' (listas de spans) usando el umbral epsilon.
    Ordena cada línea internamente por x0.
    """
    if not spans:
        return []

    sorted_spans = sorted(spans, key=calculate_y_mid)
    lines = []
    current_line = [sorted_spans[0]]
    current_y_mid = calculate_y_mid(sorted_spans[0])

    for span in sorted_spans[1:]:
        y_mid = calculate_y_mid(span)

        if abs(y_mid - current_y_mid) <= epsilon:
            current_line.append(span)
            current_y_mid = (current_y_mid * (len(current_line) - 1) + y_mid) / len(
                current_line
            )
        else:
            lines.append(sorted(current_line, key=lambda s: s["bbox"][0]))
            current_line = [span]
            current_y_mid = y_mid

    lines.append(sorted(current_line, key=lambda s: s["bbox"][0]))
    return lines


# 3. FUNCIONES DE CLASIFICACIÓN Y PARSEO
def classify_line(line_spans: list, page_width: float) -> str:
    """
    Evalúa coordenadas, fuentes y contenido.
    Retorna tipo de línea: 'TITULO', 'SUBTITULO', 'DATOS' o 'IGNORAR'.
    """
    if not line_spans:
        return "IGNORAR"

    line_text = " ".join(s.get("text", "").strip() for s in line_spans).strip()
    if not line_text:
        return "IGNORAR"

    first_bbox = line_spans[0].get("bbox", (0, 0, 0, 0))
    last_bbox = line_spans[-1].get("bbox", (0, 0, 0, 0))
    line_x0 = first_bbox[0]
    line_center_x = (first_bbox[0] + last_bbox[2]) / 2.0

    fonts = [s.get("font", "") for s in line_spans]
    sizes = [float(s.get("size", 0.0)) for s in line_spans]
    is_bold = any("Bold" in f for f in fonts)
    is_upper = is_heading_upper_like(line_text)
    has_numeric = bool(VALUE_RE.search(line_text))
    has_range = bool(RANGE_RE.search(line_text))

    # TITULO: centrado, mayúsculas, negrita y tamaño grande.
    if (
        abs(line_center_x - (page_width / 2.0)) <= 20
        and is_bold
        and is_upper
        and max(sizes, default=0.0) >= 12.0
    ):
        return "TITULO"

    # SUBTITULO: alineado a izquierda, mayúsculas y negrita.
    if line_x0 < 50 and is_bold and is_upper:
        return "SUBTITULO"

    # DATOS: fila con valor numérico y rango de referencia.
    if has_numeric and has_range:
        return "DATOS"

    return "IGNORAR"


def parse_data_row(line_spans: list) -> dict:
    """
    Extrae (nombre, flag, valor, unidad, rango) basándose en el orden de los spans.
    Aplica regex para limpiar y validar tipos numéricos.
    """
    cleaned_spans = []
    for s in line_spans:
        text = s.get("text", "").strip()
        if not text:
            continue
        if RESIDUAL_DOT_RE.match(text):
            continue
        cleaned_spans.append({"text": text, "bbox": s.get("bbox", (0, 0, 0, 0))})

    if not cleaned_spans:
        return None

    tokens = [s["text"] for s in cleaned_spans]

    flag_idx = None
    for i, t in enumerate(tokens):
        if FLAG_RE.search(t):
            flag_idx = i
            break

    range_idx = None
    for i in range(len(tokens) - 1, -1, -1):
        if RANGE_RE.fullmatch(tokens[i]):
            range_idx = i
            break

    value_idx = None
    for i, t in enumerate(tokens):
        if i == range_idx:
            continue
        if VALUE_RE.fullmatch(t):
            value_idx = i
            break

    if value_idx is None or range_idx is None:
        return None

    name_end_idx = value_idx
    if flag_idx is not None and flag_idx < value_idx:
        name_end_idx = flag_idx

    parameter_name = " ".join(tokens[:name_end_idx]).strip()
    if not parameter_name:
        return None

    flag = tokens[flag_idx] if flag_idx is not None else None
    value = tokens[value_idx]

    unit_start = value_idx + 1
    unit_end = range_idx
    if unit_end <= unit_start:
        unit = None
    else:
        unit = " ".join(tokens[unit_start:unit_end]).strip() or None

    reference_range = tokens[range_idx]

    return {
        "parameter": parameter_name,
        "flag": flag,
        "value": value,
        "unit": unit,
        "reference_range": reference_range,
    }


def _update_extraction_state(
    current_section, current_subsection, line_type: str, line_spans: list
):
    """Actualiza current_section o current_subsection si la línea es un encabezado."""
    text = " ".join(s.get("text", "").strip() for s in line_spans).strip()
    if not text:
        return current_section, current_subsection

    if line_type == "TITULO":
        return text, None
    elif line_type == "SUBTITULO":
        return current_section, text

    return current_section, current_subsection


def _insert_row_data(data: dict, current_section, current_subsection, row_data: dict):
    """Inserta la fila procesada en la salida respetando el estado actual."""
    data.setdefault(current_section, {})
    data[current_section].setdefault(current_subsection, [])
    data[current_section][current_subsection].append(row_data)


def extract_measurement_hierarchy(filepath) -> dict:
    """
    Método principal:
    1. Abre el documento.
    2. Itera por cada página.
    3. Llama a get_page_spans -> cluster_spans_into_lines.
    4. Itera sobre las líneas lógicas:
        a. classify_line
        b. Si es encabezado: _update_extraction_state
        c. Si son datos: parse_data_row -> _insert_row_data
    5. Retorna la jerarquía extraída.
    """
    data = {}
    current_section = None
    current_subsection = None
    document = fitz.open(filepath)

    try:
        for page in document:
            spans = get_page_spans(page)
            lines = cluster_spans_into_lines(spans, EPSILON_Y)

            for line_spans in lines:
                line_type = classify_line(line_spans, page.rect.width)

                if line_type in ("TITULO", "SUBTITULO"):
                    current_section, current_subsection = _update_extraction_state(
                        current_section,
                        current_subsection,
                        line_type,
                        line_spans,
                    )
                elif line_type == "DATOS":
                    row_data = parse_data_row(line_spans)
                    if row_data:
                        _insert_row_data(
                            data,
                            current_section,
                            current_subsection,
                            row_data,
                        )

        return data
    finally:
        document.close()


def _flatten_hierarchy(extracted_hierarchy: dict) -> list:
    measurements = []
    for section, subsections in extracted_hierarchy.items():
        for subsection, rows in subsections.items():
            for row in rows:
                measurements.append(
                    {
                        "section": section,
                        "subsection": subsection,
                        "parameter": row.get("parameter"),
                        "value": row.get("value"),
                        "unit": row.get("unit"),
                        "reference_range": row.get("reference_range"),
                        "value_in_bold": True if row.get("flag") else False,
                    }
                )
    return measurements


def process_pdf(filepath, file_hash, source_file_type=None):
    """
    Adaptador para el pipeline ETL: convierte la salida jerárquica del extractor
    al formato esperado por loader.save_to_db().
    """
    try:
        # Extraer mediciones con el motor geométrico.
        hierarchy = extract_measurement_hierarchy(filepath)
        measurements = _flatten_hierarchy(hierarchy)

        if not measurements:
            return None

        # Reabrir solo para metadatos ligeros de cabecera.
        doc = fitz.open(filepath)
        try:
            page_text = doc[0].get_text("text") if len(doc) else ""
            patient_info, report_info = get_report_metadata(
                page_text,
                source_file_type or "vh_blood_test",
            )
        finally:
            doc.close()

        return {
            "file_info": {"filename": filepath.name, "file_hash": file_hash},
            "patient": patient_info,
            "report": report_info,
            "measurements": measurements,
        }

    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        logging.error("Error procesando %s: %s", filepath.name, error)
        return None
    except Exception:
        logging.exception("Error inesperado procesando %s", filepath.name)
        return None
