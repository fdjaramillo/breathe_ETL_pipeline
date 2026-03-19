import re

from utils.normalization import normalize_date, normalize_sex


_SOURCE_ALIASES = {
    "BLOOD_TEST_CLINIC": "clinic_blood_test",
    "BLOOD_TEST_VH": "vh_blood_test",
    "clinic": "clinic_blood_test",
    "vh": "vh_blood_test",
}


_METADATA_PATTERNS = {
    "clinic_blood_test": {
        "nhc": re.compile(r"NHC\s*:\s*([A-Za-z0-9]+)"),
        "birth_date": re.compile(r"Fecha nac\./Data naix\.\s*:\s*(\d{2}/\d{2}/\d{4})"),
        "sex": re.compile(r"Sexo/Sexe\s*:\s*([A-Za-z])"),
        "lab_request_number": re.compile(r"N\. Sol·licitud Lab\.\s*:\s*([0-9]+)"),
        "report_date": re.compile(r"Data recepció mostra\s*:\s*(\d{2}/\d{2}/\d{4})"),
    },
    "vh_blood_test": {
        "nhc": re.compile(r"NHC\s*:\s*([A-Za-z0-9]+)"),
        "birth_date": re.compile(r"Naixement\s*:\s*(\d{2}/\d{2}/\d{4})"),
        "sex": re.compile(r"Sexe\s*:\s*([A-Za-z])"),
        "lab_request_number": re.compile(r"Petició\s*:\s*([0-9]+)"),
        "report_date": re.compile(r"Recepció\s*:\s*(\d{1,2}/\d{1,2}/\d{2})"),
    },
}


def _resolve_source_type(source_type: str) -> str:
    if not source_type:
        return "clinic_blood_test"

    normalized = _SOURCE_ALIASES.get(source_type, source_type)
    return normalized if normalized in _METADATA_PATTERNS else "clinic_blood_test"


def get_report_metadata(page_text: str, source_type: str):
    """
    Extract and normalize common blood-test metadata from the first page text.

    Returns:
        Tuple[dict, dict] with (patient_info, report_info).
    """
    page_text = page_text or ""
    patterns = _METADATA_PATTERNS[_resolve_source_type(source_type)]

    patient_info = {}

    nhc_match = patterns["nhc"].search(page_text)
    if nhc_match:
        patient_info["nhc"] = nhc_match.group(1).strip()

    birth_date_match = patterns["birth_date"].search(page_text)
    if birth_date_match:
        patient_info["birth_date"] = normalize_date(birth_date_match.group(1))

    sex_match = patterns["sex"].search(page_text)
    if sex_match:
        patient_info["sex"] = normalize_sex(sex_match.group(1).strip())

    report_info = {"report_type": "blood_test"}

    lab_request_match = patterns["lab_request_number"].search(page_text)
    if lab_request_match:
        report_info["lab_request_number"] = lab_request_match.group(1).strip()

    report_date_match = patterns["report_date"].search(page_text)
    if report_date_match:
        report_info["report_date"] = normalize_date(report_date_match.group(1))

    return patient_info, report_info
