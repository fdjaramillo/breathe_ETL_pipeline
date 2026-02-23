import sqlite3


def save_to_db(data_object, nhc_mapping, db_path):
    """
    Recibe el Diccionario Jerárquico del extractor e inserta los datos
    en SQLite usando una transacción atómica.

    Args:
        data_object: Diccionario con los datos extraídos
        nhc_mapping: Diccionario {nhc: subject_id} para anonimización
        db_path: Ruta de la base de datos

    Retorna: True si tuvo éxito, False si falló.
    """
    if not data_object:
        return False

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Activar Foreign Keys explícitamente
        cursor.execute("PRAGMA foreign_keys = ON;")

        # INICIO DE TRANSACCIÓN
        # Si algo falla dentro de este bloque, se hace rollback automático al final
        cursor.execute("BEGIN TRANSACTION;")

        # 1. GESTIÓN DEL PACIENTE (Anonimización Estricta)
        patient_data = data_object["patient"]
        nhc = patient_data.get("nhc")

        # Validar que el NHC existe en el mapeo
        if nhc not in nhc_mapping:
            print(
                f"    [ERROR ANONIMIZACIÓN] NHC '{nhc}' no encontrado en el mapeo. "
                f"Archivo: {data_object['file_info']['filename']}"
            )
            conn.rollback()
            return False

        subject_id = nhc_mapping[nhc]

        # Verificar si el paciente ya existe usando subject_id
        cursor.execute("SELECT patient_id FROM patients WHERE subject_id = ?", (subject_id,))
        result = cursor.fetchone()

        if result:
            # Paciente existe: Usamos su ID
            patient_id = result[0]
        else:
            # Paciente nuevo: Insertamos (sin nhc ni name)
            cursor.execute(
                """
                INSERT INTO patients (subject_id, birth_date, sex)
                VALUES (?, ?, ?)
            """,
                (
                    subject_id,
                    patient_data.get("birth_date"),
                    patient_data.get("sex"),
                ),
            )
            patient_id = cursor.lastrowid

        # 2. INSERCIÓN DEL INFORME (LAB REPORT)
        report_data = data_object["report"]
        file_info = data_object["file_info"]

        cursor.execute(
            """
            INSERT INTO pdf_reports (
                patient_id, 
                report_type,
                lab_request_number, 
                episode_number, 
                report_date, 
                weight_kg,
                height_cm,
                source_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                patient_id,
                report_data.get("report_type"),
                report_data.get("lab_request_number"),
                report_data.get("episode_number"),
                report_data.get("report_date"),
                report_data.get("weight_kg"),
                report_data.get("height_cm"),
                file_info.get("filename"),
            ),
        )

        report_id = cursor.lastrowid

        # 3. INSERCIÓN MASIVA DE MEDICIONES
        measurements = data_object["measurements"]
        report_type = report_data.get("report_type")

        if report_type == "blood_test":
            measurements_to_insert = []
            for m in measurements:
                measurements_to_insert.append(
                    (
                        report_id,
                        m.get("section"),
                        m.get("subsection"),
                        m.get("parameter"),
                        m.get("value"),
                        m.get("unit"),
                        m.get("reference_range"),
                        m.get("value_in_bold"),
                    )
                )

            if measurements_to_insert:
                cursor.executemany(
                    """
                    INSERT INTO blood_measurements (
                        report_id, section, subsection, parameter, 
                        value, unit, reference_range, value_in_bold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    measurements_to_insert,
                )

        elif report_type == "spirometry":
            measurements_to_insert = []
            for m in measurements:
                measurements_to_insert.append(
                    (
                        report_id,
                        m.get("parameter"),
                        m.get("unit"),
                        m.get("phase"),
                        m.get("value"),
                        m.get("theoretical"),
                        m.get("lin"),
                        m.get("z_score"),
                        m.get("perc_theoretical"),
                        m.get("perc_change"),
                    )
                )

            if measurements_to_insert:
                cursor.executemany(
                    """
                    INSERT INTO spirometry_measurements (
                        report_id, parameter, unit, phase, value, 
                        theoretical, lin, z_score, perc_theoretical, perc_change
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    measurements_to_insert,
                )

        # 4. REGISTRO DE CONTROL
        cursor.execute(
            """
            INSERT INTO processed_files (file_hash, file_name)
            VALUES (?, ?)
        """,
            (file_info.get("file_hash"), file_info.get("filename")),
        )

        # CONFIRMAR TRANSACCIÓN
        conn.commit()

        return True

    except sqlite3.IntegrityError as e:
        if conn:
            conn.rollback()
        print(
            f"    [ERROR INTEGRIDAD] Fallo al guardar {data_object['file_info']['filename']}: {e}"
        )
        # Típico error: El hash ya existe (Unique constraint failed)
        return False

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"    [ERROR CRITICO] Fallo general en loader: {e}")
        return False

    finally:
        if conn:
            conn.close()
