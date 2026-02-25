import csv
import re


def process_csv(filepath, file_hash):
    """
    Procesa un CSV de MACRO y devuelve una lista de bloques (uno por formulario/paciente).
    """
    blocks = []
    current_block = None

    with open(filepath, "r", encoding="iso-8859-1", newline="") as f:
        reader = csv.reader(f)

        for row in reader:
            if not row or all(not (c or "").strip() for c in row):
                continue

            row_text = " ".join((c or "").strip() for c in row)

            # Detectar inicio de bloque
            if "Site:" in row_text and "Subject:" in row_text:
                if current_block:
                    blocks.append(current_block)

                subject_match = re.search(r"Subject:\s*([A-Za-z0-9_-]+)", row_text)
                visit_match = re.search(r"Visit:\s*([^/]+)", row_text)
                form_match = re.search(r"eForm:\s*(.+)$", row_text)

                current_block = {
                    "file_info": {"filename": filepath.name, "file_hash": file_hash},
                    "subject_id": (
                        subject_match.group(1).strip() if subject_match else None
                    ),
                    "macro_form": {
                        "visit": visit_match.group(1).strip() if visit_match else None,
                        "form_name": (
                            form_match.group(1).strip() if form_match else None
                        ),
                    },
                    "responses": [],
                }
                continue

            # Ignorar filas de cabecera/identificadores
            first_cell = (row[0] or "").strip() if row else ""
            if first_cell.startswith(("IDSub", "IDVer", "2026")):
                continue

            if not current_block:
                continue

            question = (row[0] or "").strip() if len(row) > 0 else ""
            value = (row[1] or "").strip() if len(row) > 1 else None
            status = (row[2] or "").strip() if len(row) > 2 else None
            date_time = (row[3] or "").strip() if len(row) > 3 else None

            if not question:
                continue

            m = re.match(r"^(.*?)(?:\s+\[(\d+)\])?$", question)
            label = m.group(1).strip() if m else question
            instance = int(m.group(2)) if m and m.group(2) else None

            current_block["responses"].append(
                {
                    "question_label": label,
                    "repeat_instance": instance,
                    "value": value,
                    "status": status,
                    "date_time": date_time,
                }
            )

    if current_block:
        blocks.append(current_block)

    return blocks
