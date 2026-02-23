import fitz  # PyMuPDF
from datetime import datetime


def debug_measurements(data_object, target_section="AL·LÈRGENS ESPECÍFICS"):
    """
    Imprime en consola las mediciones de la analítica que no empiezan con 'I' antes de guardarlas.
    Filtra por sección si se especifica.
    """
    if not data_object or "measurements" not in data_object:
        return

    print(f"\n-------------------DEBUG-------------------------")
    count = 0
    for m in data_object["measurements"]:
        # Si target_section es
        if m.get("section") == target_section and not m.get("parameter").startswith(
            "I"
        ):
            print(
                f"[{m.get('section')}] "
                f"{m.get('parameter')}: {m.get('value')} "
                f"({m.get('unit')}) "
                f"| Bold: {m.get('value_in_bold')}"
            )
            count += 1

    if count == 0:
        print(
            f"   (No se encontraron mediciones que no empiecen con 'I' para la sección: {target_section})"
        )
    print("------------------------------------------------\n")


def extract_patient_info(doc):
    page = doc[0]
    blocks = page.get_text("blocks", sort=True)[1:3]

    patient_info = {}

    # Block 1: name and NHC
    text_block_1 = blocks[0][4].splitlines()

    patient_info["nhc"] = text_block_1[1].replace("NHC:", "").strip()
    patient_info["name"] = text_block_1[0].strip()

    # Block 2:
    text_block_2 = blocks[1][4].splitlines()

    for line in text_block_2:
        if "Data naix." in line:
            date = line.split(":")[-1].strip()
            patient_info["birth_date"] = datetime.strptime(date, "%d/%m/%Y").strftime(
                "%Y-%m-%d"
            )
        elif "Sexo/Sexe" in line:
            patient_info["sex"] = line.split(":")[-1].strip()

    return patient_info


def extract_report_info(doc):
    page = doc[0]
    blocks = page.get_text("blocks", sort=True)[1:3]

    report_info = {"report_type": "blood_test"}

    # Block 2:
    text_block_2 = blocks[1][4].splitlines()
    for line in text_block_2:
        if "N. Sol·licitud Lab." in line:
            report_info["lab_request_number"] = line.split(":")[-1].strip()
        elif "Nºepis" in line:
            report_info["episode_number"] = line.split(":")[-1].strip()
        elif "Data recepció mostra" in line:
            date = line.split(",")[0].replace("Data recepció mostra:", "").strip()
            report_info["report_date"] = datetime.strptime(date, "%d/%m/%Y").strftime(
                "%Y-%m-%d"
            )

    return report_info


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
                    print(f"⚠️ Warning: invalid row found and skipped: {row}")
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
                        print(f"⚠️ Warning: skipping line without unit: {parameter}")
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
        patient = extract_patient_info(doc)
        report = extract_report_info(doc)

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
            "patient": patient,
            "report": report,
            "measurements": measurements,
        }

        return full_data_object

    except Exception as e:
        print(f"Error procesando {filepath.name}: {e}")
        return None
