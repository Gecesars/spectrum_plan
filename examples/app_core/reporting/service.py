from __future__ import annotations

import base64
import io
import textwrap
from datetime import datetime
import uuid
from typing import Any, Dict, List, Optional
import math
import json

from flask import current_app, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont

from extensions import db
from app_core.models import Asset, AssetType, Project, Report
from app_core.storage import inline_asset_path
from app_core.storage_utils import rehydrate_asset_data
from .ai import build_ai_summary, validate_ai_sections, AIUnavailable, AISummaryError
from app_core.integrations import ibge as ibge_api
from app_core.analytics.coverage_ibge import summarize_coverage_demographics


MIN_RECEIVER_POWER_DBM = -80.0
MIN_FIELD_DBUV = 25.0
MAX_RECEIVER_ROWS = None
MAX_POP_LOOKUPS = 5
DEFAULT_HEADER_COLOR = "#0d47a1"
GAIN_OFFSET_DBI_DBD = 2.15

GLOSSARY_TERMS: list[tuple[str, str]] = [
    (
        "ERP (Effective Radiated Power)",
        "Potência efetivamente irradiada considerando o ganho da antena e as perdas do sistema. "
        "É a referência para comparar projetos e validar níveis de campo.",
    ),
    (
        "HAAT / HMNT (Altura acima do terreno médio)",
        "Diferença entre a altitude do centro de irradiação e a média do terreno entre 3 e 16 km em cada radial. "
        "Indica o quão \"alto\" o sistema está em relação ao entorno.",
    ),
    (
        "Zona de Fresnel",
        "Volume elipsoidal em torno do enlace direto TX↔RX. Obstruções dentro da primeira zona impactam o nível de sinal.",
    ),
    (
        "Perdas L_b (P.452)",
        "Conjunto de perdas calculadas pelo ITU-R P.452 (difração, espalhamento, troposfera) usado para estimar o campo recebido.",
    ),
    (
        "Tilt / Azimute",
        "Tilt define a inclinação vertical do feixe; azimute define a direção horizontal de máxima irradiação.",
    ),
    (
        "Polarização",
        "Orientação do campo elétrico (vertical, horizontal ou circular) que deve ser mantida entre TX e RX para maximizar acoplamento.",
    ),
    (
        "Contorno protegido",
        "Limite geográfico onde o campo atinge o valor regulamentar (ex.: 66 dBµV/m FM). Delimita a área de proteção contra interferências.",
    ),
    (
        "RT3D",
        "Motor tridimensional que utiliza geometria urbana extrudada para estimar penalidades adicionais (sombras, múltiplos caminhos).",
    ),
    (
        "IBGE Demográfico",
        "Estimativas oficiais usadas para inferir o impacto populacional nas áreas cobertas acima do limiar definido.",
    ),
]

CLIMATE_STATE_MAP = {
    "AC": "Equatorial úmido",
    "AM": "Equatorial úmido",
    "AP": "Equatorial úmido",
    "PA": "Equatorial úmido",
    "RO": "Equatorial quente",
    "RR": "Equatorial úmido",
    "TO": "Tropical sazonal",
    "MA": "Tropical úmido",
    "PI": "Semiárido",
    "CE": "Semiárido",
    "RN": "Semiárido",
    "PB": "Semiárido",
    "PE": "Tropical úmido",
    "AL": "Tropical úmido",
    "SE": "Tropical úmido",
    "BA": "Tropical semiárido",
    "MT": "Tropical continental",
    "MS": "Tropical continental",
    "GO": "Tropical sazonal",
    "DF": "Tropical de altitude",
    "MG": "Tropical de altitude",
    "ES": "Tropical litorâneo",
    "RJ": "Tropical úmido",
    "SP": "Tropical de altitude",
    "PR": "Subtropical úmido",
    "SC": "Subtropical úmido",
    "RS": "Subtropical úmido",
}

FM_CLASS_TABLE = [
    {"label": "C", "max_erp_kw": 0.3, "max_haat_m": 60, "contour_km": 7.5},
    {"label": "B2", "max_erp_kw": 1.0, "max_haat_m": 90, "contour_km": 12.5},
    {"label": "B1", "max_erp_kw": 3.0, "max_haat_m": 90, "contour_km": 16.5},
    {"label": "A4", "max_erp_kw": 5.0, "max_haat_m": 150, "contour_km": 24.0},
    {"label": "A3", "max_erp_kw": 15.0, "max_haat_m": 150, "contour_km": 30.0},
    {"label": "A2", "max_erp_kw": 30.0, "max_haat_m": 150, "contour_km": 35.0},
    {"label": "A1", "max_erp_kw": 50.0, "max_haat_m": 150, "contour_km": 38.5},
    {"label": "E3", "max_erp_kw": 60.0, "max_haat_m": 300, "contour_km": 54.5},
    {"label": "E2", "max_erp_kw": 75.0, "max_haat_m": 450, "contour_km": 67.5},
    {"label": "E1", "max_erp_kw": 100.0, "max_haat_m": 600, "contour_km": 78.5},
]

TV_CLASS_TABLE = {
    "vhf": [
        {"label": "C", "max_erp_kw": 0.016, "max_haat_m": 150, "contour_km": 20.2},
        {"label": "B", "max_erp_kw": 0.16, "max_haat_m": 150, "contour_km": 32.3},
        {"label": "A", "max_erp_kw": 1.6, "max_haat_m": 150, "contour_km": 47.9},
        {"label": "Especial", "max_erp_kw": 16.0, "max_haat_m": 150, "contour_km": 65.6},
    ],
    "uhf": [
        {"label": "C", "max_erp_kw": 0.08, "max_haat_m": 150, "contour_km": 18.1},
        {"label": "B", "max_erp_kw": 0.8, "max_haat_m": 150, "contour_km": 29.1},
        {"label": "A", "max_erp_kw": 8.0, "max_haat_m": 150, "contour_km": 42.5},
        {"label": "Especial", "max_erp_kw": 80.0, "max_haat_m": 150, "contour_km": 58.0},
    ],
    "uhf_high": [
        {"label": "C", "max_erp_kw": 0.08, "max_haat_m": 150, "contour_km": 18.1},
        {"label": "B", "max_erp_kw": 0.8, "max_haat_m": 150, "contour_km": 29.1},
        {"label": "A", "max_erp_kw": 8.0, "max_haat_m": 150, "contour_km": 42.5},
        {"label": "Especial", "max_erp_kw": 100.0, "max_haat_m": 150, "contour_km": 58.0},
    ],
}



class AnalysisReportError(RuntimeError):
    pass


def _latest_snapshot(project: Project) -> Dict[str, Any]:
    settings = project.settings or {}
    snapshot = settings.get('lastCoverage')
    if not snapshot:
        raise AnalysisReportError('Projeto não possui mancha de cobertura salva.')
    normalized_snapshot = dict(snapshot)

    def _coerce_uuid(value):
        if isinstance(value, dict):
            value = value.get('id') or value.get('value')
        if value in (None, ''):
            return None
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            return None

    for key in ('asset_id', 'colorbar_asset_id', 'map_snapshot_asset_id', 'json_asset_id'):
        coerced = _coerce_uuid(normalized_snapshot.get(key))
        if coerced:
            normalized_snapshot[key] = coerced
        else:
            normalized_snapshot.pop(key, None)

    return normalized_snapshot


def _format_number(value, unit=""):
    if value in (None, ""):
        return "—"
    if isinstance(value, (int, float)):
        formatted = f"{value:.2f}".rstrip('0').rstrip('.')
    else:
        formatted = str(value)
    return f"{formatted} {unit}".strip()


def _safe_float(value) -> float | None:
    try:
        if value in (None, "", []):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _gain_dbi_to_dbd(value):
    num = _safe_float(value)
    if num is None:
        return None
    return num - GAIN_OFFSET_DBI_DBD


def _decode_inline_image(value) -> bytes | None:
    if not value:
        return None
    if isinstance(value, dict):
        data = value.get('data')
    else:
        data = value
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if not isinstance(data, str):
        return None
    payload = data.split(',', 1)[-1] if 'base64,' in data else data
    try:
        return base64.b64decode(payload)
    except Exception:
        return None


def _project_company_logo(project: Project) -> bytes | None:
    settings = project.settings or {}
    logo_meta = settings.get('reportLogo')
    return _decode_inline_image(logo_meta)


def _infer_state_code(project: Project, snapshot: Dict[str, Any]) -> str | None:
    settings = project.settings or {}
    candidate = settings.get('state') or settings.get('uf')
    if not candidate:
        location_label = settings.get('txLocationName') or snapshot.get('tx_location_name') or settings.get('tx_location_name') or getattr(project.user, 'tx_location_name', None)
        if location_label:
            parts = [part.strip() for part in str(location_label).split(',') if part.strip()]
            if len(parts) >= 2:
                candidate = parts[1]
    if not candidate:
        return None
    return ibge_api.normalize_state_code(candidate)


def _climate_from_latitude(lat: float | None) -> str | None:
    if lat is None:
        return None
    try:
        lat_abs = abs(float(lat))
    except (TypeError, ValueError):
        return None
    if lat_abs <= 5.0:
        return "Clima equatorial"
    if lat_abs <= 18.0:
        return "Clima tropical úmido"
    if lat_abs <= 23.5:
        return "Clima tropical"
    if lat_abs <= 32.0:
        return "Clima tropical com invernos amenos"
    if lat_abs <= 40.0:
        return "Clima subtropical"
    return "Clima temperado"


def _infer_climate_descriptor(project: Project, snapshot: Dict[str, Any]) -> str | None:
    state_code = _infer_state_code(project, snapshot)
    location_name = (project.settings or {}).get('txLocationName') or snapshot.get('tx_location_name') or (project.settings or {}).get('tx_location_name') or getattr(project.user, 'tx_location_name', None)
    climate_label = CLIMATE_STATE_MAP.get(state_code)
    if not climate_label:
        center = snapshot.get('center') or {}
        lat = center.get('lat')
        if lat is None:
            lat = getattr(project.user, 'latitude', None)
        climate_label = _climate_from_latitude(lat)
    if climate_label and location_name:
        return f"{climate_label} — {location_name}"
    return climate_label


