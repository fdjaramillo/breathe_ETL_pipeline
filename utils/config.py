import json
import logging
from pathlib import Path


_NON_PATH_KEYS = {
    "run_phase_0",
    "run_phase_1",
    "run_phase_3",
    "run_phase_4",
    "run_phase_5",
}


def load_config(config_path="config.json"):
    """
    Load runtime configuration from JSON and coerce path-like values to Path.

    Args:
        config_path: Relative or absolute path to the JSON config file.

    Returns:
        Dict with configuration values, or None if loading fails.
    """
    config_path = Path(config_path)

    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)

        for key, value in config.items():
            if isinstance(value, str) and key not in _NON_PATH_KEYS:
                config[key] = Path(value)

        logging.info("Configuración cargada desde %s", config_path)
        return config
    except FileNotFoundError:
        logging.error("Archivo de configuración no encontrado: %s", config_path)
        return None
    except json.JSONDecodeError:
        logging.exception("Error al parsear JSON en %s", config_path)
        return None
    except Exception:
        logging.exception(
            "Error inesperado al cargar configuración desde %s", config_path
        )
        return None
