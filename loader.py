import sqlite3
import logging
import json


def _normalize_key_part(value, default_value=""):
    if value is None:
        return default_value
    normalized = str(value).strip()
    return normalized if normalized else default_value


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _log_change(
    cursor,
    report_id,
    entity_type,
    field_name,
    previous_value,
    new_value,
    source_filename,
):
    previous_text = "" if previous_value is None else str(previous_value)
    new_text = "" if new_value is None else str(new_value)

    if previous_text == new_text:
        return

    cursor.execute(
        """
        INSERT INTO audit_changes (
            report_id, entity_type, field_name, previous_value, new_value, source_filename
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            entity_type,
            field_name,
            previous_text,
            new_text,
            source_filename,
        ),
    )


def _fetch_existing_report(
    cursor, patient_id, report_type, report_date, source_file_type
):
    cursor.execute(
        """
        SELECT
            report_id,
            lab_request_number,
            episode_number,
            report_date,
            weight_kg,
            height_cm,
            source_filename,
            source_file_type
        FROM pdf_reports
        WHERE patient_id = ?
          AND report_type = ?
          AND report_date = ?
          AND source_file_type = ?
        """,
        (patient_id, report_type, report_date, source_file_type),
    )
    return cursor.fetchone()


def _fetch_measurements(cursor, report_id, report_type):
    if report_type == "blood_test":
        cursor.execute(
            """
            SELECT section, subsection, parameter, value, unit, reference_range, value_in_bold
            FROM blood_measurements
            WHERE report_id = ?
            ORDER BY measurement_id
            """,
            (report_id,),
        )
        rows = cursor.fetchall()
        return [
            {
                "section": row[0],
                "subsection": row[1],
                "parameter": row[2],
                "value": row[3],
                "unit": row[4],
                "reference_range": row[5],
                "value_in_bold": row[6],
            }
            for row in rows
        ]

    if report_type == "spirometry":
        cursor.execute(
            """
            SELECT parameter, unit, phase, value, theoretical, lin, z_score, perc_theoretical, perc_change
            FROM spirometry_measurements
            WHERE report_id = ?
            ORDER BY measurement_id
            """,
            (report_id,),
        )
        rows = cursor.fetchall()
        return [
            {
                "parameter": row[0],
                "unit": row[1],
                "phase": row[2],
                "value": row[3],
                "theoretical": row[4],
                "lin": row[5],
                "z_score": row[6],
                "perc_theoretical": row[7],
                "perc_change": row[8],
            }
            for row in rows
        ]

    return []


def _replace_measurements(cursor, report_id, report_type, measurements):
    if report_type == "blood_test":
        cursor.execute(
            "DELETE FROM blood_measurements WHERE report_id = ?", (report_id,)
        )
        measurements_to_insert = [
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
            for m in measurements
        ]
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
        return

    if report_type == "spirometry":
        cursor.execute(
            "DELETE FROM spirometry_measurements WHERE report_id = ?", (report_id,)
        )
        measurements_to_insert = [
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
            for m in measurements
        ]
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


def mark_file_processed(file_hash, file_name, db_path):
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO processed_files (file_hash, file_name)
            VALUES (?, ?)
            """,
            (file_hash, file_name),
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logging.error(f"No se pudo marcar archivo procesado: {e}")
        return False
    finally:
        if conn:
            conn.close()


def upsert_patient_details(patient_data, db_path):
    """
    Inserta un paciente nuevo o actualiza EXCLUSIVAMENTE los campos NULL de uno existente.
    Registra en log cada operación realizada.

    Args:
        patient_data: Diccionario con subject_id, birth_date, sex
        db_path: Ruta de la base de datos

    Returns:
        True si tuvo éxito, False si falló
    """
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        subject_id = patient_data.get("subject_id")
        new_birth = patient_data.get("birth_date")
        new_sex = patient_data.get("sex")

        # 1. Comprobar si el paciente existe
        cursor.execute(
            "SELECT patient_id, birth_date, sex FROM patients WHERE subject_id = ?",
            (subject_id,),
        )
        row = cursor.fetchone()

        if not row:
            # CASO INSERT: Paciente nuevo
            cursor.execute(
                "INSERT INTO patients (subject_id, birth_date, sex) VALUES (?, ?, ?)",
                (subject_id, new_birth, new_sex),
            )
            conn.commit()
            logging.info(
                f"Paciente {subject_id}: Nuevo registro creado (birth_date={new_birth}, sex={new_sex})"
            )
            return True

        # CASO UPDATE: Paciente existente
        patient_id, current_birth, current_sex = row
        updates = []
        params = []
        changes = []

        # Solo actualizar campos NULL
        if current_birth is None and new_birth is not None:
            updates.append("birth_date = ?")
            params.append(new_birth)
            changes.append(f"birth_date: NULL → {new_birth}")

        if current_sex is None and new_sex is not None:
            updates.append("sex = ?")
            params.append(new_sex)
            changes.append(f"sex: NULL → {new_sex}")

        if updates:
            params.append(subject_id)
            query = f"UPDATE patients SET {', '.join(updates)} WHERE subject_id = ?"
            cursor.execute(query, params)
            conn.commit()
            logging.info(f"Paciente {subject_id}: Enriquecido ({', '.join(changes)})")
            return True
        else:
            # No había nada que actualizar
            logging.debug(f"Paciente {subject_id}: Sin cambios (datos ya completos)")
            return True

    except sqlite3.IntegrityError as e:
        if conn:
            conn.rollback()
        logging.error(f"Paciente {subject_id}: Error de integridad ({e})")
        return False

    except Exception:
        if conn:
            conn.rollback()
        logging.exception("Paciente %s: Fallo crítico en upsert", subject_id)
        return False

    finally:
        if conn:
            conn.close()


def save_to_db(data_object, db_path, nhc_mapping=None):
    """
    Recibe el Diccionario Jerárquico del extractor e inserta los datos
    en SQLite usando una transacción atómica.

    Args:
        data_object: Diccionario con los datos extraídos
        db_path: Ruta de la base de datos
        nhc_mapping: Diccionario {nhc: subject_id} para anonimización

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

        # --- Resolución de identidad ---
        patient_data = data_object.get("patient", {}) or {}
        nhc = patient_data.get("nhc")
        subject_id = None

        if nhc:
            # Si hay NHC, DEBE haber mapeo
            if nhc_mapping is None:
                logging.error(
                    f"    [ERROR ANONIMIZACIÓN] NHC '{nhc}' encontrado pero nhc_mapping es None. "
                    f"Archivo: {data_object.get('file_info', {}).get('filename', 'UNKNOWN')}"
                )
                conn.rollback()
                return False

            if nhc not in nhc_mapping:
                logging.error(
                    f"    [ERROR ANONIMIZACIÓN] NHC '{nhc}' no encontrado en el mapeo. "
                    f"Archivo: {data_object.get('file_info', {}).get('filename', 'UNKNOWN')}"
                )
                conn.rollback()
                return False

            subject_id = nhc_mapping[nhc]

        elif data_object.get("subject_id"):
            subject_id = data_object.get("subject_id")

        else:
            logging.error("No se encontró 'nhc' ni 'subject_id' en data_object.")
            conn.rollback()
            return False

        # --- Paciente ---
        cursor.execute(
            "SELECT patient_id FROM patients WHERE subject_id = ?", (subject_id,)
        )
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

        # --- Rama MACRO ---
        if "macro_form" in data_object:
            form = data_object.get("macro_form", {})
            cursor.execute(
                """
                INSERT INTO macro_forms (patient_id, visit, form_name)
                VALUES (?, ?, ?)
                """,
                (patient_id, form.get("visit"), form.get("form_name")),
            )
            form_id = cursor.lastrowid

            responses = data_object.get("responses", []) or []
            if responses:
                cursor.executemany(
                    """
                    INSERT INTO form_responses (
                        form_id, question_label, repeat_instance, value, status, date_time
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            form_id,
                            r.get("question_label"),
                            r.get("repeat_instance"),
                            r.get("value"),
                            r.get("status"),
                            r.get("date_time"),
                        )
                        for r in responses
                    ],
                )

            conn.commit()
            return True

        # --- Rama CUESTIONARIOS EXCEL ---
        if "questionnaire" in data_object:
            q_info = data_object["questionnaire"]

            # Verificar idempotencia: ¿Ya existe esta sesión?
            cursor.execute(
                """
                SELECT session_id FROM questionnaire_sessions 
                WHERE patient_id = ? AND questionnaire_name = ? AND entry_date = ?
                """,
                (patient_id, q_info.get("name"), q_info.get("entry_date")),
            )
            existing_session = cursor.fetchone()

            if existing_session:
                logging.debug(
                    f"Sesión de cuestionario ya existe para paciente {subject_id}, "
                    f"cuestionario '{q_info.get('name')}', fecha {q_info.get('entry_date')}. Omitida."
                )
                conn.rollback()
                return True  # Éxito: ya existe, no duplicamos

            # 1. Insertar el "Evento" en el Maestro
            cursor.execute(
                """
                INSERT INTO questionnaire_sessions (patient_id, questionnaire_name, entry_date)
                VALUES (?, ?, ?)
                """,
                (patient_id, q_info.get("name"), q_info.get("entry_date")),
            )
            session_id = cursor.lastrowid

            # 2. Insertar todos los "Datos" en el Detalle usando el session_id
            responses = data_object.get("responses", [])
            if responses:
                cursor.executemany(
                    """
                    INSERT INTO questionnaire_responses (session_id, question_label, value)
                    VALUES (?, ?, ?)
                    """,
                    [
                        (session_id, r.get("question"), r.get("value"))
                        for r in responses
                    ],
                )

            conn.commit()
            return True

        # --- Rama PDF ---
        report_data = data_object["report"]
        file_info = data_object["file_info"]

        report_type = _normalize_key_part(report_data.get("report_type"), "unknown")
        report_date = _normalize_key_part(report_data.get("report_date"), "")
        source_file_type = _normalize_key_part(
            file_info.get("source_file_type"), "unknown"
        )
        source_filename = file_info.get("filename")

        existing_report = _fetch_existing_report(
            cursor,
            patient_id,
            report_type,
            report_date,
            source_file_type,
        )

        if existing_report:
            report_id = existing_report[0]
            previous_report_values = {
                "lab_request_number": existing_report[1],
                "episode_number": existing_report[2],
                "report_date": existing_report[3],
                "weight_kg": existing_report[4],
                "height_cm": existing_report[5],
                "source_filename": existing_report[6],
                "source_file_type": existing_report[7],
            }

            new_report_values = {
                "lab_request_number": report_data.get("lab_request_number"),
                "episode_number": report_data.get("episode_number"),
                "report_date": report_date,
                "weight_kg": report_data.get("weight_kg"),
                "height_cm": report_data.get("height_cm"),
                "source_filename": source_filename,
                "source_file_type": source_file_type,
            }

            cursor.execute(
                """
                UPDATE pdf_reports
                SET lab_request_number = ?,
                    episode_number = ?,
                    report_date = ?,
                    weight_kg = ?,
                    height_cm = ?,
                    source_filename = ?,
                    source_file_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE report_id = ?
                """,
                (
                    new_report_values["lab_request_number"],
                    new_report_values["episode_number"],
                    new_report_values["report_date"],
                    new_report_values["weight_kg"],
                    new_report_values["height_cm"],
                    new_report_values["source_filename"],
                    new_report_values["source_file_type"],
                    report_id,
                ),
            )

            for field_name, old_value in previous_report_values.items():
                _log_change(
                    cursor,
                    report_id,
                    "pdf_report",
                    field_name,
                    old_value,
                    new_report_values[field_name],
                    source_filename,
                )

            previous_measurements = _fetch_measurements(cursor, report_id, report_type)
            new_measurements = data_object.get("measurements", []) or []

            if _json_dump(previous_measurements) != _json_dump(new_measurements):
                _log_change(
                    cursor,
                    report_id,
                    "measurements",
                    report_type,
                    _json_dump(previous_measurements),
                    _json_dump(new_measurements),
                    source_filename,
                )

            _replace_measurements(cursor, report_id, report_type, new_measurements)
        else:
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
                    source_filename,
                    source_file_type,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    patient_id,
                    report_type,
                    report_data.get("lab_request_number"),
                    report_data.get("episode_number"),
                    report_date,
                    report_data.get("weight_kg"),
                    report_data.get("height_cm"),
                    source_filename,
                    source_file_type,
                ),
            )
            report_id = cursor.lastrowid
            _replace_measurements(
                cursor,
                report_id,
                report_type,
                data_object.get("measurements", []) or [],
            )

        # CONFIRMAR TRANSACCIÓN
        conn.commit()
        return True

    except sqlite3.IntegrityError as e:
        if conn:
            conn.rollback()
        logging.error(
            f"[ERROR INTEGRIDAD] Fallo al guardar {data_object.get('file_info', {}).get('filename', 'UNKNOWN')}: {e}"
        )
        return False

    except Exception:
        if conn:
            conn.rollback()
        logging.exception("[ERROR CRÍTICO] Fallo general en loader")
        return False

    finally:
        if conn:
            conn.close()
