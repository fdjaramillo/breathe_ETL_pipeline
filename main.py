import sqlite3
import csv
import hashlib
import logging
from pathlib import Path
import importlib

import schema
import dispatcher
import audit_logger
import extractor_macro
import extractor_manual
import extractor_master
import extractor_questionnaires
import loader
from utils.config import load_config


def get_file_hash(filepath):
    """
    Calcula el hash SHA256 de un archivo.

    Args:
        filepath: Ruta del archivo

    Returns:
        String con el hash SHA256 en hexadecimal
    """
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_nhc_mapping(csv_path):
    """
    Lee el archivo CSV de mapeo NHC -> ID y retorna un diccionario.
    RETORNA NONE si el archivo no existe (sin abortar).

    Args:
        csv_path: Ruta del archivo CSV

    Returns:
        Diccionario {nhc: subject_id} o None si falla
    """
    csv_path = Path(csv_path)

    if not csv_path.exists():
        logging.warning(f"Archivo de mapeo no encontrado: {csv_path}")
        return None

    nhc_mapping = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row and "nhc" in row and "id" in row:
                    nhc = row["nhc"].strip()
                    subject_id = row["id"].strip()
                    nhc_mapping[nhc] = subject_id

        logging.info(
            f"Cargado mapeo NHC -> ID: {len(nhc_mapping)} registros desde {csv_path}"
        )
        return nhc_mapping

    except (OSError, csv.Error) as error:
        logging.error("Error leyendo archivo de mapeo %s: %s", csv_path, error)
        return None
    except Exception:
        logging.exception("Error inesperado leyendo archivo de mapeo %s", csv_path)
        return None


