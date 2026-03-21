import sqlite3
import csv
import hashlib
import logging
from pathlib import Path
import importlib
from datetime import datetime

import schema
import dispatcher
import audit_logger
import extractor_macro
import extractor_manual
import extractor_master
import extractor_questionnaires
import loader
from utils.config import load_config
from utils.normalization import find_match, normalize_clinical_name


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


def load_mapping_data(csv_path):
    """
    Lee el archivo CSV de mapeo (nombre, nhc, id).

    Args:
        csv_path: Ruta del archivo CSV

    Returns:
        Tupla (nhc_to_id, name_to_id)
    """
    csv_path = Path(csv_path)

    if not csv_path.exists():
        logging.warning(f"Archivo de mapeo no encontrado: {csv_path}")
        return {}, {}

    nhc_to_id = {}
    name_to_id = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue

                subject_id = (row.get("id") or "").strip()
                if not subject_id:
                    continue

                nhc = (row.get("nhc") or "").strip()
                if nhc:
                    nhc_to_id[nhc] = subject_id

                cleaned_name = normalize_clinical_name(row.get("nombre"))
                if cleaned_name:
                    name_to_id[cleaned_name] = subject_id

        logging.info(
            "Cargado mapeo de anonimización: %s NHC y %s nombres desde %s",
            len(nhc_to_id),
            len(name_to_id),
            csv_path,
        )
        return nhc_to_id, name_to_id

    except (OSError, csv.Error) as error:
        logging.error("Error leyendo archivo de mapeo %s: %s", csv_path, error)
        return {}, {}
    except Exception:
        logging.exception("Error inesperado leyendo archivo de mapeo %s", csv_path)
        return {}, {}


