from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from extensions import db
from app_core.storage import inline_asset_path
from app_core.storage_utils import rehydrate_asset_data
from app_core.utils import slugify
from app_core.models import Asset, AssetType, Project

from .models import (
    RegulatoryAttachment,
    RegulatoryAttachmentType,
    RegulatoryPillar,
    RegulatoryReport,
    RegulatoryReportStatus,
    RegulatoryValidation,
    RegulatoryValidationStatus,
)
from .report.generator import RegulatoryReportGenerator
from .validators import PipelineOutcome, ValidationResult
from .validators.decea_validator import DECEAValidator
from .validators.rni_validator import RNIValidator
from .validators.servico_validator import ServiceValidator
from .validators.sarc_validator import SARCValidator
from .anatel_basic import build_basic_form
from .attachments import build_auto_attachments


class RegulatoryPipeline:
    def __init__(self) -> None:
        self.validators = [
            DECEAValidator(),
            RNIValidator(),
            ServiceValidator(),
            SARCValidator(),
        ]

    def run(self, payload: Dict[str, Any]) -> PipelineOutcome:
        results: List[ValidationResult] = []
        metrics: Dict[str, Any] = {}
        flags: List[str] = []
        for validator in self.validators:
            result = validator.validate(payload)
            results.append(result)
            metrics[validator.pillar] = result.metrics
            flags.append(result.status)
        if any(flag == "blocked" for flag in flags):
            overall = "blocked"
        elif any(flag == "attention" for flag in flags):
            overall = "attention"
        else:
            overall = "approved"
        return PipelineOutcome(overall, results, metrics)


def _pick(settings: Dict[str, Any], keys: Tuple[str, ...], fallback=None):
    for key in keys:
        if key in settings and settings[key] not in (None, ''):
            return settings[key]
    return fallback


def build_default_payload(project: Project) -> Dict[str, Any]:
    settings = project.settings or {}
    user = project.user
    coverage = settings.get("lastCoverage") or {}

    lat = _pick(settings, ("latitude",), getattr(user, "latitude", None))
    lon = _pick(settings, ("longitude",), getattr(user, "longitude", None))
    torre = _pick(settings, ("towerHeight", "altura_torre"), getattr(user, "tower_height", None))

    station = {
        "servico": settings.get("serviceType") or getattr(user, "servico", "FM"),
        "classe": settings.get("serviceClass") or settings.get("classe") or "B1",
        "canal": settings.get("canal") or settings.get("channel") or settings.get("frequency") or getattr(user, "frequencia", None),
        "frequencia": settings.get("frequency") or getattr(user, "frequencia", None),
        "descricao": project.description,
    }

    system = {
        "potencia_w": _pick(settings, ("transmissionPower", "potencia_w"), getattr(user, "transmission_power", 1.0)),
        "ganho_tx_dbi": _pick(settings, ("antennaGain",), getattr(user, "antenna_gain", 0.0)),
        "perdas_db": _pick(settings, ("Total_loss", "total_loss"), getattr(user, "total_loss", 0.0)),
        "polarizacao": settings.get("polarization") or getattr(user, "polarization", "horizontal"),
        "modelo": settings.get("antennaModel") or settings.get("antenna_model"),
        "altura_torre": torre,
        "azimute": _pick(settings, ("antennaDirection",), getattr(user, "antenna_direction", 0.0)),
        "tilt": _pick(settings, ("antennaTilt",), getattr(user, "antenna_tilt", 0.0)),
        "frequencia_mhz": settings.get("frequency") or getattr(user, "frequencia", 100.0),
        "pattern_metrics": settings.get("patternMetrics"),
    }

    pilar_decea = {
        "coordenadas": {"lat": lat, "lon": lon},
        "altura": torre,
        "pbzpa": settings.get("pbzpa") or {},
        "condicionantes": settings.get("deceaConditions") or [],
    }

    pilar_rni = {
        "classificacao": settings.get("rniScenario") or "ocupacional",
        "distancia_m": settings.get("rniDistance") or 5,
        "responsavel_tecnico": settings.get("rniResponsible") or getattr(user, "username", None),
        "frequencia_mhz": system["frequencia_mhz"],
    }

    attachments = settings.get("regulatoryAttachments")
    if not attachments:
        attachments = build_auto_attachments(project, station, system, coverage, pilar_decea, pilar_rni)

    return {
        "estacao": station,
        "sistema_irradiante": system,
        "pilar_decea": pilar_decea,
        "pilar_rni": pilar_rni,
        "sarc": settings.get("sarcLinks") or [],
        "attachments": attachments,
        "lastCoverage": coverage,
    }


def _attachment_type(value: str) -> RegulatoryAttachmentType:
    try:
        return RegulatoryAttachmentType(value)
    except ValueError:
        return RegulatoryAttachmentType.custom


