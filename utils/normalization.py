from datetime import datetime
import re
import pandas as pd
from rapidfuzz import process, fuzz
import unicodedata

_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

_MALE_VARIANTS = {"M", "MALE", "HOMBRE", "MASCULINO", "H", "HOME"}
_FEMALE_VARIANTS = {"F", "FEMALE", "MUJER", "FEMENINO", "DONA"}


def normalize_date(date_value):
    """
    Normalize dates from strings, datetimes, pandas timestamps, or Excel-like values.

    Returns ISO format (YYYY-MM-DD) or None when the value is empty/invalid.
    """
    if date_value is None:
        return None

    if hasattr(pd, "isna") and pd.isna(date_value):
        return None

    if isinstance(date_value, datetime):
        return date_value.strftime("%Y-%m-%d")

    if hasattr(date_value, "to_pydatetime"):
        return date_value.to_pydatetime().strftime("%Y-%m-%d")

    if isinstance(date_value, str):
        cleaned_value = date_value.strip()
        if not cleaned_value:
            return None

        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(cleaned_value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

        try:
            return pd.to_datetime(cleaned_value).strftime("%Y-%m-%d")
        except Exception:
            return None

    try:
        return pd.to_datetime(date_value).strftime("%Y-%m-%d")
    except Exception:
        return None


def normalize_sex(sex_value):
    """
    Normalize sex values to 'M', 'F', or None.
    """
    if sex_value is None:
        return None

    if hasattr(pd, "isna") and pd.isna(sex_value):
        return None

    cleaned_value = str(sex_value).strip().upper()
    if not cleaned_value:
        return None

    if cleaned_value in _MALE_VARIANTS:
        return "M"
    if cleaned_value in _FEMALE_VARIANTS:
        return "F"
    return None


def normalize_clinical_name(text):
    if not text:
        return ""
    normalized = "".join(
        c
        for c in unicodedata.normalize("NFKD", text)
        if unicodedata.category(c) != "Mn"
    )
    normalized = re.sub(r"[^0-9A-Za-z\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower().strip()


def find_match(name, mapping_dict, threshold=90):
    """
    name: nombre extraído del PDF
    mapping_dict: {nombre_csv_limpio: id_anonimo}
    """
    if not name or not mapping_dict:
        return None, 0

    cleaned_pdf = normalize_clinical_name(name)
    if not cleaned_pdf:
        return None, 0

    # Extraer la mejor coincidencia
    # mapping_dict.keys() contiene los nombres del CSV ya normalizados
    result = process.extractOne(
        cleaned_pdf, mapping_dict.keys(), scorer=fuzz.token_set_ratio
    )

    if result and result[1] >= threshold:
        match_name = result[0]
        return mapping_dict[match_name], result[1]

    return None, 0
