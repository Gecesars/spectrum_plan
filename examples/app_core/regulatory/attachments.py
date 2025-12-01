from __future__ import annotations

import base64
import io
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from .engine.erp import compute_erp_data
from .engine.rni import estimate_power_density, limit_for_environment

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None


def _pdf_bytes(title: str, sections: Sequence[Tuple[str, Any]], footer: str | None = None) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, title)
    y -= 30
    c.setFont("Helvetica", 11)
    for label, value in sections:
        lines = str(value if value not in (None, "") else "—").splitlines() or ["—"]
        c.drawString(40, y, f"{label}:")
        y -= 16
        for line in lines:
            c.drawString(60, y, line[:110])
            y -= 14
            if y < 60:
                c.showPage()
                y = height - 50
                c.setFont("Helvetica", 11)
    if footer:
        c.setFont("Helvetica-Oblique", 9)
        for line in footer.splitlines():
            c.drawString(40, y, line[:120])
            y -= 12
            if y < 60:
                c.showPage()
                y = height - 50
                c.setFont("Helvetica-Oblique", 9)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


def _encode_pdf(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


@lru_cache(maxsize=1)
def _anatel_excerpt() -> str:
    base_dir = Path(__file__).resolve().parents[2]
    pdf_path = base_dir / "codex" / "anatel.pdf"
    if not pdf_path.exists() or PdfReader is None:
        return "Resumo baseado no caderno 'Básico de Radiodifusão' (ANATEL/CMPRL, mai/2003)."
    try:
        reader = PdfReader(str(pdf_path))
        text = []
        for page in reader.pages[:2]:
            extract = page.extract_text()
            if extract:
                text.append(extract.strip())
        excerpt = " ".join(text)
        return excerpt[:1000] + "..."
    except Exception:
        return "Resumo baseado no caderno 'Básico de Radiodifusão' (ANATEL/CMPRL, mai/2003)."


def build_auto_attachments(
    project,
    station: Dict[str, Any],
    system: Dict[str, Any],
    coverage: Dict[str, Any],
    pilar_decea: Dict[str, Any],
    pilar_rni: Dict[str, Any],
) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []

    resp_nome = getattr(project.user, "username", "Responsável Técnico")
    resp_email = getattr(project.user, "email", "—")
    engenheiro_crea = project.settings.get("artCREA") if project.settings else None

    art_sections = [
        ("Responsável técnico", resp_nome),
        ("Email", resp_email),
        ("CREA", engenheiro_crea or "Pendente"),
        ("Serviço", station.get("servico") or "Radiodifusão"),
        ("Classe", station.get("classe") or "B1"),
        ("Canal/Frequência", station.get("canal") or station.get("frequencia") or "—"),
        ("Potência TX (W)", system.get("potencia_w")),
        ("Ganho antena (dBi)", system.get("ganho_tx_dbi")),
        ("Perdas (dB)", system.get("perdas_db")),
    ]
    art_pdf = _encode_pdf(_pdf_bytes("ART Profissional - Resumo técnico", art_sections, _anatel_excerpt()))
    attachments.append({
        "type": "art_profissional",
        "name": f"art_{project.slug}.pdf",
        "mime_type": "application/pdf",
        "content": art_pdf,
        "description": "Resumo automático da ART com dados do projeto.",
    })

    coords = pilar_decea.get("coordenadas") or {}
    decea_sections = [
        ("Latitude", coords.get("lat")),
        ("Longitude", coords.get("lon")),
        ("Altitude torre (m)", pilar_decea.get("altura")),
        ("PBZPA", (pilar_decea.get("pbzpa") or {}).get("classe")),
        ("Protocolo SYSAGA", (pilar_decea.get("pbzpa") or {}).get("protocolo") or "Pendente"),
        ("Condicionantes", ", ".join(pilar_decea.get("condicionantes") or [])),
    ]
    decea_pdf = _encode_pdf(_pdf_bytes("Protocolo DECEA/SYSAGA", decea_sections, "Dados georreferenciados para submissão ao DECEA."))
    attachments.append({
        "type": "decea_protocolo",
        "name": f"decea_{project.slug}.pdf",
        "mime_type": "application/pdf",
        "content": decea_pdf,
        "description": "Resumo automático para submissão ao DECEA.",
    })

    rni_distance = float(pilar_rni.get("distancia_m") or 5.0)
    rni_env = pilar_rni.get("classificacao") or "ocupacional"
    erp = compute_erp_data(system)
    density = estimate_power_density(erp["erp_kw"], rni_distance)
    limit = limit_for_environment(rni_env, system.get("frequencia_mhz") or 100.0)
    rni_sections = [
        ("ERP (kW)", erp["erp_kw"]),
        ("Distância avaliativa (m)", rni_distance),
        ("Cenário", rni_env),
        ("Densidade calculada (W/m²)", round(density, 5)),
        ("Limite Res.700 (W/m²)", round(limit, 5)),
        ("Responsável técnico", resp_nome),
    ]
    rni_footer = "Documento automático conforme Resolução 700/2018 (RNI)."
    rni_pdf = _encode_pdf(_pdf_bytes("Laudo de Exposição - RNI", rni_sections, rni_footer))
    attachments.append({
        "type": "rni_relatorio",
        "name": f"rni_{project.slug}.pdf",
        "mime_type": "application/pdf",
        "content": rni_pdf,
        "description": "Laudo automático com cálculo de densidade de potência.",
    })

    pattern = system.get("pattern_metrics") or {}
    hrp_sections = [
        ("HPBW", pattern.get("hpbw")),
        ("F/B Ratio", pattern.get("fbr")),
        ("SLL", pattern.get("sll")),
        ("Ripple", pattern.get("ripple")),
        ("Direção máxima", pattern.get("max_dir")),
    ]
    hrp_pdf = _encode_pdf(_pdf_bytes("HRP/VRP - Métricas resumidas", hrp_sections))
    attachments.append({
        "type": "hrp_vrp",
        "name": f"hrp_vrp_{project.slug}.pdf",
        "mime_type": "application/pdf",
        "content": hrp_pdf,
        "description": "Resumo automático dos diagramas HRP/VRP.",
    })

    if coverage:
        cov_sections = [
            ("Engine", coverage.get("engine")),
            ("Raio (km)", coverage.get("radius_km") or coverage.get("requested_radius_km")),
            ("Perda combinada (dB)", (coverage.get("center_metrics") or {}).get("combined_loss_center_db")),
        ]
        laudo_pdf = _encode_pdf(_pdf_bytes("Laudo de Vistoria - Cobertura", cov_sections))
        attachments.append({
            "type": "laudo_vistoria",
            "name": f"laudo_vistoria_{project.slug}.pdf",
            "mime_type": "application/pdf",
            "content": laudo_pdf,
            "description": "Laudo sintético baseado na última mancha gerada.",
        })

    return attachments