def _tv_band_for_frequency(freq_mhz: float | None) -> str:
    if freq_mhz is None:
        return 'uhf'
    try:
        freq = float(freq_mhz)
    except (TypeError, ValueError):
        return 'uhf'
    if freq < 470.0:
        return 'vhf'
    if freq >= 668.0:
        return 'uhf_high'
    return 'uhf'


def _dbm_to_kw(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        mw = 10 ** ((float(value)) / 10.0)
    except (TypeError, ValueError):
        return None
    kw = mw / 1_000_000.0
    return max(kw, 0.0)


def _classify_station(service_label, frequency_mhz, erp_dbm, haat_m):
    erp_kw = _dbm_to_kw(_safe_float(erp_dbm))
    haat_value = _safe_float(haat_m)
    if erp_kw is None or haat_value is None:
        return {
            'label': None,
            'erp_kw': erp_kw,
            'haat_m': haat_value,
        }
    service_key = (service_label or '').lower()
    if 'tv' in service_key or 'rtvd' in service_key or 'gtvd' in service_key:
        band = _tv_band_for_frequency(_safe_float(frequency_mhz))
        table = TV_CLASS_TABLE.get(band, TV_CLASS_TABLE['uhf'])
        category = 'TV Digital'
    else:
        table = FM_CLASS_TABLE
        category = 'FM'
    selected = None
    for entry in table:
        if erp_kw <= entry['max_erp_kw'] + 1e-9 and haat_value <= entry['max_haat_m'] + 1e-6:
            selected = entry
            break
    if not selected:
        return {
            'label': 'Fora do plano básico',
            'category': category,
            'erp_kw': erp_kw,
            'haat_m': haat_value,
            'limits': table[-1],
            'contour_km': table[-1].get('contour_km'),
            'reason': 'ERP/HAAT excedem os limites regulamentares.',
        }
    return {
        'label': selected['label'],
        'category': category,
        'erp_kw': erp_kw,
        'haat_m': haat_value,
        'limits': selected,
        'contour_km': selected.get('contour_km'),
    }


def _synthesize_radials(avg_haat_m, avg_terrain_m=None, bearing_step=15):
    try:
        haat_value = round(float(avg_haat_m), 2)
    except (TypeError, ValueError):
        return []
    radials: list[dict[str, float]] = []
    step = max(1, int(bearing_step))
    for bearing in range(0, 360, step):
        entry: dict[str, float] = {
            'bearing_deg': float(bearing),
            'haat_m': haat_value,
        }
        if avg_terrain_m is not None:
            try:
                entry['avg_terrain_m'] = round(float(avg_terrain_m), 2)
            except (TypeError, ValueError):
                pass
        radials.append(entry)
    return radials


def _estimate_population_impact(
    snapshot: Dict[str, Any],
    allow_remote_lookup: bool = True,
    receivers_preprocessed: list[Dict[str, Any]] | None = None,
    service_type: str | None = None,
) -> tuple[list[Dict[str, Any]], int]:
    """
    Estima o impacto populacional filtrando receptores acima do limiar em dBµV/m.
    Quando `receivers_preprocessed` é fornecido, reutiliza os dados já enriquecidos
    por `_collect_receiver_entries` para evitar inconsistências com projetos antigos.
    """
    processed_receivers = receivers_preprocessed or _collect_receiver_entries(snapshot, limit=None)
    registry = snapshot.get('ibge_registry') or {}

    # Determina limiar padrão baseado no serviço
    default_threshold = 28.0
    if service_type:
        st = service_type.lower()
        if 'tv' in st or 'televis' in st:
            default_threshold = 41.0  # Limiar mais conservador para TV Digital

    try:
        val = snapshot.get('min_field_dbuv_m')
        if val is not None:
            field_threshold_dbuv_m = float(str(val).replace(",", "."))
        else:
            field_threshold_dbuv_m = default_threshold
    except (TypeError, ValueError):
        field_threshold_dbuv_m = default_threshold

    summary: list[Dict[str, Any]] = []
    total = 0
    seen: set[tuple[str | None, str | None]] = set()

    for entry in processed_receivers:
        field_val = entry.get('field_dbuv_m')
        if field_val is None or field_val < field_threshold_dbuv_m:
            continue

        key = (entry.get('municipality'), entry.get('state'))
        if key in seen:
            continue
        seen.add(key)

        demographics = entry.get('demographics')
        code = entry.get('ibge_code')
        if not demographics and code and registry.get(str(code)):
            demographics = registry.get(str(code))

        if not demographics and allow_remote_lookup:
            try:
                demographics = ibge_api.fetch_demographics_by_city(entry.get('municipality'), entry.get('state'))
            except Exception:
                demographics = None

        population_value = entry.get('population')
        if population_value is None:
            population_value = (demographics or {}).get('total')

        population_year = entry.get('population_year')
        if not population_year and isinstance(demographics, dict):
            population_year = demographics.get('period')

        summary_entry = {
            "label": entry.get('label'),
            "municipality": entry.get('municipality'),
            "state": entry.get('state'),
            "distance_km": entry.get('distance_km'),
            "field_dbuv_m": entry.get('field_dbuv_m'),
            "ibge_code": entry.get('ibge_code'),
            "population": population_value,
            "population_year": population_year,
            "demographics": demographics,
        }
        summary.append(summary_entry)
        if isinstance(population_value, (int, float)):
            total += int(population_value)

        if len(summary) >= MAX_POP_LOOKUPS:
            break

    return summary, total



def _wrap_text(c: canvas.Canvas, text: str, x: int, y: int, width_chars: int = 95, line_height: int = 14) -> int:
    for paragraph in text.splitlines():
        if not paragraph.strip():
            y -= line_height
            continue
        for line in textwrap.wrap(paragraph.strip(), width=width_chars):
            c.drawString(x, y, line)
            y -= line_height
    return y


def _draw_text_block(c: canvas.Canvas, x: int, y: int, lines):
    c.setFont('Helvetica', 10)
    line_height = 14
    for label, value in lines:
        c.drawString(x, y, f"{label}: {value}")
        y -= line_height
    return y


def _draw_columns(c: canvas.Canvas, top_y: int, columns: list[tuple[int, list[tuple[str, str]]]]) -> int:
    bottoms = []
    for x, lines in columns:
        bottoms.append(_draw_text_block(c, x, top_y, lines))
    return min(bottoms) if bottoms else top_y


def _embed_binary_image(c: canvas.Canvas, blob: bytes | None, x: int, y: int, max_width: int, max_height: int) -> int:
    if not blob:
        return y
    try:
        reader = ImageReader(io.BytesIO(blob))
        width, height = reader.getSize()
        ratio = min(max_width / width, max_height / height)
        c.drawImage(reader, x, y - height * ratio, width=width * ratio, height=height * ratio)
        return y - height * ratio - 20
    except Exception:
        return y


def _blob_to_data_uri(blob: bytes | None) -> str | None:
    if not blob:
        return None
    try:
        encoded = base64.b64encode(blob).decode('utf-8')
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None


def _draw_header_logo(c: canvas.Canvas, blob: bytes | None, x: float, y: float, max_width: float = 70.0, max_height: float = 60.0):
    if not blob:
        return
    try:
        reader = ImageReader(io.BytesIO(blob))
        width, height = reader.getSize()
        ratio = min(max_width / width, max_height / height)
        c.drawImage(reader, x, y, width=width * ratio, height=height * ratio, mask='auto')
    except Exception:
        return


def _render_receiver_profile_plot(receiver: Dict[str, Any]) -> bytes | None:
    asset_blob = _load_profile_asset(receiver)
    if asset_blob:
        return asset_blob
    profile = receiver.get('profile') or {}
    elevations = profile.get('elevations_m') or []
    if not elevations or len(elevations) < 2:
        return None
    try:
        elevations = [float(value) for value in elevations]
    except (TypeError, ValueError):
        return None
    total_distance_km = profile.get('distance_km')
    if total_distance_km is None:
        total_distance_km = receiver.get('distance_km') or receiver.get('distance')
    try:
        total_distance_km = float(total_distance_km)
    except (TypeError, ValueError):
        total_distance_km = None
    n = len(elevations)
    if not total_distance_km or total_distance_km <= 0.0 or n < 2:
        distances = list(range(n))
        xlabel = 'Amostras'
    else:
        step = total_distance_km / (n - 1)
        distances = [i * step for i in range(n)]
        xlabel = 'Distância (km)'
    meta = receiver.get('profile_meta') or {}
    tx_height = _safe_float(meta.get('tx_height_m')) or 0.0
    rx_height = _safe_float(meta.get('rx_height_m')) or 0.0
    tx_ground = elevations[0]
    rx_ground = elevations[-1]
    tx_total = (_safe_float(meta.get('tx_elevation_m')) or tx_ground) + tx_height
    rx_total = (_safe_float(meta.get('rx_elevation_m')) or rx_ground) + rx_height

    ground_profile = list(elevations)
    los_heights: list[float] = []
    freq_mhz = _safe_float(
        meta.get('frequency_mhz')
        or receiver.get('frequency_mhz')
        or receiver.get('frequency')
    ) or 100.0
    freq_mhz = max(freq_mhz, 0.1)
    wavelength_m = 300.0 / freq_mhz

    k_factor = 4.0 / 3.0
    effective_radius_m = 6_371_000.0 * k_factor
    use_curvature = (
        total_distance_km
        and len(distances) == len(elevations)
        and total_distance_km > 0.0
        and tx_total is not None
        and rx_total is not None
    )
    base_line = []
    if use_curvature:
        adjusted = []
        los_curve = []
        for idx, distance in enumerate(distances):
            d_km = distance if total_distance_km else idx
            d_m = d_km * 1000.0
            drop = (d_m ** 2) / (2 * effective_radius_m)
            adjusted.append(elevations[idx] - drop)
            frac = (d_km / total_distance_km) if total_distance_km else 0.0
            straight_height = tx_total + (rx_total - tx_total) * frac
            los_curve.append(straight_height - drop)
        ground_profile = adjusted
        los_heights = los_curve
        base_line = los_curve[:]
    else:
        if total_distance_km and tx_total is not None and rx_total is not None:
            for distance in distances:
                frac = (distance / total_distance_km) if total_distance_km else 0.0
                base_line.append(tx_total + (rx_total - tx_total) * frac)

    fig, ax = plt.subplots(figsize=(5.4, 3.0), dpi=180)
    ax.set_facecolor('#f8fafc')
    ax.plot(distances, ground_profile, color='#0d47a1', linewidth=1.8, label='Terreno ajustado')
    ax.fill_between(distances, ground_profile, color='#90caf9', alpha=0.3)

    fresnel_top = []
    fresnel_bottom = []
    if base_line and total_distance_km:
        for idx, distance in enumerate(distances):
            d1 = max(distance * 1000.0, 1e-6)
            d2 = max((total_distance_km - distance) * 1000.0, 1e-6)
            radius = math.sqrt((wavelength_m * d1 * d2) / (d1 + d2))
            center_height = base_line[idx] if idx < len(base_line) else base_line[-1]
            fresnel_top.append(center_height + radius)
            fresnel_bottom.append(center_height - radius)
        ax.fill_between(distances, fresnel_bottom, fresnel_top, color='#fde68a', alpha=0.25, label='1ª zona de Fresnel')

    if los_heights:
        ax.plot(distances, los_heights, linestyle='--', color='#f97316', linewidth=1.2, label='Linha de visada')

    ax.scatter([distances[0]], [ground_profile[0]], marker='^', color='#1d4ed8', s=38, label='TX')
    ax.scatter([distances[-1]], [ground_profile[-1]], marker='s', color='#d946ef', s=34, label='RX')
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc='upper right', fontsize=7, framealpha=0.82)

    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel('Elevação (m)', fontsize=8)
    ax.grid(True, linestyle='--', alpha=0.25)
    ax.tick_params(labelsize=7)

    annotation_lines: list[str] = []
    field_value = _safe_float(
        receiver.get('field_dbuv_m')
        or (receiver.get('summary') or {}).get('field_dbuv_m')
        or (receiver.get('summary') or {}).get('fieldValue')
        or receiver.get('field')
    )
    if field_value is not None:
        annotation_lines.append(f"Campo RX: {field_value:.1f} dBµV/m")
    population_value = receiver.get('population') or ((receiver.get('demographics') or {}).get('total'))
    if population_value:
        annotation_lines.append(f"População: {_format_int(population_value)}")
    if annotation_lines:
        ax.text(
            0.99,
            0.05,
            "\n".join(annotation_lines),
            transform=ax.transAxes,
            ha='right',
            va='bottom',
            fontsize=7,
            color='#0f172a',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='white', alpha=0.85, edgecolor='#e2e8f0'),
        )

    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format='png', dpi=150)
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def _load_profile_asset(receiver: Dict[str, Any]) -> bytes | None:
    blob = _load_asset_blob(
        receiver.get('profile_asset_id'),
        receiver.get('profile_asset_path'),
    )
    if blob:
        return blob
    inline = receiver.get('profile_image')
    if inline:
        try:
            base64_data = inline.split(',', 1)[-1]
            return base64.b64decode(base64_data)
        except Exception:
            return None
    return None


