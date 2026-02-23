import fitz
import re
from datetime import datetime


def normalize_headers(line):
    """
    Normalizes the headers detected in a line.
    """
    # Split the line into tokens using spaces as delimiters
    tokens = re.split(r"\s{1,}", line.strip())

    # Rebuild headers based on known patterns
    known_patterns = ["%Teòric", "PostBD", "Z-Score", "%Canvi"]
    headers = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        # Check if the current token or the next one form a known pattern
        if i + 1 < len(tokens) and f"{token}{tokens[i + 1]}" in known_patterns:
            headers.append(f"{token}{tokens[i + 1]}")
            i += 2  # Skip to the next token after the pattern
        else:
            headers.append(token)
            i += 1
    return headers


def associate_repeated_headers(headers):
    """
    Associates repeated headers like "%Teòric" and "Z-Score" with the Pre and Post/PostBD contexts.
    If a header appears twice, one is assigned to Pre and the other to Post or PostBD as appropriate.
    """
    # List of headers that are repeated and should be associated with Pre and Post/PostBD
    repeated_headers = ["%Teòric", "Z-Score"]

    # Dictionary to count the occurrences of each header
    header_counts = {header: 0 for header in repeated_headers}

    # List to store headers associated with their context
    associated_headers = []

    # Detect if the context is "Post" or "PostBD"
    post_context = "PostBD" if "PostBD" in headers else "Post"

    for header in headers:
        if header in repeated_headers:
            # Increment the header counter
            header_counts[header] += 1
            # Associate with Pre or Post/PostBD according to the occurrence
            if header_counts[header] == 1:
                associated_headers.append(f"Pre.{header}")
            elif header_counts[header] == 2:
                associated_headers.append(f"{post_context}.{header}")
            else:
                # If there are more than two occurrences, keep the original header
                associated_headers.append(header)
        else:
            # Headers that are not repeated are added as is
            associated_headers.append(header)

    return associated_headers


def extract_metadata(text):
    """
    Extrae la información del paciente y del informe desde el texto del PDF.
    Devuelve diccionarios compatibles con las claves de loader.py.
    """
    patient = {}
    report = {"report_type": "spirometry"}

    # Expresiones regulares
    nhc_pattern = r"NHC\s*:?\s*(\w+).*?Edat"
    height_pattern = r"Alçada\s*\(cm\):\s*(\d+(?:\.\d+)?)"
    weight_pattern = r"Pes\s*\(Kg\):\s*(\d+(?:\.\d+)?)"
    report_date_pattern = r"Data exploraci[oó]\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4})"

    for line in text.splitlines():
        line = line.strip()

        # Buscar NHC
        nhc_match = re.search(nhc_pattern, line)
        if nhc_match:
            patient["nhc"] = nhc_match.group(1).lstrip("0")

        # Buscar altura
        height_match = re.search(height_pattern, line)
        if height_match:
            report["height_cm"] = float(height_match.group(1))

        # Buscar peso
        weight_match = re.search(weight_pattern, line)
        if weight_match:
            report["weight_kg"] = float(weight_match.group(1))

        # Buscar fecha del reporte
        date_match = re.search(report_date_pattern, line)
        if date_match:
            date_str = date_match.group(1)
            try:
                report["report_date"] = datetime.strptime(
                    date_str, "%d/%m/%Y"
                ).strftime("%Y-%m-%d")
            except ValueError:
                report["report_date"] = date_str

    return patient, report


def extract_measurements(text):
    """
    Parsea la sección ESPIROMETRIA FORÇADA.
    Separa parámetro de unidad y genera registros independientes para Pre y Post.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    measurements_list = []
    spirometry_section = False
    headers = None

    for line in lines:
        if "ESPIROMETRIA FORÇADA" in line:
            spirometry_section = True
            continue

        if spirometry_section:
            # Detect end of section
            if any(
                keyword in line
                for keyword in ["HISTÒRIC", "VOLUMS PULMONARS", "DIFUSIÓ"]
            ):
                break

            # Detect header line
            if re.search(r"Pre\s+Teòric|Pre\s+Teòric\s+LIN", line, re.IGNORECASE):
                headers = normalize_headers(line)
                headers = associate_repeated_headers(headers)
                continue

            if any(
                param in line for param in ["FVC", "FEV1", "FEV1/FVC", "MEF", "PEF"]
            ):
                # Regex para extraer "FVC" (grupo 1) y "L" (grupo 2) de "FVC(L)"
                param_match = re.match(r"^\s*([A-Z]+[A-Z0-9/]*)(?:\(([^)]+)\))?", line)
                if not param_match:
                    continue

                param_name = param_match.group(1).strip()
                unit = param_match.group(2).strip() if param_match.group(2) else None

                values_part = line[param_match.end() :].strip()
                values = re.split(r"\s{2,}", values_part)
                values = [v.strip() for v in values if v.strip()]

                if not headers:
                    continue

                row_data = {}
                relevant_headers = (
                    ["Pre", "Teòric", "LIN", "PostBD"]
                    if param_name.startswith("FEV1/FVC")
                    else headers
                )

                for i, val in enumerate(values):
                    # Asociar el valor con el header correspondiente, solo si no es "----"
                    if i < len(relevant_headers) and val != "----":
                        row_data[relevant_headers[i]] = val

                # Generar registro PRE
                if "Pre" in row_data:
                    measurements_list.append(
                        {
                            "parameter": param_name,
                            "unit": unit,
                            "phase": "Pre",
                            "value": row_data.get("Pre"),
                            "theoretical": row_data.get("Teòric"),
                            "lin": row_data.get("LIN"),
                            "z_score": row_data.get("Pre.Z-Score"),
                            "perc_theoretical": row_data.get("Pre.%Teòric"),
                        }
                    )

                # Generar registro POSTBD (Si existe)
                post_key = (
                    "PostBD"
                    if "PostBD" in row_data
                    else ("Post" if "Post" in row_data else None)
                )
                if post_key and row_data.get(post_key):
                    measurements_list.append(
                        {
                            "parameter": param_name,
                            "unit": unit,
                            "phase": "PostBD",
                            "value": row_data.get(post_key),
                            "theoretical": row_data.get("Teòric"),
                            "lin": row_data.get("LIN"),
                            "z_score": row_data.get(f"{post_key}.Z-Score"),
                            "perc_theoretical": row_data.get(f"{post_key}.%Teòric"),
                            "perc_change": row_data.get("%Canvi"),
                        }
                    )

    return measurements_list


def process_pdf(filepath, file_hash):
    """
    Función orquestadora que devuelve el objeto compatible con loader.py
    
    Args:
        filepath: Ruta del archivo PDF
        file_hash: Hash SHA256 del archivo (calculado en main.py)
        
    Retorna:
        Diccionario con los datos extraídos o None si falla.
    """
    try:
        doc = fitz.open(filepath)
        text = doc[0].get_text("text", sort=True)
        doc.close()

        # Extraer metadata y mediciones
        patient, report = extract_metadata(text)
        measurements = extract_measurements(text)

        # Extraer información del archivo
        file_info = {
            "filename": filepath.name,
            "file_hash": file_hash,
        }

        # Empaquetar todo en el formato esperado por loader.py
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
