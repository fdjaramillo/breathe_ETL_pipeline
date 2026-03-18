import fitz  # PyMuPDF
import logging
import re

from utils.normalization import normalize_date, normalize_sex

# Compilar expresiones regulares
NHC_RE = re.compile(r"NHC\s*:\s*([A-Za-z0-9]+)")
BIRTH_DATE_RE = re.compile(r"Fecha nac./Data naix.\s*:\s*(\d{2}/\d{2}/\d{4})")
SEX_RE = re.compile(r"Sexo/Sexe\s*:\s*([A-Za-z])")

LAB_REQUEST_NUMBER_RE = re.compile(r"N\. Sol·licitud Lab\.\s*:\s*([0-9]+)")
REPORT_DATE_RE = re.compile(r"Data recepció mostra\s*:\s*(\d{2}/\d{2}/\d{4})")


def debug_measurements(data_object, target_section="AL·LÈRGENS ESPECÍFICS"):
    """
    Imprime en consola las mediciones de la analítica que no empiezan con 'I' antes de guardarlas.
    Filtra por sección si se especifica.
    """
    if not data_object or "measurements" not in data_object:
        return

    logging.info("-------------------DEBUG-------------------------")
    count = 0
    for m in data_object["measurements"]:
        # Si target_section es
        if m.get("section") == target_section and not m.get("parameter").startswith(
            "I"
        ):
            logging.info(
                "[%s] %s: %s (%s) | Bold: %s",
                m.get("section"),
                m.get("parameter"),
                m.get("value"),
                m.get("unit"),
                m.get("value_in_bold"),
            )
            count += 1

    if count == 0:
        logging.info(
            "   (No se encontraron mediciones que no empiecen con 'I' para la sección: %s)",
            target_section,
        )
    logging.info("------------------------------------------------")


def _extract_report_metadata(doc) -> dict:
    page_text = doc[0].get_text("text") if len(doc) else ""
    # Patient info
    nhc_match = NHC_RE.search(page_text)
    birth_date_match = BIRTH_DATE_RE.search(page_text)
    sex_match = SEX_RE.search(page_text)

    patient_info = {}
    if nhc_match:
        patient_info["nhc"] = nhc_match.group(1).strip()
    if birth_date_match:
        patient_info["birth_date"] = normalize_date(birth_date_match.group(1))
    if sex_match:
        patient_info["sex"] = normalize_sex(sex_match.group(1).strip())

    # Report info
    lab_request_match = LAB_REQUEST_NUMBER_RE.search(page_text)
    report_date_match = REPORT_DATE_RE.search(page_text)

    report_info = {"report_type": "blood_test"}

    if lab_request_match:
        report_info["lab_request_number"] = lab_request_match.group(1).strip()
    if report_date_match:
        report_info["report_date"] = normalize_date(report_date_match.group(1))

    return patient_info, report_info


def extract_measurements(doc):
    """
    Recorre todas las páginas buscando tablas y aplicando contexto.
    Input: Objeto documento.
    Output: Lista de diccionarios (cada diccionario es una fila de resultado).
    """
    measurements_list = []

    # inicializar variables de contexto
    current_section = None
    current_subsection = None

    # Definir la línea vertical para detectar tablas
    vertical_line = ((377, 282), (377, 758))

    for page in doc:

        # Obtener bloques de texto
        styled_blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT, sort=True)[
            "blocks"
        ]

        for table in page.find_tables(add_lines=[vertical_line]).tables:
            for row in table.extract():

                # skip empty or invalid rows
                if not row or len(row) < 4:
                    logging.warning("Warning: invalid row found and skipped: %s", row)
                    continue

                # initialize variables
                parameter, value, unit, ref_interval = (
                    row[0].strip(),
                    row[1].strip(),
                    row[2].strip(),
                    row[3].strip(),
                )

                # Skip lines containing 'Prestació'
                if parameter == "Prestació":
                    continue

                # Detect subsections based on uppercase text
                if (
                    parameter.isupper()
                    and not value
                    and parameter.startswith("AL·LÈRGIA ")
                    and current_section == "AL·LÈRGENS ESPECÍFICS"
                ):
                    current_subsection = parameter
                    continue

                # Detect new sections based on uppercase text
                # cuando la linea tiene seccion y subseccion none, o si tiene seccion y subseccion
                if parameter.isupper() and not value:
                    current_section = parameter
                    current_subsection = None  # Reset subsection when changing sections
                    continue

                # Skip if there's no unit (filter out non-result lines)
                if not unit:
                    if not value.startswith("Classe"):
                        parameter = parameter.replace("\n", " ")
                        logging.warning(
                            "Warning: skipping line without unit: %s", parameter
                        )
                    continue

                # Check bold
                is_bold = any(
                    value in span.get("text", "") and "Bold" in span.get("font", "")
                    for block in styled_blocks
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                )

                # Save measurement with context
                measurements_list.append(
                    {
                        "section": current_section,
                        "subsection": current_subsection,
                        "parameter": parameter,
                        "value": value,
                        "unit": unit,
                        "reference_range": ref_interval,
                        "value_in_bold": is_bold,
                    }
                )
    return measurements_list


def process_pdf(filepath, file_hash):
    """
    Procesa un solo archivo de principio a fin.

    Args:
        filepath: Ruta del archivo PDF
        file_hash: Hash SHA256 del archivo (calculado en main.py)

    Retorna:
        Diccionario con los datos extraídos o None si falla.
    """
    try:
        doc = fitz.open(filepath)

        # Paso 1: Metadatos
        patient_info, report_info = _extract_report_metadata(doc)

        # Paso 2: Resultados con contexto
        measurements = extract_measurements(doc)

        doc.close()

        # Paso 3: Información del archivo
        file_info = {
            "filename": filepath.name,
            "file_hash": file_hash,
        }

        # Paso 4: Empaquetar todo en el formato esperado por loader.py
        full_data_object = {
            "file_info": file_info,
            "patient": patient_info,
            "report": report_info,
            "measurements": measurements,
        }

        return full_data_object

    except (fitz.FileDataError, IndexError, AttributeError, ValueError) as error:
        logging.error("Error procesando %s: %s", filepath.name, error)
        return None
    except Exception:
        logging.exception("Error inesperado procesando %s", filepath.name)
        return None
