import sqlite3


def create_schema(db_path):
    try:
        # Conexión a la base de datos (se crea si no existe)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Habilitar soporte para Foreign Keys (SQLite lo tiene desactivado por defecto)
        cursor.execute("PRAGMA foreign_keys = ON;")

        # 1. TABLA PATIENTS (Anonimizada)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS patients (
                patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id TEXT UNIQUE NOT NULL,  -- ID anonimizado del sujeto
                birth_date TEXT,                  -- Formato ISO8601 (YYYY-MM-DD)
                sex TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """
        )

        # 2. TABLA LAB_REPORTS (Eventos/Informes)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pdf_reports (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                report_type TEXT,                 -- Tipo: 'blood_test', 'spirometry', etc.
                lab_request_number TEXT,          -- N. Sol·licitud Lab
                episode_number TEXT,              -- Nº Episodio
                report_date TEXT,                 -- Data recepció mostra
                weight_kg REAL,                   -- Peso del paciente (si disponible)
                height_cm REAL,                   -- Altura del paciente (si disponible)
                source_filename TEXT,             -- Para auditoría
                source_file_type TEXT,            -- Origen del dato (cap, hosp_mar, manual_csv, etc.)
                extraction_date TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
            );
        """
        )

        # 3. TABLA BLOOD_MEASUREMENTS (Resultados Análisis de Sangre)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS blood_measurements (
                measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                section TEXT,                     -- Ej: 'HEMATOLOGIA', 'AL·LÈRGENS'
                subsection TEXT,                  -- Ej: 'PÒL·LENS' o NULL
                parameter TEXT NOT NULL,          -- Nombre tal cual aparece en el PDF
                value TEXT,                       -- Valor original
                unit TEXT,                        -- Unidad original
                reference_range TEXT,             -- Texto completo del rango (ej: "4.5 - 11.0")
                value_in_bold INTEGER DEFAULT 0,  -- 1 si el valor estaba en negrita, 0 si no
                FOREIGN KEY (report_id) REFERENCES pdf_reports(report_id) ON DELETE CASCADE
            );
        """
        )

        # 4. TABLA SPIROMETRY_MEASUREMENTS (Resultados Espirometría)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS spirometry_measurements (
                measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                parameter TEXT NOT NULL,          -- Ej: 'FVC', 'FEV1', 'FEV1/FVC'
                unit TEXT,                        -- Ej: 'L', '%', 'L/s'
                phase TEXT,                       -- 'Pre' o 'PostBD'
                value TEXT,                       -- Valor medido
                theoretical TEXT,                 -- Valor teórico
                lin TEXT,                         -- LIN (Límite Inferior Normal)
                z_score TEXT,                     -- Z-Score
                perc_theoretical TEXT,            -- % del valor teórico
                perc_change TEXT,                 -- % de cambio entre Pre y PostBD (si aplica)
                FOREIGN KEY (report_id) REFERENCES pdf_reports(report_id) ON DELETE CASCADE
            );
        """
        )

        # 5. TABLA PROCESSED_FILES (Control de Idempotencia)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_files (
                file_hash TEXT PRIMARY KEY,       -- SHA256 del archivo (evita duplicados de contenido)
                file_name TEXT,
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """
        )

        # 6. TABLA MACRO_FORMS (Bloques de MACRO)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS macro_forms (
                form_id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                visit TEXT,
                form_name TEXT,
                extraction_date TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
            );
            """
        )

        # 7. TABLA FORM_RESPONSES (Respuestas de formularios MACRO)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS form_responses (
                response_id INTEGER PRIMARY KEY AUTOINCREMENT,
                form_id INTEGER NOT NULL,
                question_label TEXT,
                repeat_instance INTEGER,
                value TEXT,
                status TEXT,
                date_time TEXT,
                FOREIGN KEY (form_id) REFERENCES macro_forms(form_id) ON DELETE CASCADE
            );
            """
        )

        # 8. TABLA QUESTIONNAIRE_SESSIONS (Maestro: Una fila por cuestionario/paciente/día)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS questionnaire_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                questionnaire_name TEXT NOT NULL,
                entry_date TEXT,
                extraction_date TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
            );
            """
        )

        # 9. TABLA QUESTIONNAIRE_RESPONSES (Detalle: Las respuestas vinculadas a la sesión)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS questionnaire_responses (
                response_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question_label TEXT NOT NULL,
                value TEXT,
                FOREIGN KEY (session_id) REFERENCES questionnaire_sessions(session_id) ON DELETE CASCADE
            );
            """
        )

        # Creación de índices para optimizar búsquedas futuras
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_patient_subject_id ON patients(subject_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_date ON pdf_reports(report_date);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_type ON pdf_reports(report_type);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_measurement_param ON blood_measurements(parameter);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_spirometry_param ON spirometry_measurements(parameter);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_spirometry_phase ON spirometry_measurements(phase);"
        )

        conn.commit()
        print(f"Base de datos '{db_path}' inicializada y verificada correctamente.")

    except sqlite3.Error as e:
        print(f"Error crítico creando el esquema: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    create_schema("clinical_data.db")