def _profile_modal_font(size: int = 16, *, bold: bool = False):
    font_candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in font_candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _font_line_height(font) -> int:
    try:
        bbox = font.getbbox("Ag")
        return int((bbox[3] - bbox[1]) * 1.2)
    except Exception:
        return 18


def _normalize_label(value: str | None) -> str:
    return (value or "").strip().lower()


def _compose_profile_modal_image(receiver: Dict[str, Any], *, user, snapshot: Dict[str, Any], project: Project | None = None) -> bytes | None:
    base_blob = _load_profile_asset(receiver) or _render_receiver_profile_plot(receiver)
    if not base_blob:
        return None
    try:
        chart_image = Image.open(io.BytesIO(base_blob)).convert('RGBA')
    except Exception:
        return base_blob

    width = max(1100, chart_image.width + 200)
    header_height = 220
    chart_max_width = width - 80
    ratio = min(1.0, chart_max_width / float(chart_image.width or 1))
    chart_height = int(chart_image.height * ratio)
    info_lines = receiver.get('profile_info') or _profile_info_from_meta(receiver.get('profile_meta'))
    info_block_height = max(120, 20 + len(info_lines or []) * 20)
    canvas_height = header_height + chart_height + info_block_height + 80

    canvas = Image.new('RGB', (width, canvas_height), (249, 250, 251))
    draw = ImageDraw.Draw(canvas)
    title_font = _profile_modal_font(26, bold=True)
    section_font = _profile_modal_font(16, bold=True)
    text_font = _profile_modal_font(16, bold=False)
    small_font = _profile_modal_font(14)

    def _safe_coord(value):
        try:
            return f"{float(value):.4f}"
        except (TypeError, ValueError):
            return "—"

    tx_location = (project.settings or {}).get('txLocationName') if project else None
    if not tx_location:
         tx_location = snapshot.get('tx_location_name') or getattr(user, 'tx_location_name', None) or '—'

    tx_altitude = snapshot.get('tx_site_elevation') or getattr(user, 'tx_site_elevation', None)
    tower_height = getattr(user, 'tower_height', None)
    tx_direction = getattr(user, 'antenna_direction', None)
    tx_tilt = getattr(user, 'antenna_tilt', None)
    location = receiver.get('location') or {}
    rx_municipality = receiver.get('municipality') or location.get('municipality') or '—'
    rx_state = receiver.get('state') or location.get('state') or '—'
    rx_altitude = receiver.get('altitude_m') or location.get('altitude')
    rx_field = _receiver_field_dbuv(receiver)
    rx_lat = location.get('lat') or receiver.get('lat')
    rx_lng = location.get('lng') or location.get('lon') or receiver.get('lng')
    profile_meta = receiver.get('profile_meta') or {}
    distance_val = profile_meta.get('distance_km') or receiver.get('distance_km') or receiver.get('distance')
    erp_val = profile_meta.get('erp_dbm')
    rx_power_val = profile_meta.get('rx_power_dbm') or _receiver_power_dbm(receiver)
    observations = []
    if profile_meta.get('obstacles'):
        observations.append(str(profile_meta['obstacles']))
    if info_lines:
        observations.extend(info_lines)
    if not observations:
        observations = ["Sem observações adicionais registradas para este enlace."]

    tx_lines = [
        ("Município", tx_location),
        ("Altitude do sítio", _format_number(tx_altitude, 'm')),
        ("Altura da torre", _format_number(tower_height, 'm')),
        ("Azimute/Tilt", f"{_format_number(tx_direction, '°')} / {_format_number(tx_tilt, '°')}"),
    ]
    rx_field_text = f"{rx_field:.1f} dBµV/m" if isinstance(rx_field, (int, float)) else "—"
    rx_lines = [
        ("Município/UF", f"{rx_municipality} / {rx_state}"),
        ("Altitude", _format_number(rx_altitude, 'm')),
        ("Campo estimado", rx_field_text),
        ("Coordenadas", f"{_safe_coord(rx_lat)}, {_safe_coord(rx_lng)}"),
    ]
    link_lines = [
        ("Distância", _format_number(distance_val, 'km')),
        ("ERP na direção", _format_number(erp_val, 'dBm')),
        ("Potência RX", _format_number(rx_power_val, 'dBm')),
        ("Qualidade", receiver.get('quality') or '—'),
    ]

    columns = [
        ("Transmissor", tx_lines),
        ("Receptor", rx_lines),
        ("Link", link_lines),
    ]
    col_width = (width - 80) // len(columns)
    x_cursor = 40
    draw.text((40, 20), "Perfil de enlace", font=title_font, fill=(15, 23, 42))
    for title, lines in columns:
        draw.text((x_cursor, 70), title, font=section_font, fill=(31, 41, 55))
        y_cursor = 100
        for label, value in lines:
            text = f"{label}: {value}"
            wrapped = textwrap.wrap(text, width=max(20, int(col_width / 9)))
            for wrapped_line in wrapped:
                draw.text((x_cursor, y_cursor), wrapped_line, font=text_font, fill=(55, 65, 81))
                y_cursor += _font_line_height(text_font)
        x_cursor += col_width

    chart_position_y = header_height
    try:
        resized_chart = chart_image.resize((int(chart_image.width * ratio), max(1, chart_height)), Image.LANCZOS)
    except Exception:
        resized_chart = chart_image.copy()
    chart_x = (width - resized_chart.width) // 2
    canvas.paste(resized_chart, (chart_x, chart_position_y), resized_chart if resized_chart.mode == 'RGBA' else None)

    obs_y = chart_position_y + resized_chart.height + 20
    draw.text((40, obs_y), "Observações", font=section_font, fill=(31, 41, 55))
    obs_y += 28
    for line in observations:
        wrapped = textwrap.wrap(str(line), width=110)
        for wrapped_line in wrapped:
            draw.text((40, obs_y), wrapped_line, font=small_font, fill=(55, 65, 81))
            obs_y += _font_line_height(small_font)

    output = io.BytesIO()
    canvas.save(output, format='PNG', optimize=True)
    chart_image.close()
    resized_chart.close()
    return output.getvalue()


def _profile_modal_images(
    receivers: list[Dict[str, Any]],
    preferred_payload: list[Dict[str, Any]],
    user,
    snapshot: Dict[str, Any],
    limit: int = 2,
    project: Project | None = None,
) -> Dict[str, bytes]:
    if not receivers or limit <= 0:
        return {}
    lookup: dict[str, Dict[str, Any]] = {}
    for rx in receivers:
        label = rx.get('label') or rx.get('name')
        norm = _normalize_label(label)
        if norm and norm not in lookup:
            lookup[norm] = rx
    ordered: list[tuple[Dict[str, Any], str]] = []
    seen: set[str] = set()
    for payload_entry in preferred_payload or []:
        label = payload_entry.get('label')
        norm = _normalize_label(label)
        if norm and norm in lookup and norm not in seen:
            ordered.append((lookup[norm], label or lookup[norm].get('label')))
            seen.add(norm)
        if len(ordered) >= limit:
            break
    if len(ordered) < limit:
        for rx in receivers:
            norm = _normalize_label(rx.get('label') or rx.get('name'))
            if not norm or norm in seen:
                continue
            ordered.append((rx, rx.get('label') or rx.get('name') or f"RX {len(ordered) + 1}"))
            seen.add(norm)
            if len(ordered) >= limit:
                break
    results: dict[str, bytes] = {}
    for rx, label in ordered:
        blob = _compose_profile_modal_image(rx, user=user, snapshot=snapshot, project=project)
        if blob:
            results[label or f"RX {len(results) + 1}"] = blob
        if len(results) >= limit:
            break
    return results