def _load_attachment_bytes(project: Project, path_value: str | None) -> bytes | None:
    if not path_value:
        return None
    if str(path_value).startswith('inline://'):
        asset = Asset.query.filter_by(project_id=project.id, path=path_value).first()
        if asset:
            if asset.data:
                return bytes(asset.data)
            return rehydrate_asset_data(asset)
        return None
    # Legacy reference pointing to disk; try to locate Asset row and rehydrate.
    asset = Asset.query.filter_by(project_id=project.id, path=path_value).first()
    if asset:
        payload = asset.data or rehydrate_asset_data(asset)
        return payload
    return None


def _persist_attachment(report: RegulatoryReport, attachment_data: Dict[str, Any]) -> Tuple[RegulatoryAttachment, Tuple[str, bytes]]:
    project = report.project
    raw_name = attachment_data.get('name') or f"attachment-{datetime.utcnow().timestamp():.0f}.pdf"
    filename = slugify(Path(raw_name).stem) + Path(raw_name).suffix

    data = None
    if attachment_data.get('content'):
        data = base64.b64decode(attachment_data['content'])
    elif attachment_data.get('path'):
        data = _load_attachment_bytes(project, attachment_data['path'])
    if data is None:
        data = f"Arquivo gerado automaticamente em {datetime.utcnow():%Y-%m-%d %H:%M UTC}".encode('utf-8')

    extension = Path(filename).suffix or '.bin'
    asset = Asset(
        project_id=project.id,
        type=AssetType.pdf if extension.lower() == '.pdf' else AssetType.other,
        path=inline_asset_path('regulatory', extension),
        mime_type=attachment_data.get('mime_type') or ('application/pdf' if extension.lower() == '.pdf' else 'application/octet-stream'),
        byte_size=len(data),
        data=data,
        meta={
            'report_id': str(report.id),
            'attachment_type': attachment_data.get('type'),
            'name': filename,
        },
    )
    db.session.add(asset)
    db.session.flush()

    attachment = RegulatoryAttachment(
        report_id=report.id,
        type=_attachment_type(attachment_data.get('type', 'custom')),
        path=asset.path,
        description=attachment_data.get('description'),
        mime_type=attachment_data.get('mime_type') or asset.mime_type,
    )
    db.session.add(attachment)
    return attachment, (filename, data)


def generate_regulatory_report(project: Project, payload: Dict[str, Any], *, name: str | None = None) -> RegulatoryReport:
    pipeline = RegulatoryPipeline()
    outcome = pipeline.run(payload)

    report_name = name or payload.get('nome') or f"Relatório {project.name}"
    report_slug = slugify(report_name + f"-{datetime.utcnow():%Y%m%d%H%M}")
    report = RegulatoryReport(
        project_id=project.id,
        name=report_name,
        slug=report_slug,
        payload=payload,
        status=RegulatoryReportStatus.pending,
        validation_summary={
            'overall': outcome.overall_status,
            'generated_at': datetime.utcnow().isoformat(),
        },
    )
    db.session.add(report)
    db.session.flush()

    for result in outcome.results:
        status_key = result.status if result.status in RegulatoryValidationStatus.__members__ else 'attention'
        validation = RegulatoryValidation(
            report_id=report.id,
            pillar=RegulatoryPillar(result.pillar),
            status=RegulatoryValidationStatus[status_key],
            messages=result.messages,
            metrics=result.metrics,
        )
        db.session.add(validation)

    attachments_payload = payload.get('attachments') or []
    saved_attachments: List[Tuple[str, bytes]] = []
    for attachment in attachments_payload:
        _, bundle_entry = _persist_attachment(report, attachment)
        if bundle_entry:
            saved_attachments.append(bundle_entry)

    generator = RegulatoryReportGenerator()
    context = generator.build_context(project, report, payload, outcome.results, outcome.metrics)
    html = generator.render_html(context)

    pdf_bytes = generator.generate_pdf_bytes(html)
    zip_bytes = generator.build_zip_bytes(pdf_bytes, saved_attachments)

    pdf_asset = Asset(
        project_id=project.id,
        type=AssetType.pdf,
        path=inline_asset_path('regulatory', 'pdf'),
        mime_type='application/pdf',
        byte_size=len(pdf_bytes),
        data=pdf_bytes,
        meta={'report_id': str(report.id), 'name': f"{report.slug}.pdf"},
    )
    db.session.add(pdf_asset)
    db.session.flush()

    zip_asset = Asset(
        project_id=project.id,
        type=AssetType.other,
        path=inline_asset_path('regulatory', 'zip'),
        mime_type='application/zip',
        byte_size=len(zip_bytes),
        data=zip_bytes,
        meta={'report_id': str(report.id), 'name': 'mosaico_submit.zip'},
    )
    db.session.add(zip_asset)
    db.session.flush()

    report.mark_generated(pdf_asset.path, zip_asset.path)

    if outcome.overall_status == 'blocked':
        report.status = RegulatoryReportStatus.failed
    elif outcome.overall_status == 'attention':
        report.status = RegulatoryReportStatus.validated
    else:
        report.status = RegulatoryReportStatus.generated

    db.session.commit()
    return report
