from __future__ import annotations

from flask import jsonify, request, url_for
from flask_login import login_required, current_user
from datetime import datetime
import base64
import binascii

from app_core.utils import project_by_slug_or_404
from extensions import db

from ..service import generate_analysis_report, AnalysisReportError, build_analysis_preview, analyze_ai_inconsistencies
from ..ai import AIUnavailable, AISummaryError
from . import bp


@bp.route('/analysis', methods=['POST'])
@login_required
def analysis_report():
    payload = request.get_json() or {}
    slug = payload.get('project') or payload.get('projectSlug')
    if not slug:
        return jsonify({'error': 'Informe o slug do projeto.'}), 400
    project = project_by_slug_or_404(slug, current_user.uuid)
    try:
        overrides = payload.get('overrides') or {}
        report = generate_analysis_report(project, overrides=overrides)
    except AnalysisReportError as exc:
        return jsonify({'error': str(exc)}), 400
    download_url = None
    if report.pdf_asset:
        download_url = url_for('projects.asset_preview', slug=project.slug, asset_id=report.pdf_asset.id)
    return jsonify({
        'report_id': str(report.id),
        'project': project.slug,
        'title': report.title,
        'download_url': download_url,
    }), 201


@bp.route('/analysis/context', methods=['GET'])
@login_required
def analysis_context():
    slug = request.args.get('project') or request.args.get('projectSlug')
    if not slug:
        return jsonify({'error': 'Informe o slug do projeto.'}), 400
    project = project_by_slug_or_404(slug, current_user.uuid)
    try:
        context = build_analysis_preview(project, allow_ibge=False)
    except AnalysisReportError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify(context), 200


@bp.route('/analysis/validate', methods=['POST'])
@login_required
def analysis_validate():
    payload = request.get_json() or {}
    slug = payload.get('project') or payload.get('projectSlug')
    if not slug:
        return jsonify({'error': 'Informe o slug do projeto.'}), 400
    ai_sections = payload.get('ai_sections') or {}
    if not isinstance(ai_sections, dict) or not ai_sections:
        return jsonify({'error': 'Envie as seções do relatório para validação.'}), 400
    project = project_by_slug_or_404(slug, current_user.uuid)
    try:
        issues = analyze_ai_inconsistencies(project, ai_sections)
    except (AnalysisReportError, AIUnavailable, AISummaryError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'issues': issues}), 200


@bp.route('/coverage_ibge', methods=['GET'])
@login_required
def coverage_ibge_summary():
    slug = request.args.get('project') or request.args.get('projectSlug')
    if not slug:
        return jsonify({'error': 'Informe o slug do projeto.'}), 400
    project = project_by_slug_or_404(slug, current_user.uuid)
    try:
        context = build_analysis_preview(project, allow_ibge=False)
    except AnalysisReportError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify(context.get('coverage_ibge') or {}), 200


@bp.route('/logo', methods=['POST'])
@login_required
def update_report_logo():
    payload = request.get_json(silent=True) or {}
    slug = payload.get('project') or payload.get('projectSlug')
    if not slug:
        return jsonify({'error': 'Informe o slug do projeto.'}), 400
    project = project_by_slug_or_404(slug, current_user.uuid)
    logo_data = payload.get('logo')
    settings = dict(project.settings or {})
    if not logo_data:
        settings.pop('reportLogo', None)
        project.settings = settings
        db.session.commit()
        return jsonify({'company_logo': None}), 200

    if isinstance(logo_data, str) and logo_data.startswith('data:'):
        header, _, encoded = logo_data.partition(',')
        mime_type = header.split(';')[0].split(':')[-1] or 'image/png'
        b64_payload = encoded
    else:
        mime_type = 'image/png'
        b64_payload = logo_data
        logo_data = f"data:{mime_type};base64,{logo_data}"

    try:
        blob = base64.b64decode(b64_payload, validate=True)
    except (binascii.Error, ValueError):
        return jsonify({'error': 'Logo inválido.'}), 400

    if len(blob) > 1_500_000:
        return jsonify({'error': 'Logo deve ter no máximo 1,5 MB.'}), 400

    settings['reportLogo'] = {
        'data': logo_data,
        'mime_type': mime_type,
        'updated_at': datetime.utcnow().isoformat(),
    }
    project.settings = settings
    db.session.commit()
    return jsonify({'company_logo': logo_data}), 200
