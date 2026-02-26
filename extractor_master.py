import csv
import re
from datetime import datetime


def normalize_date(date_str):
    """
    Normaliza fechas en formatos mixtos a ISO (YYYY-MM-DD).
    Retorna None si el formato es irreconocible.
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    # Intentar parsear múltiples formatos
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Si ningún formato coincide, retornar None
    return None


def normalize_sex(sex_str):
    """
    Normaliza el campo sexo a 'M', 'F' o None.
    Acepta variantes en español e inglés (case-insensitive).
    """
    if not sex_str:
        return None

    sex_str = sex_str.strip().upper()

    # Mapeo de variantes
    male_variants = ["M", "MALE", "HOMBRE", "MASCULINO", "H"]
    female_variants = ["F", "FEMALE", "MUJER", "FEMENINO"]

    if sex_str in male_variants:
        return "M"
    elif sex_str in female_variants:
        return "F"
    else:
        return None  # Valor irreconocible


def process_master_csv(filepath, file_hash):
    """
    Lee un archivo maestro de pacientes y normaliza los datos.

    Args:
        filepath: Ruta del CSV maestro
        file_hash: Hash SHA256 del archivo

    Returns:
        Lista de diccionarios con datos demográficos normalizados
    """
    results = []

    with open(filepath, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row_num, row in enumerate(reader, start=2):  # start=2 por header
            subject_id = row.get("subject_id", "").strip()

            if not subject_id:
                # Log de advertencia: fila sin ID
                print(f"    [WARN] Fila {row_num}: subject_id vacío, omitida.")
                continue

            # Normalizar fecha de nacimiento
            raw_birth = row.get("birth_date", "").strip()
            normalized_birth = normalize_date(raw_birth)

            if raw_birth and not normalized_birth:
                print(
                    f"    [WARN] Paciente {subject_id}: formato de fecha inválido '{raw_birth}', se guardará como NULL."
                )

            # Normalizar sexo
            raw_sex = row.get("sex", "").strip()
            normalized_sex = normalize_sex(raw_sex)

            if raw_sex and not normalized_sex:
                print(
                    f"    [WARN] Paciente {subject_id}: valor de sexo irreconocible '{raw_sex}', se guardará como NULL."
                )

            results.append(
                {
                    "file_info": {"filename": filepath.name, "file_hash": file_hash},
                    "subject_id": subject_id,
                    "birth_date": normalized_birth,
                    "sex": normalized_sex,
                    "source_system": row.get("source_system", "master_csv").strip(),
                }
            )

    return results
