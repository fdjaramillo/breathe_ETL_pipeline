"""
dispatcher.py
-------------
Identifies the type of a PDF report by scanning keywords on the first page,
then maps the detected type to the corresponding extractor function.

Priority order (checked top to bottom):
  1. VH_BLOOD      — Vall d'Hebron blood tests
  2. CLINIC_BLOOD  — Hospital Clínic blood tests
  3. SPIROMETRY    — Spirometry reports (any centre)
  4. UNKNOWN       — Unrecognised format
"""

import logging
from pathlib import Path

import fitz  # PyMuPDF

import extractor_blood_test
import extractor_vh_blood_test
import extractor_spiro

# ---------------------------------------------------------------------------
# Public type constants
# ---------------------------------------------------------------------------
CLINIC_BLOOD = "CLINIC_BLOOD"
VH_BLOOD = "VH_BLOOD"
SPIROMETRY = "SPIROMETRY"
UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# Keyword rules — evaluated in order; first match wins.
# Each entry is (file_type, [list of candidate keywords]).
# ---------------------------------------------------------------------------
_PRIORITY_RULES = [
    (
        VH_BLOOD,
        [
            "Vall d'Hebron",
            "Vall d Hebron",
            "VALL D'HEBRON",
            "HOSPITAL UNIVERSITARI VALL",
        ],
    ),
    (
        CLINIC_BLOOD,
        ["Hospital Clínic", "Hospital Clinic", "HOSPITAL CLÍNIC", "Clínic Barcelona"],
    ),
    (
        SPIROMETRY,
        ["FEV1", "FVC", "ESPIROMETRIA", "ESPIROMETRÍA", "SPIROMETRY", "SPIROMETRIA"],
    ),
]

# ---------------------------------------------------------------------------
# Public dispatch map: detected type -> extractor callable
# ---------------------------------------------------------------------------
FILE_TYPE_TO_EXTRACTOR = {
    CLINIC_BLOOD: extractor_blood_test.process_pdf,
    VH_BLOOD: extractor_vh_blood_test.process_pdf,
    SPIROMETRY: extractor_spiro.process_pdf,
}


def identify_file_type(filepath) -> str:
    """
    Opens the first page of *filepath* with PyMuPDF and returns a file-type
    constant based on keyword matching.

    Args:
        filepath: Path-like object pointing to a PDF file.

    Returns:
        One of: CLINIC_BLOOD, VH_BLOOD, SPIROMETRY, UNKNOWN.
    """
    filepath = Path(filepath)
    doc = None
    try:
        doc = fitz.open(str(filepath))

        if doc.page_count == 0:
            logging.warning(f"  → [DISPATCHER] PDF sin páginas: {filepath.name}")
            return UNKNOWN

        first_page_text = doc[0].get_text("text")

        for file_type, keywords in _PRIORITY_RULES:
            for kw in keywords:
                if kw in first_page_text:
                    logging.info(
                        f"  → [DISPATCHER] Tipo identificado: {file_type} "
                        f"(keyword: '{kw}')"
                    )
                    return file_type

        logging.warning(f"  → [DISPATCHER] Tipo no reconocido para: {filepath.name}")
        return UNKNOWN

    except Exception as e:
        logging.error(f"  → [DISPATCHER] Error al abrir PDF {filepath.name}: {e}")
        return UNKNOWN

    finally:
        if doc is not None:
            doc.close()