def is_file_processed(file_hash, db_path):
    """
    Consulta la base de datos para ver si este hash ya existe.
    Retorna True si el archivo ya fue procesado anteriormente.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM processed_files WHERE file_hash = ?", (file_hash,)
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except sqlite3.Error:
        return False


def _resolve_patient_id(block, nhc_mapping):
    """
    Best-effort extraction of a patient identifier from a data block.
    Used only for audit CSV enrichment — never blocks processing.
    """
    subject_id = block.get("subject_id")
    if subject_id:
        return str(subject_id)

    nhc = block.get("patient", {}).get("nhc")
    if nhc:
        if nhc_mapping:
            return nhc_mapping.get(nhc, nhc)
        return nhc

    return None


def _debug_measurements_if_available(extract_func, data_result):
    """
    Executes extractor-specific debug_measurements() only when available.
    """
    module = importlib.import_module(extract_func.__module__)
    debug_fn = getattr(module, "debug_measurements", None)
    if callable(debug_fn):
        debug_fn(data_result)


def process_files(
    directory, db_path, extract_func=None, nhc_mapping=None, extension="pdf"
):
    """
    Processes files recursively under *directory*.

    If extract_func is None, the extractor is selected dynamically through the
    PDF dispatcher. Otherwise, the provided extractor is used directly.

    Args:
        directory: Root directory to search recursively.
        db_path: SQLite database path.
        extract_func: Optional extraction callable.
        nhc_mapping: Optional {nhc: subject_id} dict for PHI de-identification.
        extension: File extension to search for.

    Returns:
        Tuple (processed_count, skipped_count, error_count).
    """
    directory = Path(directory)

    if not directory.is_dir():
        logging.warning(f"Directorio no encontrado: {directory}")
        return 0, 0, 0

    target_files = sorted(directory.rglob(f"*.{extension}"))
    total_files = len(target_files)

    if total_files == 0:
        logging.info(
            f"No se encontraron archivos {extension} en {directory} (búsqueda recursiva)"
        )
        return 0, 0, 0

    logging.info(
        f"Encontrados {total_files} archivos {extension} en {directory} (búsqueda recursiva)"
    )

    processed_count = 0
    skipped_count = 0
    error_count = 0

    for index, filepath in enumerate(target_files, start=1):
        logging.info(f"[{index}/{total_files}] Procesando: {filepath}")

        try:
            # A. Hash + idempotencia
            current_hash = get_file_hash(filepath)
            if is_file_processed(current_hash, db_path):
                logging.info(
                    "  → [SKIP] Archivo ya procesado previamente (Hash coincide)."
                )
                audit_logger.log_to_master_csv(
                    filepath, "SKIPPED", reason="Hash duplicado"
                )
                skipped_count += 1
                continue

            # B. Selección de extractor
            selected_extract_func = extract_func
            if selected_extract_func is None:
                file_type = dispatcher.identify_file_type(filepath)
                selected_extract_func = dispatcher.FILE_TYPE_TO_EXTRACTOR.get(file_type)

                if selected_extract_func is None:
                    logging.warning(
                        f"  → [UNKNOWN] Formato no soportado para: {filepath.name}"
                    )
                    audit_logger.log_to_master_csv(
                        filepath, "UNKNOWN", reason="Formato no soportado"
                    )
                    error_count += 1
                    continue

                logging.info(
                    f"  → [DISPATCHER] Extractor seleccionado: {selected_extract_func.__module__}"
                )

            # C. Extracción
            logging.info(
                f"  → Extrayendo datos con {selected_extract_func.__module__}..."
            )
            data_result = selected_extract_func(filepath, current_hash)

            if not data_result:
                logging.error("  → [ERROR] El extractor devolvió datos vacíos.")
                audit_logger.log_to_master_csv(
                    filepath,
                    "ERROR",
                    reason="El extractor devolvió datos vacíos",
                )
                error_count += 1
                continue

            _debug_measurements_if_available(selected_extract_func, data_result)

            # D. Carga
            data_blocks = (
                data_result if isinstance(data_result, list) else [data_result]
            )

            logging.info("  → Guardando en base de datos...")
            all_blocks_success = True
            patient_id = None

            for block in data_blocks:
                success = loader.save_to_db(block, db_path, nhc_mapping)
                if not success:
                    all_blocks_success = False
                if patient_id is None:
                    patient_id = _resolve_patient_id(block, nhc_mapping)

            if all_blocks_success:
                loader.mark_file_processed(current_hash, filepath.name, db_path)
                audit_logger.log_to_master_csv(
                    filepath, "PROCESSED", patient_id=patient_id
                )
                logging.info("  → [OK] Procesamiento completado con éxito.")
                processed_count += 1
            else:
                audit_logger.log_to_master_csv(
                    filepath,
                    "ERROR",
                    patient_id=patient_id,
                    reason="Error en inserción DB",
                )
                logging.error(
                    "  → [FALLO] Error durante la inserción de uno o más bloques en DB."
                )
                error_count += 1

        except Exception as error:
            logging.exception(
                "  → [EXCEPCIÓN] Error crítico procesando %s",
                filepath.name,
            )
            audit_logger.log_to_master_csv(
                filepath, "ERROR", reason=f"Excepción: {error}"
            )
            error_count += 1

    return processed_count, skipped_count, error_count


def setup_logging(log_dir):
    """
    Configura el sistema de logging del pipeline.

    Args:
        log_dir: Directorio donde se almacenarán los logs
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "pipeline_activity.log"

    # Configurar formato
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    logging.info("=" * 100)
    logging.info("INICIANDO PIPELINE DE EXTRACCIÓN CLÍNICA")
    logging.info("=" * 100)


