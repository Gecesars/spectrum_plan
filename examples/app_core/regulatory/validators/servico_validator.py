from __future__ import annotations

from typing import Any, Dict

from . import ValidationResult
from ..engine.erp import compute_erp_data


class ServiceValidator:
    """Pilar 3 — Serviço / Ato Técnico."""

    CLASS_LIMITS_ERP_KW = {
        "A": 120,
        "B1": 15,
        "B2": 6,
        "C": 3,
        "D": 0.5,
    }

    def __init__(self) -> None:
        self.pillar = "servico"

    def validate(self, payload: Dict[str, Any]) -> ValidationResult:
        estacao = (payload or {}).get("estacao") or {}
        system = (payload or {}).get("sistema_irradiante") or {}
        classe = (estacao.get("classe") or "").upper()
        canal = estacao.get("canal") or estacao.get("frequencia")
        service = estacao.get("servico") or "FM"

        erp_info = compute_erp_data(system)
        limit_kw = self.CLASS_LIMITS_ERP_KW.get(classe, 10)

        status = "approved"
        messages = [f"Classe {classe or '—'} – limite ERP {limit_kw} kW"]

        if erp_info["erp_kw"] > limit_kw:
            status = "blocked"
            messages.append(
                f"ERP calculada ({erp_info['erp_kw']:.2f} kW) excede o limite permitido para a classe {classe}."
            )
        elif erp_info["erp_kw"] > limit_kw * 0.9:
            status = "attention"
            messages.append("Ajuste sugerido: reduzir potência/ganho em 10% para manter margem regulatória.")

        if not system.get("hrp") or not system.get("vrp"):
            status = "attention"
            messages.append("Importe os diagramas HRP/VRP para validar coerência direcional.")

        metrics = {
            "service": service,
            "classe": classe,
            "canal": canal,
            "erp_kw": erp_info["erp_kw"],
            "limit_kw": limit_kw,
            "antena": system.get("modelo"),
            "erp": erp_info,
        }

        return ValidationResult(self.pillar, status, messages, metrics)
