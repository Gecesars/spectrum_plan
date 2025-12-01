from __future__ import annotations

from typing import Any, Dict

from . import ValidationResult


class DECEAValidator:
    """Valida o Pilar 1 (DECEA / ICA 11-408)."""

    REQUIRED_FIELDS = ("coordenadas", "altura", "pbzpa")

    def __init__(self) -> None:
        self.pillar = "decea"

    def validate(self, payload: Dict[str, Any]) -> ValidationResult:
        pillar_data = (payload or {}).get("pilar_decea") or {}
        messages = []
        status = "approved"

        for field in self.REQUIRED_FIELDS:
            if not pillar_data.get(field):
                status = "blocked"
                messages.append(f"Campo obrigatório ausente: {field}.")

        coords = pillar_data.get("coordenadas") or {}
        lat = coords.get("lat")
        lon = coords.get("lon") or coords.get("lng")
        altura = pillar_data.get("altura")

        if lat is None or lon is None:
            status = "blocked"
            messages.append("Coordenadas incompletas para submissão ao DECEA.")

        if altura is None:
            altura = 0
        elif altura > 150:
            status = "attention"
            messages.append("Altura acima de 150 m exige PBZPA e análise adicional.")

        pbzpa = pillar_data.get("pbzpa") or {}
        pbzpa_required = bool(pbzpa.get("classe"))
        if pbzpa_required and not pbzpa.get("protocolo"):
            status = "attention"
            messages.append("Informe o número do protocolo SYSAGA/DECEA.")

        condicionantes = pillar_data.get("condicionantes") or []
        if condicionantes:
            messages.extend([f"Condicionante: {item}" for item in condicionantes])

        metrics = {
            "latitude": lat,
            "longitude": lon,
            "altura_m": altura,
            "pbzpa": pbzpa,
            "condicionantes": condicionantes,
        }

        if not messages:
            messages.append("Plano DECEA validado sem pendências.")

        return ValidationResult(self.pillar, status, messages, metrics)
