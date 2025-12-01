from __future__ import annotations

from typing import Any, Dict

from . import ValidationResult
from ..engine.erp import compute_erp_data
from ..engine.rni import estimate_power_density, limit_for_environment


class RNIValidator:
    """Pilar 2 – Resolução 700/2018 (RNI)."""

    def __init__(self) -> None:
        self.pillar = "rni"

    def validate(self, payload: Dict[str, Any]) -> ValidationResult:
        system = (payload or {}).get("sistema_irradiante") or {}
        rni_block = (payload or {}).get("pilar_rni") or {}
        env = rni_block.get("classificacao") or "ocupacional"
        distance = float(rni_block.get("distancia_m") or 2.0)
        frequency = float(system.get("frequencia_mhz") or rni_block.get("frequencia_mhz") or 100.0)

        erp_info = compute_erp_data(system)
        density = estimate_power_density(erp_info["erp_kw"], distance)
        limit = limit_for_environment(env, frequency)

        status = "approved"
        messages = []
        if density > limit:
            status = "blocked"
            messages.append(
                f"Densidade {density:.3f} W/m² excede o limite para o cenário {env} ({limit:.3f} W/m²)."
            )
        elif density > (limit * 0.8):
            status = "attention"
            messages.append("Margem inferior a 20% do limite de exposição.")
        else:
            messages.append("Densidade de potência dentro da faixa permitida.")

        if not rni_block.get("responsavel_tecnico"):
            status = "attention"
            messages.append("Informe o responsável técnico/ART para o laudo de RNI.")

        metrics = {
            "erp_kw": erp_info["erp_kw"],
            "eirp_dbw": erp_info["eirp_dbw"],
            "density_w_m2": density,
            "limit_w_m2": limit,
            "environment": env,
            "distance_m": distance,
            "frequency_mhz": frequency,
        }

        return ValidationResult(self.pillar, status, messages, metrics)
