import logging
from pathlib import Path

import fitz

from extractor_blood_test import ClinicBloodTestExtractor, debug_measurements
from extractor_vh_blood_test import VHBloodTestExtractor


def _process_with(strategy_cls, filepath, file_hash):
    result = strategy_cls(filepath, file_hash).process()
    if not result or not result.get("measurements"):
        return None
    return result


def process_clinic_pdf(filepath, file_hash):
    try:
        return _process_with(ClinicBloodTestExtractor, filepath, file_hash)
    except (
        fitz.FileDataError,
        IndexError,
        AttributeError,
        RuntimeError,
        ValueError,
    ) as error:
        logging.error("Error procesando %s: %s", Path(filepath).name, error)
        return None
    except Exception:
        logging.exception("Error inesperado procesando %s", Path(filepath).name)
        return None


def process_vh_pdf(filepath, file_hash):
    try:
        return _process_with(VHBloodTestExtractor, filepath, file_hash)
    except (
        fitz.FileDataError,
        IndexError,
        AttributeError,
        RuntimeError,
        ValueError,
    ) as error:
        logging.error("Error procesando %s: %s", Path(filepath).name, error)
        return None
    except Exception:
        logging.exception("Error inesperado procesando %s", Path(filepath).name)
        return None
