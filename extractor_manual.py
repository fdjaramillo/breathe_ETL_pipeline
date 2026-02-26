import csv
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import re


def normalize_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def process_manual_csv(data_filepath, file_hash):
    """
    Procesa batch de datos manuales.
    Cruza el archivo (data) con su equivalente (metadata).
    """
    report_type = None
    if "spirometry" in data_filepath.name.lower():
        report_type = "spirometry"
    elif "blood" in data_filepath.name.lower():
        report_type = "blood_test"
    else:
        print(
            f"    [WARN] No se pudo determinar el tipo de reporte para el archivo: {data_filepath.name}. Se omitirá."
        )
        return []

    # cargar archivo de metadatos asociado
    meta_filepath = data_filepath.parent / data_filepath.name.replace(
        "(data)", "(metadata)"
    )

    metadata = {}
    if meta_filepath.exists():
        with open(meta_filepath, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                metadata[row.get("id", "").strip()] = {
                    "date": normalize_date(row.get("report_date", "")),
                    "source": row.get("source", "manual_csv").strip(),
                }
    else:
        print(
            f"    [WARN] Archivo de metadatos no encontrado para: {data_filepath.name}"
        )

    blocks = defaultdict(list)
    UNIT_PATTERN = re.compile(r"^(.*?)\s*[\(\[]([^()\[\]]+)[\)\]]$")

    with open(data_filepath, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            subject_id = row.get("id", "").strip()
            raw_parameter = row.get("parameter", "").strip()
            value = row.get("value", "").strip()
            unit = row.get("unit", "").strip()

            if not subject_id or not raw_parameter or not value:
                continue

            if report_type == "spirometry":
                # Si no hay .get("unit") en el CSV, se intentará extraer la unidad del nombre del parámetro usando el patrón regex.
                # Extraer unidad y parámetro limpio
                match = UNIT_PATTERN.match(raw_parameter)
                if match:
                    parameter = match.group(1).strip()
                    unit = match.group(2).strip()
                else:
                    # Fallback si el parámetro viene sin unidad en alguna fila
                    parameter = raw_parameter
                    unit = None

                # Agrupar por paciente
                blocks[subject_id].append(
                    {
                        "parameter": parameter,
                        "unit": unit,
                        "phase": row.get("phase", "").strip(),
                        "value": value,
                        "theoretical": row.get("theoretical", "").strip(),
                        "lin": row.get("lin", "").strip(),
                        "z_score": row.get("z_score", "").strip(),
                        "perc_theoretical": row.get("perc_theoretical", "").strip(),
                        "perc_change": row.get("perc_change", "").strip(),
                    }
                )
            elif report_type == "blood_test":
                # Agrupar por paciente
                blocks[subject_id].append(
                    {
                        "parameter": raw_parameter,
                        "value": value,
                        "unit": unit,
                        "reference_range": row.get("reference_range"),
                        "value_in_bold": row.get("value_in_bold"),
                    }
                )

    results = []
    for subject_id, measurements in blocks.items():
        results.append(
            {
                "file_info": {"filename": data_filepath.name, "file_hash": file_hash},
                "subject_id": subject_id,
                "report": {
                    "report_type": report_type,
                    "report_date": metadata.get(subject_id, {}).get("date"),
                    "source_file_type": metadata.get(subject_id, {}).get("source"),
                },
                "measurements": measurements,
            }
        )
    return results
