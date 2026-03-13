import logging
from pathlib import Path

import fitz  # PyMuPDF

import extractor_blood
import extractor_spiro

# ---------------------------------------------------------------------------
# Public type constants
# ---------------------------------------------------------------------------
BLOOD_TEST_CLINIC = "BLOOD_TEST_CLINIC"
BLOOD_TEST_VH = "BLOOD_TEST_VH"
SPIROMETRY_CLINIC = "SPIROMETRY_CLINIC"
SPIROMETRY_CAP = "SPIROMETRY_CAP"
UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# Keyword rules — evaluated in order; first matching AND-set wins.
# Each rule contains OR-alternative sets, where every term inside a set must
# appear in the first-page text.
# ---------------------------------------------------------------------------
_PRIORITY_RULES = [
    {
        "file_type": SPIROMETRY_CLINIC,
        "and_sets": [
            ["CENTRE DIAGNÒSTIC RESPIRATORI", "FEV1"],
            ["PNEUMOLOGIA", "FEV1"],
            ["HOSPITAL CLÍNIC", "FEV1"],
        ],
    },
    {
        "file_type": SPIROMETRY_CAP,
        "and_sets": [
            ["CAP", "FEV1"],
            ["Atencio Primaria", "FEV1"],
        ],
    },
    {
        "file_type": BLOOD_TEST_CLINIC,
        "and_sets": [
            ["LABORATORI CENTRAL", "BIOQUÍMICA GENERAL"],
            ["HOSPITAL CLÍNIC", "BIOQUÍMICA GENERAL"],
            ["Clínic Barcelona"],
        ],
    },
    {
        "file_type": BLOOD_TEST_VH,
        "and_sets": [
            ["Laboratoris Clínics Vall d'Hebron"],
            ["Vall d'Hebron"],
            ["Vall d Hebron"],
        ],
    },
]

# ---------------------------------------------------------------------------
# Public dispatch map: detected type -> extractor callable
# ---------------------------------------------------------------------------
FILE_TYPE_TO_EXTRACTOR = {
    BLOOD_TEST_CLINIC: extractor_blood.process_clinic_pdf,
    BLOOD_TEST_VH: extractor_blood.process_vh_pdf,
    SPIROMETRY_CLINIC: extractor_spiro.process_pdf,
    SPIROMETRY_CAP: extractor_spiro.process_pdf,
}


def identify_file_type(filepath) -> str:
    """
    Opens the first page of *filepath* with PyMuPDF and returns a file-type
    constant based on keyword matching.

    Args:
        filepath: Path-like object pointing to a PDF file.

    Returns:
        One of: BLOOD_TEST_CLINIC, BLOOD_TEST_VH, SPIROMETRY_CLINIC,
        SPIROMETRY_CAP, UNKNOWN.
    """
    filepath = Path(filepath)
    doc = None
    try:
        doc = fitz.open(str(filepath))

        if doc.page_count == 0:
            logging.warning(f"  → [DISPATCHER] PDF sin páginas: {filepath.name}")
            return UNKNOWN

        first_page_text = doc[0].get_text("text")

        for rule in _PRIORITY_RULES:
            file_type = rule["file_type"]
            for and_set in rule["and_sets"]:
                if all(term in first_page_text for term in and_set):
                    logging.info(
                        f"  → [DISPATCHER] Tipo identificado: {file_type} "
                        f"(combinación: {and_set})"
                    )
                    return file_type

        logging.warning(f"  → [DISPATCHER] Tipo no reconocido para: {filepath.name}")
        return UNKNOWN

    except (fitz.FileDataError, RuntimeError, ValueError) as error:
        logging.error(
            "  → [DISPATCHER] Error al abrir PDF %s: %s",
            filepath.name,
            error,
        )
        return UNKNOWN
    except Exception:
        logging.exception(
            "  → [DISPATCHER] Error inesperado al abrir PDF %s", filepath.name
        )
        return UNKNOWN

    finally:
        if doc is not None:
            doc.close()
