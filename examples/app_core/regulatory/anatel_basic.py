from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app_core.models import Project


@dataclass
class BasicSection:
    code: str
    title: str
    status: str
    data: Dict[str, Any]
    notes: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "code": self.code,
            "title": self.title,
            "status": self.status,
            "data": self.data,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload


def _status(value) -> str:
    return "ok" if value not in (None, "", [], {}) else "pending"


def _default(value, placeholder="—"):
    return value if value not in (None, "") else placeholder


def build_basic_form(project: Project) -> List[Dict[str, Any]]:
    settings = project.settings or {}
    user = project.user
    coverage = settings.get("lastCoverage") or {}

    lat = settings.get("latitude") or getattr(user, "latitude", None)
    lon = settings.get("longitude") or getattr(user, "longitude", None)
    station_class = settings.get("serviceClass") or settings.get("classe") or "B1"
    frequency = settings.get("frequency") or getattr(user, "frequencia", None)

    sections: List[BasicSection] = []

    sections.append(BasicSection(
        code="01",
        title="Conceituação e finalidade dos serviços",
        status="ok",
        data={
            "servico": settings.get("serviceType") or getattr(user, "servico", "Radiodifusão"),
            "finalidade": settings.get("servicePurpose") or "educativa/cultural com aspectos informativo e recreativo",
        },
        notes="Baseado no material Básico de Radiodifusão/ANATEL (mai/2003).",
    ))

    sections.append(BasicSection(
        code="02",
        title="Modalidades e classificação",
        status=_status(station_class),
        data={
            "classe": station_class,
            "modalidade": settings.get("serviceModality") or "comercial" if getattr(user, "servico", "FM").lower() != "educativo" else "educativo",
            "canal": settings.get("canal") or settings.get("channel") or frequency,
        },
    ))

    sections.append(BasicSection(
        code="03",
        title="Faixas de frequências atribuídas",
        status=_status(frequency),
        data={
            "frequencia_mhz": frequency,
            "faixa": settings.get("faixa") or ("VHF" if frequency and 30 <= frequency <= 300 else "UHF" if frequency else None),
        },
    ))

    sections.append(BasicSection(
        code="04",
        title="Competência para execução",
        status="ok",
        data={
            "orgao": "Ministério das Comunicações / ANATEL",
            "supervisao": "SCM / CMPR / CMPRL",
        },
    ))

    sections.append(BasicSection(
        code="05",
        title="Forma de autorização",
        status=_status(settings.get("authorizationType")),
        data={
            "tipo": settings.get("authorizationType") or "Decreto Presidencial + Portaria MCom",
            "processo": settings.get("authorizationProcess") or settings.get("processo") or "n/d",
        },
    ))

    sections.append(BasicSection(
        code="06",
        title="Tipos de outorga",
        status=_status(settings.get("outorgaTipo")),
        data={
            "tipo": settings.get("outorgaTipo") or "Permissão",
            "ato": settings.get("outorgaAto") or settings.get("ato") or "—",
        },
    ))

    sections.append(BasicSection(
        code="07",
        title="Quantidade de outorgas permitida",
        status="ok",
        data={
            "outorgas_usuario": project.user.projects.count() if hasattr(project.user.projects, 'count') else len(list(project.user.projects)),
            "limite": settings.get("outorgaLimite") or 2,
        },
    ))

    sections.append(BasicSection(
        code="08",
        title="Transferência de outorga",
        status=_status(settings.get("transferenciaStatus")),
        data={
            "permitido": settings.get("transferenciaStatus") or "necessita anuência do MCom/ANATEL",
            "observacoes": settings.get("transferenciaNotas"),
        },
    ))

    sections.append(BasicSection(
        code="09",
        title="Prazos",
        status="ok",
        data={
            "publicacao": settings.get("dataPublicacao") or project.created_at.isoformat() if project.created_at else None,
            "vigencia": settings.get("prazoVigencia") or "10 anos",
        },
    ))

    sections.append(BasicSection(
        code="10",
        title="Área de prestação",
        status=_status(settings.get("tx_location_name") or settings.get("areaCobertura")),
        data={
            "municipio": settings.get("tx_location_name") or settings.get("municipio") or getattr(user, "tx_location_name", None),
            "uf": settings.get("uf") or getattr(user, "estado", None),
            "aoi": settings.get("aoi_geojson"),
        },
    ))

    sections.append(BasicSection(
        code="11",
        title="Localização das estações",
        status=_status(lat and lon),
        data={
            "latitude": lat,
            "longitude": lon,
            "altura": settings.get("towerHeight") or getattr(user, "tower_height", None),
        },
    ))

    sections.append(BasicSection(
        code="12",
        title="Planos básicos de distribuição de canais",
        status=_status(settings.get("planosBasicos")),
        data={
            "plano": settings.get("planosBasicos") or "PBFM",
            "canal": settings.get("canal") or settings.get("channel") or frequency,
        },
    ))

    sections.append(BasicSection(
        code="13",
        title="Autorização de uso de radiofrequência",
        status=_status(settings.get("autorizacaoRF")),
        data={
            "ato": settings.get("autorizacaoRF") or coverage.get("request", {}).get("coverageEngine"),
            "status": coverage.get("location_status") or settings.get("rfStatus"),
        },
    ))

    sections.append(BasicSection(
        code="14",
        title="Localização dos estúdios",
        status=_status(settings.get("studioAddress")),
        data={
            "endereco": settings.get("studioAddress") or settings.get("studioAddress1"),
            "municipio": settings.get("studioCity"),
        },
    ))

    sections.append(BasicSection(
        code="15",
        title="Irradiações em caráter experimental",
        status=_status(settings.get("experimental") ),
        data={
            "experimental": settings.get("experimental") or False,
            "prazo": settings.get("experimentalPrazo"),
        },
    ))

    sections.append(BasicSection(
        code="16",
        title="Licenciamento de estações",
        status=_status(settings.get("licenciamentoStatus")),
        data={
            "status": settings.get("licenciamentoStatus") or "pendente",
            "licencas": settings.get("licencas") or [],
        },
    ))

    sections.append(BasicSection(
        code="17",
        title="FISTEL/TFI/TFF/PPDUR",
        status=_status(settings.get("fistel")),
        data={
            "fistel": settings.get("fistel"),
            "tfi": settings.get("tfi"),
            "tff": settings.get("tff"),
            "ppdur": settings.get("ppdur"),
        },
    ))

    sections.append(BasicSection(
        code="18",
        title="Serviços auxiliares de radiodifusão e correlatos",
        status=_status(settings.get("servicosAuxiliares")),
        data={
            "servicos": settings.get("servicosAuxiliares") or [],
        },
    ))

    sections.append(BasicSection(
        code="19",
        title="Legislação aplicável",
        status="ok",
        data={
            "leis": settings.get("legislacaoExtra") or [
                "Resolução 700/2018 (RNI)",
                "Ato 17542/2023 (SARC)",
                "ICA 11-408 (DECEA)",
                "Portaria 2840/2024 (Laudo)",
            ],
        },
    ))

    return [section.as_dict() for section in sections]
