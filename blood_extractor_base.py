from abc import abstractmethod

import fitz

from extractor_base import BaseExtractor


class BaseBloodTestExtractor(BaseExtractor):
    """Template method for blood PDF extractors with shared output shaping."""

    source_file_type = None

    @abstractmethod
    def extract_patient(self, doc):
        """Extract patient metadata from an open PDF document."""

    @abstractmethod
    def extract_report(self, doc):
        """Extract report metadata from an open PDF document."""

    @abstractmethod
    def extract_measurements(self, doc):
        """Extract normalized blood measurements from an open PDF document."""

    def build_report(self, doc):
        report = self.extract_report(doc) or {}
        report.setdefault("report_type", "blood_test")
        return report

    def process(self):
        with fitz.open(self.filepath) as doc:
            patient = self.extract_patient(doc) or {}
            report = self.build_report(doc)
            measurements = self.extract_measurements(doc) or []

        file_info_kwargs = {}
        if self.source_file_type:
            file_info_kwargs["source_file_type"] = self.source_file_type

        return {
            "file_info": self.build_file_info(**file_info_kwargs),
            "patient": patient,
            "report": report,
            "measurements": measurements,
        }
