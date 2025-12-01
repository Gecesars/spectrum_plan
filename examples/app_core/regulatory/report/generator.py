from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from flask import current_app
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from weasyprint import HTML  # type: ignore
except Exception:  # pragma: no cover
    HTML = None

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from ..engine.coverage import summarize_coverage
from ..anatel_basic import build_basic_form


class RegulatoryReportGenerator:
    def __init__(self) -> None:
        template_path = Path(__file__).parent / 'templates'
        self.env = Environment(
            loader=FileSystemLoader(str(template_path)),
            autoescape=select_autoescape(['html'])
        )

    def build_context(self, project, report, payload, validations, derived_metrics):
        context = {
            'project': project,
            'report': report,
            'station': payload.get('estacao', {}),
            'system': payload.get('sistema_irradiante', {}),
            'decea': derived_metrics.get('decea', {}),
            'rni': derived_metrics.get('rni', {}),
            'erp': derived_metrics.get('servico', {}).get('erp', derived_metrics.get('rni', {})),
            'patterns': payload.get('sistema_irradiante', {}).get('pattern_metrics', {}),
            'sarc_links': payload.get('sarc', []),
            'sarc_budget': derived_metrics.get('sarc', {}),
            'validations': [v.to_dict() for v in validations],
            'coverage': summarize_coverage(payload),
            'generated_at': datetime.utcnow(),
            'anatel_basic_form': build_basic_form(project),
        }
        if not context['erp']:
            context['erp'] = derived_metrics.get('rni', {})
        return context

    def render_html(self, context: Dict[str, Any]) -> str:
        template = self.env.get_template('relatorio_base.html')
        return template.render(**context)

    def _render_with_reportlab(self, html: str, target) -> None:
        c = canvas.Canvas(target, pagesize=A4)
        textobject = c.beginText(40, A4[1] - 40)
        for line in html.splitlines():
            textobject.textLine(line[:110])
        c.drawText(textobject)
        c.showPage()
        c.save()

    def generate_pdf_bytes(self, html: str) -> bytes:
        buffer = io.BytesIO()
        if HTML:
            HTML(string=html, base_url=str(Path(current_app.root_path))).write_pdf(buffer)
        else:  # pragma: no cover
            self._render_with_reportlab(html, buffer)
        buffer.seek(0)
        return buffer.read()

    def build_zip_bytes(self, pdf_bytes: bytes, attachments: Iterable[Tuple[str, bytes]]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('relatorio.pdf', pdf_bytes)
            for name, payload in attachments:
                if not payload or not name:
                    continue
                bundle.writestr(name, payload)
        buffer.seek(0)
        return buffer.read()
