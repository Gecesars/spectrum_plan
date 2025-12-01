"""Validação dos pilares regulatórios."""
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class ValidationResult:
    pillar: str
    status: str
    messages: List[str]
    metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pillar": self.pillar,
            "status": self.status,
            "messages": self.messages,
            "metrics": self.metrics,
        }


@dataclass
class PipelineOutcome:
    overall_status: str
    results: List[ValidationResult]
    metrics: Dict[str, Any]
