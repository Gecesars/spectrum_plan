from __future__ import annotations

import io

from flask import jsonify, request, send_file, url_for
from flask_login import login_required, current_user

from extensions import db
from app_core.utils import project_by_slug_or_404
from app_core.models import Asset
from app_core.storage_utils import rehydrate_asset_data

from ..models import RegulatoryReport, RegulatoryReportStatus
from ..service import generate_regulatory_report
from ..anatel_basic import build_basic_form
from . import bp


@bp.route('/reports', methods=['POST'])
@login_required
def create_report():
    payload = request.get_json() or {}
    project_slug = payload.get('project') or payload.get('projectSlug')
    if not project_slug:
        return jsonify({'error': 'Informe o slug do projeto.'}), 400
    project = project_by_slug_or_404(project_slug, current_user.uuid)

    report = generate_regulatory_report(project, payload.get('data') or payload)

    return jsonify(_serialize_report(report)), 201


@bp.route('/reports/<report_id>', methods=['GET'])
@login_required
def show_report(report_id):
    report = RegulatoryReport.query.get_or_404(report_id)
    if report.project.user_uuid != current_user.uuid:
        return jsonify({'error': 'Não autorizado.'}), 403
    return jsonify(_serialize_report(report))


@bp.route('/reports/<report_id>/download/pdf', methods=['GET'])
@login_required
def download_pdf(report_id):
    report = RegulatoryReport.query.get_or_404(report_id)
    if report.project.user_uuid != current_user.uuid:
        return jsonify({'error': 'Não autorizado.'}), 403
    if not report.output_pdf_path:
        return jsonify({'error': 'Relatório ainda não gerado.'}), 404
    blob = _resolve_report_blob(report, report.output_pdf_path)
    if not blob:
        return jsonify({'error': 'Arquivo indisponível.'}), 404
    data, filename = blob
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=filename or f"{report.slug}.pdf",
        mimetype='application/pdf',
    )


@bp.route('/reports/<report_id>/download/bundle', methods=['GET'])
@login_required
def download_bundle(report_id):
    report = RegulatoryReport.query.get_or_404(report_id)
    if report.project.user_uuid != current_user.uuid:
        return jsonify({'error': 'Não autorizado.'}), 403
    if not report.output_zip_path:
        return jsonify({'error': 'Pacote ainda não gerado.'}), 404
    blob = _resolve_report_blob(report, report.output_zip_path)
    if not blob:
        return jsonify({'error': 'Pacote indisponível.'}), 404
    data, filename = blob
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=filename or 'mosaico_submit.zip',
        mimetype='application/zip',
    )


@bp.route('/projects/<slug>/basic-form', methods=['GET'])
@login_required
def anatel_basic_form(slug):
    project = project_by_slug_or_404(slug, current_user.uuid)
    sections = build_basic_form(project)
    return jsonify({'project': project.slug, 'sections': sections})


def _serialize_report(report: RegulatoryReport) -> dict:
    pdf_url = url_for('regulator_api.download_pdf', report_id=report.id)
    bundle_url = url_for('regulator_api.download_bundle', report_id=report.id)
    return {
        'id': str(report.id),
        'project': {'slug': report.project.slug, 'name': report.project.name},
        'name': report.name,
        'status': report.status.value if isinstance(report.status, RegulatoryReportStatus) else report.status,
        'pdf': report.output_pdf_path,
        'bundle': report.output_zip_path,
        'summary': report.validation_summary,
        'links': {
            'pdf': pdf_url,
            'bundle': bundle_url,
        },
    }


def _resolve_report_blob(report: RegulatoryReport, path_value: str | None) -> tuple[bytes, str | None] | None:
    if not path_value:
        return None
    asset = Asset.query.filter_by(project_id=report.project_id, path=path_value).first()
    if asset:
        payload = asset.data or rehydrate_asset_data(asset)
        if payload:
            filename = (asset.meta or {}).get('name') or path_value.rsplit('/', 1)[-1]
            blob = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload)
            return blob, filename
    return None
