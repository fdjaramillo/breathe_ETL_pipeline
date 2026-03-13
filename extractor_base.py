from abc import ABC, abstractmethod
from pathlib import Path


class BaseExtractor(ABC):
    """Common contract for ETL extractors that emit loader-compatible payloads."""

    def __init__(self, filepath, file_hash):
        self.filepath = Path(filepath)
        self.file_hash = file_hash

    def build_file_info(self, **extra_fields):
        file_info = {
            "filename": self.filepath.name,
            "file_hash": self.file_hash,
        }
        file_info.update(extra_fields)
        return file_info

    @abstractmethod
    def process(self):
        """Return the loader-compatible payload for the current file."""
