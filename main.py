import sqlite3
import csv
import hashlib
import json
import logging
from pathlib import Path

import schema
import extractor_blood_test
import extractor_spiro
import extractor_macro
import extractor_manual
import extractor_master
import extractor_questionnaires
import loader


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


def load_config(config_path="config.json"):
    """
    Carga la configuración desde un archivo JSON.
    Convierte todas las rutas a objetos Path para compatibilidad multiplataforma.

    Args:
        config_path: Ruta del archivo de configuración

    Returns:
        Diccionario con las claves de configuración o None si falla
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Convertir TODAS las rutas a objetos Path para compatibilidad multiplataforma
        for key, value in config.items():
            if isinstance(value, str):
                # Solo convertir si parece una ruta (Unix o Windows)
                config[key] = Path(value)

        print(f"[INFO] Configuración cargada desde {config_path}")
        return config
    except FileNotFoundError:
        print(f"[ERROR] Archivo de configuración no encontrado: {config_path}")
        return None
    except json.JSONDecodeError as e:
        print(f"[ERROR] Error al parsear JSON en {config_path}: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Error inesperado al cargar configuración: {e}")
        return None


def load_nhc_mapping(csv_path):
    """
    Lee el archivo CSV de mapeo NHC -> ID y retorna un diccionario.

    Args:
        csv_path: Ruta del archivo CSV

    Returns:
        Diccionario {nhc: subject_id}
    """
    nhc_mapping = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row and "nhc" in row and "id" in row:
                    nhc = row["nhc"].strip()
                    subject_id = row["id"].strip()
                    nhc_mapping[nhc] = subject_id

        print(
            f"[INFO] Cargado mapeo NHC -> ID: {len(nhc_mapping)} registros desde {csv_path}"
        )
        return nhc_mapping
    except FileNotFoundError:
        print(f"[ERROR] Archivo de mapeo no encontrado: {csv_path}")
        return {}
    except Exception as e:
        print(f"[ERROR] Error leyendo archivo de mapeo: {e}")
        return {}


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
        # Si da error (ej. la tabla no existe aun), asumimos que no está procesado
        return False


def process_directory(directory, extract_func, db_path, nhc_mapping, extension="pdf"):
    """
    Procesa todos los PDFs de un directorio con el extractor especificado.

    Args:
        directory: Ruta del directorio con PDFs
        extract_func: Función de extracción (ej: extractor_manual.process_manual_csv o extractor_blood_test.process_pdf)
        nhc_mapping: Diccionario {nhc: subject_id} para anonimización
        db_path: Ruta de la base de datos

    Returns:
        Tupla (processed_count, skipped_count, error_count)
    """
    directory = Path(directory)

    if not directory.is_dir():
        print(f"[WARN] No existe el directorio: {directory}")
        return 0, 0, 0

    # Listar archivos PDF
    target_files = list(directory.glob(f"*.{extension}"))
    total_files = len(target_files)

    if total_files == 0:
        print(f"[INFO] No se encontraron archivos {extension} en {directory}")
        return 0, 0, 0

    print(f"\n[INFO] Encontrados {total_files} archivos {extension} en {directory}")

    processed_count = 0
    skipped_count = 0
    error_count = 0

    # Bucle de procesamiento
    for index, filepath in enumerate(target_files):
        if "(metadata)" in filepath.name:
            continue

        print(f"\n[{index + 1}/{total_files}] Procesando: {filepath.name}")

        try:
            # A. Calcular Hash para verificar idempotencia
            current_hash = get_file_hash(filepath)

            # B. Verificar si ya existe en DB
            if is_file_processed(current_hash, db_path):
                print(f"   -> [SKIP] Archivo ya procesado previamente (Hash coincide).")
                skipped_count += 1
                continue

            # C. Extracción (Minería)
            print("   -> Extrayendo datos...")
            data_result = extract_func(filepath, current_hash)

            # Depuracion opcional
            if (
                isinstance(data_result, dict)
                and data_result.get("report", {}).get("report_type") == "blood_test"
            ):
                extractor_blood_test.debug_measurements(data_result)

            if not data_result:
                print("   -> [ERROR] El extractor devolvió datos vacíos.")
                error_count += 1
                continue

            # D. Carga (Almacenamiento)
            data_blocks = (
                data_result if isinstance(data_result, list) else [data_result]
            )

            print("   -> Guardando en base de datos...")
            all_blocks_success = True
            for block in data_blocks:
                success = loader.save_to_db(block, db_path, nhc_mapping)
                if not success:
                    all_blocks_success = False

            if all_blocks_success:
                loader.mark_file_processed(current_hash, filepath.name, db_path)
                print("   -> [OK] Procesamiento completado con éxito.")
                processed_count += 1
            else:
                print(
                    "   -> [FALLO] Error durante la inserción de uno o más bloques en DB."
                )
                error_count += 1

        except Exception as e:
            print(
                f"   -> [EXCEPCIÓN] Error crítico procesando archivo {filepath.name}: {e}"
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
            logging.StreamHandler(),  # También muestra en consola
        ],
    )

    logging.info("=" * 100)
    logging.info("INICIANDO PIPELINE DE EXTRACCIÓN CLÍNICA")
    logging.info("=" * 100)


def main():
    # 1. Cargar configuración
    print("\n[INIT] Cargando configuración...")
    config = load_config()
    if not config:
        print("[ERROR] No se pudo cargar la configuración. Abortando...")
        return

    # Extraer valores del diccionario de configuración
    blood_test_dir = config.get("blood_test_dir")
    spirometry_dir = config.get("spirometry_dir")
    csv_mapping_path = config.get("csv_mapping_path")
    macro_dir = config.get("macro_dir")
    manual_entry_dir = config.get("manual_entry_dir")
    master_csvs = config.get("master_csvs")
    logs_dir = config.get("logs_dir")
    questionnaires_dir = config.get("questionnaires_dir")

    # Nombre fijo de la base de datos
    db_path = "clinical_data.db"

    # Validar configuración
    if not all(
        [
            blood_test_dir,
            spirometry_dir,
            csv_mapping_path,
            macro_dir,
            manual_entry_dir,
            master_csvs,
        ]
    ):
        print("[ERROR] Configuración incompleta. Verifica config.json")
        return

    # 2. Configurar sistema de logging
    setup_logging(logs_dir)

    # 3. Cargar mapeo NHC -> ID
    logging.info("Cargando mapeo de anonimización...")
    nhc_mapping = load_nhc_mapping(csv_mapping_path)
    if not nhc_mapping:
        logging.error("No se pudo cargar el mapeo de NHC. Abortando...")
        return

    # 4. Asegurar esquema de base de datos
    logging.info("Verificando esquema de base de datos...")
    schema.create_schema(db_path)

    # 5. Contadores globales
    total_processed = 0
    total_skipped = 0
    total_errors = 0

    # ========================================
    # FASE 0: CONCILIACIÓN DE IDENTIDADES
    # ========================================
    logging.info("=" * 100)
    logging.info("FASE 0: CONCILIACIÓN DE IDENTIDADES (MASTER CSV)")
    logging.info("=" * 100)

    master_csvs_dir = Path(master_csvs)
    if not master_csvs_dir.is_dir():
        logging.warning(f"Directorio maestro no encontrado: {master_csvs_dir}")
    else:
        csv_files = list(master_csvs_dir.glob("*.csv"))

        if not csv_files:
            logging.info(f"No se encontraron archivos CSV en {master_csvs_dir}")
        else:
            for index, filepath in enumerate(csv_files, start=1):
                logging.info(f"[{index}/{len(csv_files)}] Procesando: {filepath.name}")

                try:
                    # Calcular hash
                    current_hash = get_file_hash(filepath)

                    # Verificar idempotencia
                    if is_file_processed(current_hash, db_path):
                        logging.info(
                            f"  → [SKIP] Archivo ya procesado previamente (Hash coincide)."
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
                        success = loader.upsert_patient_details(patient, db_path)
                        if success:
                            success_count += 1
                        else:
                            error_count += 1

                    # Marcar archivo como procesado solo si no hubo errores críticos
                    if error_count == 0:
                        loader.mark_file_processed(current_hash, filepath.name, db_path)
                        logging.info(
                            f"  → [OK] {success_count} pacientes procesados correctamente."
                        )
                        total_processed += 1
                    else:
                        logging.warning(
                            f"  → [PARCIAL] {success_count} OK, {error_count} errores. No se marca como procesado."
                        )
                        total_errors += 1

                except Exception as e:
                    logging.error(
                        f"  → [EXCEPCIÓN] Error crítico procesando {filepath.name}: {e}"
                    )
                    total_errors += 1

    # 6. FASE 1: Procesar Análisis de Sangre (Blood Tests)
    logging.info("=" * 100)
    logging.info("FASE 1: ANÁLISIS DE SANGRE")
    logging.info("=" * 100)
    processed, skipped, errors = process_directory(
        blood_test_dir, extractor_blood_test.process_pdf, db_path, nhc_mapping
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 7. FASE 2: Procesar Espirometrías (Spirometry)
    logging.info("=" * 100)
    logging.info("FASE 2: ESPIROMETRÍA")
    logging.info("=" * 100)
    processed, skipped, errors = process_directory(
        spirometry_dir, extractor_spiro.process_pdf, db_path, nhc_mapping
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 8. FASE 3: Procesar MACRO (CSV)
    logging.info("=" * 100)
    logging.info("FASE 3: MACRO (CSV)")
    logging.info("=" * 100)
    processed, skipped, errors = process_directory(
        macro_dir,
        extractor_macro.process_csv,
        db_path,
        nhc_mapping=None,
        extension="csv",
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 9. FASE 4: Procesar Entradas Manuales (Manual Entry)
    manual_blood_test_dir = manual_entry_dir / "blood_tests"
    manual_spirometry_dir = manual_entry_dir / "spirometry"

    logging.info("=" * 100)
    logging.info("FASE 4.1: ENTRADAS MANUALES - ANÁLISIS DE SANGRE")
    logging.info("=" * 100)
    processed, skipped, errors = process_directory(
        manual_blood_test_dir,
        extractor_manual.process_manual_csv,
        db_path,
        nhc_mapping=None,
        extension="csv",
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    logging.info("=" * 100)
    logging.info("FASE 4.2: ENTRADAS MANUALES - ESPIROMETRÍA")
    logging.info("=" * 100)
    processed, skipped, errors = process_directory(
        manual_spirometry_dir,
        extractor_manual.process_manual_csv,
        db_path,
        nhc_mapping=None,
        extension="csv",
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 10. FASE 5: Cuestionarios en Excel
    logging.info("=" * 100)
    logging.info("FASE 5: CUESTIONARIOS (EXCEL)")
    logging.info("=" * 100)
    processed, skipped, errors = process_directory(
        questionnaires_dir,
        extractor_questionnaires.process_excel,
        db_path,
        nhc_mapping=None,
        extension="xlsx",
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 9. Resumen Final
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
