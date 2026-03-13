import csv
import logging

from utils.normalization import normalize_date, normalize_sex


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
                logging.warning(
                    "    [WARN] Fila %s: subject_id vacío, omitida.", row_num
                )
                continue

            # Normalizar fecha de nacimiento
            raw_birth = row.get("birth_date", "").strip()
            normalized_birth = normalize_date(raw_birth)

            if raw_birth and not normalized_birth:
                logging.warning(
                    "    [WARN] Paciente %s: formato de fecha inválido '%s', se guardará como NULL.",
                    subject_id,
                    raw_birth,
                )

            # Normalizar sexo
            raw_sex = row.get("sex", "").strip()
            normalized_sex = normalize_sex(raw_sex)

            if raw_sex and not normalized_sex:
                logging.warning(
                    "    [WARN] Paciente %s: valor de sexo irreconocible '%s', se guardará como NULL.",
                    subject_id,
                    raw_sex,
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
