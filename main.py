import sqlite3
from pathlib import Path

import schema
import extractor_blood_test
import extractor_spiro
import loader

# --- CONFIGURACIÓN ---
BLOOD_TEST_DIR = Path("/Users/davidjaramillo/Downloads/breathe-project/haemogram")
SPIROMETRY_DIR = Path("/Users/davidjaramillo/Downloads/spirometry")
DB_PATH = "clinical_data.db"


def is_file_processed(file_hash, db_path=DB_PATH):
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


def process_directory(directory, extractor, report_type, db_path=DB_PATH):
    """
    Procesa todos los PDFs de un directorio con el extractor especificado.

    Args:
        directory: Ruta del directorio con PDFs
        extractor: Módulo extractor (extractor_blood_test o extractor_spiro)
        report_type: Tipo de reporte ('blood_test' o 'spirometry')
        db_path: Ruta de la base de datos

    Returns:
        Tupla (processed_count, skipped_count, error_count)
    """
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
            current_hash = extractor.get_file_hash(filepath)

            # B. Verificar si ya existe en DB
            if is_file_processed(current_hash, db_path):
                print(f"   -> [SKIP] Archivo ya procesado previamente (Hash coincide).")
                skipped_count += 1
                continue

            # C. Extracción (Minería)
            print("   -> Extrayendo datos...")
            data_object = extractor.process_pdf(filepath)

            # Depuracion opcional
            if report_type == "blood_test" and extractor == extractor_blood_test:
                extractor_blood_test.debug_measurements(data_object)

            if not data_object:
                print("   -> [ERROR] El extractor devolvió datos vacíos.")
                error_count += 1
                continue

            # D. Carga (Almacenamiento)
            print("   -> Guardando en base de datos...")
            success = loader.save_to_db(data_object, db_path)

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
    # printar banner de bienvenida centrado
    print("INICIANDO PIPELINE DE EXTRACCIÓN CLÍNICA".center(100))
    print("=" * 100)

    # 1. Asegurar que la estructura de la base de datos existe
    print("\n[INIT] Verificando esquema de base de datos...")
    schema.create_schema(DB_PATH)

    # 2. Contadores globales
    total_processed = 0
    total_skipped = 0
    total_errors = 0

    # 3. FASE 1: Procesar Análisis de Sangre (Blood Tests)
    print("\n" + "=" * 100)
    print("FASE 1: ANÁLISIS DE SANGRE")
    print("=" * 100)
    processed, skipped, errors = process_directory(
        BLOOD_TEST_DIR, extractor_blood_test, "blood_test", DB_PATH
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 4. FASE 2: Procesar Espirometrías (Spirometry)
    print("\n" + "=" * 100)
    print("FASE 2: ESPIROMETRÍA")
    print("=" * 100)
    processed, skipped, errors = process_directory(
        SPIROMETRY_DIR, extractor_spiro, "spirometry", DB_PATH
    )
    total_processed += processed
    total_skipped += skipped
    total_errors += errors

    # 5. Resumen Final
    print("\n" + "=" * 100)
    print("RESUMEN DE EJECUCIÓN")
    print("=" * 100)
    print(f"Procesados (OK):  {total_processed}")
    print(f"Omitidos (Skip):  {total_skipped}")
    print(f"Errores:          {total_errors}")
    print(f"Base de datos:    {DB_PATH}")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()
