import re

from utils.normalization import normalize_date, normalize_sex


_METADATA_PATTERNS = {
    "BLOOD_TEST_CLINIC": {
        "nhc": re.compile(r"NHC\s*:\s*([A-Za-z0-9]+)"),
        # name
        "birth_date": re.compile(r"Fecha nac\./Data naix\.\s*:\s*(\d{2}/\d{2}/\d{4})"),
        "sex": re.compile(r"Sexo/Sexe\s*:\s*([A-Za-z]+)"),
        "lab_request_number": re.compile(r"N\. Sol·licitud Lab\.\s*:\s*([0-9]+)"),
        "report_date": re.compile(r"Data recepció mostra\s*:\s*(\d{2}/\d{2}/\d{4})"),
    },
    "BLOOD_TEST_VH": {
        "nhc": re.compile(r"NHC\s*:\s*([A-Za-z0-9]+)"),
        "name": re.compile(r"Pacient:\s*(.+?)\s{2,}Petició:"),
        "birth_date": re.compile(r"Naixement\s*:\s*(\d{2}/\d{2}/\d{4})"),
        "sex": re.compile(r"Sexe\s*:\s*([A-Za-z]+)"),
        "lab_request_number": re.compile(r"Petició\s*:\s*([0-9]+)"),
        "report_date": re.compile(r"Recepció\s*:\s*(\d{1,2}/\d{1,2}/\d{2})"),
    },
    "SPIROMETRY_CLINIC": {
        "nhc": re.compile(r"NHC\s*:?\s*(\w+).*?Edat"),
        # name
        "edad": re.compile(r"Edat\s*\(anys\):\s*(\d+)"),
        "sex": re.compile(r"Sexe\s*:\s*([A-Za-z]+)"),
        "height": re.compile(r"Alçada\s*\(cm\):\s*(\d+(?:\.\d+)?)"),
        "weight": re.compile(r"Pes\s*\(Kg\):\s*(\d+(?:\.\d+)?)"),
        "report_date": re.compile(
            r"Data exploraci[oó]\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4})"
        ),
    },
    "SPIROMETRY_CAP": {
        "nhc": re.compile(r"NHC\s*:\s*([A-Za-z0-9]+)"),
        "name": re.compile(r"Nombre:\s*(.+)"),
        "edad": re.compile(r"Edad\s*\(a\):\s*(\d+)"),
        "sex": re.compile(r"Sexo\s*:\s*([A-Za-z]+)"),
        "height": re.compile(r"Talla\(cm\):\s*(\d+(?:\.\d+)?)"),
        "weight": re.compile(r"Peso\(Kg\):\s*(\d+(?:\.\d+)?)"),
        "report_date": re.compile(r"Fecha:\s*([0-9]{2}-[0-9]{2}-[0-9]{4})"),
    },
}


def get_report_metadata(page_text: str, source_type: str):
    """
    Extract and normalize common blood-test metadata from the first page text.

    Returns:
        Tuple[dict, dict] with (patient_info, report_info).
    """
    page_text = page_text or ""
    patterns = _METADATA_PATTERNS.get(source_type, {})

    patient_info = {}

    nhc_match = patterns["nhc"].search(page_text)
    if nhc_match:
        patient_info["nhc"] = nhc_match.group(1).strip().lstrip("0")

    name_match = patterns["name"].search(page_text)
    if name_match:
        patient_info["name"] = name_match.group(1).strip()

    birth_date_match = patterns["birth_date"].search(page_text)
    if birth_date_match:
        patient_info["birth_date"] = normalize_date(birth_date_match.group(1))

    sex_match = patterns["sex"].search(page_text)
    if sex_match:
        patient_info["sex"] = normalize_sex(sex_match.group(1).strip())

    height_match = patterns["height"].search(page_text)
    if height_match:
        patient_info["height_cm"] = float(height_match.group(1))

    weight_match = patterns["weight"].search(page_text)
    if weight_match:
        patient_info["weight_kg"] = float(weight_match.group(1))

    report_info = {"report_type": "blood_test"}

    lab_request_match = patterns["lab_request_number"].search(page_text)
    if lab_request_match:
        report_info["lab_request_number"] = lab_request_match.group(1).strip()

    report_date_match = patterns["report_date"].search(page_text)
    if report_date_match:
        report_info["report_date"] = normalize_date(report_date_match.group(1))

    return patient_info, report_info
