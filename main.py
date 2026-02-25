import sqlite3
import csv
import hashlib
import json
from pathlib import Path

import schema
import extractor_blood_test
import extractor_spiro
import extractor_macro
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


def process_directory(directory, extractor, report_type, nhc_mapping, db_path):
    """
    Procesa todos los PDFs de un directorio con el extractor especificado.

    Args:
        directory: Ruta del directorio con PDFs
        extractor: Módulo extractor (extractor_blood_test o extractor_spiro)
        report_type: Tipo de reporte ('blood_test' o 'spirometry')
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
    pdf_files = list(directory.glob("*.pdf"))
    total_files = len(pdf_files)

    if total_files == 0:
        print(f"[INFO] No se encontraron archivos PDF en {directory}")
        return 0, 0, 0

    print(f"\n[INFO] Encontrados {total_files} archivos PDF en {directory}")

    processed_count = 0
    skipped_count = 0
    error_count = 0

    # Bucle de procesamiento
    for index, filepath in enumerate(pdf_files):
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
            data_object = extractor.process_pdf(filepath, current_hash)

            # Depuracion opcional
            if report_type == "blood_test" and extractor == extractor_blood_test:
                extractor_blood_test.debug_measurements(data_object)

            if not data_object:
                print("   -> [ERROR] El extractor devolvió datos vacíos.")
                error_count += 1
                continue

            # D. Carga (Almacenamiento)
            print("   -> Guardando en base de datos...")
            success = loader.save_to_db(data_object, nhc_mapping, db_path)

            if success:
                print("   -> [OK] Procesamiento completado con éxito.")
                processed_count += 1
            else:
                print("   -> [FALLO] Error durante la inserción en DB.")
                error_count += 1

        except Exception as e:
            print(
                f"   -> [EXCEPCIÓN] Error crítico procesando archivo {filepath.name}: {e}"
            )
            error_count += 1

    return processed_count, skipped_count, error_count


def main():
    print("=" * 100)
    print("INICIANDO PIPELINE DE EXTRACCIÓN CLÍNICA".center(100))
    print("=" * 100)

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

    # Nombre fijo de la base de datos (no viene del JSON)
    db_path = "clinical_data.db"

    # Validar que todas las claves estén presentes
    if not all([blood_test_dir, spirometry_dir, csv_mapping_path, macro_dir]):
        print("[ERROR] Configuración incompleta. Verifica config.json")
        return

    # 2. Cargar mapeo NHC -> ID
    print("\n[INIT] Cargando mapeo de anonimización...")
    nhc_mapping = load_nhc_mapping(csv_mapping_path)
    if not nhc_mapping:
        print("[ERROR] No se pudo cargar el mapeo de NHC. Abortando...")
        return

    # 3. Asegurar que la estructura de la base de datos existe
    print("\n[INIT] Verificando esquema de base de datos...")
    schema.create_schema(db_path)

    # 4. Contadores globales
    total_processed = 0
    total_skipped = 0
    total_errors = 0

    # 5. FASE 1: Procesar Análisis de Sangre (Blood Tests)
    print("\n" + "=" * 100)
    print("FASE 1: ANÁLISIS DE SANGRE")
    print("=" * 100)
    processed, skipped, errors = process_directory(
        blood_test_dir, extractor_blood_test, "blood_test", nhc_mapping, db_path
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 6. FASE 2: Procesar Espirometrías (Spirometry)
    print("\n" + "=" * 100)
    print("FASE 2: ESPIROMETRÍA")
    print("=" * 100)
    processed, skipped, errors = process_directory(
        spirometry_dir, extractor_spiro, "spirometry", nhc_mapping, db_path
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 7. FASE 3: Procesar MACRO (CSV)
    print("\n" + "=" * 100)
    print("FASE 3: MACRO (CSV)")
    print("=" * 100)

    if not macro_dir.is_dir():
        print(f"[WARN] No existe el directorio: {macro_dir}")
    else:
        csv_files = list(macro_dir.glob("*.csv"))
        if not csv_files:
            print(f"[INFO] No se encontraron archivos CSV en {macro_dir}")
        else:
            for index, filepath in enumerate(csv_files):
                print(f"\n[{index + 1}/{len(csv_files)}] Procesando: {filepath.name}")

                current_hash = get_file_hash(filepath)

                if is_file_processed(current_hash, db_path):
                    print(
                        "   -> [SKIP] Archivo ya procesado previamente (Hash coincide)."
                    )
                    total_skipped += 1
                    continue

                print("   -> Extrayendo datos MACRO...")
                blocks = extractor_macro.process_csv(filepath, current_hash)

                if not blocks:
                    print("   -> [ERROR] No se extrajeron bloques MACRO.")
                    total_errors += 1
                    continue

                file_ok = True
                for block in blocks:
                    success = loader.save_to_db(block, db_path, nhc_mapping)
                    if not success:
                        file_ok = False
                        break

                if file_ok:
                    loader.mark_file_processed(current_hash, filepath.name, db_path)
                    print("   -> [OK] CSV MACRO procesado con éxito.")
                    total_processed += 1
                else:
                    print("   -> [FALLO] Error en uno o más bloques MACRO.")
                    total_errors += 1

    # 8. Resumen Final
    print("\n" + "=" * 100)
    print("RESUMEN DE EJECUCIÓN")
    print("=" * 100)
    print(f"Procesados (OK):  {total_processed}")
    print(f"Omitidos (Skip):  {total_skipped}")
    print(f"Errores:          {total_errors}")
    print(f"Base de datos:    {db_path}")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()
