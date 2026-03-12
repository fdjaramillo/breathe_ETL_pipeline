"""
audit_logger.py
---------------
Provides a single function to append a row to the persistent audit CSV
located at data/registro_master.csv.

The CSV is purely for human-readable traceability and does NOT replace the
SQLite processed_files idempotency mechanism.

Columns:
  Original_Path    — Absolute resolved path of the file.
  Ingestion_Folder — Immediate parent directory name (useful for batch grouping).
  Timestamp        — ISO-8601 datetime to the second.
  Patient_ID       — Resolved patient identifier, or empty string if unknown.
  Status           — One of: PROCESSED, SKIPPED, ERROR, UNKNOWN.
  Reason           — Human-readable explanation (optional, empty string if none).
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

_AUDIT_CSV = Path("data") / "registro_master.csv"

_FIELDNAMES = [
    "Original_Path",
    "Ingestion_Folder",
    "Timestamp",
    "Patient_ID",
    "Status",
    "Reason",
]


def log_to_master_csv(filepath, status, patient_id=None, reason=None):
    """
    Appends a single audit row to data/registro_master.csv.

    Creates the data/ directory and the CSV header on first use.
    Each row is flushed immediately so partial runs are always recoverable.

    Args:
        filepath:   Full path to the file being audited.
        status:     One of 'PROCESSED', 'SKIPPED', 'ERROR', 'UNKNOWN'.
        patient_id: Optional patient identifier string.
        reason:     Optional human-readable reason for the status.
    """
    filepath = Path(filepath)
    _AUDIT_CSV.parent.mkdir(parents=True, exist_ok=True)

    write_header = not _AUDIT_CSV.exists()

    try:
        with open(_AUDIT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "Original_Path": str(filepath.resolve()),
                    "Ingestion_Folder": filepath.parent.name,
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Patient_ID": patient_id,
                    "Status": status,
                    "Reason": reason,
                }
            )
            f.flush()
    except Exception as e:
        logging.error(f"[AUDIT] No se pudo escribir en {_AUDIT_CSV}: {e}")