def main():
    # 1. Cargar configuración
    logging.info("[INIT] Cargando configuración...")
    config = load_config()
    if not config:
        logging.error("[ERROR] No se pudo cargar la configuración. Abortando...")
        return

    # 2. Configurar sistema de logging
    logs_dir = config.get("logs_dir", "logs")
    setup_logging(logs_dir)
    audit_logger.configure(config.get("audit_csv_path"))

    # 3. Extraer flags de ejecución
    run_phase_0 = config.get("run_phase_0", True)
    run_phase_1 = config.get("run_phase_1", True)
    run_phase_3 = config.get("run_phase_3", True)
    run_phase_4 = config.get("run_phase_4", True)
    run_phase_5 = config.get("run_phase_5", True)

    # 4. Ruta de la base de datos
    db_path = config.get("db_path", Path("clinical_data.db"))

    # 5. Asegurar esquema de base de datos
    logging.info("Verificando esquema de base de datos...")
    schema.create_schema(db_path)

    # 6. Contadores globales
    total_processed = 0
    total_skipped = 0
    total_errors = 0

    # 7. Cargar mapeo NHC (SOLO si la fase PDF unificada está activa)
    nhc_mapping = None
    if run_phase_1:
        csv_mapping_path = config.get("csv_mapping_path")
        if csv_mapping_path:
            logging.info("Cargando mapeo de anonimización...")
            nhc_mapping = load_nhc_mapping(csv_mapping_path)
            if nhc_mapping is None:
                logging.warning(
                    "Mapeo NHC no disponible. La fase PDF (triaje e ingesta) puede requerirlo."
                )
        else:
            logging.warning("csv_mapping_path no definido en config.json")

    # ========================================
    # FASE 0: CONCILIACIÓN DE IDENTIDADES
    # ========================================
    if run_phase_0:
        logging.info("=" * 100)
        logging.info("FASE 0: CONCILIACIÓN DE IDENTIDADES (MASTER CSV)")
        logging.info("=" * 100)

        master_csvs = config.get("master_csvs")
        if master_csvs:
            master_csvs_dir = Path(master_csvs)
            if not master_csvs_dir.is_dir():
                logging.warning(f"Directorio maestro no encontrado: {master_csvs_dir}")
            else:
                csv_files = list(master_csvs_dir.glob("*.csv"))

                if not csv_files:
                    logging.info(f"No se encontraron archivos CSV en {master_csvs_dir}")
                else:
                    for index, filepath in enumerate(csv_files, start=1):
                        logging.info(
                            f"[{index}/{len(csv_files)}] Procesando: {filepath.name}"
                        )

                        try:
                            # Calcular hash
                            current_hash = get_file_hash(filepath)

                            # Verificar idempotencia
                            if is_file_processed(current_hash, db_path):
                                logging.info(
                                    "  → [SKIP] Archivo ya procesado previamente (Hash coincide)."
                                )
                                total_skipped += 1
                                continue

                            # Extracción
                            logging.info("  → Extrayendo datos maestros...")
                            patient_records = extractor_master.process_master_csv(
                                filepath, current_hash
                            )

                            if not patient_records:
                                logging.error(
                                    "  → [ERROR] No se extrajeron registros de pacientes."
                                )
                                total_errors += 1
                                continue

                            # Procesamiento registro por registro
                            success_count = 0
                            error_count = 0

                            for patient in patient_records:
                                success = loader.upsert_patient_details(
                                    patient, db_path
                                )
                                if success:
                                    success_count += 1
                                else:
                                    error_count += 1

                            # Marcar archivo como procesado solo si no hubo errores críticos
                            if error_count == 0:
                                loader.mark_file_processed(
                                    current_hash, filepath.name, db_path
                                )
                                logging.info(
                                    f"  → [OK] {success_count} pacientes procesados correctamente."
                                )
                                total_processed += 1
                            else:
                                logging.warning(
                                    f"  → [PARCIAL] {success_count} OK, {error_count} errores. No se marca como procesado."
                                )
                                total_errors += 1

                        except Exception:
                            logging.exception(
                                "  → [EXCEPCIÓN] Error crítico procesando %s",
                                filepath.name,
                            )
                            total_errors += 1
        else:
            logging.warning("master_csvs no definido en config.json")
    else:
        logging.info("FASE 0 desactivada (run_phase_0=False)")

    # ========================================
    # FASE 1: TRIAJE E INGESTA PDF
    # ========================================
    if run_phase_1:
        logging.info("=" * 100)
        logging.info("FASE 1: TRIAJE E INGESTA PDF")
        logging.info("=" * 100)

        raw_ingestion_dir = config.get("raw_ingestion_dir")
        if raw_ingestion_dir:
            processed, skipped, errors = process_files(
                raw_ingestion_dir,
                db_path,
                extract_func=None,
                nhc_mapping=nhc_mapping,
                extension="pdf",
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors
        else:
            logging.warning("raw_ingestion_dir no definido en config.json")
    else:
        logging.info("FASE 1 desactivada (run_phase_1=False)")

    # ========================================
    # FASE 3: MACRO (CSV)
    # ========================================
    if run_phase_3:
        logging.info("=" * 100)
        logging.info("FASE 3: MACRO (CSV)")
        logging.info("=" * 100)

        macro_dir = config.get("macro_dir")
        if macro_dir:
            processed, skipped, errors = process_files(
                macro_dir,
                db_path,
                extract_func=extractor_macro.process_csv,
                nhc_mapping=None,
                extension="csv",
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors
        else:
            logging.warning("macro_dir no definido en config.json")
    else:
        logging.info("FASE 3 desactivada (run_phase_3=False)")

    # ========================================
    # FASE 4: ENTRADAS MANUALES
    # ========================================
    if run_phase_4:
        logging.info("=" * 100)
        logging.info("FASE 4.1: ENTRADAS MANUALES (EXCEL) - ANÁLISIS DE SANGRE")
        logging.info("=" * 100)

        manual_entry_dir = config.get("manual_entry_dir")
        if manual_entry_dir:
            manual_blood_test_dir = Path(manual_entry_dir) / "blood_tests"
            processed, skipped, errors = process_files(
                manual_blood_test_dir,
                db_path,
                extract_func=extractor_manual.process_manual_excel,
                nhc_mapping=None,
                extension="xlsx",
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors

            logging.info("=" * 100)
            logging.info("FASE 4.2: ENTRADAS MANUALES (EXCEL) - ESPIROMETRÍA")
            logging.info("=" * 100)

            manual_spirometry_dir = Path(manual_entry_dir) / "spirometry"
            processed, skipped, errors = process_files(
                manual_spirometry_dir,
                db_path,
                extract_func=extractor_manual.process_manual_excel,
                nhc_mapping=None,
                extension="xlsx",
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors
        else:
            logging.warning("manual_entry_dir no definido en config.json")
    else:
        logging.info("FASE 4 desactivada (run_phase_4=False)")

    # ========================================
    # FASE 5: CUESTIONARIOS (EXCEL)
    # ========================================
    if run_phase_5:
        logging.info("=" * 100)
        logging.info("FASE 5: CUESTIONARIOS (EXCEL)")
        logging.info("=" * 100)

        questionnaires_dir = config.get("questionnaires_dir")
        if questionnaires_dir:
            processed, skipped, errors = process_files(
                questionnaires_dir,
                db_path,
                extract_func=extractor_questionnaires.process_excel,
                nhc_mapping=None,
                extension="xlsx",
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors
        else:
            logging.warning("questionnaires_dir no definido en config.json")
    else:
        logging.info("FASE 5 desactivada (run_phase_5=False)")

    # ========================================
    # RESUMEN FINAL
    # ========================================
    logging.info("=" * 100)
    logging.info("RESUMEN DE EJECUCIÓN")
    logging.info("=" * 100)
    logging.info(f"Procesados (OK):  {total_processed}")
    logging.info(f"Omitidos (Skip):  {total_skipped}")
    logging.info(f"Errores:          {total_errors}")
    logging.info(f"Base de datos:    {db_path}")
    logging.info(f"Logs guardados en: {logs_dir}/pipeline_activity.log")
    logging.info("=" * 100)


if __name__ == "__main__":
    main()
