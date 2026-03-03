import pandas as pd
from datetime import datetime
import numpy as np
from pathlib import Path


def normalize_date(date_val):
    """Fuerza el formato YYYY-MM-DD."""
    if pd.isna(date_val):
        return None
    try:
        if isinstance(date_val, datetime):
            return date_val.strftime("%Y-%m-%d")
        return pd.to_datetime(date_val).strftime("%Y-%m-%d")
    except Exception:
        return None


def process_excel(filepath, file_hash):
    """
    Extrae datos de cuestionarios desde un archivo Excel multicapa.
    Requiere una hoja llamada 'entry' con mapeo de fechas.
    """
    try:
        xls = pd.ExcelFile(filepath)
    except Exception as e:
        print(f"    [ERROR CRÍTICO] Imposible leer el Excel {filepath.name}: {e}")
        return []

    if "entry" not in xls.sheet_names:
        raise ValueError(
            f"El archivo {filepath.name} carece de la hoja obligatoria 'entry'."
        )

    # 1. Extraer mapeo de fechas (asumiendo columnas 'subject_id' y 'entry_date')
    df_entry = pd.read_excel(xls, sheet_name="entry")

    # Asegurar que las columnas existen ignorando mayúsculas/minúsculas
    df_entry.columns = df_entry.columns.str.lower().str.strip()
    if not {"id", "entry_date"}.issubset(set(df_entry.columns)) and not {
        "subject_id",
        "entry_date",
    }.issubset(set(df_entry.columns)):
        id_col = "subject_id" if "subject_id" in df_entry.columns else "id"
        if id_col not in df_entry.columns or "entry_date" not in df_entry.columns:
            raise ValueError(
                "La hoja 'entry' debe contener las columnas 'id' (o 'subject_id') y 'entry_date'."
            )

    id_col_name = "subject_id" if "subject_id" in df_entry.columns else "id"

    # Crear diccionario de fechas: { 'HCB001': '2023-10-12' }
    df_entry["entry_date"] = df_entry["entry_date"].apply(normalize_date)
    date_mapping = dict(zip(df_entry[id_col_name].astype(str), df_entry["entry_date"]))

    results = []

    # 2. Procesar cada hoja de cuestionario
    for sheet in xls.sheet_names:
        if sheet.lower() in ["entry", "observaciones", "metadata"]:
            continue

        df = pd.read_excel(xls, sheet_name=sheet)
        df.columns = df.columns.astype(str).str.strip()

        # Validación estricta de estructura
        if "question" not in df.columns.str.lower():
            raise ValueError(f"Hoja '{sheet}': Falta la columna pivote 'question'.")

        # Estandarizar nombre de la columna pivote
        col_idx = df.columns.str.lower().get_loc("question")
        df.columns.values[col_idx] = "question"

        # Pivotaje (Wide to Long)
        df_long = df.melt(
            id_vars=["question"], var_name="subject_id", value_name="value"
        )

        # Limpieza: eliminar NAs y respuestas vacías
        df_long = df_long[df_long["value"].astype(str).str.strip() != ""]

        # Agrupar por paciente para construir los bloques
        for subject_id, group in df_long.groupby("subject_id"):
            subject_id_str = str(subject_id).strip()

            # Validación de integridad: ¿El paciente tiene fecha de entrada registrada?
            if subject_id_str not in date_mapping:
                print(
                    f"    [WARN] ID {subject_id_str} en hoja '{sheet}' no tiene fecha en 'entry'. Se ignora."
                )
                continue

            # Construir bloque de datos
            block = {
                "file_info": {"filename": filepath.name, "file_hash": file_hash},
                "subject_id": subject_id_str,
                "questionnaire": {
                    "name": sheet,
                    "entry_date": date_mapping[subject_id_str],
                },
                "responses": group[["question", "value"]].to_dict("records"),
            }
            results.append(block)

    return results
