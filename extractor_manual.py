import logging
from collections import defaultdict
import re

import pandas as pd

from utils.normalization import normalize_date


_SPIRO_COLUMNS = {
    "phase",
    "theoretical",
    "lin",
    "z_score",
    "perc_theoretical",
    "perc_change",
}


def _safe_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _resolve_manual_report_type(excel_filepath):
    name = excel_filepath.name.lower()
    parent_name = excel_filepath.parent.name.lower()

    if "spirometry" in name or "spirometry" in parent_name:
        return "spirometry"
    if "blood" in name or "blood" in parent_name:
        return "blood_test"
    return None


def _validate_required_columns(df, required_columns, sheet_name, file_name):
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        logging.error(
            "    [ERROR] %s: faltan columnas %s en la hoja '%s' de %s.",
            sheet_name,
            missing,
            sheet_name,
            file_name,
        )
        return False
    return True


def _load_metadata(df_metadata):
    metadata = {}
    for _, row in df_metadata.iterrows():
        subject_id = _safe_text(row.get("id"))
        if not subject_id:
            continue

        metadata[subject_id] = {
            "date": normalize_date(row.get("report_date")),
            "source": _safe_text(row.get("source")) or "manual_excel",
        }

    return metadata


def _build_measurement_block(row, report_type, unit_pattern):
    subject_id = _safe_text(row.get("id"))
    raw_parameter = _safe_text(row.get("parameter"))
    value = _safe_text(row.get("value"))
    unit = _safe_text(row.get("unit"))

    if not subject_id or not raw_parameter or not value:
        return None, None

    if report_type == "spirometry":
        match = unit_pattern.match(raw_parameter)
        if match:
            parameter = match.group(1).strip()
            inferred_unit = match.group(2).strip()
            if inferred_unit:
                unit = inferred_unit
        else:
            parameter = raw_parameter

        measurement = {
            "parameter": parameter,
            "unit": unit or None,
            "phase": _safe_text(row.get("phase")),
            "value": value,
            "theoretical": _safe_text(row.get("theoretical")),
            "lin": _safe_text(row.get("lin")),
            "z_score": _safe_text(row.get("z_score")),
            "perc_theoretical": _safe_text(row.get("perc_theoretical")),
            "perc_change": _safe_text(row.get("perc_change")),
        }
    else:
        measurement = {
            "section": _safe_text(row.get("section")) or None,
            "parameter": raw_parameter,
            "value": value,
            "unit": unit,
            "reference_range": _safe_text(row.get("reference_range")) or None,
            "value_in_bold": _safe_text(row.get("value_in_bold")) or None,
        }

    return subject_id, measurement


def process_manual_excel(excel_filepath, file_hash):
    """
    Procesa un workbook manual con dos hojas obligatorias: 'data' y 'metadata'.

    El tipo de reporte se infiere por nombre de archivo/carpeta:
    - spirometry -> reporte de espirometría
    - blood      -> reporte de análisis de sangre
    """
    report_type = _resolve_manual_report_type(excel_filepath)
    if not report_type:
        logging.warning(
            "    [WARN] No se pudo determinar el tipo de reporte para el archivo Excel: %s. Se omitirá.",
            excel_filepath.name,
        )
        return []

    try:
        xls = pd.ExcelFile(excel_filepath)
    except (FileNotFoundError, OSError, ValueError) as error:
        logging.error(
            "    [ERROR] No se pudo abrir el Excel manual %s: %s",
            excel_filepath.name,
            error,
        )
        return []
    except Exception:
        logging.exception(
            "    [ERROR] Fallo inesperado abriendo Excel manual %s",
            excel_filepath.name,
        )
        return []

    normalized_sheet_names = {sheet.lower(): sheet for sheet in xls.sheet_names}
    if "data" not in normalized_sheet_names or "metadata" not in normalized_sheet_names:
        logging.error(
            "    [ERROR] Excel manual %s debe contener hojas 'data' y 'metadata'. Hojas encontradas: %s",
            excel_filepath.name,
            xls.sheet_names,
        )
        return []

    data_sheet = normalized_sheet_names["data"]
    metadata_sheet = normalized_sheet_names["metadata"]

    df_data = pd.read_excel(xls, sheet_name=data_sheet)
    df_metadata = pd.read_excel(xls, sheet_name=metadata_sheet)

    df_data.columns = df_data.columns.astype(str).str.strip().str.lower()
    df_metadata.columns = df_metadata.columns.astype(str).str.strip().str.lower()

    if not _validate_required_columns(
        df_data,
        ["id", "parameter", "value"],
        data_sheet,
        excel_filepath.name,
    ):
        return []

    required_meta_columns = ["id", "report_date"]
    if not _validate_required_columns(
        df_metadata,
        required_meta_columns,
        metadata_sheet,
        excel_filepath.name,
    ):
        return []

    if "source" not in df_metadata.columns:
        df_metadata["source"] = "manual_excel"

    if report_type == "spirometry":
        missing_spiro_cols = _SPIRO_COLUMNS.difference(set(df_data.columns))
        if missing_spiro_cols:
            logging.warning(
                "    [WARN] %s: columnas opcionales de espirometría no encontradas: %s",
                excel_filepath.name,
                sorted(missing_spiro_cols),
            )
    elif "section" not in df_data.columns:
        df_data["section"] = None
    if "unit" not in df_data.columns:
        df_data["unit"] = None
    if "reference_range" not in df_data.columns:
        df_data["reference_range"] = None
    if "value_in_bold" not in df_data.columns:
        df_data["value_in_bold"] = None

    metadata = _load_metadata(df_metadata)

    blocks = defaultdict(list)
    unit_pattern = re.compile(r"^(.*?)\s*[\(\[]([^()\[\]]+)[\)\]]$")

    for _, row in df_data.iterrows():
        subject_id, measurement = _build_measurement_block(
            row, report_type, unit_pattern
        )
        if not subject_id:
            continue
        blocks[subject_id].append(measurement)

    if not blocks:
        logging.warning(
            "    [WARN] No se encontraron mediciones válidas en el Excel manual %s",
            excel_filepath.name,
        )
        return []

    results = []
    for subject_id, measurements in blocks.items():
        results.append(
            {
                "file_info": {
                    "filename": excel_filepath.name,
                    "file_hash": file_hash,
                    "source_file_type": metadata.get(subject_id, {}).get("source"),
                },
                "subject_id": subject_id,
                "report": {
                    "report_type": report_type,
                    "report_date": metadata.get(subject_id, {}).get("date"),
                },
                "measurements": measurements,
            }
        )

    return results
