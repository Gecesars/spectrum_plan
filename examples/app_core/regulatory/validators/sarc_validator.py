from __future__ import annotations

from typing import Any, Dict, List

from . import ValidationResult
from ..engine.sarc_linkbudget import evaluate_links


BLOCKED_SUBBANDS = {"3.5GHz", "26GHz"}


class SARCValidator:
    """Validação SARC (Ato 17542/2023)."""

    def __init__(self) -> None:
        self.pillar = "sarc"

    def validate(self, payload: Dict[str, Any]) -> ValidationResult:
        links: List[Dict[str, Any]] = list((payload or {}).get("sarc") or [])
        if not links:
            return ValidationResult(self.pillar, "approved", ["Sem enlaces SARC declarados."], {})

        attention = False
        blocked = False
        messages: List[str] = []

        budget = evaluate_links(links)

        for link in links:
            subfaixa = (link.get("subfaixa") or "").upper()
            if subfaixa in BLOCKED_SUBBANDS:
                blocked = True
                messages.append(f"Subfaixa {subfaixa} não disponível para SARC/MCom.")
            if not link.get("homologacao"):
                attention = True
                messages.append(f"Link {link.get('identificacao','?')} sem homologação cadastrada.")

        if budget.get("links_sem_margem"):
            attention = True
            messages.append("Há enlaces com margem inferior a 3 dB.")

        status = "approved"
        if blocked:
            status = "blocked"
        elif attention:
            status = "attention"

        if not messages:
            messages.append("Todos os enlaces SARC atendem aos critérios mínimos.")

        return ValidationResult(self.pillar, status, messages, budget)