def get_processed_hashes(db_path):
    """
    Recupera todos los hashes de archivos ya procesados en un set.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_hash FROM processed_files")
            return {row[0] for row in cursor.fetchall() if row and row[0]}
    except sqlite3.Error as error:
        logging.error("Error obteniendo hashes procesados: %s", error)
        return set()


def _resolve_patient_id(block, nhc_to_id, name_to_id):
    """
    Best-effort extraction of a patient identifier from a data block.
    Used only for audit CSV enrichment — never blocks processing.
    """
    subject_id = block.get("subject_id")
    if subject_id:
        return str(subject_id)

    nhc = block.get("patient", {}).get("nhc")
    name = block.get("patient", {}).get("name")

    if nhc:
        resolved = nhc_to_id.get(nhc) if nhc_to_id else None
        if resolved:
            return resolved

    if name and name_to_id:
        matched_id, _ = find_match(name, name_to_id)
        if matched_id:
            return matched_id

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
    directory,
    db_path,
    extract_func=None,
    source_file_type=None,
    nhc_to_id=None,
    name_to_id=None,
    extension="pdf",
    processed_hashes=None,
):
    """
    Processes files recursively under *directory*.

    If extract_func is None, the extractor is selected dynamically through the
    PDF dispatcher. Otherwise, the provided extractor is used directly.

    Args:
        directory: Root directory to search recursively.
        db_path: SQLite database path.
        extract_func: Optional extraction callable.
        source_file_type: Optional file type for dynamic dispatcher selection.
        nhc_to_id: Optional {nhc: subject_id} dict for PHI de-identification.
        name_to_id: Optional {normalized_name: subject_id} dict for PHI de-identification.
        extension: File extension to search for.
        processed_hashes: Optional set with preloaded processed hashes.

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
    if processed_hashes is None:
        processed_hashes = get_processed_hashes(db_path)

    for index, filepath in enumerate(target_files, start=1):
        logging.info(f"[{index}/{total_files}] Procesando: {filepath.name}")

        try:
            # A. Hash + idempotencia
            current_hash = get_file_hash(filepath)
            if current_hash in processed_hashes:
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
                # skip si el tipo identificado es UNKNOWN o SPIROMETRY_CAP (requiere extractor específico)
                if file_type in ["UNKNOWN", "SPIROMETRY_CAP"]:
                    logging.warning(
                        f"  → [SKIP] Tipo no procesable para: {filepath.name} (Identificado como: {file_type})"
                    )
                    audit_logger.log_to_master_csv(
                        filepath,
                        "SKIPPED",
                        reason=f"Tipo no procesable: {file_type}",
                        source_filetype=file_type,
                    )
                    skipped_count += 1
                    continue

                source_file_type = file_type
                selected_extract_func = dispatcher.FILE_TYPE_TO_EXTRACTOR.get(file_type)

                if selected_extract_func is None:
                    logging.warning(
                        f"  → [UNKNOWN] Formato no soportado para: {filepath.name}"
                    )
                    audit_logger.log_to_master_csv(
                        filepath,
                        "UNKNOWN",
                        reason="Formato no soportado",
                        source_filetype=source_file_type,
                    )
                    error_count += 1
                    continue

                logging.info(
                    f"  → [DISPATCHER] Extractor seleccionado: {selected_extract_func.__module__}"
                )

                # C. Extracción para blood_test o spirometry
                # SPIROMETRY_CLINIC no esta preparado para recibir source_file_type
                if source_file_type != "SPIROMETRY_CLINIC":
                    data_result = selected_extract_func(
                        filepath, current_hash, source_file_type=source_file_type
                    )
                else:
                    data_result = selected_extract_func(filepath, current_hash)

            if not data_result:
                # C. Extracción general
                logging.info(
                    f"  → Extrayendo datos con {selected_extract_func.__module__}..."
                )
                data_result = selected_extract_func(filepath, current_hash)

            # add source_file_type to data_result for better traceability
            if isinstance(data_result, dict):
                if "file_info" not in data_result:
                    data_result["file_info"] = {}
                if not data_result["file_info"].get("source_file_type"):
                    data_result["file_info"]["source_file_type"] = source_file_type

            if not data_result:
                logging.error("  → [ERROR] El extractor devolvió datos vacíos.")
                audit_logger.log_to_master_csv(
                    filepath,
                    "ERROR",
                    reason="El extractor devolvió datos vacíos",
                    source_filetype=source_file_type,
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
                success = loader.save_to_db(block, db_path, nhc_to_id)
                if not success:
                    all_blocks_success = False
                if patient_id is None:
                    patient_id = _resolve_patient_id(block, nhc_to_id, name_to_id)

            if all_blocks_success:
                loader.mark_file_processed(current_hash, filepath.name, db_path)
                processed_hashes.add(current_hash)
                audit_logger.log_to_master_csv(
                    filepath,
                    "PROCESSED",
                    patient_id=patient_id,
                    source_filetype=source_file_type,
                )
                logging.info("  → [OK] Procesamiento completado con éxito.")
                processed_count += 1
            else:
                audit_logger.log_to_master_csv(
                    filepath,
                    "ERROR",
                    patient_id=patient_id,
                    reason="Error en inserción DB",
                    source_filetype=source_file_type,
                )
                logging.error(
                    "  → [FALLO] Error durante la inserción de uno o más bloques en DB."
                )
                error_count += 1

        except (OSError, sqlite3.Error, csv.Error, FileNotFoundError) as error:
            logging.exception(
                "  → [EXCEPCIÓN] Error crítico procesando %s",
                filepath.name,
            )
            audit_logger.log_to_master_csv(
                filepath,
                "ERROR",
                reason=f"Excepción: {error}",
                source_filetype=source_file_type,
            )
            error_count += 1

    return processed_count, skipped_count, error_count


def process_files_master(master_csvs, db_path):
    """
    Procesa archivos maestros CSV para conciliación de identidades.

    Args:
        master_csvs: Directorio con archivos CSV maestros.
        db_path: Ruta de la base de datos.

    Returns:
        Tuple (processed_count, skipped_count, error_count).
    """
    if not master_csvs:
        logging.warning("master_csvs no definido en config.json")
        return 0, 0, 0

    master_csvs_dir = Path(master_csvs)
    if not master_csvs_dir.is_dir():
        logging.warning(f"Directorio maestro no encontrado: {master_csvs_dir}")
        return 0, 0, 0

    csv_files = list(master_csvs_dir.glob("*.csv"))
    if not csv_files:
        logging.info(f"No se encontraron archivos CSV en {master_csvs_dir}")
        return 0, 0, 0

    processed_count = 0
    skipped_count = 0
    error_count = 0
    processed_hashes = get_processed_hashes(db_path)

    for index, filepath in enumerate(csv_files, start=1):
        logging.info(f"[{index}/{len(csv_files)}] Procesando: {filepath.name}")

        try:
            # Calcular hash
            current_hash = get_file_hash(filepath)

            # Verificar idempotencia
            if current_hash in processed_hashes:
                logging.info(
                    "  → [SKIP] Archivo ya procesado previamente (Hash coincide)."
                )
                skipped_count += 1
                continue

            # Extracción
            logging.info("  → Extrayendo datos maestros...")
            patient_records = extractor_master.process_master_csv(
                filepath, current_hash
            )

            if not patient_records:
                logging.error("  → [ERROR] No se extrajeron registros de pacientes.")
                error_count += 1
                continue

            # Procesamiento registro por registro
            success_count = 0
            failed_count = 0

            for patient in patient_records:
                success = loader.upsert_patient_details(patient, db_path)
                if success:
                    success_count += 1
                else:
                    failed_count += 1

            # Marcar archivo como procesado solo si no hubo errores críticos
            if failed_count == 0:
                loader.mark_file_processed(current_hash, filepath.name, db_path)
                processed_hashes.add(current_hash)
                logging.info(
                    f"  → [OK] {success_count} pacientes procesados correctamente."
                )
                processed_count += 1
            else:
                logging.warning(
                    f"  → [PARCIAL] {success_count} OK, {failed_count} errores. No se marca como procesado."
                )
                error_count += 1

        except (OSError, sqlite3.Error, csv.Error, FileNotFoundError):
            logging.exception(
                "  → [EXCEPCIÓN] Error crítico procesando %s",
                filepath.name,
            )
            error_count += 1

    return processed_count, skipped_count, error_count


def setup_logging(use_timestamp=True):
    """
    Configura el sistema de logging del pipeline.

    Args:
        use_timestamp: Si True, crea un archivo por ejecución con timestamp.

    Returns:
        Path del archivo de log.
    """
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    if use_timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"pipeline_activity_{timestamp}.log"
    else:
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

    return log_file


def main():
    # 1. Configurar sistema de logging
    log_file = setup_logging(use_timestamp=True)

    # 2. Cargar configuración
    config = load_config()
    if not config:
        logging.error("[ERROR] No se pudo cargar la configuración. Abortando...")
        return
    logging.info("[INIT] Configuración cargada correctamente")

    # 3. Configurar auditoría
    audit_logger.configure(config.get("audit_csv_path"))

    # 4. Extraer flags de ejecución
    run_phase_0 = config.get("run_phase_0", True)
    run_phase_1 = config.get("run_phase_1", True)
    run_phase_3 = config.get("run_phase_3", True)
    run_phase_4 = config.get("run_phase_4", True)
    run_phase_5 = config.get("run_phase_5", True)

    # 5. Ruta de la base de datos
    db_path = config.get("db_path", Path("clinical_data.db"))

    # 6. Asegurar esquema de base de datos
    logging.info("Verificando esquema de base de datos...")
    schema.create_schema(db_path)

    # 7. Contadores globales
    total_processed = 0
    total_skipped = 0
    total_errors = 0

    # 8. Cargar mapeo NHC (SOLO si la fase PDF unificada está activa)
    nhc_to_id = {}
    name_to_id = {}
    if run_phase_1:
        csv_mapping_path = config.get("csv_mapping_path")
        if csv_mapping_path:
            logging.info("Cargando mapeo de anonimización...")
            nhc_to_id, name_to_id = load_mapping_data(csv_mapping_path)
            if not nhc_to_id and not name_to_id:
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
        processed, skipped, errors = process_files_master(
            config.get("master_csvs"),
            db_path,
        )
        total_processed += processed
        total_skipped += skipped
        total_errors += errors
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
            processed_hashes = get_processed_hashes(db_path)
            processed, skipped, errors = process_files(
                raw_ingestion_dir,
                db_path,
                extract_func=None,
                source_file_type=None,
                nhc_to_id=nhc_to_id,
                name_to_id=name_to_id,
                extension="pdf",
                processed_hashes=processed_hashes,
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
                source_file_type="macro_csv",
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
        logging.info("FASE 4: ENTRADAS MANUALES")
        logging.info("=" * 100)

        manual_entry_dir = config.get("manual_entry_dir")
        if manual_entry_dir:
            # BLOOD ANALYSIS
            manual_blood_test_dir = Path(manual_entry_dir) / "blood_tests"
            processed, skipped, errors = process_files(
                manual_blood_test_dir,
                db_path,
                extract_func=extractor_manual.process_manual_excel,
                source_file_type=f"manual_entry_{manual_blood_test_dir.name}",
                extension="xlsx",
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors

            # SPIROMETRY
            manual_spirometry_dir = Path(manual_entry_dir) / "spirometry"
            processed, skipped, errors = process_files(
                manual_spirometry_dir,
                db_path,
                extract_func=extractor_manual.process_manual_excel,
                source_file_type=f"manual_entry_{manual_spirometry_dir.name}",
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
                source_file_type="questionnaires_excel",
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
    logging.info(f"Logs guardados en: {log_file}")
    logging.info("=" * 100)


if __name__ == "__main__":
    main()