def _profile_info_from_meta(meta: Dict[str, Any] | None) -> list[str]:
    if not isinstance(meta, dict) or not meta:
        return []
    lines = []
    distance = meta.get('distance_km')
    if distance is not None:
        try:
            lines.append(f"Distância TX→RX: {float(distance):.2f} km")
        except (TypeError, ValueError):
            pass
    erp_dbm = meta.get('erp_dbm')
    if erp_dbm is not None:
        lines.append(f"ERP na direção: {_format_number(erp_dbm, ' dBm')}")
    rx_power = meta.get('rx_power_dbm')
    if rx_power is not None:
        lines.append(f"Potência recebida estimada: {_format_number(rx_power, ' dBm')}")
    field_val = meta.get('field_dbuv_m')
    if field_val is not None:
        lines.append(f"Campo estimado no RX: {_format_number(field_val, ' dBµV/m')}")
    obstacles = meta.get('obstacles')
    if obstacles:
        lines.append(f"Obstáculos na 1ª Fresnel: {obstacles}")
    return lines


def _build_link_summary(receivers: list[Dict[str, Any]]) -> tuple[str, list[Dict[str, Any]]]:
    if not receivers:
        return "Nenhum receptor cadastrado.", []
    lines = []
    payload: list[Dict[str, Any]] = []
    for rx in receivers:
        label = rx.get('label') or rx.get('name') or 'Receptor'
        municipality = rx.get('municipality') or (rx.get('location') or {}).get('municipality') or '—'
        state = rx.get('state') or (rx.get('location') or {}).get('state') or '—'
        distance = rx.get('distance_km') or rx.get('distance')
        power = rx.get('power_dbm') or rx.get('power')
        field = rx.get('field_strength_dbuv_m') or rx.get('field')
        quality = rx.get('quality') or ''
        try:
            distance_text = f"{float(distance):.1f} km" if distance is not None else "—"
        except (TypeError, ValueError):
            distance_text = "—"
        try:
            power_text = f"{float(power):.1f} dBm" if power is not None else "—"
        except (TypeError, ValueError):
            power_text = "—"
        try:
            field_text = f"{float(field):.1f} dBµV/m" if field is not None else "—"
        except (TypeError, ValueError):
            field_text = "—"
        profile = rx.get('profile') or {}
        profile_span = profile.get('distance_km')
        if profile_span:
            try:
                profile_text = f" · perfil {float(profile_span):.1f} km"
            except (TypeError, ValueError):
                profile_text = ""
        else:
            profile_text = ""
        line = (
            f"{label} — {municipality}/{state} · {distance_text} · campo {field_text} · potência {power_text}"
        )
        if quality:
            line += f" · qualidade {quality}"
        line += profile_text
        lines.append(line)
        payload.append({
            'label': label,
            'municipality': municipality,
            'state': state,
            'distance_km': distance,
            'field_dbuv_m': field,
            'power_dbm': power,
            'quality': quality,
            'profile_distance_km': profile_span,
        })
    return "\n".join(f"- {line}" for line in lines), payload


def _horizontal_peak_to_peak_db(user) -> float | None:
    pattern_data = (
        getattr(user, "antenna_pattern_data_h_modified", None)
        or getattr(user, "antenna_pattern_data_h", None)
    )
    if not pattern_data:
        return None
    try:
        entries = json.loads(pattern_data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    values: list[float] = []
    for entry in entries:
        gain = entry.get("gain")
        if gain in (None, ""):
            continue
        try:
            values.append(float(str(gain).replace(",", ".")))
        except (TypeError, ValueError):
            continue
    values = [max(value, 1e-6) for value in values if value is not None]
    if len(values) < 2:
        return None
    peak = max(values)
    trough = min(values)
    if trough <= 0:
        return None
    return 20.0 * math.log10(peak / trough)


def _dominant_category(counts: dict[str, int] | None) -> tuple[str | None, float | None]:
    if not counts:
        return None, None
    best_name = None
    best_value = -1
    total = 0
    for name, value in counts.items():
        if value is None:
            continue
        total += value
        if value > best_value:
            best_name = name
            best_value = value
    if best_name is None or best_value <= 0:
        return None, None
    percent = (best_value / total * 100.0) if total else None
    return best_name, percent


def _load_asset_blob(asset_id: str | None = None, fallback_path: str | None = None) -> bytes | None:
    asset = None
    if asset_id:
        asset = Asset.query.filter_by(id=asset_id).first()
    elif fallback_path:
        if str(fallback_path).startswith('inline://'):
            asset = Asset.query.filter_by(path=fallback_path).first()
        else:
            asset = Asset.query.filter_by(path=fallback_path).first()
    if asset:
        if asset.data:
            return bytes(asset.data)
        payload = rehydrate_asset_data(asset)
        if payload:
            return payload
    return None


def _read_storage_blob(relative_path: str | None) -> bytes | None:
    return _load_asset_blob(fallback_path=relative_path)


def _load_coverage_ibge(snapshot: Dict[str, Any], threshold_dbuv: float = 25.0) -> Optional[Dict[str, Any]]:
    if not snapshot:
        return None
    try:
        return summarize_coverage_demographics(
            summary_payload=snapshot,
            min_field_dbuvm=threshold_dbuv,
        )
    except Exception as exc:  # pragma: no cover - proteção adicional
        current_app.logger.warning('reporting.coverage_ibge_failed', extra={'error': str(exc)})
        return None


def _format_user_climate(user) -> str | None:
    parts = []
    if getattr(user, "temperature_k", None):
        temp_c = float(user.temperature_k) - 273.15
        parts.append(f"Temperatura {temp_c:.1f} °C")
    if getattr(user, "pressure_hpa", None):
        parts.append(f"Pressão {float(user.pressure_hpa):.0f} hPa")
    if getattr(user, "water_density", None):
        parts.append(f"Umidade abs. {float(user.water_density):.1f} g/m³")
    if not parts:
        return None
    text = " / ".join(parts)
    if getattr(user, "climate_updated_at", None):
        text += f" (amostrado em {user.climate_updated_at:%d/%m/%Y})"
    return text
def _build_metrics(project: Project, snapshot: Dict[str, Any], center_metrics: Dict[str, Any]) -> Dict[str, Any]:
    settings = project.settings or {}
    user = project.user
    
    # Helper to get value from settings or user
    def _get_val(keys, user_attr, default=None):
        if isinstance(keys, str):
            keys = [keys]
        for k in keys:
            if k in settings and settings[k] is not None:
                return settings[k]
        return getattr(user, user_attr, default)

    power_w = _safe_float(_get_val(['transmissionPower', 'transmission_power'], 'transmission_power'))
    gain_dbi = _safe_float(_get_val(['antennaGain', 'antenna_gain'], 'antenna_gain'))
    loss_db = _safe_float(_get_val(['Total_loss', 'total_loss'], 'total_loss'))
    freq_mhz = _safe_float(_get_val(['frequency', 'frequencia'], 'frequencia'))
    polarization = _get_val(['polarization'], 'polarization')
    
    tx_power_dbm = None
    if power_w is not None:
        try:
            tx_power_dbm = 10 * math.log10(max(power_w, 1e-6) * 1000.0)
        except (ValueError, OverflowError):
            tx_power_dbm = None
    erp_dbm = None
    if tx_power_dbm is not None:
        erp_dbm = tx_power_dbm + (gain_dbi or 0.0) - (loss_db or 0.0)
    climate_text = _infer_climate_descriptor(project, snapshot) or "Clima não informado"
    haat_average = snapshot.get('haat_average_m')
    if haat_average is None:
        fallback_haat = _safe_float(
            (snapshot.get('tx_parameters') or {}).get('tower_height_m')
            or (settings.get('towerHeight') or settings.get('tower_height'))
            or getattr(user, 'tower_height', None)
        )
        haat_average = fallback_haat
    return {
        "service": settings.get("serviceType") or getattr(user, "servico", "Radiodifusão"),
        "service_class": settings.get("serviceClass") or settings.get("classe") or "—",
        "location": settings.get("txLocationName") or settings.get("tx_location_name") or snapshot.get("tx_location_name") or getattr(user, "tx_location_name", "—"),
        "erp_dbm": erp_dbm,
        "radius_km": snapshot.get("radius_km") or snapshot.get("requested_radius_km"),
        "frequency_mhz": freq_mhz,
        "polarization": polarization,
        "horizontal_peak_to_peak_db": _horizontal_peak_to_peak_db(user),
        "climate": climate_text,
        "tx_power_w": power_w,
        "antenna_gain_dbi": gain_dbi,
        "antenna_gain_dbd": _gain_dbi_to_dbd(gain_dbi),
        "losses_db": loss_db,
        "haat_average_m": _safe_float(haat_average),
    }


def _receiver_power_dbm(receiver: Dict[str, Any]) -> float | None:
    for key in ("power_dbm", "received_power_dbm", "power"):
        value = receiver.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _receiver_field_dbuv(receiver: Dict[str, Any]) -> float | None:
    for key in ("field_strength_dbuv_m", "field_dbuv", "field", "field_dbuv_m"):
        value = receiver.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _clean_city_label(value) -> str | None:
    if not value:
        return None
    parts = [segment.strip() for segment in str(value).split(",")]
    for part in parts:
        if part:
            return part
    return None


def _receiver_location_hints(municipality_label, state_label, location) -> tuple[str | None, str | None]:
    location = location or {}
    city = None
    for candidate in (
        location.get("municipality"),
        location.get("city"),
        location.get("name"),
        municipality_label,
    ):
        city = _clean_city_label(candidate)
        if city:
            break

    state = None
    for candidate in (
        location.get("state_code"),
        location.get("state"),
        state_label,
    ):
        if candidate:
            state = str(candidate).strip()
            if state:
                break
    normalized_state = ibge_api.normalize_state_code(state) if state else None
    return city, normalized_state or state


def _collect_receiver_entries(snapshot: Dict[str, Any], limit: int | None = MAX_RECEIVER_ROWS) -> list[Dict[str, Any]]:
    receivers = snapshot.get('receivers') or []
    entries: list[Dict[str, Any]] = []
    for rx in receivers:
        power = _receiver_power_dbm(rx)
        field = _receiver_field_dbuv(rx)
        location = rx.get('location') or {}
        municipality = rx.get('municipality') or location.get('municipality') or location.get('city')
        state = rx.get('state') or location.get('state') or location.get('uf')
        distance = rx.get('distance_km') or rx.get('distance')
        try:
            distance = float(distance) if distance is not None else None
        except (TypeError, ValueError):
            distance = None
        ibge_info = rx.get('ibge') or {}
        demographics = ibge_info.get('demographics')
        if isinstance(demographics, dict) and demographics.get('total') is None:
            demographics = None
        profile_meta = rx.get('profile_meta') or {}
        profile_info = rx.get('profile_info') or rx.get('profile_info_lines')
        city_hint, state_hint = _receiver_location_hints(municipality, state, location)
        resolved_ibge_code = ibge_info.get('code') or ibge_info.get('ibge_code')
        if city_hint:
            target_code = ibge_api.resolve_municipality_code(city_hint, state_hint)
        else:
            target_code = resolved_ibge_code
        target_code = str(target_code) if target_code else None

        refreshed_demographics = demographics
        if target_code and (not demographics or str(demographics.get('code')) != target_code):
            fetched = ibge_api.fetch_demographics_by_code(target_code)
            if fetched:
                refreshed_demographics = fetched
                resolved_ibge_code = target_code

        if (not refreshed_demographics) and city_hint:
            fetched = ibge_api.fetch_demographics_by_city(city_hint, state_hint)
            if fetched:
                refreshed_demographics = fetched
                resolved_ibge_code = fetched.get('code') or resolved_ibge_code

        population_value = (refreshed_demographics or {}).get('total')
        population_year = None
        if isinstance(refreshed_demographics, dict) and refreshed_demographics.get('period'):
            population_year = refreshed_demographics['period']
        elif snapshot.get('population_year'):
            population_year = snapshot.get('population_year')

        entries.append({
            "label": rx.get('label') or rx.get('name') or f"RX {len(entries) + 1}",
            "municipality": municipality,
            "state": state,
            "power_dbm": power,
            "distance_km": distance,
            "field_dbuv_m": field,
            "altitude_m": rx.get('altitude_m') or location.get('altitude'),
            "quality": rx.get('quality') or rx.get('status'),
            "meets_field_min": field is not None and field >= MIN_FIELD_DBUV,
            "demographics": refreshed_demographics,
            "ibge_code": resolved_ibge_code,
            "population": population_value,
            "population_year": population_year,
            "erp_dbm": profile_meta.get('erp_dbm'),
            "profile": rx.get('profile') or {},
            "profile_meta": profile_meta,
            "profile_info": profile_info,
            "profile_asset_path": rx.get('profile_asset_path'),
            "profile_asset_id": rx.get('profile_asset_id'),
            "profile_asset_url": rx.get('profile_asset_url'),
        })
    entries.sort(
        key=lambda item: item['power_dbm'] if item.get('power_dbm') is not None else MIN_RECEIVER_POWER_DBM,
        reverse=True,
    )
    if limit is None:
        return entries
    return entries[:limit]


def _start_page(
    c: canvas.Canvas,
    width: float,
    height: float,
    title: str,
    subtitle: str,
    theme_color: str = DEFAULT_HEADER_COLOR,
    *,
    company_logo: bytes | None = None,
) -> int:
    try:
        color_value = colors.HexColor(theme_color)
    except Exception:
        color_value = colors.HexColor(DEFAULT_HEADER_COLOR)
    c.setFillColor(color_value)
    c.rect(0, height - 80, width, 80, fill=1, stroke=0)
    c.setFillColor(colors.white)
    if company_logo:
        _draw_header_logo(c, company_logo, 34, height - 75)
        text_x = 120
    else:
        text_x = 40
    c.setFont('Helvetica-Bold', 20)
    c.drawString(text_x, height - 45, title)
    c.setFont('Helvetica', 11)
    c.drawString(text_x, height - 65, subtitle)
    c.setFillColor(colors.black)
    return height - 110


def _ensure_space(
    c: canvas.Canvas,
    y: float,
    required: float,
    width: float,
    height: float,
    title: str,
    subtitle: str,
    theme_color: str = DEFAULT_HEADER_COLOR,
    company_logo: bytes | None = None,
) -> float:
    if y - required < 70:
        c.showPage()
        return _start_page(
            c,
            width,
            height,
            title,
            subtitle,
            theme_color,
            company_logo=company_logo,
        )
    return y


def _draw_table(
    c: canvas.Canvas,
    y: float,
    columns: list[tuple[str, int]],
    rows: list[list[str]],
    width: float,
    height: float,
    project_slug: str,
    continuation_title: str,
    empty_message: str | None = None,
    line_height: int = 14,
    theme_color: str = DEFAULT_HEADER_COLOR,
    company_logo: bytes | None = None,
) -> float:
    if not rows:
        if empty_message:
            c.setFont('Helvetica', 10)
            c.drawString(40, y, empty_message)
            return y - line_height
        return y

    def _draw_header(current_y: float) -> float:
        c.setFont('Helvetica-Bold', 9)
        x = 40
        for label, col_width in columns:
            c.drawString(x, current_y, label)
            x += col_width
        return current_y - line_height

    y = _draw_header(y)
    c.setFont('Helvetica', 9)
    for row in rows:
        if y < 80:
            c.showPage()
            y = _start_page(
                c,
                width,
                height,
                continuation_title,
                project_slug,
                theme_color,
                company_logo=company_logo,
            ) - 20
            y = _draw_header(y)
            c.setFont('Helvetica', 9)
        x = 40
        for text, (_, col_width) in zip(row, columns):
            c.drawString(x, y, text)
            x += col_width
        y -= line_height
    return y



def build_analysis_preview(project: Project, *, allow_ibge: bool = True) -> Dict[str, Any]:
    snapshot = _latest_snapshot(project)
    user = project.user
    settings = project.settings or {}
    # sincroniza centro/nome/elevação da TX com o valor mais recente salvo no projeto
    tx_lat = settings.get('latitude')
    tx_lng = settings.get('longitude')
    tx_name = settings.get('txLocationName') or settings.get('tx_location_name')
    tx_elev = settings.get('txElevation') or settings.get('tx_site_elevation')
    if tx_lat is not None and tx_lng is not None:
        center = snapshot.get('center') or snapshot.get('tx_location') or {}
        center = dict(center)
        center['lat'] = tx_lat
        center['lng'] = tx_lng
        snapshot['center'] = center
        snapshot['tx_location'] = center
    if tx_name:
        snapshot['tx_location_name'] = tx_name
    if tx_elev is not None:
        snapshot['tx_site_elevation'] = tx_elev
    company_logo_blob = _project_company_logo(project)
    center_metrics = snapshot.get('center_metrics') or {}
    loss_components = snapshot.get('loss_components') or {}
    gain_components = snapshot.get('gain_components') or {}
    metrics = _build_metrics(project, snapshot, center_metrics)
    project_notes = (
        (settings.get('projectNotes') or settings.get('project_notes') or settings.get('notes'))
        or project.description
        or getattr(user, 'notes', None)
    )
    metrics['project_notes'] = project_notes or "Sem notas adicionais registradas."

    receivers_full = snapshot.get('receivers') or []
    link_summary_text, link_payload = _build_link_summary(receivers_full)
    coverage_ibge = _load_coverage_ibge(snapshot) if allow_ibge else None
    metrics['link_summary'] = link_summary_text
    metrics['coverage_ibge'] = coverage_ibge

    profile_modal_images = _profile_modal_images(receivers_full, link_payload[:2], user, snapshot, limit=2, project=project)
    fallback_profile_blob = getattr(user, "perfil_img", None)
    if not fallback_profile_blob and receivers_full:
        fallback_profile_blob = _render_receiver_profile_plot(receivers_full[0])
    primary_profile_blob = next(iter(profile_modal_images.values()), None) or fallback_profile_blob
    saved_horizontal = _decode_inline_image(snapshot.get('diagram_horizontal_b64'))
    saved_vertical = _decode_inline_image(snapshot.get('diagram_vertical_b64'))
    coverage_overlay_blob = _read_storage_blob(snapshot.get('asset_path'))
    coverage_snapshot_blob = _read_storage_blob(snapshot.get('map_snapshot_path'))
    diagram_images = {
        "mancha_de_cobertura": coverage_overlay_blob or coverage_snapshot_blob,
        "perfil": primary_profile_blob,
        "diagrama_horizontal": saved_horizontal or getattr(user, "antenna_pattern_img_dia_H", None),
        "diagrama_vertical": saved_vertical or getattr(user, "antenna_pattern_img_dia_V", None),
    }

    limited_payload = link_payload[:2]
    summary_lines = link_summary_text.splitlines()
    limited_summary_text = "\n".join(summary_lines[:len(limited_payload)]) if limited_payload else "Nenhum receptor selecionado."
    ai_metrics = dict(metrics)
    ai_metrics['link_summary'] = limited_summary_text
    ai_images = dict(diagram_images)
    for modal_label, modal_blob in profile_modal_images.items():
        ai_images[f"perfil_modal::{modal_label}"] = modal_blob
    try:
        ai_sections = build_ai_summary(project, snapshot, ai_metrics, ai_images, links_payload=limited_payload)
    except (AIUnavailable, AISummaryError) as exc:
        raise AnalysisReportError(str(exc)) from exc

    receiver_entries = _collect_receiver_entries(snapshot, limit=None)
    population_details, population_total = _estimate_population_impact(
        snapshot,
        allow_remote_lookup=allow_ibge,
        receivers_preprocessed=receiver_entries,
    )
    haat_radials = snapshot.get('haat_radials') or []
    if not haat_radials:
        terrain_hint = snapshot.get('tx_site_elevation') or getattr(user, 'tx_site_elevation', None)
        synthetic_radials = _synthesize_radials(metrics.get('haat_average_m'), terrain_hint)
        if synthetic_radials:
            haat_radials = synthetic_radials
    classification = _classify_station(
        metrics.get('service'),
        metrics.get('frequency_mhz'),
        metrics.get('erp_dbm'),
        metrics.get('haat_average_m'),
    )
    if classification.get('label'):
        metrics['service_class'] = classification['label']
    metrics['classification'] = classification
    metrics['contour_distance_km'] = classification.get('contour_km')
    metrics['contour_distance_km'] = classification.get('contour_km')
    metrics['haat_radials'] = haat_radials

    heatmap_url = None
    colorbar_url = None
    map_snapshot_url = None
    if snapshot.get('asset_id'):
        asset = Asset.query.filter_by(id=snapshot.get('asset_id')).first()
        if asset:
            heatmap_url = url_for('projects.asset_preview', slug=project.slug, asset_id=asset.id)
    if snapshot.get('colorbar_asset_id'):
        asset = Asset.query.filter_by(id=snapshot.get('colorbar_asset_id')).first()
        if asset:
            colorbar_url = url_for('projects.asset_preview', slug=project.slug, asset_id=asset.id)
    if snapshot.get('map_snapshot_asset_id'):
        asset = Asset.query.filter_by(id=snapshot.get('map_snapshot_asset_id')).first()
        if asset:
            map_snapshot_url = url_for('projects.asset_preview', slug=project.slug, asset_id=asset.id)

    return {
        'project': {
            'slug': project.slug,
            'name': project.name,
        },
        'coverage': {
            'engine': snapshot.get('engine'),
            'generated_at': snapshot.get('generated_at'),
            'heatmap_url': heatmap_url,
            'colorbar_url': colorbar_url,
            'map_snapshot_url': map_snapshot_url,
        },
        'metrics': metrics,
        'ai_sections': ai_sections,
        'receivers': receivers_full,
        'receiver_summary': receiver_entries,
        'population': {
            'summary': population_details,
            'total': population_total,
        },
        'coverage_ibge': coverage_ibge,
        'diagram_images': {
            'perfil': _blob_to_data_uri(diagram_images.get('perfil')),
            'diagrama_horizontal': _blob_to_data_uri(diagram_images.get('diagrama_horizontal')),
            'diagrama_vertical': _blob_to_data_uri(diagram_images.get('diagrama_vertical')),
            'company_logo': _blob_to_data_uri(company_logo_blob),
        },
        'header_color': DEFAULT_HEADER_COLOR,
        'notes': metrics['project_notes'],
        'ibge_registry': snapshot.get('ibge_registry'),
        'link_summary': link_summary_text,
        'link_payload': link_payload,
        'branding': {
            'company_logo': _blob_to_data_uri(company_logo_blob),
        },
        'regulatory': {
            'classification': classification,
            'haat_radials': haat_radials,
        },
        'haat': {
            'average_m': metrics.get('haat_average_m'),
            'radials': haat_radials,
        },
    }

def _format_int(value) -> str:
    """
    Formata inteiros com separador de milhar como ponto (1.234.567).
    Aceita value numérico ou string (inclui '1.234,56'); arredonda quando for float.
    Retorna '—' se não for possível converter.
    """
    if value in (None, ""):
        return "—"
    try:
        # normaliza vírgula decimal para ponto e remove espaços
        s = str(value).strip().replace(",", ".")
        n = float(s)
        i = int(round(n))
        return f"{i:,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _format_currency(value) -> str:
    if value in (None, ""):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"






def generate_analysis_report(
    project: Project,
    overrides: Dict[str, Any] | None = None,
    *,
    allow_ibge: bool = False,
) -> Report:
    overrides = overrides or {}
    snapshot = _latest_snapshot(project)
    user = project.user
    settings = project.settings or {}
    center_metrics = snapshot.get('center_metrics') or {}
    loss_components = snapshot.get('loss_components') or {}
    gain_components = snapshot.get('gain_components') or {}
    metrics = _build_metrics(project, snapshot, center_metrics)
    project_notes = (
        (settings.get('projectNotes') or settings.get('project_notes') or settings.get('notes'))
        or project.description
        or getattr(user, 'notes', None)
    )
    if overrides.get('project_notes'):
        project_notes = overrides['project_notes']
    metrics['project_notes'] = project_notes or "Sem notas adicionais registradas."

    receivers_full = snapshot.get('receivers') or []
    link_summary_text, link_payload = _build_link_summary(receivers_full)
    metrics['link_summary'] = link_summary_text

    overlay_rel_path = snapshot.get('asset_path')
    snapshot_rel_path = snapshot.get('map_snapshot_path')
    coverage_overlay_blob = _read_storage_blob(overlay_rel_path)
    coverage_snapshot_blob = _read_storage_blob(snapshot_rel_path)

    profile_modal_images = _profile_modal_images(receivers_full, link_payload[:2], user, snapshot, limit=2, project=project)
    fallback_profile_blob = getattr(user, "perfil_img", None)
    if not fallback_profile_blob and receivers_full:
        fallback_profile_blob = _render_receiver_profile_plot(receivers_full[0])
    primary_profile_blob = next(iter(profile_modal_images.values()), None) or fallback_profile_blob
    saved_horizontal = _decode_inline_image(snapshot.get('diagram_horizontal_b64'))
    saved_vertical = _decode_inline_image(snapshot.get('diagram_vertical_b64'))
    diagram_images = {
        "mancha_de_cobertura": coverage_overlay_blob or coverage_snapshot_blob,
        "perfil": primary_profile_blob,
        "diagrama_horizontal": saved_horizontal or getattr(user, "antenna_pattern_img_dia_H", None),
        "diagrama_vertical": saved_vertical or getattr(user, "antenna_pattern_img_dia_V", None),
    }
    haat_radials = snapshot.get('haat_radials') or []
    if not haat_radials:
        terrain_hint = snapshot.get('tx_site_elevation') or getattr(user, 'tx_site_elevation', None)
        synthetic_radials = _synthesize_radials(metrics.get('haat_average_m'), terrain_hint)
        if synthetic_radials:
            haat_radials = synthetic_radials
    classification = _classify_station(
        metrics.get('service'),
        metrics.get('frequency_mhz'),
        metrics.get('erp_dbm'),
        metrics.get('haat_average_m'),
    )
    if classification.get('label'):
        metrics['service_class'] = classification['label']
    metrics['classification'] = classification
    metrics['haat_radials'] = haat_radials

    provided_sections = overrides.get('ai_sections')
    if provided_sections:
        ai_sections = dict(provided_sections)
    else:
        limited_payload = link_payload[:2]
        summary_lines = link_summary_text.splitlines()
        limited_summary_text = "\n".join(summary_lines[:len(limited_payload)]) if limited_payload else "Nenhum receptor selecionado."
        ai_metrics = dict(metrics)
        ai_metrics['link_summary'] = limited_summary_text
        ai_images = dict(diagram_images)
        for modal_label, modal_blob in profile_modal_images.items():
            ai_images[f"perfil_modal::{modal_label}"] = modal_blob
        try:
            ai_sections = build_ai_summary(project, snapshot, ai_metrics, ai_images, links_payload=limited_payload)
        except (AIUnavailable, AISummaryError) as exc:
            raise AnalysisReportError(str(exc)) from exc

    if isinstance(ai_sections.get('recommendations'), str):
        ai_sections['recommendations'] = [
            item.strip()
            for item in ai_sections['recommendations'].splitlines()
            if item.strip()
        ]

    required_ai_fields = ["overview", "coverage", "profile", "pattern_horizontal", "pattern_vertical", "recommendations", "conclusion", "link_analyses"]
    for field in required_ai_fields:
        if field in ("recommendations", "link_analyses"):
            ai_sections.setdefault(field, [])
        else:
            ai_sections.setdefault(field, "")

    receiver_entries = _collect_receiver_entries(snapshot, limit=None)
    population_details, population_total = _estimate_population_impact(
        snapshot,
        allow_remote_lookup=allow_ibge,
        receivers_preprocessed=receiver_entries,
    )
    population_lookup: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for entry in receiver_entries:
        key = (entry.get('municipality'), entry.get('state'))
        demographics = entry.get('demographics')
        if key not in population_lookup and demographics:
            population_lookup[key] = demographics
    for row in population_details:
        key = (row.get('municipality'), row.get('state'))
        demographics = row.get('demographics')
        if demographics and key not in population_lookup:
            population_lookup[key] = demographics
    coverage_ibge = _load_coverage_ibge(snapshot) if allow_ibge else None

    header_color = overrides.get('header_color') or DEFAULT_HEADER_COLOR
    company_logo_blob = _project_company_logo(project)

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f"analysis_{project.slug}_{timestamp}.pdf"

    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4

    y = _start_page(
        c,
        width,
        height,
        f"Relatório Técnico — {project.name}",
        f"Gerado em {datetime.utcnow():%d/%m/%Y %H:%M UTC}",
        header_color,
        company_logo=company_logo_blob,
    )

    primary_rx = receiver_entries[0] if receiver_entries else None
    primary_rx_loss = None
    primary_rx_label = None
    if primary_rx:
        try:
            erp_value = float(metrics.get("erp_dbm")) if metrics.get("erp_dbm") is not None else None
            rx_power = float(primary_rx.get('power_dbm')) if primary_rx.get('power_dbm') is not None else None
            if erp_value is not None and rx_power is not None:
                primary_rx_loss = erp_value - rx_power
                primary_rx_label = primary_rx.get('label') or 'RX'
        except (TypeError, ValueError):
            primary_rx_loss = None

    left_column = [
        ('Projeto', project.name),
        ('Slug', project.slug),
        ('Serviço / Classe', f"{metrics.get('service')} / {metrics.get('service_class')}"),
        ('Engine', snapshot.get('engine', '—')),
        ('Localização', metrics.get("location") or '—'),
        ('Raio planejado', _format_number(metrics.get("radius_km"), 'km')),
        ('Clima', metrics.get("climate")),
        ('HAAT médio (3-16 km)', _format_number(metrics.get('haat_average_m'), 'm')),
        ('Radiais analisadas', str(len(haat_radials)) if haat_radials else '—'),
        ('Contorno protegido', _format_number(metrics.get('contour_distance_km'), 'km')),
    ]
    right_column = [
        ('Potência TX', _format_number(metrics.get('tx_power_w'), 'W')),
        ('Ganho TX (dBd)', _format_number(metrics.get('antenna_gain_dbd'), 'dBd')),
        ('Perdas Sistêmicas', _format_number(metrics.get('losses_db'), 'dB')),
        ('Polarização', metrics.get('polarization') or '—'),
        ('Perda combinada', _format_number(center_metrics.get('combined_loss_center_db'), 'dB')),
        ('Ganho efetivo', _format_number(center_metrics.get('effective_gain_center_db'), 'dB')),
        ('L_b (centro)', _format_number((loss_components.get('L_b') or {}).get('center'), 'dB')),
        ('Ajuste horizontal', _format_number(gain_components.get('horizontal_adjustment_db_min'), 'dB')),
        ('Ajuste vertical', _format_number(gain_components.get('vertical_adjustment_db'), 'dB')),
        ('Pico a pico (H)', _format_number(metrics.get('horizontal_peak_to_peak_db'), 'dB')),
    ]
    if primary_rx_label and primary_rx_loss is not None:
        right_column.append((f'Atenuação até {primary_rx_label}', _format_number(primary_rx_loss, 'dB')))
    class_limits = classification.get('limits') or {}
    if class_limits:
        limit_text = f"{_format_number(class_limits.get('max_erp_kw'), 'kW')} / {_format_number(class_limits.get('max_haat_m'), 'm')}"
        right_column.append(('Limite ERP/HAAT da classe', limit_text))
    y = _draw_columns(c, y, [
        (40, left_column),
        (320, right_column),
    ]) - 18

    classification_text = None
    if classification.get('label'):
        erp_text = _format_number(classification.get('erp_kw'), 'kW')
        haat_text = _format_number(classification.get('haat_m'), 'm')
        contour_text = _format_number(classification.get('contour_km'), 'km')
        pieces = [
            f"Classe {classification['label']} ({classification.get('category') or 'FM/TV'})",
            f"ERP analisada {erp_text}",
            f"HAAT efetivo {haat_text}",
        ]
        if classification.get('contour_km') is not None:
            pieces.append(f"Contorno protegido {contour_text}")
        classification_text = ' · '.join(filter(None, pieces))
    elif classification.get('reason'):
        classification_text = classification['reason']
    if classification_text:
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Classificação regulamentar")
        y -= 16
        c.setFont('Helvetica', 10)
        y = _wrap_text(c, classification_text, 40, y, width_chars=95)
        y -= 6

    notes_text = metrics.get("project_notes")
    if notes_text:
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Notas do projeto")
        y = _wrap_text(c, notes_text, 40, y - 18, width_chars=95)
        y -= 6

    c.setFont('Helvetica-Bold', 11)
    c.drawString(40, y, "Resumo executivo")
    overview_text = ai_sections.get("overview") or "Resumo indisponível."
    y = _wrap_text(c, overview_text, 40, y - 18, width_chars=95)

    y = _ensure_space(
        c,
        y,
        360,
        width,
        height,
        f"Relatório Técnico — {project.name}",
        f"Gerado em {datetime.utcnow():%d/%m/%Y %H:%M UTC}",
        header_color,
        company_logo=company_logo_blob,
    )
    c.setFont('Helvetica-Bold', 11)
    c.drawString(40, y, "Mancha de cobertura")
    map_y = y - 20
    max_map_width = int(width - 80)
    colorbar_blob = _load_asset_blob(snapshot.get('colorbar_asset_id'), snapshot.get('colorbar_asset_path'))
    if colorbar_blob:
        map_y = _embed_binary_image(
            c,
            colorbar_blob,
            40,
            map_y,
            max_width=max_map_width,
            max_height=50,
        )
    if coverage_snapshot_blob:
        map_y = _embed_binary_image(
            c,
            coverage_snapshot_blob,
            40,
            map_y - 6,
            max_width=max_map_width,
            max_height=360,
        )
    elif coverage_overlay_blob:
        map_y = _embed_binary_image(
            c,
            coverage_overlay_blob,
            40,
            map_y - 6,
            max_width=max_map_width,
            max_height=360,
        )
    else:
        c.setFont('Helvetica-Oblique', 9)
        c.drawString(40, map_y, 'Prévia da cobertura não localizada.')
        map_y -= 18
    c.setFont('Helvetica', 10)
    coverage_text = ai_sections.get("coverage") or "Observações de cobertura indisponíveis."
    map_y = _wrap_text(c, coverage_text, 40, map_y - 6, width_chars=95)
    y = map_y - 10

    c.showPage()

    y = _start_page(
        c,
        width,
        height,
        "Sistema irradiante",
        project.slug,
        header_color,
        company_logo=company_logo_blob,
    )

    antenna_block = [
        ('Modelo', settings.get('antennaModel') or settings.get('antenna_model') or '—'),
        ('Altura da torre', _format_number(getattr(user, 'tower_height', None), 'm')),
        ('Azimute/Tilt', f"{_format_number(getattr(user, 'antenna_direction', None), '°')} / {_format_number(getattr(user, 'antenna_tilt', None), '°')}"),
        ('Polarização', getattr(user, 'polarization', '—')),
    ]
    y = _draw_text_block(c, 40, y, antenna_block)

    antenna_metrics_block = [
        ('ERP estimada', _format_number(metrics.get("erp_dbm"), 'dBm')),
        ('Frequência', _format_number(metrics.get("frequency_mhz"), 'MHz')),
        ('Polarização de projeto', metrics.get("polarization") or '—'),
    ]
    y = _draw_text_block(c, 40, y - 4, antenna_metrics_block)

    diagram_sections = [
        ("Diagrama Horizontal", diagram_images.get("diagrama_horizontal"), ai_sections.get("pattern_horizontal")),
        ("Diagrama Vertical", diagram_images.get("diagrama_vertical"), ai_sections.get("pattern_vertical")),
    ]
    for title, blob, note in diagram_sections:
        if not blob:
            continue
        y = _ensure_space(
            c,
            y,
            260,
            width,
            height,
            "Sistema irradiante (cont.)",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, title)
        y = _embed_binary_image(c, blob, 40, y - 6, max_width=int(width - 120), max_height=240)
        explanation = note or "Análise não disponível para este diagrama."
        c.setFont('Helvetica', 10)
        y = _wrap_text(c, explanation, 40, y, width_chars=95, line_height=13)
        y -= 12

    if haat_radials:
        y = _ensure_space(
            c,
            y,
            200,
            width,
            height,
            "Sistema irradiante (cont.)",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Altura média por radial (HMNT 3–16 km)")
        y -= 18
        haat_columns = [
            ("Azimute", 80),
            ("HMNT", 80),
            ("Terreno médio", 110),
        ]
        haat_rows = [
            [
                f"{int(item.get('bearing_deg', 0))}°",
                _format_number(item.get('hmnt_m') or item.get('haat_m'), 'm'),
                _format_number(item.get('avg_terrain_m'), 'm'),
            ]
            for item in haat_radials
        ]
        y = _draw_table(
            c,
            y,
            haat_columns,
            haat_rows,
            width,
            height,
            project.slug,
            "Sistema irradiante (cont.)",
            theme_color=header_color,
            company_logo=company_logo_blob,
        )
        y -= 6

    c.showPage()

    y = _start_page(
        c,
        width,
        height,
        "Enlaces e impacto populacional",
        project.slug,
        header_color,
        company_logo=company_logo_blob,
    )

    c.setFont('Helvetica-Bold', 11)
    c.drawString(40, y, "Perfil do enlace principal")
    y = _embed_binary_image(c, primary_profile_blob, 40, y - 6, max_width=int(width - 70), max_height=280)
    profile_text = ai_sections.get("profile") or "Sem observações adicionais registradas para o perfil."
    c.setFont('Helvetica', 10)
    y = _wrap_text(c, profile_text, 40, y, width_chars=95)
    y -= 12

    c.setFont('Helvetica-Bold', 11)
    c.drawString(40, y, f"Receptores avaliados (≥ {int(MIN_RECEIVER_POWER_DBM)} dBm)")
    y -= 18

    for idx, entry in enumerate(receiver_entries, 1):
        y = _ensure_space(
            c,
            y,
            130,
            width,
            height,
            "Receptores avaliados (cont.)",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        label = entry.get('label') or f"Receptor {idx}"
        c.setFont('Helvetica-Bold', 10)
        c.drawString(40, y, label)
        y -= 12
        municipality = entry.get('municipality') or '—'
        state = entry.get('state') or '—'
        distance_text = _format_number(entry.get('distance_km'), 'km')
        field_text = _format_number(entry.get('field_dbuv_m'), 'dBµV/m')
        erp_text = _format_number(entry.get('erp_dbm'), 'dBm')
        altitude_text = _format_number(entry.get('altitude_m'), 'm')
        quality_text = entry.get('quality') or '—'
        compliance = "Atende (>=25 dBµV/m)" if entry.get('meets_field_min') else "Abaixo de 25 dBµV/m"
        key = (entry.get('municipality'), entry.get('state'))
        demo = entry.get('demographics') or population_lookup.get(key) or {}
        population_value = entry.get('population') or (demo or {}).get('total')
        population_year = entry.get('population_year') or (demo or {}).get('period')
        population_text = _format_int(population_value)
        info_lines = [
            ("Município/UF", f"{municipality} / {state}"),
            ("Distância", distance_text),
            ("Campo", field_text),
            ("ERP na direção", erp_text),
            ("Altitude RX", altitude_text),
            ("População (IBGE)", f"{population_text} {f'({population_year})' if population_year else ''}".strip()),
            ("Conformidade", compliance),
            ("Qualidade", quality_text),
        ]
        y = _draw_text_block(c, 50, y, info_lines)
        pop_value = population_value
        sex_dom = _dominant_category(demo.get('sex') or {})
        age_dom = _dominant_category(demo.get('age') or {})
        demography_text = ""
        if pop_value:
            demography_text += f"População estimada: {_format_int(pop_value)}."
        if sex_dom[0]:
            demography_text += f" Sexo dominante: {sex_dom[0]}"
            if sex_dom[1] is not None:
                demography_text += f" ({sex_dom[1]:.1f}%)."
        if age_dom[0]:
            demography_text += f" Faixa etária predominante: {age_dom[0]}"
            if age_dom[1] is not None:
                demography_text += f" ({age_dom[1]:.1f}%)."
        if demography_text:
            y = _wrap_text(c, demography_text, 50, y - 4, width_chars=95)
            y -= 4
        y -= 4

    if population_details:
        y = _ensure_space(
            c,
            y,
            120,
            width,
            height,
            "Enlaces e impacto populacional (cont.)",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Demografia detalhada por município")
        y -= 18
        c.setFont('Helvetica', 9)
        for detail in population_details:
            demo = detail.get('demographics') or {}
            pop_text = _format_int(demo.get('total'))
            sex_breakdown = demo.get('sex') or {}
            age_breakdown = demo.get('age') or {}
            sex_items = sorted(sex_breakdown.items(), key=lambda item: item[1], reverse=True)
            age_items = sorted(age_breakdown.items(), key=lambda item: item[1], reverse=True)
            sex_parts = [f"{name}: {_format_int(value)}" for name, value in sex_items[:2]]
            age_parts = [f"{name}: {_format_int(value)}" for name, value in age_items[:3]]
            label = f"{detail.get('municipality') or '—'} / {detail.get('state') or '—'}"
            text = f"{label} — População: {pop_text}"
            if sex_parts:
                text += f" | Sexo: {'; '.join(sex_parts)}"
            if age_parts:
                text += f" | Idade: {'; '.join(age_parts)}"
            y = _wrap_text(c, text, 40, y, width_chars=95, line_height=12) - 4
            if y < 90:
                c.showPage()
                y = _start_page(
                    c,
                    width,
                    height,
                    "Enlaces e impacto populacional (cont.)",
                    project.slug,
                    header_color,
                    company_logo=company_logo_blob,
                ) - 20
                c.setFont('Helvetica-Bold', 11)
                c.drawString(40, y, "Demografia detalhada por município (cont.)")
                y -= 18
                c.setFont('Helvetica', 9)

    coverage_ibge_municipalities = (coverage_ibge or {}).get('municipalities') if coverage_ibge else []
    if coverage_ibge_municipalities:
        y = _ensure_space(
            c,
            y,
            160,
            width,
            height,
            "Enlaces e impacto populacional (cont.)",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        threshold_value = int((coverage_ibge or {}).get('threshold_dbuv', 25))
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, f"Municípios com campo ≥ {threshold_value} dBµV/m")
        y -= 18

        summary_lines: list[str] = []
        tiles_total = (coverage_ibge or {}).get('tiles_total')
        tiles_covered = (coverage_ibge or {}).get('tiles_covered')
        tile_zoom = (coverage_ibge or {}).get('tile_zoom')
        municipality_count = (coverage_ibge or {}).get('municipality_count')
        population_estimate = (coverage_ibge or {}).get('population_covered')
        if tiles_total not in (None, 0):
            zoom_text = f" (z{tile_zoom})" if tile_zoom is not None else ""
            summary_lines.append(f"Tiles avaliados{zoom_text}: {_format_int(tiles_total)}")
        if tiles_covered is not None:
            summary_lines.append(f"Tiles acima do limiar: {_format_int(tiles_covered)}")
        if municipality_count not in (None, 0):
            summary_lines.append(f"Municípios elegíveis: {_format_int(municipality_count)}")
        if population_estimate not in (None, 0):
            summary_lines.append(f"População estimada atendida: {_format_int(population_estimate)}")
        if summary_lines:
            c.setFont('Helvetica', 9)
            for line in summary_lines:
                c.drawString(40, y, line)
                y -= 12
            y -= 6

        coverage_columns = [
            ("Município/UF", 150),
            ("Campo máx (dBµV/m)", 100),
            ("População", 90),
            ("Ano Pop", 60),
            ("Renda per capita", 120),
            ("Ano Renda", 70),
        ]
        coverage_rows: List[List[str]] = []
        for entry in coverage_ibge_municipalities:
            city_state = f"{entry.get('municipality') or '—'} / {entry.get('state') or '—'}"
            field_val = entry.get('max_field_dbuvm')
            field_text = f"{field_val:.1f}" if isinstance(field_val, (int, float)) else "—"
            pop_text = _format_int(entry.get('population'))
            pop_year = entry.get('population_year')
            pop_year_text = str(pop_year) if pop_year else "—"
            income_text = _format_currency(entry.get('income_per_capita'))
            income_year = entry.get('income_year')
            income_year_text = str(income_year) if income_year else "—"
            coverage_rows.append([
                city_state,
                field_text,
                pop_text,
                pop_year_text,
                income_text,
                income_year_text,
            ])
        y = _draw_table(
            c,
            y,
            coverage_columns,
            coverage_rows,
            width,
            height,
            project.slug,
            "Municípios com campo ≥ 25 dBµV/m (cont.)",
            theme_color=header_color,
            company_logo=company_logo_blob,
        )
        y -= 6

    link_analyses = []
    for item in ai_sections.get("link_analyses") or []:
        if isinstance(item, dict) and item.get('label') and item.get('analysis'):
            link_analyses.append({"label": str(item['label']), "analysis": str(item['analysis'])})

    if link_analyses:
        y = _ensure_space(
            c,
            y,
            200,
            width,
            height,
            "Observações automáticas por receptor",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Observações automáticas por receptor")
        y -= 18
        for entry in link_analyses:
            if y < 140:
                c.showPage()
                y = _start_page(
                    c,
                    width,
                    height,
                    "Observações automáticas por receptor (cont.)",
                    project.slug,
                    header_color,
                    company_logo=company_logo_blob,
                )
                c.setFont('Helvetica-Bold', 11)
                c.drawString(40, y, "Observações automáticas por receptor (cont.)")
                y -= 18
            c.setFont('Helvetica-Bold', 10)
            c.drawString(40, y, entry.get('label') or 'Receptor')
            c.setFont('Helvetica', 9)
            y = _wrap_text(c, entry.get('analysis') or "Sem observações adicionais.", 40, y - 14, width_chars=95, line_height=13)
            y -= 6

    y = _ensure_space(
        c,
        y,
        120,
        width,
        height,
        "Conclusão e alcance estimado",
        project.slug,
        header_color,
        company_logo=company_logo_blob,
    )
    c.setFont('Helvetica-Bold', 11)
    c.drawString(40, y, "Conclusão e alcance estimado")
    y -= 16
    if population_total:
        viewers_text = f"{population_total:,}".replace(",", ".")
        conclusion = (
            f"Com base nos receptores acima de {int(MIN_RECEIVER_POWER_DBM)} dBm e nos dados públicos do IBGE, "
            f"estima-se que a mancha de cobertura atinge aproximadamente {viewers_text} telespectadores potenciais."
        )
    else:
        conclusion = (
            "Não foi possível estimar o alcance de telespectadores por indisponibilidade temporária dos serviços do IBGE."
        )
    y = _wrap_text(c, conclusion, 40, y, width_chars=95)
    y -= 10

    y = _ensure_space(
        c,
        y,
        120,
        width,
        height,
        "Parecer técnico",
        project.slug,
        header_color,
        company_logo=company_logo_blob,
    )
    ai_conclusion = ai_sections.get("conclusion") or "Dados ainda não consolidados para o parecer automatizado."
    if ai_conclusion:
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Parecer técnico consolidado")
        y -= 16
        c.setFont('Helvetica', 10)
        y = _wrap_text(c, ai_conclusion, 40, y, width_chars=95)
        y -= 10
    positive_note = (
        f"A equipe técnica mantém perspectiva otimista para {project.name}, "
        "uma vez que as condições climáticas e os níveis de ERP indicam margem para otimizações contínuas."
    )
    c.setFont('Helvetica', 10)
    y = _wrap_text(c, positive_note, 40, y, width_chars=95)
    y -= 10

    recommendations = ai_sections.get("recommendations") or []
    if recommendations:
        y = _ensure_space(
            c,
            y,
            140,
            width,
            height,
            "Recomendações técnicas",
            project.slug,
            header_color,
            company_logo=company_logo_blob,
        )
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, "Recomendações técnicas")
        y -= 16
        c.setFont('Helvetica', 10)
        for rec in recommendations:
            y = _wrap_text(c, f"- {rec}", 40, y, width_chars=95)
            y -= 4

    y = _ensure_space(
        c,
        y,
        260,
        width,
        height,
        "Glossário técnico",
        project.slug,
        header_color,
        company_logo=company_logo_blob,
    )
    c.setFont('Helvetica-Bold', 11)
    c.drawString(40, y, "Glossário técnico")
    y -= 18
    for term, description in GLOSSARY_TERMS:
        if y < 120:
            c.showPage()
            y = _start_page(
                c,
                width,
                height,
                "Glossário técnico (cont.)",
                project.slug,
                header_color,
                company_logo=company_logo_blob,
            )
            c.setFont('Helvetica-Bold', 11)
            c.drawString(40, y, "Glossário técnico (cont.)")
            y -= 18
        c.setFont('Helvetica-Bold', 10)
        c.drawString(40, y, term)
        c.setFont('Helvetica', 9)
        y = _wrap_text(c, description, 40, y - 12, width_chars=95, line_height=13)
        y -= 4

    c.setFont('Helvetica-Oblique', 8)
    c.drawString(40, 40, "Documento interno ATX Coverage")

    c.save()
    pdf_buffer.seek(0)
    pdf_blob = pdf_buffer.read()

    asset = Asset(
        project_id=project.id,
        type=AssetType.pdf,
        path=inline_asset_path('reports', 'pdf'),
        mime_type='application/pdf',
        byte_size=len(pdf_blob),
        data=pdf_blob,
        meta={'kind': 'analysis', 'snapshot_asset': snapshot.get('asset_id')},
    )
    db.session.add(asset)
    db.session.flush()

    report_entry = Report(
        project_id=project.id,
        title=f"Relatório de Análise {datetime.utcnow():%d/%m/%Y %H:%M}",
        description='Relatório automático de análise de cobertura.',
        template_name='analysis_pdf',
        json_payload={'snapshot': snapshot, 'generated_at': datetime.utcnow().isoformat()},
        pdf_asset_id=asset.id,
    )
    db.session.add(report_entry)
    db.session.commit()
    return report_entry


def analyze_ai_inconsistencies(project: Project, ai_sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = _latest_snapshot(project)
    center_metrics = snapshot.get('center_metrics') or {}
    metrics = _build_metrics(project, snapshot, center_metrics)
    receivers = snapshot.get('receivers') or []
    link_summary, link_payload = _build_link_summary(receivers)
    normalized_sections = {
        "overview": ai_sections.get("overview") or "",
        "coverage": ai_sections.get("coverage") or "",
        "profile": ai_sections.get("profile") or "",
        "pattern_horizontal": ai_sections.get("pattern_horizontal") or "",
        "pattern_vertical": ai_sections.get("pattern_vertical") or "",
        "recommendations": ai_sections.get("recommendations") if isinstance(ai_sections.get("recommendations"), list) else [],
        "conclusion": ai_sections.get("conclusion") or "",
    }
    return validate_ai_sections(
        project,
        snapshot,
        metrics,
        normalized_sections,
        link_summary,
        link_payload,
    )
