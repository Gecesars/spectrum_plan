import base64
import html
import io
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from math import radians, cos, sin, asin, sqrt, degrees
from pathlib import Path
from typing import Iterable
import astropy
import geojson
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pycraf
import requests
from sklearn.linear_model import LinearRegression
from PIL import Image, ImageDraw
from astropy import units as u
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.colors import LinearSegmentedColormap, ListedColormap, Normalize
from matplotlib.cm import ScalarMappable
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.table import Table
from datetime import datetime, timedelta
from pycraf import pathprof, antenna, conversions as cnv
from pycraf.pathprof import SrtmConf
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from scipy.constants import c
from scipy.integrate import simpson
from scipy.interpolate import interp1d, CubicSpline, griddata
from scipy.ndimage import gaussian_filter1d
from shapely.geometry import Point, Polygon
from geopy.distance import geodesic
from geopy.point import Point
from types import SimpleNamespace
from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError

from sqlalchemy import or_

from extensions import db
from user import User
from app_core.models import (
    Project,
    Asset,
    AssetType,
    CoverageEngine,
    CoverageJob,
    CoverageStatus,
    ProjectReceiver,
    ProjectCoverage,
)
from app_core.email_utils import generate_token, load_token, send_email
from app_core.storage import inline_asset_path
from app_core.storage_utils import rehydrate_asset_data
from app_core.reporting.service import generate_analysis_report, AnalysisReportError
from app_core.data_acquisition import ensure_geodata_availability, ensure_rt3d_scene, global_srtm_dir
from app_core.utils import (
    ensure_unique_slug,
    project_by_slug_or_404,
    project_to_dict,
    projects_to_dict,
    slugify,
)
from app_core.regulatory.service import build_default_payload
from app_core.integrations import ibge as ibge_api

GAIN_OFFSET_DBI_DBD = 2.15


PROJECT_SETTING_FIELDS = [
    "towerHeight",
    "rxHeight",
    "Total_loss",
    "antennaGain",
    "rxGain",
    "transmissionPower",
    "frequency",
    "antennaTilt",
    "antennaDirection",
    "latitude",
    "longitude",
    "waterDensity",
    "timePercentage",
    "temperature",
    "pressure",
    "serviceType",
    "propagationModel",
    "polarization",
    "p452Version",
    "coverageEngine",
    "radius",
    "minSignalLevel",
    "maxSignalLevel",
]


def _blank_project_payload(user: User, project: Project) -> dict:
    defaults = {
        'username': user.username,
        'email': user.email,
        'nomeUsuario': user.username,
        'coverageEngine': CoverageEngine.p1546.value,
        'projectSlug': project.slug,
        'projectName': project.name,
        'projectDescription': project.description,
        'projectSettings': {},
        'receiverBookmarks': [],
        'lastCoverage': None,
        'projectLastSavedAt': None,
        'txLocationName': user.tx_location_name,
        'txElevation': user.tx_site_elevation,
        'latitude': user.latitude,
        'longitude': user.longitude,
        'transmissionPower': user.transmission_power,
        'frequency': user.frequencia,
        'towerHeight': user.tower_height,
        'rxHeight': user.rx_height,
        'Total_loss': user.total_loss,
        'antennaGain': user.antenna_gain,
        'rxGain': user.rx_gain,
        'antennaTilt': user.antenna_tilt,
        'antennaDirection': user.antenna_direction,
        'serviceType': user.servico,
        'propagationModel': user.propagation_model,
        'polarization': (user.polarization or 'vertical').lower() if user.polarization else None,
        'timePercentage': user.time_percentage or 40.0,
        'p452Version': user.p452_version or 16,
        'temperature': (user.temperature_k - 273.15) if user.temperature_k else 20.0,
        'pressure': user.pressure_hpa or 1013.0,
        'waterDensity': user.water_density or 7.5,
        'txData': {},
        'txLocation': None,
    }
    return defaults


def _is_project_settings_empty(payload: dict | None) -> bool:
    if not payload:
        return True
    allowed_empty_keys = {'receiverBookmarks', 'lastCoverage', 'lastSavedAt'}
    for key, value in payload.items():
        if key in allowed_empty_keys:
            if value:
                return False
            continue
        return False
    return True


def _apply_project_settings(payload: dict, settings: dict | None) -> dict:
    if not payload or not settings:
        return payload
    for key in PROJECT_SETTING_FIELDS:
        if key in settings:
            payload[key] = settings[key]
    tx_name = settings.get('txLocationName')
    if tx_name is not None:
        payload['txLocationName'] = tx_name
    tx_elev = settings.get('txElevation')
    if tx_elev is not None:
        payload['txElevation'] = tx_elev
    return payload
def _remember_active_project(project: Project | None) -> None:
    if project and getattr(project, "slug", None):
        session['active_project_slug'] = project.slug
    else:
        session.pop('active_project_slug', None)


def _active_project_from_session() -> Project | None:
    slug = session.get('active_project_slug')
    if not slug:
        return None
    try:
        return project_by_slug_or_404(slug, current_user.uuid)
    except Exception:
        session.pop('active_project_slug', None)
        return None


def _load_project_for_current_user(slug: str) -> Project:
    project = project_by_slug_or_404(slug, current_user.uuid)
    _remember_active_project(project)
    return project


def _gain_dbi_to_dbd(value):
    try:
        return float(value) - GAIN_OFFSET_DBI_DBD
    except (TypeError, ValueError):
        return None


def _gain_dbd_to_dbi(value):
    try:
        return float(value) + GAIN_OFFSET_DBI_DBD
    except (TypeError, ValueError):
        return None


matplotlib.use('Agg')

bp = Blueprint('ui', __name__)


# === Helper utilities ======================================================


def _create_default_project(user: User) -> Project:
    base_label = user.username or (user.email.split('@')[0] if user.email else 'projeto-atx')
    slug_candidate = slugify(base_label)
    slug = ensure_unique_slug(user.uuid, slug_candidate)
    project = Project(
        user_uuid=user.uuid,
        name=f"Projeto de {base_label}",
        slug=slug,
        description="Projeto padrão criado automaticamente.",
    )
    db.session.add(project)
    db.session.flush()
    return project


def _purge_project_asset_folders(project: Project, folders: Iterable[str]) -> None:
    if not project:
        return
    path_prefix = f"{project.user_uuid}/{project.slug}/assets/"
    for folder in folders:
        prefix_like = f"{path_prefix}{folder}/%"
        inline_like = f"inline://{folder}/%"
        Asset.query.filter(
            Asset.project_id == project.id,
            or_(
                Asset.path.like(prefix_like),
                Asset.path.like(inline_like),
            ),
        ).delete(synchronize_session=False)


def _asset_file_exists(asset: Asset | None) -> bool:
    return bool(asset and getattr(asset, 'data', None))


def _load_asset_bytes(asset_id: str | None = None, asset_path: str | None = None) -> bytes | None:
    asset = None
    if asset_id:
        asset = Asset.query.filter_by(id=asset_id).first()
    elif asset_path and str(asset_path).startswith('inline://'):
        asset = Asset.query.filter_by(path=asset_path).first()
    
    if asset:
        # Use read_asset_data to handle both DB and filesystem (including file:// paths)
        # without forcing rehydration (which deletes the file).
        from app_core.storage_utils import read_asset_data
        return read_asset_data(asset)
        
    return None


def _delete_receiver_profile_assets(project: Project, receiver_entry: dict) -> None:
    asset = None
    asset_id = receiver_entry.get('profile_asset_id')
    if asset_id:
        asset = Asset.query.filter_by(id=asset_id, project_id=project.id).first()
    if asset:
        db.session.delete(asset)


def _serialize_project_receiver(record: ProjectReceiver, *, include_urls: bool = False) -> dict:
    payload = dict(record.summary or {})
    payload_id = payload.get('id') or record.legacy_id
    payload['id'] = payload_id
    payload.setdefault('label', record.label or payload_id)
    location = payload.get('location') or {}
    if record.latitude is not None:
        location.setdefault('lat', record.latitude)
        payload.setdefault('lat', record.latitude)
    if record.longitude is not None:
        location.setdefault('lng', record.longitude)
        payload.setdefault('lng', record.longitude)
    if location:
        payload['location'] = location
    if record.municipality and not payload.get('municipality'):
        payload['municipality'] = record.municipality
    if record.state and not payload.get('state'):
        payload['state'] = record.state
    asset = record.profile_asset
    if _asset_file_exists(asset):
        payload.setdefault('profile_asset_id', str(asset.id))
        payload.setdefault('profile_asset_path', asset.path)
        if include_urls and record.project and has_request_context():
            try:
                payload['profile_asset_url'] = url_for(
                    'projects.asset_preview',
                    slug=record.project.slug,
                    asset_id=asset.id,
                )
            except Exception:
                payload.pop('profile_asset_url', None)
        elif not include_urls:
            payload.pop('profile_asset_url', None)
    else:
        payload.pop('profile_asset_id', None)
        payload.pop('profile_asset_path', None)
        payload.pop('profile_asset_url', None)
    payload.setdefault('ibge_code', record.ibge_code)
    payload.setdefault('population', record.population)
    payload.setdefault('population_year', record.population_year)
    return payload


def _is_valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _normalized_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {'none', 'null', 'undefined'}:
        return None
    return text


def _project_receivers_payload(project: Project | None, *, include_urls: bool = False) -> list[dict]:
    if not project:
        return []
    return [
        _serialize_project_receiver(record, include_urls=include_urls)
        for record in project.receivers
    ]


def _sanitize_tiles_payload(project: Project, tile_payload: dict | None) -> dict | None:
    if not project or not tile_payload:
        return None
    asset_id = tile_payload.get('asset_id') or tile_payload.get('assetId')
    asset_id = _normalized_identifier(asset_id)
    if not asset_id:
        return None
    asset = Asset.query.filter_by(id=asset_id, project_id=project.id).first()
    if not _asset_file_exists(asset):
        return None
    template = tile_payload.get('url_template') or tile_payload.get('urlTemplate')
    if not template or '/None/' in template:
        return None
    sanitized = dict(tile_payload)
    sanitized['asset_id'] = str(asset.id)
    return sanitized


def _serialize_project_coverage(record: ProjectCoverage | None) -> dict | None:
    if not record:
        return None
    payload = dict(record.payload or {})
    payload.setdefault('engine', record.engine)
    if record.generated_at and not payload.get('generated_at'):
        payload['generated_at'] = record.generated_at.isoformat()
    if record.project:
        payload.setdefault('project_slug', record.project.slug)
    def _apply_asset_reference(payload_key, column_attr, path_key=None):
        candidate = payload.get(payload_key)
        candidate = _normalized_identifier(candidate)
        asset_candidate = candidate or getattr(record, column_attr)
        if not asset_candidate:
            payload.pop(payload_key, None)
            if path_key:
                payload.pop(path_key, None)
            return
        if isinstance(asset_candidate, dict):
            candidate_id = asset_candidate.get('id')
        else:
            candidate_id = asset_candidate
        candidate_id = _normalized_identifier(candidate_id)
        if not candidate_id:
            payload.pop(payload_key, None)
            if path_key:
                payload.pop(path_key, None)
            return
        try:
            uuid.UUID(candidate_id)
        except (ValueError, AttributeError, TypeError):
            payload.pop(payload_key, None)
            if path_key:
                payload.pop(path_key, None)
            return
        asset = Asset.query.filter_by(id=candidate_id, project_id=record.project_id).first()
        if not asset or not _asset_file_exists(asset):
            payload.pop(payload_key, None)
            if path_key:
                payload.pop(path_key, None)
            return
        payload[payload_key] = str(asset.id)
        if path_key:
            payload[path_key] = asset.path

    _apply_asset_reference('asset_id', 'heatmap_asset_id', 'asset_path')
    _apply_asset_reference('colorbar_asset_id', 'colorbar_asset_id')
    _apply_asset_reference('map_snapshot_asset_id', 'map_snapshot_asset_id', 'map_snapshot_path')
    _apply_asset_reference('json_asset_id', 'summary_asset_id')

    tile_payload = payload.get('tiles')
    if tile_payload:
        sanitized_tiles = _sanitize_tiles_payload(record.project, tile_payload)
        if sanitized_tiles:
            payload['tiles'] = sanitized_tiles
        else:
            payload.pop('tiles', None)

    payload.setdefault('receivers', _project_receivers_payload(record.project))
    return payload


def _sanitize_snapshot_assets(project: Project, snapshot: dict | None) -> dict | None:
    if not project or not snapshot:
        return snapshot
    cleaned = dict(snapshot)
    asset_keys = (
        ('asset_id', 'asset_path'),
        ('colorbar_asset_id', None),
        ('map_snapshot_asset_id', 'map_snapshot_path'),
        ('json_asset_id', None),
    )
    for key, path_key in asset_keys:
        raw_id = cleaned.get(key)
        asset_id = _normalized_identifier(raw_id)
        if not asset_id:
            continue
        asset = Asset.query.filter_by(id=asset_id, project_id=project.id).first()
        if not _asset_file_exists(asset):
            cleaned.pop(key, None)
            if path_key:
                cleaned.pop(path_key, None)
    tile_payload = cleaned.get('tiles')
    if tile_payload:
        sanitized_tiles = _sanitize_tiles_payload(project, tile_payload)
        if sanitized_tiles:
            cleaned['tiles'] = sanitized_tiles
        else:
            cleaned.pop('tiles', None)
    return cleaned


def _project_settings_with_dynamic(project: Project | None) -> dict:
    base = dict(project.settings or {}) if project else {}
    if project and base.get('lastCoverage'):
        base['lastCoverage'] = _sanitize_snapshot_assets(project, base.get('lastCoverage'))
    if project:
        base['receiverBookmarks'] = _project_receivers_payload(project, include_urls=True)
        coverage_payload = _serialize_project_coverage(
            ProjectCoverage.query.filter_by(project_id=project.id)
            .order_by(ProjectCoverage.generated_at.desc().nullslast(), ProjectCoverage.created_at.desc().nullslast())
            .first()
        )
        if coverage_payload:
            base['lastCoverage'] = coverage_payload
        elif 'lastCoverage' in base:
            base.pop('lastCoverage', None)
    return base


def _sync_project_receivers(project: Project, receivers: list[dict] | None) -> None:
    if project is None:
        return
    if receivers is None:
        receivers = []

    def _coerce_int_local(value):
        try:
            if value in (None, "", "-", "..."):
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None
    # remove todos os registros antigos e recria a partir do payload atual
    ProjectReceiver.query.filter_by(project_id=project.id).delete(synchronize_session=False)
    sanitized: list[dict] = []
    for idx, raw in enumerate(receivers):
        if not isinstance(raw, dict):
            continue
        lat = _coerce_float(raw.get('lat') or (raw.get('location') or {}).get('lat'))
        lon = _coerce_float(raw.get('lng') or raw.get('lon') or (raw.get('location') or {}).get('lng') or (raw.get('location') or {}).get('lon'))
        legacy_id = raw.get('id') or raw.get('label') or f"rx-{idx+1}"
        label = raw.get('label') or raw.get('name') or legacy_id
        municipality = raw.get('municipality') or (raw.get('location') or {}).get('municipality')
        state = raw.get('state') or (raw.get('location') or {}).get('state')
        ibge_info = raw.get('ibge') or {}
        ibge_code = ibge_info.get('code') or ibge_info.get('ibge_code') or raw.get('ibge_code')
        population = raw.get('population') or ibge_info.get('population')
        population_year = raw.get('population_year') or ibge_info.get('population_year')
        record = ProjectReceiver(
            project_id=project.id,
            legacy_id=str(legacy_id),
            label=str(label),
            latitude=lat,
            longitude=lon,
            municipality=municipality,
            state=state,
            summary=raw,
            ibge_code=ibge_code,
            population=_coerce_int_local(population),
            population_year=_coerce_int_local(population_year),
            profile_asset_id=raw.get('profile_asset_id'),
        )
        db.session.add(record)
        sanitized.append(raw)
    settings = dict(project.settings or {})
    settings['receiverBookmarks'] = sanitized
    project.settings = settings


def _persist_project_coverage_record(
    project: Project,
    summary_payload: dict,
    *,
    heatmap_asset=None,
    colorbar_asset=None,
    map_snapshot_asset=None,
    summary_asset=None,
) -> None:
    summary = dict(summary_payload or {})
    summary.setdefault('project_slug', project.slug)
    summary.setdefault('receivers', _project_receivers_payload(project))
    record = ProjectCoverage(
        project_id=project.id,
        engine=summary.get('engine'),
        generated_at=datetime.utcnow().replace(tzinfo=timezone.utc),
        payload=summary,
        heatmap_asset_id=heatmap_asset.id if heatmap_asset else None,
        colorbar_asset_id=colorbar_asset.id if colorbar_asset else None,
        map_snapshot_asset_id=map_snapshot_asset.id if map_snapshot_asset else None,
        summary_asset_id=summary_asset.id if summary_asset else None,
    )
    db.session.add(record)


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _normalize_datetime(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return _parse_iso_datetime(dt)


# ==========================================================================
# =========================
# Helpers para parsing/validação
# =========================

def _is_db_values(vals):
    vals = np.asarray(vals, dtype=float)
    if vals.size == 0:
        return False
    if np.nanmin(vals) < -0.5:  # valores negativos plausíveis em dB
        return True
    return np.nanmax(vals) > 20  # acima de 20 em "campo" é improvável

def _safe_float(x):
    try:
        return float(x)
    except Exception:
        return np.nan

def _mirror_vertical_if_needed(angles_deg, values):
    """
    Recebe listas possivelmente só com 0..-90 (ou 0..+90) e devolve pares
    cobrindo -90..+90 por simetria em torno de 0°.
    """
    a = np.asarray(angles_deg, dtype=float)
    v = np.asarray(values,     dtype=float)

    # Remove NaN e ordena
    m = np.isfinite(a) & np.isfinite(v)
    a, v = a[m], v[m]
    if a.size == 0:
        return np.array([-90.0,  0.0, 90.0]), np.array([0.0, 1.0, 0.0])

    order = np.argsort(a)
    a, v = a[order], v[order]

    has_neg = np.any(a < 0)
    has_pos = np.any(a > 0)

    if not has_neg and has_pos:
        # só 0..+90: espelha para o lado negativo
        a_neg = -a[a >= 0]
        v_neg =  v[a >= 0]
        a_all = np.concatenate([a_neg, a])
        v_all = np.concatenate([v_neg, v])
        order = np.argsort(a_all)
        return a_all[order], v_all[order]

    if has_neg and not has_pos:
        # só -90..0: espelha para + lado
        a_pos = -a[a <= 0]
        v_pos =  v[a <= 0]
        a_all = np.concatenate([a, a_pos])
        v_all = np.concatenate([v, v_pos])
        order = np.argsort(a_all)
        return a_all[order], v_all[order]

    return a, v  # já tem dos dois lados

def parse_pat(text):
    """
    Saída:
      horiz_lin: (360,)  E/Emax linear (0..1), azimutes 0..359
      vert_lin:  (181,)  E/Emax linear (0..1), ângulos -90..+90 (passo 1°)
      meta: dict
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    meta = {}

    # Cabeçalho opcional
    header_re = re.compile(r"^['\"]?(.*?)[\"']?\s*,\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)$")
    if lines and (lines[0].startswith("'") or lines[0][0].isalpha()):
        m = header_re.match(lines[0])
        if m:
            meta['title']  = m.group(1).strip()
            meta['param1'] = _safe_float(m.group(2))
            meta['param2'] = _safe_float(m.group(3))
        lines = lines[1:]

    # Horizontal até '999'
    horiz_map = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln == '999':
            i += 1
            break
        parts = [p.strip() for p in ln.split(',')]
        if parts and re.fullmatch(r"[-+]?\d+", parts[0] or ""):
            az = int(parts[0]) % 360
            val = _safe_float(parts[1]) if len(parts) >= 2 and parts[1] != '' else np.nan
            horiz_map[az] = val
        i += 1

    # Vertical: pares (ângulo, valor) livres
    v_angles, v_vals = [], []
    while i < len(lines):
        ln = lines[i]
        parts = [p.strip() for p in ln.split(',')]
        if len(parts) >= 2 and parts[0] and parts[1]:
            a0 = _safe_float(parts[0]); v0 = _safe_float(parts[1])
            if np.isfinite(a0) and np.isfinite(v0) and -360 <= a0 <= 360:
                v_angles.append(a0); v_vals.append(v0)
        i += 1

    # --- Horizontal: vetor 0..359, preenchimento + normalização para E/Emax ---
    horiz_vals = np.full(360, np.nan, float)
    for az, val in horiz_map.items():
        horiz_vals[az] = val

    if np.isnan(horiz_vals).any():
        # forward/backward fill + média
        last = np.nan
        for k in range(360):
            if np.isfinite(horiz_vals[k]): last = horiz_vals[k]
            else:                          horiz_vals[k] = last
        last = np.nan
        for k in range(359, -1, -1):
            if np.isfinite(horiz_vals[k]): last = horiz_vals[k]
            else:                          horiz_vals[k] = last
        if np.isnan(horiz_vals).any():
            mean_val = np.nanmean(horiz_vals)
            horiz_vals[np.isnan(horiz_vals)] = 0.0 if np.isnan(mean_val) else mean_val

    horiz_lin = 10**(horiz_vals/20.0) if _is_db_values(horiz_vals) else horiz_vals.astype(float)
    max_h = np.nanmax(horiz_lin) if np.isfinite(horiz_lin).any() else 1.0
    horiz_lin = np.clip(horiz_lin/max_h, 0.0, 1.0)

    # --- Vertical: reconstrução simétrica e interp. para -90..+90 ---
    if len(v_angles) == 0:
        # fallback "gaussiano"
        target = np.arange(-90, 91, 1.0)
        vert_lin = np.exp(-0.5 * (target/15.0)**2)
        vert_lin /= vert_lin.max()
        return horiz_lin.astype(float), vert_lin.astype(float), meta

    v_angles = np.asarray(v_angles, float)
    v_vals   = np.asarray(v_vals,   float)
    # Some .pat variants include a metadata/count line like "1, 91" before the real pairs.
    # If we detect unusually large values (>>1) mixed with normal 0..1 samples, drop those metadata entries.
    try:
        if np.any(v_vals > 10) and np.nanmax(v_vals[np.where(v_vals <= 10)]) <= 2:
            # remove entries with implausibly large values
            mask_valid = v_vals <= 10
            v_angles = v_angles[mask_valid]
            v_vals = v_vals[mask_valid]
            try:
                current_app.logger.debug(f"parse_pat: removed metadata vertical entries, kept {len(v_vals)} samples")
            except Exception:
                pass
    except Exception:
        pass
    v_angles, v_vals = _mirror_vertical_if_needed(v_angles, v_vals)

    # passa p/ E/Emax linear
    v_vals_lin = 10**(v_vals/20.0) if _is_db_values(v_vals) else v_vals.astype(float)

    # normaliza por pico
    vmax = np.nanmax(v_vals_lin) if np.isfinite(v_vals_lin).any() else 1.0
    v_vals_lin = np.clip(v_vals_lin / vmax, 0.0, 1.0)

    # garante cobertura de -90 e +90
    if v_angles[0] > -90:
        # If the vertical samples don't reach -90, pad with 0 at the edge
        v_angles   = np.insert(v_angles,  0, -90.0)
        v_vals_lin = np.insert(v_vals_lin, 0, 0.0)
    if v_angles[-1] < 90:
        # If the vertical samples don't reach +90, pad with 0 at the edge
        v_angles   = np.append(v_angles,  90.0)
        v_vals_lin = np.append(v_vals_lin, 0.0)

    target = np.arange(-90.0, 91.0, 1.0, dtype=float)
    vert_lin = np.interp(target, v_angles, v_vals_lin)

    # Debug: log vertical parsing details
    try:
        from flask import current_app
        current_app.logger.debug(f"parse_pat: v_angles_in={v_angles.tolist() if hasattr(v_angles,'tolist') else v_angles}, v_vals_in_sample={v_vals[:10].tolist() if hasattr(v_vals,'tolist') else v_vals}")
        current_app.logger.debug(f"parse_pat: v_vals_lin_sample={v_vals_lin[:10].tolist() if hasattr(v_vals_lin,'tolist') else v_vals_lin}, vert_lin_len={len(vert_lin)}")
        current_app.logger.debug(f"parse_pat: is_db_values={_is_db_values(v_vals)}")
    except Exception:
        pass

    return horiz_lin.astype(float), vert_lin.astype(float), meta

# =========================
# Funções Auxiliares
# =========================

def _save_diagram_to_project(project: Project, file, direction, tilt):
    """Salva diagrama como Asset do projeto e atualiza settings."""
    try:
        file_content = file.read()
        
        # Remove existing pattern asset
        existing = Asset.query.filter_by(
            project_id=project.id,
            type=AssetType.other
        ).filter(Asset.meta['kind'].astext == 'antenna_pattern').first()
        
        if existing:
            db.session.delete(existing)
            
        # Create new asset
        asset = Asset(
            project_id=project.id,
            type=AssetType.other,
            path=inline_asset_path('antenna', 'pat'),
            mime_type='text/plain',
            byte_size=len(file_content),
            data=file_content,
            meta={'kind': 'antenna_pattern', 'filename': file.filename}
        )
        db.session.add(asset)
        
        # Update settings
        settings = dict(project.settings or {})
        if direction is not None:
            settings['antennaDirection'] = direction
        if tilt is not None:
            settings['antennaTilt'] = tilt
        project.settings = settings
        
        db.session.commit()
        return True, "Diagrama salvo no projeto com sucesso"
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao salvar no projeto: {str(e)}"

def salvar_diagrama_usuario(user, file, direction, tilt):
    """Função auxiliar para salvar diagrama no usuário (LEGADO)"""
    try:
        file_content = file.read()
        user.antenna_pattern = file_content
        user.antenna_direction = direction
        user.antenna_tilt = tilt
        db.session.commit()
        return True, "Diagrama salvo com sucesso"
    except Exception as e:
        db.session.rollback()
        return False, f"Erro ao salvar: {str(e)}"

# =========================
# Correção para Curvatura da Terra
# =========================

def earth_curvature_correction(distance_km, height_above_ground=0):
    """
    Calcula a correção da curvatura da Terra para uma dada distância.
    
    Parâmetros:
    - distance_km: distância em quilômetros
    - height_above_ground: altura acima do solo em metros
    
    Retorna:
    - drop: queda devido à curvatura em metros
    """
    # Raio da Terra em metros
    R = 6371000  # metros
    
    # Para distâncias maiores, usar fórmula mais precisa
    if distance_km > 10:
        drop = (distance_km * 1000) ** 2 / (8 * R)
    else:
        # Ângulo central em radianos
        theta = distance_km * 1000 / R
        # Queda devido à curvatura (metros)
        drop = R * (1 - np.cos(theta/2))
    
    return drop

def adjust_heights_for_curvature(distances, heights, h_tg, h_rg):
    """
    Ajusta as alturas considerando a curvatura da Terra.
    
    Parâmetros:
    - distances: array de distâncias do TX em metros
    - heights: array de alturas do terreno em metros
    - h_tg: altura da antena TX em metros
    - h_rg: altura da antena RX em metros
    
    Retorna:
    - adjusted_heights: alturas ajustadas considerando curvatura
    """
    distances_km = distances / 1000.0
    adjusted_heights = heights.copy()
    
    # Ajustar para cada ponto ao longo do perfil
    for i, dist_km in enumerate(distances_km):
        if i == 0:
            # Ponto do transmissor
            adjusted_heights[i] += h_tg
        elif i == len(distances_km) - 1:
            # Ponto do receptor
            adjusted_heights[i] += h_rg
        else:
            # Pontos intermediários - calcular queda da curvatura
            drop = earth_curvature_correction(dist_km)
            adjusted_heights[i] -= drop
    
    return adjusted_heights

def calculate_effective_earth_radius(k_factor=4/3):
    """
    Calcula o raio efetivo da Terra considerando refração atmosférica.
    
    Parâmetros:
    - k_factor: fator de refração (padrão 4/3 para condições normais)
    
    Retorna:
    - Raio efetivo em metros
    """
    R_earth = 6371000  # Raio real da Terra em metros
    return k_factor * R_earth


def get_google_maps_key():
    return current_app.config.get('GOOGLE_MAPS_API_KEY')


def get_solid_png_dir():
    return current_app.config['SOLID_PNG_ROOT']

def _coerce_float(value):
    """
    Converte value para float ou retorna None.
    Aceita "", None, "   ", etc.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return None
    # qualquer outro tipo inesperado
    return None


def _coerce_str(value):
    """
    Aceita string, número, None.
    Normaliza "" -> None, senão retorna string.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v if v != "" else None
    # ex. número (frequency às vezes mandam como 105.7 mas você quer string? não, mas p/ campos textuais)
    return str(value)


def _coerce_optional(value):
    """
    Converte valores opcionais para float, retornando None caso vazio ou inválido.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value_str = str(value).strip()
    if not value_str:
        return None
    try:
        return float(value_str)
    except ValueError:
        return None


def _normalize_direction_value(value, default=None):
    try:
        ang = float(value)
    except (TypeError, ValueError):
        if default is None:
            return None
        ang = float(default)
    ang = ang % 360.0
    if ang < 0:
        ang += 360.0
    return float(ang)


def _prepare_tx_object(base_tx, overrides=None, pattern_bytes=None):
    """
    Constroi um objeto compatível com User para cálculos de cobertura.
    """
    overrides = overrides or {}
    attrs = {
        'propagation_model': getattr(base_tx, 'propagation_model', None),
        'frequencia': getattr(base_tx, 'frequencia', None),
        'tower_height': getattr(base_tx, 'tower_height', None),
        'rx_height': getattr(base_tx, 'rx_height', None),
        'total_loss': getattr(base_tx, 'total_loss', None),
        'transmission_power': getattr(base_tx, 'transmission_power', None),
        'antenna_gain': getattr(base_tx, 'antenna_gain', None),
        'rx_gain': getattr(base_tx, 'rx_gain', None),
        'antenna_direction': getattr(base_tx, 'antenna_direction', None),
        'antenna_tilt': getattr(base_tx, 'antenna_tilt', None),
        'latitude': getattr(base_tx, 'latitude', None),
        'longitude': getattr(base_tx, 'longitude', None),
        'servico': getattr(base_tx, 'servico', None),
        'time_percentage': getattr(base_tx, 'time_percentage', None),
        'polarization': getattr(base_tx, 'polarization', None),
        'p452_version': getattr(base_tx, 'p452_version', None),
        'temperature_k': getattr(base_tx, 'temperature_k', None),
        'pressure_hpa': getattr(base_tx, 'pressure_hpa', None),
        'water_density': getattr(base_tx, 'water_density', None),
        'tx_location_name': getattr(base_tx, 'tx_location_name', None),
        'tx_site_elevation': getattr(base_tx, 'tx_site_elevation', None),
        'climate_lat': getattr(base_tx, 'climate_lat', None),
        'climate_lon': getattr(base_tx, 'climate_lon', None),
        'climate_updated_at': getattr(base_tx, 'climate_updated_at', None),
        'antenna_pattern': getattr(base_tx, 'antenna_pattern', pattern_bytes),
    }
    attrs.update(overrides or {})
    return SimpleNamespace(**attrs)


@bp.route("/salvar-dados", methods=["POST"])
@login_required
def salvar_dados():
    try:
        db.session.rollback()
    except Exception:
        pass
    data = request.get_json(silent=True) or {}
    project_slug = request.args.get('project') or data.get('projectSlug')
    project = None
    if project_slug:
        project = _load_project_for_current_user(project_slug)
        _remember_active_project(project)

    coverage_engine = data.get('coverageEngine') or CoverageEngine.p1546.value
    valid_engines = {engine.value for engine in CoverageEngine}
    if coverage_engine not in valid_engines:
        coverage_engine = CoverageEngine.p1546.value

    # 1. Extrair cada campo do payload sem assumir que existe
    #    Se não existir, não sobrescreve o valor atual.
    #    Se existir mas vier vazio, salva como None.

    simple_float_fields = {
        "towerHeight": "tower_height",
        "rxHeight": "rx_height",
        "Total_loss": "total_loss",
        "antennaGain": "antenna_gain",
        "rxGain": "rx_gain",
        "transmissionPower": "transmission_power",
        "frequency": "frequencia",
        "antennaTilt": "antenna_tilt",
        "latitude": "latitude",
        "longitude": "longitude",
        "waterDensity": "water_density",
    }

    project_settings_payload = dict(project.settings or {}) if project else {}
    user_temperature_c = (current_user.temperature_k - 273.15) if current_user.temperature_k is not None else None

    def _assign_value(key, attr_name, value):
        if project:
            project_settings_payload[key] = value
        else:
            setattr(current_user, attr_name, value)

    for incoming_key, model_attr in simple_float_fields.items():
        if incoming_key in data:
            value = _coerce_float(data.get(incoming_key))
            # Se value for None, significa que o usuário limpou o campo.
            # Devemos salvar None no projeto para que ele possa herdar o default (ou ficar vazio).
            _assign_value(incoming_key, model_attr, value)

    # Ganho de receptor fixo em 0 dBi para estudos ponto-área
    _assign_value("rxGain", "rx_gain", 0.0)

    # tempo percentual (0.001 a 50%)
    if "timePercentage" in data:
        time_pct_val = _coerce_float(data.get("timePercentage"))
        if time_pct_val is not None:
            _assign_value("timePercentage", "time_percentage", max(0.001, min(time_pct_val, 50.0)))

    # temperatura (°C -> K)
    if "temperature" in data:
        temp_c = _coerce_float(data.get("temperature"))
        if temp_c is not None:
            if project:
                project_settings_payload["temperature"] = temp_c
            else:
                current_user.temperature_k = temp_c + 273.15

    # pressão (hPa)
    if "pressure" in data:
        pressure_val = _coerce_float(data.get("pressure"))
        if pressure_val is not None:
            _assign_value("pressure", "pressure_hpa", pressure_val)

    # azimute (0..359°)
    if "antennaDirection" in data:
        direction_raw = data.get("antennaDirection")
        if direction_raw is None or str(direction_raw).strip() == "":
            _assign_value("antennaDirection", "antenna_direction", None)
        else:
            _assign_value("antennaDirection", "antenna_direction", _normalize_direction_value(direction_raw))

    # campos textuais / categóricos
    if "propagationModel" in data:
        _assign_value("propagationModel", "propagation_model", _coerce_str(data.get("propagationModel")))

    if "serviceType" in data:
        _assign_value("serviceType", "servico", _coerce_str(data.get("serviceType")))
    elif "service" in data:
        _assign_value("serviceType", "servico", _coerce_str(data.get("service")))

    if "polarization" in data:
        pol_val = _coerce_str(data.get("polarization"))
        if pol_val:
            _assign_value("polarization", "polarization", pol_val.lower())

    if "p452Version" in data:
        version_val = data.get("p452Version")
        try:
            version_val = int(version_val)
        except (TypeError, ValueError):
            version_val = current_user.p452_version or 16
        if version_val not in (14, 16):
            version_val = 16
        _assign_value("p452Version", "p452_version", version_val)

    if project and "txLocationName" in data:
        project_settings_payload["txLocationName"] = _coerce_str(data.get("txLocationName"))
    if project and "txElevation" in data:
        project_settings_payload["txElevation"] = _coerce_float(data.get("txElevation"))

    if project:
        project_settings_payload["coverageEngine"] = coverage_engine
        project_settings_payload["lastSavedAt"] = datetime.utcnow().isoformat()
        if 'radius' in data:
            radius_value = _coerce_float(data.get('radius'))
            if radius_value is not None:
                project_settings_payload['radius'] = radius_value
        if 'minSignalLevel' in data:
            value = _coerce_float(data.get('minSignalLevel'))
            if value is not None:
                project_settings_payload['minSignalLevel'] = value
        if 'maxSignalLevel' in data:
            value = _coerce_float(data.get('maxSignalLevel'))
            if value is not None:
                project_settings_payload['maxSignalLevel'] = value
        if coverage_engine == CoverageEngine.rt3d.value:
            rt3d_numeric_fields = (
                "rt3dUrbanRadius",
                "rt3dRays",
                "rt3dBounces",
                "rt3dOcclusionPerMeter",
                "rt3dReflectionGain",
                "rt3dInterferencePenalty",
                "rt3dSamples",
                "rt3dRings",
                "rt3dRayStep",
                "rt3dDiffractionBoost",
                "rt3dMinimumClearance",
            )
            for key in rt3d_numeric_fields:
                if key in data:
                    value = _coerce_float(data.get(key))
                    if value is not None:
                        project_settings_payload[key] = value
            if "rt3dBuildingSource" in data:
                source_value = _coerce_str(data.get("rt3dBuildingSource"))
                if source_value:
                    project_settings_payload["rt3dBuildingSource"] = source_value
        if project_settings_payload.get("temperature") is None and user_temperature_c is not None:
            project_settings_payload["temperature"] = user_temperature_c
        project.settings = project_settings_payload

        # sincroniza receptores atuais com o banco e com os bookmarks
        if 'receivers' in data:
            incoming_receivers = data.get('receivers') if isinstance(data.get('receivers'), list) else []
            _sync_project_receivers(project, incoming_receivers)

    # 5. Commit no banco
    try:
        db.session.add(current_user)
        if project:
            db.session.add(project)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # Aqui NÃO vamos devolver 500 cego. Vamos devolver JSON dizendo erro,
        # mas ainda assim status 200 se você quiser que o front trate como "ok mas com aviso".
        # Se preferir status 500 mantenha 500.
        return jsonify({
            "status": "error",
            "message": "Falha ao gravar no banco",
            "detail": str(e),
        }), 500

    # 6. Retornar snapshot atualizado
    temperature_c = user_temperature_c
    if temperature_c is None and "temperature" in data:
        temperature_c = _coerce_float(data.get("temperature"))

    response_payload = {
        "status": "ok",
        "towerHeight": current_user.tower_height,
        "rxHeight": current_user.rx_height,
        "Total_loss": current_user.total_loss,
        "antennaGain": current_user.antenna_gain,
        "rxGain": 0.0,
        "transmissionPower": current_user.transmission_power,
        "frequency": current_user.frequencia,
        "antennaTilt": getattr(current_user, "antenna_tilt", None),
        "antennaDirection": getattr(current_user, "antenna_direction", None),
        "timePercentage": current_user.time_percentage,
        "temperature": temperature_c,
        "pressure": current_user.pressure_hpa,
        "waterDensity": current_user.water_density,
        "propagationModel": current_user.propagation_model,
        "service": current_user.servico,
        "polarization": current_user.polarization,
        "p452Version": current_user.p452_version,
        "latitude": current_user.latitude,
        "longitude": current_user.longitude,
        "coverageEngine": coverage_engine,
        "projectSlug": project.slug if project else None,
    }
    if coverage_engine == CoverageEngine.rt3d.value:
        response_payload.update({
            "rt3dUrbanRadius": _coerce_float(data.get('rt3dUrbanRadius')),
            "rt3dRays": _coerce_float(data.get('rt3dRays')),
            "rt3dBounces": _coerce_float(data.get('rt3dBounces')),
            "rt3dOcclusionPerMeter": _coerce_float(data.get('rt3dOcclusionPerMeter')),
            "rt3dReflectionGain": _coerce_float(data.get('rt3dReflectionGain')),
            "rt3dInterferencePenalty": _coerce_float(data.get('rt3dInterferencePenalty')),
            "rt3dSamples": _coerce_float(data.get('rt3dSamples')),
            "rt3dRings": _coerce_float(data.get('rt3dRings')),
            "rt3dBuildingSource": _coerce_str(data.get('rt3dBuildingSource')),
            "rt3dRayStep": _coerce_float(data.get('rt3dRayStep')),
            "rt3dDiffractionBoost": _coerce_float(data.get('rt3dDiffractionBoost')),
            "rt3dMinimumClearance": _coerce_float(data.get('rt3dMinimumClearance')),
        })
    if project:
        response_payload["projectSettings"] = project.settings or {}

    return jsonify(response_payload), 200

@bp.route('/')
def inicio():
    return render_template('inicio.html')

@bp.route('/sensors')
def sensors():
    return render_template('sensors2.html')

@bp.route('/index')
def index():
    return render_template('index.html')

@bp.route('/antena')
@login_required
def antena():
    return render_template('antena.html')

@bp.route('/calcular-cobertura')
@login_required
def calcular_cobertura():
    maps_api_key = get_google_maps_key()
    projects = (
        current_user.projects.order_by(Project.created_at.asc()).all()
        if hasattr(current_user, "projects")
        else []
    )
    requested_slug = request.args.get('project')
    active_project = None
    if requested_slug:
        try:
            active_project = _load_project_for_current_user(requested_slug)
        except Exception:
            if projects:
                active_project = projects[0]
    if not active_project:
        session_project = _active_project_from_session()
        if session_project:
            active_project = session_project
    if not active_project and projects:
        def _coverage_sort_key(proj: Project):
            settings = proj.settings or {}
            coverage = settings.get('lastCoverage') or {}
            generated = coverage.get('generated_at')
            generated_dt = _parse_iso_datetime(generated)
            if generated_dt:
                return generated_dt
            fallback_dt = _normalize_datetime(proj.created_at)
            return fallback_dt or datetime.min

        try:
            active_project = max(projects, key=_coverage_sort_key)
        except ValueError:
            active_project = projects[0]

    if requested_slug and not active_project:
        # slug informado mas nenhum projeto encontrado
        flash('Projeto não encontrado ou sem acesso.', 'error')
        return redirect(url_for('projects.list_projects'))
    _remember_active_project(active_project)

    engines = list(CoverageEngine)
    selected_engine = None
    if active_project and active_project.settings:
        selected_engine = active_project.settings.get('coverageEngine')

    return render_template(
        'calcular_cobertura.html',
        maps_api_key=maps_api_key,
        project=active_project,
        projects=projects,
        engines=engines,
        selected_engine=selected_engine,
    )

@bp.route('/save-map-image', methods=['POST'])
@login_required
def save_map_image():
    data = request.get_json()
    image_data = data['image']
    user = current_user
    if image_data:
        image_data = base64.b64decode(image_data.split(',')[1])
        user.cobertura_img = image_data
        db.session.commit()
    return jsonify({"message": "Imagem salva com sucesso"})

@bp.route('/list_files/<path:folder>', methods=['GET'])
def list_files(folder):
    base_dir = get_solid_png_dir()
    folder_path = os.path.join(base_dir, folder)
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        files = [f for f in os.listdir(folder_path) if f.lower().endswith('.png')]
        return jsonify(files)
    else:
        return jsonify([]), 404

@bp.route('/static/SOLID_PRT_ASM/PNGS/<path:filename>', methods=['GET'])
def serve_file(filename):
    return send_from_directory(get_solid_png_dir(), filename)

@bp.route('/calculos-rf')
@login_required
def calculos_rf():
    return render_template('calculos-rf.html')

@bp.route('/gerar-relatorio', methods=['GET'])
@login_required
def download_report():
    project_slug = request.args.get('project')
    project = None
    if project_slug:
        project = _load_project_for_current_user(project_slug)
    else:
        project = (
            current_user.projects.order_by(Project.created_at.desc()).first()
            if hasattr(current_user, "projects")
            else None
        )

    if project:
        try:
            report_entry = generate_analysis_report(project)
            asset = Asset.query.filter_by(id=report_entry.pdf_asset_id).first()
            if asset:
                blob = _load_asset_bytes(asset_id=str(asset.id), asset_path=asset.path)
                if blob:
                    filename = f"relatorio_{project.slug}.pdf"
                    return send_file(
                        io.BytesIO(blob),
                        as_attachment=True,
                        download_name=filename,
                        mimetype='application/pdf',
                    )
            raise AnalysisReportError("Falha ao localizar o PDF gerado.")
        except AnalysisReportError as exc:
            current_app.logger.warning(
                "analysis_report.failure",
                extra={"project": project.slug if project else "sem-projeto", "error": str(exc)},
            )
            return str(exc), 400

    buffer = _build_user_snapshot_pdf(current_user)
    return send_file(buffer, as_attachment=True, download_name='relatorio_usuario.pdf', mimetype='application/pdf')


def _build_user_snapshot_pdf(user: User) -> io.BytesIO:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 50

    c.setFont('Helvetica-Bold', 16)
    c.drawString(40, y, f"Resumo do usuário — {user.username}")
    y -= 30
    c.setFont('Helvetica', 11)

    details = [
        f"Frequência: {user.frequencia or '—'} MHz",
        f"Potência de Transmissão: {user.transmission_power or '—'} W",
        f"Ganho da Antena: {user.antenna_gain or '—'} dBi",
        f"Perdas do sistema: {user.total_loss or '—'} dB",
        f"Direção/Tilt: {user.antenna_direction or '—'}° / {user.antenna_tilt or '—'}°",
        f"Coordenadas: {user.latitude or '—'}, {user.longitude or '—'}",
        f"Serviço: {user.servico or '—'}",
        f"Notas: {user.notes or 'Sem observações.'}",
    ]
    for line in details:
        c.drawString(40, y, line)
        y -= 16

    image_slots: Iterable[tuple[str, bytes | None]] = [
        ("Perfil de enlace", user.perfil_img),
        ("Cobertura histórica", user.cobertura_img),
        ("Diagrama Horizontal", user.antenna_pattern_img_dia_H),
        ("Diagrama Vertical", user.antenna_pattern_img_dia_V),
    ]
    x_offset = 40
    y_block = y - 10
    for label, blob in image_slots:
        if not blob:
            continue
        if y_block < 200:
            c.showPage()
            y_block = height - 120
            x_offset = 40
        c.setFont('Helvetica-Bold', 10)
        c.drawString(x_offset, y_block, label)
        try:
            reader = ImageReader(io.BytesIO(blob))
            c.drawImage(reader, x_offset, y_block - 110, width=160, height=110, preserveAspectRatio=True, mask='auto')
        except Exception:
            c.setFont('Helvetica-Oblique', 9)
            c.drawString(x_offset, y_block - 12, "Imagem indisponível.")
        x_offset += 180
        if x_offset > width - 160:
            x_offset = 40
            y_block -= 140
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

@bp.route('/calculate-distance', methods=['POST'])
def calculate_distance():
    data = request.get_json()
    start = data['start']; end = data['end']
    start_str = f"{start['lat']},{start['lng']}"
    end_str   = f"{end['lat']},{end['lng']}"

    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    params = {
            'origins': start_str,
            'destinations': end_str,
            'key': get_google_maps_key()
        }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        distance_matrix_data = response.json()
        if distance_matrix_data['rows'][0]['elements'][0]['status'] == 'OK':
            distance = distance_matrix_data['rows'][0]['elements'][0]['distance']['value']
            return jsonify({'distance': distance})
        else:
            return jsonify({'error': 'Não foi possível calcular a distância.'}), 400
    else:
        return jsonify({'error': 'Falha na requisição à Google Maps Distance Matrix API.'}), response.status_code

@bp.route('/mapa')
@login_required
def mapa():
    maps_key = get_google_maps_key()

    projects = (
        current_user.projects.order_by(Project.created_at.asc()).all()
        if hasattr(current_user, "projects") else []
    )

    project_slug = request.args.get("project")
    active_project = None

    if project_slug:
        try:
            active_project = _load_project_for_current_user(project_slug)
        except Exception:
            flash("Projeto não encontrado ou sem acesso.", "error")
            return redirect(url_for("projects.list_projects"))
    if not active_project:
        session_project = _active_project_from_session()
        if session_project:
            active_project = session_project
    if not active_project and projects:
        active_project = projects[0]

    # Se o usuário não tem posição definida, redireciona para configurar
    if current_user.latitude is None or current_user.longitude is None:
        flash("Por favor, defina a posição da torre primeiro.", "error")
        target_slug = active_project.slug if active_project else (projects[0].slug if projects else None)
        if target_slug:
            return redirect(url_for("ui.calcular_cobertura", project=target_slug))
        return redirect(url_for("ui.calcular_cobertura"))

    # Coordenadas iniciais: preferir as do projeto (se existirem), senão usar as do usuário
    start_coords = {"lat": current_user.latitude, "lng": current_user.longitude}
    if active_project and getattr(active_project, "settings", None):
        project_lat = active_project.settings.get("latitude")
        project_lng = active_project.settings.get("longitude")
        if project_lat is not None and project_lng is not None:
            start_coords = {"lat": project_lat, "lng": project_lng}

    _remember_active_project(active_project)
    return render_template(
        "mapa.html",
        start_coords=start_coords,
        maps_api_key=maps_key,
        project=active_project,
        projects=projects,
    )


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user is None:
            error = 'Usuário não existe.'
            return render_template('index.html', error=error)
        elif not user.check_password(password):
            error = 'Senha incorreta.'
            return render_template('index.html', error=error)
        elif not user.is_active:
            error = 'Conta desativada. Entre em contato com o suporte.'
            return render_template('index.html', error=error)
        elif (not user.is_email_confirmed) and not current_app.config.get('ALLOW_UNCONFIRMED', False):
            flash('Confirme o seu e-mail para acessar a plataforma.', 'warning')
            return render_template('index.html', error='Confirmação de e-mail pendente.')
        login_user(user)
        flash('Login realizado com sucesso.', 'success')
        return redirect(url_for('ui.home'))
    return render_template('index.html')

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email    = request.form['email'].strip().lower()
        password = request.form['password']

        existing_user  = User.query.filter_by(username=username).first()
        existing_email = User.query.filter_by(email=email).first()

        if existing_user:
            return render_template('register.html', error="Usuário já existe.")
        if existing_email:
            return render_template('register.html', error="E-mail já cadastrado.")

        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.flush()
        _create_default_project(new_user)
        db.session.commit()

        token = generate_token(new_user.email, 'confirm')
        confirm_url = url_for('ui.confirm_email', token=token, _external=True)
        send_email(
            "Confirme sua conta ATX Coverage",
            new_user.email,
            "email/confirm_email.html",
            "email/confirm_email.txt",
            user=new_user,
            confirm_url=confirm_url,
        )
        flash('Cadastro realizado! Verifique seu e-mail para confirmar a conta.', 'success')
        return redirect(url_for('ui.index'))

    return render_template('register.html')


@bp.route('/auth/confirm/<token>')
def confirm_email(token):
    max_age = current_app.config.get('EMAIL_CONFIRM_MAX_AGE', 86400)
    email = load_token(token, max_age=max_age, expected_purpose='confirm')
    if not email:
        flash('Link de confirmação inválido ou expirado.', 'error')
        return redirect(url_for('ui.index'))

    user = User.query.filter_by(email=email.lower()).first()
    if user is None:
        flash('Conta não encontrada para o e-mail informado.', 'error')
        return redirect(url_for('ui.index'))

    if not user.is_email_confirmed:
        user.is_email_confirmed = True
        user.updated_at = datetime.utcnow()
        db.session.commit()
        flash('E-mail confirmado com sucesso!', 'success')
    else:
        flash('Sua conta já estava confirmada.', 'info')

    if not current_user.is_authenticated:
        login_user(user)
    return redirect(url_for('ui.home'))


@bp.route('/auth/resend-confirmation', methods=['GET', 'POST'])
def resend_confirmation():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and not user.is_email_confirmed:
            token = generate_token(user.email, 'confirm')
            confirm_url = url_for('ui.confirm_email', token=token, _external=True)
            send_email(
                "Confirme sua conta ATX Coverage",
                user.email,
                "email/confirm_email.html",
                "email/confirm_email.txt",
                user=user,
                confirm_url=confirm_url,
            )
        flash('Se o e-mail estiver cadastrado e pendente de confirmação, reenviamos as instruções.', 'info')
        return redirect(url_for('ui.index'))
    return render_template('auth/resend_confirmation.html')


@bp.route('/auth/request-reset', methods=['GET', 'POST'])
def request_password_reset():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_token(user.email, 'reset')
            reset_url = url_for('ui.reset_password', token=token, _external=True)
            send_email(
                "Redefina sua senha ATX Coverage",
                user.email,
                "email/reset_password.html",
                "email/reset_password.txt",
                user=user,
                reset_url=reset_url,
            )
        flash('Se o e-mail estiver cadastrado, enviaremos instruções de redefinição.', 'info')
        return redirect(url_for('ui.index'))
    return render_template('auth/request_reset.html')


@bp.route('/auth/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    max_age = current_app.config.get('PASSWORD_RESET_MAX_AGE', 7200)
    email = load_token(token, max_age=max_age, expected_purpose='reset')
    if not email:
        flash('Token de redefinição inválido ou expirado.', 'error')
        return redirect(url_for('ui.index'))

    user = User.query.filter_by(email=email.lower()).first()
    if user is None:
        flash('Usuário não encontrado.', 'error')
        return redirect(url_for('ui.index'))

    error = None
    if request.method == 'POST':
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        if password != confirm_password:
            error = 'As senhas não coincidem.'
        elif len(password) < 8:
            error = 'A senha deve possuir pelo menos 8 caracteres.'
        else:
            user.set_password(password)
            user.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Senha atualizada com sucesso! Faça login com a nova credencial.', 'success')
            return redirect(url_for('ui.index'))
    return render_template('auth/reset_password.html', token=token, error=error)


@bp.route('/home')
@login_required
def home():
    try:
        projects = current_user.projects.order_by(Project.created_at.desc()).all()
    except Exception:
        projects = []

    requested_slug = request.args.get('project')
    selected_project = None
    if requested_slug:
        selected_project = next((p for p in projects if p.slug == requested_slug), None)
    if not selected_project:
        session_project = _active_project_from_session()
        if session_project:
            selected_project = next((p for p in projects if p.slug == session_project.slug), None)
    if not selected_project and projects:
        selected_project = projects[0]
    _remember_active_project(selected_project)

    project_settings = _project_settings_with_dynamic(selected_project) if selected_project else {}

    # snapshot já contempla merge entre job e artifacts;
    # usamos o helper para não duplicar lógica com outras telas.
    coverage_snapshot = _latest_coverage_snapshot(selected_project) if selected_project else None
    if selected_project and selected_project.settings:
        # alinhar o snapshot (lastCoverage) ao último save de coordenadas
        base_settings = dict(selected_project.settings or {})
        tx_lat = base_settings.get('latitude')
        tx_lon = base_settings.get('longitude')
        tx_name = base_settings.get('txLocationName')
        tx_elev = base_settings.get('txElevation')
        if coverage_snapshot:
            if tx_lat is not None and tx_lon is not None:
                center = coverage_snapshot.get('center') or coverage_snapshot.get('tx_location') or {}
                center = dict(center)
                center['lat'] = tx_lat
                center['lng'] = tx_lon
                coverage_snapshot['center'] = center
                coverage_snapshot['tx_location'] = center
            if tx_name:
                coverage_snapshot['tx_location_name'] = tx_name
            if tx_elev is not None:
                coverage_snapshot['tx_site_elevation'] = tx_elev

    def _value_from_sources(keys, user_attr=None):
        """Busca o primeiro valor válido considerando settings salvos,
        payload da última cobertura e, por fim, o atributo do usuário."""
        keys_list = keys if isinstance(keys, (list, tuple)) else [keys]
        sources = []
        if isinstance(project_settings, dict):
            sources.append(project_settings)
        if isinstance(coverage_snapshot, dict):
            request_payload = coverage_snapshot.get('request')
            if isinstance(request_payload, dict):
                sources.append(request_payload)
            sources.append(coverage_snapshot)
        for source in sources:
            for key in keys_list:
                if key in source:
                    value = source.get(key)
                    if value not in (None, '', []):
                        return value
        if user_attr:
            return getattr(current_user, user_attr, None)
        return None

    def _format_metric(label, value, unit=None, precision=2):
        if value is None or value == '':
            return {'label': label, 'value': '—', 'is_empty': True}
        if isinstance(value, (int, float)):
            formatted = f"{float(value):.{precision}f}"
            if '.' in formatted:
                formatted = formatted.rstrip('0').rstrip('.')
        else:
            formatted = str(value)
        if unit:
            formatted = f"{formatted} {unit}"
        return {'label': label, 'value': formatted, 'is_empty': False}

    def _asset_url(asset_id):
        if not (selected_project and asset_id):
            return None
        asset = Asset.query.filter_by(id=asset_id, project_id=selected_project.id).first()
        if not _asset_file_exists(asset):
            return None
        try:
            return url_for('projects.asset_preview', slug=selected_project.slug, asset_id=asset_id)
        except Exception:
            return None

    def _encode_image(blob):
        if not blob:
            return None
        return base64.b64encode(blob).decode('utf-8')

    def _sanitize_numeric_string(value: str):
        cleaned = value.strip()
        for suffix in ('dBm', 'dBµV/m', 'dBuV/m', 'dBuv/m', 'km', 'm', 'MHz', 'kW', 'W'):
            if cleaned.lower().endswith(suffix.lower()):
                cleaned = cleaned[: -len(suffix)]
        cleaned = cleaned.replace(',', '.')
        cleaned = re.sub(r'[^0-9\-\.+eE]', '', cleaned)
        return cleaned

    def _coerce_float(value):
        if value in (None, '', [], '—'):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = _sanitize_numeric_string(value)
            if cleaned in ('', '-', '.', '-.', '+'):
                return None
            try:
                return float(cleaned)
            except (TypeError, ValueError):
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_receivers(receivers):
        normalized = []
        if not isinstance(receivers, list):
            return normalized
        for idx, raw in enumerate(receivers):
            if not isinstance(raw, dict):
                continue
            label = raw.get('label') or raw.get('name') or f'RX {idx + 1}'
            location = raw.get('location') if isinstance(raw.get('location'), dict) else {}
            lat = raw.get('lat')
            if lat is None:
                lat = location.get('lat')
            lng = raw.get('lng')
            if lng is None:
                lng = location.get('lng') or location.get('lon')
            field_value = _coerce_float(
                raw.get('field_strength_dbuv_m')
                or raw.get('field_dbuv')
                or raw.get('field')
            )
            power_value = _coerce_float(
                raw.get('power_dbm') or raw.get('received_power_dbm') or raw.get('power')
            )
            distance_value = _coerce_float(raw.get('distance_km') or raw.get('distance'))
            altitude_value = _coerce_float(raw.get('altitude_m') or location.get('altitude'))
            normalized.append({
                'label': label,
                'municipality': raw.get('municipality') or location.get('municipality'),
                'coordinates': {'lat': lat, 'lng': lng} if lat is not None and lng is not None else None,
                'field': field_value,
                'power': power_value,
                'distance': distance_value,
                'altitude': altitude_value,
                'quality': raw.get('quality') or raw.get('status'),
            })
        return normalized

    freq_mhz = _value_from_sources(['frequency', 'frequencia'], 'frequencia')
    tx_power_w = _value_from_sources(['transmissionPower', 'transmission_power'], 'transmission_power')
    antenna_gain_dbi = _value_from_sources(['antennaGain', 'antenna_gain'], 'antenna_gain')
    rx_gain_dbi = _value_from_sources(['rxGain', 'rx_gain'], 'rx_gain')
    tower_height_m = _value_from_sources(['towerHeight', 'tower_height'], 'tower_height')
    rx_height_m = _value_from_sources(['rxHeight', 'rx_height'], 'rx_height')
    total_loss_db = _value_from_sources(['Total_loss', 'total_loss', 'totalLoss'], 'total_loss')
    direction_deg = _value_from_sources(['antennaDirection'], 'antenna_direction')
    tilt_deg = _value_from_sources(['antennaTilt'], 'antenna_tilt')
    time_percentage = _value_from_sources(['timePercentage'], 'time_percentage')
    polarization = _value_from_sources(['polarization'], 'polarization')
    service_type = _value_from_sources(['serviceType', 'servico'], 'servico')
    propagation_model = _value_from_sources(['propagationModel'], 'propagation_model')
    temperature_val = _value_from_sources(['temperature'], 'temperature_k')
    pressure_hpa = _value_from_sources(['pressure'], 'pressure_hpa')
    water_density = _value_from_sources(['waterDensity'], 'water_density')
    latitude = _value_from_sources(['latitude'], 'latitude')
    longitude = _value_from_sources(['longitude'], 'longitude')
    tx_site_elevation = _value_from_sources(['tx_site_elevation', 'txSiteElevation'], 'tx_site_elevation')
    tx_location_name = _value_from_sources(['tx_location_name', 'txLocationName'], 'tx_location_name')

    if temperature_val is not None and temperature_val > 200:
        temperature_display = temperature_val - 273.15
    else:
        temperature_display = temperature_val

    coverage_engine = None
    if isinstance(coverage_snapshot, dict):
        coverage_engine = coverage_snapshot.get('engine')
    if not coverage_engine:
        coverage_engine = project_settings.get('coverageEngine')

    coverage_radius_km = None
    if isinstance(coverage_snapshot, dict):
        coverage_radius_km = (
            coverage_snapshot.get('radius_km')
            or coverage_snapshot.get('requested_radius_km')
            or coverage_snapshot.get('radius')
        )
    if coverage_radius_km is None:
        coverage_radius_km = project_settings.get('radius')

    coverage_generated_at = coverage_snapshot.get('generated_at') if isinstance(coverage_snapshot, dict) else None
    coverage_generated_at_dt = _parse_iso_datetime(coverage_generated_at)

    tx_coordinates = None
    if isinstance(coverage_snapshot, dict):
        tx_coordinates = coverage_snapshot.get('center') or coverage_snapshot.get('tx_location')
    if not tx_coordinates and latitude is not None and longitude is not None:
        tx_coordinates = {'lat': latitude, 'lng': longitude}
    if isinstance(tx_coordinates, dict):
        lat_val = _coerce_float(tx_coordinates.get('lat') or tx_coordinates.get('latitude'))
        lon_val = _coerce_float(tx_coordinates.get('lng') or tx_coordinates.get('lon') or tx_coordinates.get('longitude'))
        tx_coordinates = {'lat': lat_val, 'lng': lon_val}

    center_metrics = coverage_snapshot.get('center_metrics') if isinstance(coverage_snapshot, dict) else {}
    loss_components = coverage_snapshot.get('loss_components') if isinstance(coverage_snapshot, dict) else {}
    gain_components = coverage_snapshot.get('gain_components') if isinstance(coverage_snapshot, dict) else {}
    rt3d_diagnostics = coverage_snapshot.get('rt3d_diagnostics') if isinstance(coverage_snapshot, dict) else None

    erp_dbm = None
    if tx_power_w not in (None, ''):
        try:
            tx_power_dbm = 10 * math.log10(max(float(tx_power_w), 1e-6) * 1000)
            erp_dbm = tx_power_dbm + float(antenna_gain_dbi or 0.0) - float(total_loss_db or 0.0)
        except Exception:
            erp_dbm = None
    erp_kw = None
    if erp_dbm is not None:
        try:
            erp_w = 10 ** (erp_dbm / 10) / 1000.0
            erp_kw = erp_w / 1000.0
        except Exception:
            erp_kw = None

    technical_sections = []
    technical_sections.append({
        'title': 'Transmissor',
        'rows': [
            _format_metric('Frequência', freq_mhz, 'MHz'),
            _format_metric('Potência TX', tx_power_w, 'W'),
            _format_metric('ERP (dBm)', erp_dbm, 'dBm'),
            _format_metric('ERP (kW)', erp_kw, 'kW', precision=3),
            _format_metric('Ganho TX', antenna_gain_dbi, 'dBi'),
            _format_metric('Ganho RX', rx_gain_dbi, 'dBi'),
        ],
    })
    technical_sections.append({
        'title': 'Estrutura física',
        'rows': [
            _format_metric('Altura da torre', tower_height_m, 'm'),
            _format_metric('Altura do receptor', rx_height_m, 'm'),
            _format_metric('Perdas do sistema', total_loss_db, 'dB'),
            _format_metric('Azimute da antena', direction_deg, '°'),
            _format_metric('Tilt elétrico', tilt_deg, '°'),
        ],
    })
    technical_sections.append({
        'title': 'Ambiente e serviço',
        'rows': [
            _format_metric('Cenário de propagação', propagation_model, None),
            _format_metric('Serviço', service_type, None),
            _format_metric('Polarização', polarization, None),
            _format_metric('Tempo (%)', time_percentage, '%'),
            _format_metric('Temperatura', temperature_display, '°C'),
            _format_metric('Pressão', pressure_hpa, 'hPa'),
            _format_metric('Densidade de vapor', water_density, 'g/m³'),
        ],
    })

    coverage_metrics_panel = [
        _format_metric('Perda combinada', center_metrics.get('combined_loss_center_db'), 'dB'),
        _format_metric('Ganho efetivo', center_metrics.get('effective_gain_center_db'), 'dB'),
        _format_metric('Distância ao centro', center_metrics.get('distance_center_km'), 'km', precision=3),
    ]

    coverage_loss_panel = []
    for key, label in (
        ('L_b', 'Atenuação total (L_b)'),
        ('L_bd', 'Difração (L_bd)'),
        ('L_bs', 'Espalhamento (L_bs)'),
    ):
        component = loss_components.get(key) if isinstance(loss_components, dict) else None
        center_value = component.get('center') if isinstance(component, dict) else None
        coverage_loss_panel.append(_format_metric(label, center_value, component.get('unit') if isinstance(component, dict) else 'dB'))

    gain_panel = [
        _format_metric('Ganho de pico', gain_components.get('base_gain_dbi'), 'dBi'),
        _format_metric('Ajuste horizontal', gain_components.get('horizontal_adjustment_db_min'), 'dB'),
        _format_metric('Ajuste vertical', gain_components.get('vertical_adjustment_db'), 'dB'),
        _format_metric('Horizonte vertical', gain_components.get('vertical_horizon_db'), 'dB'),
    ]

    tx_site_elevation = _coerce_float(tx_site_elevation)
    coverage_radius_km = _coerce_float(coverage_radius_km)

    coverage_artifacts = {
        'map_snapshot_url': _asset_url(coverage_snapshot.get('map_snapshot_asset_id')) if coverage_snapshot else None,
        'heatmap_url': _asset_url(coverage_snapshot.get('asset_id')) if coverage_snapshot else None,
        'colorbar_url': _asset_url(coverage_snapshot.get('colorbar_asset_id')) if coverage_snapshot else None,
        'rt3d_viewer_url': url_for('ui.rt3d_viewer', project=selected_project.slug)
        if (selected_project and isinstance(coverage_snapshot, dict) and coverage_snapshot.get('rt3d_scene'))
        else None,
        'kml_url': (
            url_for('ui.download_coverage_kml', slug=selected_project.slug)
            if selected_project and coverage_snapshot
            else None
        ),
    }

    receivers_summary = _normalize_receivers(coverage_snapshot.get('receivers') if isinstance(coverage_snapshot, dict) else [])[:4] if coverage_snapshot else []

    dataset_sources_preview = []
    reports_preview = []
    if selected_project:
        dataset_sources_preview = sorted(
            selected_project.dataset_sources,
            key=lambda ds: ds.created_at or datetime.min,
            reverse=True,
        )[:4]
        reports_preview = sorted(
            selected_project.reports,
            key=lambda rp: rp.created_at or datetime.min,
            reverse=True,
        )[:3]

    request_highlights = []
    request_payload = coverage_snapshot.get('request') if isinstance(coverage_snapshot, dict) else {}
    request_label_map = {
        'coverageEngine': ('Motor solicitado', None),
        'radius': ('Raio máximo', 'km'),
        'minScale': ('Escala mínima', 'dBµV/m'),
        'maxScale': ('Escala máxima', 'dBµV/m'),
        'gridResolution': ('Resolução do grid', 'm'),
        'rt3dReflectionGain': ('Ganho de reflexão', 'dB'),
        'rt3dRayDepth': ('Máx. reflexões', None),
        'rt3dDiffractionOrder': ('Ordem de difração', None),
        'rt3dUseBuildings': ('Uso das edificações', None),
    }
    if isinstance(request_payload, dict):
        for key, (label, unit) in request_label_map.items():
            if key in request_payload:
                request_highlights.append(_format_metric(label, request_payload.get(key), unit))

    preference_highlights = []
    for key, label, unit in (
        ('coverageEngine', 'Motor preferido', None),
        ('timePercentage', 'Tempo de disponibilidade', '%'),
        ('p452Version', 'Versão ITU-R P.452', None),
        ('propagationModel', 'Cenário', None),
        ('serviceType', 'Serviço', None),
    ):
        preference_highlights.append(_format_metric(label, project_settings.get(key), unit))

    image_gallery = {
        'profile': _encode_image(current_user.perfil_img),
        'legacy_coverage': _encode_image(current_user.cobertura_img),
        'diagram_h': _encode_image(current_user.antenna_pattern_img_dia_H),
        'diagram_v': _encode_image(current_user.antenna_pattern_img_dia_V),
    }

    project_overview = {
        'project': selected_project,
        'engine': coverage_engine,
        'radius_km': coverage_radius_km,
        'location_name': tx_location_name,
        'coordinates': tx_coordinates,
        'tx_site_elevation': tx_site_elevation,
        'generated_at_dt': coverage_generated_at_dt,
        'generated_at_raw': coverage_generated_at,
        'assets_count': len(selected_project.assets) if selected_project else 0,
        'reports_count': len(selected_project.reports) if selected_project else 0,
        'jobs_count': len(selected_project.coverage_jobs) if selected_project else 0,
        'has_aoi': bool(selected_project and selected_project.aoi_geojson),
    }

    project_actions = []
    coverage_url = url_for('ui.calcular_cobertura', project=selected_project.slug) if selected_project else url_for('ui.calcular_cobertura')
    mapa_url = url_for('ui.mapa', project=selected_project.slug) if selected_project else url_for('ui.mapa')
    dados_url = url_for('ui.visualizar_dados_salvos', project=selected_project.slug) if selected_project else url_for('ui.visualizar_dados_salvos')
    project_actions.append({'label': 'Planejar cobertura', 'description': 'Atualize parâmetros e gere novas manchas.', 'url': coverage_url, 'variant': 'brand'})
    project_actions.append({'label': 'Abrir mapa & receptores', 'description': 'Visualize a última mancha em campo e gerencie receptores.', 'url': mapa_url})
    project_actions.append({'label': 'Dados e artefatos', 'description': 'Revise imagens, perfis e notas consolidadas.', 'url': dados_url})

    rt3d_panel = []
    if isinstance(rt3d_diagnostics, dict):
        rt3d_panel = [
            _format_metric('Pontuação de reflexões', rt3d_diagnostics.get('reflection_gain'), 'dB'),
            _format_metric('Média de multipath', rt3d_diagnostics.get('multipath_mean'), 'dB'),
            _format_metric('Taxa de oclusão', rt3d_diagnostics.get('occlusion_rate'), None),
            _format_metric('Altura mediana', rt3d_diagnostics.get('median_height'), 'm'),
        ]

    return render_template(
        'home.html',
        projects=projects,
        selected_project=selected_project,
        project_overview=project_overview,
        technical_sections=technical_sections,
        coverage_metrics_panel=coverage_metrics_panel,
        coverage_loss_panel=coverage_loss_panel,
        gain_panel=gain_panel,
        coverage_artifacts=coverage_artifacts,
        receivers_summary=receivers_summary,
        dataset_sources=dataset_sources_preview,
        reports_preview=reports_preview,
        image_gallery=image_gallery,
        request_highlights=request_highlights,
        preference_highlights=preference_highlights,
        notes_value=current_user.notes or '',
        project_actions=project_actions,
        rt3d_panel=rt3d_panel,
        has_projects=bool(projects),
        new_project_url=url_for('projects.new_project'),
        projects_list_url=url_for('projects.list_projects'),
        coverage_snapshot=coverage_snapshot,
    )

# -------- Antena: carregar/mostrar diagramas --------

@bp.route('/carregar_imgs', methods=['GET'])
@login_required
def carregar_imgs():
    project_slug = request.args.get('project')
    project = None
    if project_slug:
        project = _load_project_for_current_user(project_slug)

    # Fallback to user if no project (legacy behavior) or if project has no pattern
    # But ideally we want to enforce project context.
    # Let's check if project has an antenna pattern asset.
    
    file_content = None
    direction = None
    tilt = None
    
    if project:
        # Check for antenna pattern asset
        asset = Asset.query.filter_by(
            project_id=project.id,
            type=AssetType.other
        ).filter(Asset.meta['kind'].astext == 'antenna_pattern').first()
        
        if asset:
            file_content = _asset_bytes(asset)
            if file_content:
                file_content = file_content.decode('latin1', errors='ignore')
        
        settings = project.settings or {}
        direction = settings.get('antennaDirection')
        tilt = settings.get('antennaTilt')
    
    if not file_content:
        # Fallback to legacy user profile
        user = current_user
        direction = user.antenna_direction
        tilt = user.antenna_tilt
        if user.antenna_pattern:
            file_content = user.antenna_pattern.decode('latin1', errors='ignore')

    if not file_content:
        return jsonify({'error': 'Nenhum diagrama salvo.'}), 404

    # Parser universal
    horizontal_data, vertical_data, meta = parse_pat(file_content)

    # Horizontal: original vs rotacionado (se houver direção)
    if direction is not None:
        rotation_index = int(direction / (360 / len(horizontal_data)))
        rotated_data   = np.roll(horizontal_data, rotation_index)
        horizontal_image_base64, _, _ = generate_dual_polar_plot(horizontal_data, rotated_data, direction)
    else:
        horizontal_image_base64, _, _ = generate_polar_plot(horizontal_data)

    # Vertical: com/sem tilt
    angles = np.linspace(-90, 90, len(vertical_data), endpoint=True)
    if tilt is not None:
        vertical_image_base64, _ = generate_dual_rectangular_plot(vertical_data, angles, tilt)
    else:
        vertical_image_base64, _ = generate_rectangular_plot(vertical_data)

    return jsonify({
        'fileContent': file_content,
        'horizontal_image_base64': horizontal_image_base64,
        'vertical_image_base64': vertical_image_base64
    })

@bp.route('/salvar_diagrama', methods=['POST'])
@login_required
def salvar_diagrama():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    direction = request.form.get('direction')
    tilt      = request.form.get('tilt')
    project_slug = request.form.get('project') or request.args.get('project')

    try:
        direction = float(direction) if direction and direction.strip() != '' else None
    except ValueError:
        direction = None
    try:
        tilt = float(tilt) if tilt and tilt.strip() != '' else None
    except ValueError:
        tilt = None

    if project_slug:
        project = _load_project_for_current_user(project_slug)
        success, message = _save_diagram_to_project(project, file, direction, tilt)
    else:
        # Legacy fallback
        user = current_user
        success, message = salvar_diagrama_usuario(user, file, direction, tilt)

    if success:
        return jsonify({'message': 'File and settings saved successfully'})
    else:
        return jsonify({'error': message}), 500


@bp.route('/upload_diagrama', methods=['POST'])
@login_required
def gerardiagramas():
    tilt = request.form.get('tilt', type=float)
    direction = request.form.get('direction', type=float)
    project_slug = request.form.get('project') or request.args.get('project')
    file = request.files.get('file')
    
    if not file:
        return jsonify({'error': 'No file provided'}), 400

    # Save logic
    if project_slug:
        project = _load_project_for_current_user(project_slug)
        success, message = _save_diagram_to_project(project, file, direction, tilt)
    else:
        success, message = salvar_diagrama_usuario(current_user, file, direction, tilt)
        
    if not success:
        return jsonify({'error': message}), 500

    # Read back for preview
    file.seek(0)
    file_content = file.read().decode('latin1', errors='ignore')
    horizontal_data, vertical_data, meta = parse_pat(file_content)

    if direction is None:
        horizontal_image_base64, _, _ = generate_polar_plot(horizontal_data)
    else:
        rotation_index = int(direction / (360 / len(horizontal_data)))
        rotated_data   = np.roll(horizontal_data, rotation_index)
        horizontal_image_base64, _, _ = generate_dual_polar_plot(horizontal_data, rotated_data, direction)

    angles_v = np.linspace(-90, 90, len(vertical_data), endpoint=True)
    if tilt is None:
        vertical_image_base64, _ = generate_rectangular_plot(vertical_data)
    else:
        vertical_image_base64, _ = generate_dual_rectangular_plot(vertical_data, angles_v, tilt)

    return jsonify({
        'horizontal_image_base64': horizontal_image_base64,
        'vertical_image_base64': vertical_image_base64
    })

# -------- Plots H/V --------

def generate_polar_plot(data):
    azimutes = np.linspace(0, 2 * np.pi, len(data))
    fig = plt.figure(figsize=(10, 10))
    ax = plt.subplot(111, polar=True)
    ax.plot(azimutes, data, label='Horizontal Radiation Pattern')

    threshold = 1/np.sqrt(2)
    indices = np.where(data <= threshold)[0]
    if len(indices) > 1:
        idx_first = indices[0]; idx_last = indices[-1]
        ax.plot([azimutes[idx_first], azimutes[idx_first]], [0, data[idx_first]], 'k-', linewidth=2)
        ax.plot([azimutes[idx_last],  azimutes[idx_last]],  [0, data[idx_last]],  'k-', linewidth=2)
        angle_first_deg = np.degrees(azimutes[idx_first])
        angle_last_deg  = np.degrees(azimutes[idx_last])
        hpbw = 360 - angle_last_deg + angle_first_deg
        ax.text(0.97, 0.99, f'HPBW: {hpbw:.2f}°', transform=ax.transAxes,
                ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

        front_attenuation = data[np.argmin(np.abs(azimutes - 0))]
        back_attenuation  = data[np.argmin(np.abs(azimutes - np.pi))]
        fbr = 20 * math.log10(max(front_attenuation,1e-6) / max(back_attenuation,1e-6))
        ax.text(0.97, 0.95, f'F_B Ratio: {fbr:.2f} dB', transform=ax.transAxes,
                ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    peak_to_peak = np.ptp(data)
    ax.text(0.97, 0.91, f'Peak2Peak: {peak_to_peak:.2f} E/Emax', transform=ax.transAxes,
            ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    directivity_dB = calculate_directivity(data, 'h')
    data_table = [{"azimuth": f"{np.degrees(a):.1f}°", "gain": f"{g:.3f}"} for a, g in zip(azimutes, data)]
    
    ax.text(0.97, 0.87, f'Directivity: {directivity_dB:.2f} dB', transform=ax.transAxes,
            ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_title('E/Emax')
    plt.ylim(0, 1)
    plt.grid(True)

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', bbox_inches='tight')
    img_buffer.seek(0)
    img_bytes = img_buffer.getvalue()
    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
    img_buffer.close()
    plt.close(fig)
    
    return img_base64, json.dumps(data_table), img_bytes

def generate_dual_polar_plot(original_data, rotated_data, direction):
    azimutes = np.linspace(0, 2 * np.pi, len(original_data))
    fig = plt.figure(figsize=(10, 10))
    ax = plt.subplot(111, polar=True)
    ax.plot(azimutes, original_data, linestyle='dashed', color='red', label='Original Pattern')
    ax.plot(azimutes, rotated_data, color='blue', label=f'Rotated Pattern to {direction}°')

    threshold = 1/np.sqrt(2)
    indices = np.where(original_data <= threshold)[0]
    if len(indices) > 1:
        idx_first = indices[0]; idx_last = indices[-1]
        ax.plot([azimutes[idx_first], azimutes[idx_first]], [0, original_data[idx_first]], 'k-', linewidth=2)
        ax.plot([azimutes[idx_last],  azimutes[idx_last]],  [0, original_data[idx_last]],  'k-', linewidth=2)
        angle_first_deg = np.degrees(azimutes[idx_first])
        angle_last_deg  = np.degrees(azimutes[idx_last])
        hpbw = 360 - angle_last_deg + angle_first_deg
        ax.text(0.97, 0.99, f'HPBW: {hpbw:.2f}°', transform=ax.transAxes,
                ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    front_attenuation = original_data[np.argmin(np.abs(azimutes - 0))]
    back_attenuation  = original_data[np.argmin(np.abs(azimutes - np.pi))]
    fbr = 20 * math.log10(max(front_attenuation,1e-6) / max(back_attenuation,1e-6))
    ax.text(0.97, 0.95, f'F_B Ratio: {fbr:.2f} dB', transform=ax.transAxes,
            ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    peak_to_peak = np.ptp(original_data)
    ax.text(0.97, 0.91, f'Peak2Peak: {peak_to_peak:.2f} E/Emax', transform=ax.transAxes,
            ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    directivity_dB = calculate_directivity(original_data, 'h')
    ax.text(0.97, 0.87, f'Directivity: {directivity_dB:.2f} dB', transform=ax.transAxes,
            ha='left', va='top', bbox=dict(facecolor='white', alpha=0.8))

    data_table = [{"azimuth": f"{np.degrees(a):.1f}°", "gain": f"{g:.3f}"} for a, g in zip(azimutes, rotated_data)]
    
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_title('Antenna Horizontal Radiation Pattern')
    plt.ylim(0, 1)
    ax.grid(True)
    ax.legend(loc='upper left')

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', bbox_inches='tight')
    img_buffer.seek(0)
    img_bytes = img_buffer.getvalue()
    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
    img_buffer.close()
    plt.close()
    
    return img_base64, json.dumps(data_table), img_bytes

# --- util para HPBW em campo ---
def _hpbw_from_field(angles_deg, field_norm):
    ang = np.asarray(angles_deg, float)
    f   = np.asarray(field_norm,  float)
    if ang.ndim != 1 or f.ndim != 1 or ang.size != f.size or ang.size < 3:
        return np.nan
    level = 1/np.sqrt(2)  # 0.707
    peak_idx = int(np.nanargmax(f))

    left  = np.where(f[:peak_idx]  <= level)[0]
    right = np.where(f[peak_idx:] <= level)[0] + peak_idx

    def interp_cross(i1, i2):
        x1, y1 = ang[i1], f[i1]
        x2, y2 = ang[i2], f[i2]
        if x1 == x2: return x1
        w = (level - y1) / (y2 - y1)
        return x1 + w*(x2 - x1)

    if left.size > 0:
        i2 = left[-1]
        i1 = i2 + 1
        ang_left = interp_cross(i1, i2)
    else:
        ang_left = ang[0]

    if right.size > 0:
        i2 = right[0]
        i1 = i2 - 1
        ang_right = interp_cross(i1, i2)
    else:
        ang_right = ang[-1]

    return float(ang_right - ang_left)

def generate_rectangular_plot(vert_lin):
    angles = np.linspace(-90, 90, len(vert_lin), endpoint=True)
    v = np.asarray(vert_lin, float)
    # Debug: log shape and sample values to help diagnose flat-line plots
    try:
        current_app.logger.debug(f"generate_rectangular_plot: angles_len={len(angles)}, v_shape={v.shape}, v_min={np.nanmin(v):.6g}, v_max={np.nanmax(v):.6g}")
        current_app.logger.debug(f"generate_rectangular_plot: v_sample={v[:10].tolist()}")
    except Exception:
        pass
    vmax = np.nanmax(v) if np.isfinite(v).any() else 1.0
    v = np.clip(v / (vmax if vmax > 0 else 1.0), 0.0, 1.0)

    directivity_dB = calculate_directivity(v, 'v')
    hpbw = _hpbw_from_field(angles, v)

    plt.figure(figsize=(10, 9))
    mask = np.isfinite(v)
    plt.plot(angles[mask], v[mask], label=f'Elevation (Directivity: {directivity_dB:.2f} dB)')

    if np.isfinite(hpbw):
        plt.annotate(f'HPBW: {hpbw:.2f}°', xy=(0.95, 0.95), xycoords='axes fraction',
                     ha='right', va='top', bbox=dict(boxstyle="round", fc="white", ec="black"))

    i0 = np.argmin(np.abs(angles))
    plt.annotate(f'E/Emax at 0°: {v[i0]*100:.1f}%',
                 xy=(0, v[i0]), xytext=(0, -40), textcoords='offset points',
                 arrowprops=dict(arrowstyle='->'), ha='center', va='top',
                 bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="b", lw=1.5))

    plt.xlabel('Elevation Angle (degrees)')
    plt.ylabel('E/Emax')
    plt.title('Elevation Pattern')
    plt.ylim(0, 1)
    # Ensure x-axis maps -90..+90 with clear ticks
    plt.xlim(-90, 90)
    plt.xticks(np.arange(-90, 91, 30))
    plt.grid(True)
    plt.legend()

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    plt.close()
    buffer.seek(0)
    img_bytes = buffer.getvalue()
    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
    buffer.close()
    return img_base64, img_bytes

def generate_dual_rectangular_plot(original_vert_lin, angles, tilt=None):
    base = np.asarray(original_vert_lin, float)
    # Debug: log shape and sample values to help diagnose flat-line plots
    try:
        current_app.logger.debug(f"generate_dual_rectangular_plot: angles_len={len(angles)}, base_shape={base.shape}, base_min={np.nanmin(base):.6g}, base_max={np.nanmax(base):.6g}")
        current_app.logger.debug(f"generate_dual_rectangular_plot: base_sample={base[:10].tolist()}")
    except Exception:
        pass
    vmax = np.nanmax(base) if np.isfinite(base).any() else 1.0
    base = np.clip(base / (vmax if vmax > 0 else 1.0), 0.0, 1.0)

    mod = base.copy()
    if tilt is not None:
        shift = int(np.round(tilt))  # ~1 amostra/°
        mod = np.roll(mod, shift)

    directivity_base = calculate_directivity(base, 'v')
    directivity_mod  = calculate_directivity(mod,  'v')
    hpbw_mod = _hpbw_from_field(angles, mod)

    plt.figure(figsize=(10, 9))
    plt.plot(angles, base, 'r--', label=f'Original (Dir: {directivity_base:.2f} dB)')
    plt.plot(angles, mod,  'b-', label=f'Tilted (Dir: {directivity_mod:.2f} dB)')

    if np.isfinite(hpbw_mod):
        plt.annotate(f'HPBW: {hpbw_mod:.2f}°', xy=(0.95, 0.95), xycoords='axes fraction',
                     ha='right', va='top', bbox=dict(boxstyle="round", fc="white", ec="black"))

    i0 = np.argmin(np.abs(angles))
    plt.annotate(f'E/Emax at 0°: {mod[i0]*100:.1f}%',
                 xy=(0, mod[i0]), xytext=(0, -40), textcoords='offset points',
                 arrowprops=dict(arrowstyle='->'), ha='center', va='top',
                 bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="b", lw=1.5))

    plt.xlabel('Elevation Angle (degrees)')
    plt.ylabel('E/Emax')
    plt.title('Dual Elevation Pattern Comparison')
    plt.legend()
    plt.ylim(0, 1)
    # Ensure x-axis maps -90..+90 with clear ticks
    try:
        plt.xlim(-90, 90)
        plt.xticks(np.arange(-90, 91, 30))
    except Exception:
        pass
    plt.grid(True)

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    plt.close()
    buffer.seek(0)
    img_bytes = buffer.getvalue()
    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
    buffer.close()
    return img_base64, img_bytes

# -------- Diretividade --------

def calculate_directivity(smoothed_data, tipo):
    if tipo == 'h':
        power_normalized = smoothed_data / np.max(smoothed_data)
        azimutes = np.linspace(0, 2 * np.pi, len(smoothed_data))
        integral = simpson(y=power_normalized, x=azimutes)
        directivity = 2 * np.pi / integral
        return 10 * np.log10(directivity)
    elif tipo == 'v':
        angles = np.linspace(-90, 90, len(smoothed_data), endpoint=True)
        power_normalized = smoothed_data / np.max(smoothed_data)
        radians_ang = np.deg2rad(angles)
        integral = simpson(y=power_normalized, x=radians_ang)
        directivity = np.pi / integral
        return 10 * np.log10(directivity)

# -------- Notas --------

@bp.route('/update-notes', methods=['POST'])
@login_required
def update_notes():
    notes = request.form.get('notes')
    if notes is not None:
        current_user.notes = notes
        db.session.commit()
        return jsonify({'message': 'Notas atualizadas com sucesso!'}), 200
    else:
        return jsonify({'error': 'Nenhuma nota fornecida.'}), 400


@bp.route('/projects/<slug>/regulator/payload', methods=['GET'])
@login_required
def regulatory_payload(slug):
    project = _load_project_for_current_user(slug)
    payload = build_default_payload(project)
    return jsonify({'project': project.slug, 'data': payload})

# -------- Elevação Google --------

@bp.route('/fetch-elevation', methods=['POST'])
def fetch_elevation():
    try:
        path_data = request.json['path']
        path_str  = '|'.join([f"{point['lat']},{point['lng']}" for point in path_data])
        url = f"https://maps.googleapis.com/maps/api/elevation/json?path={path_str}&samples=256&key={get_google_maps_key()}"

        response = requests.get(url)
        if response.status_code == 200:
            elevation_data = response.json()
            return jsonify(elevation_data)
        else:
            current_app.logger.error('Erro na API de Elevação: Status code {}'.format(response.status_code))
            return jsonify({"error": "Failed to fetch elevation data"}), 500
    except Exception as e:
        current_app.logger.error('Erro ao processar /fetch-elevation: {}'.format(e))
        return jsonify({"error": "Internal server error"}), 500

# -------- Utilitários geodésicos --------

def adjust_center_for_coverage(lon_center, lat_center, radius_km):
    original_location = (lat_center.to(u.deg).value, lon_center.to(u.deg).value)
    northern_point = geodesic(kilometers=radius_km).destination(original_location, bearing=0)
    return northern_point.longitude, northern_point.latitude

def _slug_for_filename(label: str | None) -> str:
    text = (label or 'rx').strip().lower()
    if not text:
        text = 'rx'
    return slugify(text) or 'rx'


def _persist_receiver_profile_asset(project: Project, receiver_label: str | None, image_bytes: bytes):
    pseudo_path = inline_asset_path('profiles', 'png')
    asset = Asset(
        project_id=project.id,
        type=AssetType.png,
        path=pseudo_path,
        mime_type='image/png',
        byte_size=len(image_bytes),
        data=image_bytes,
        meta={
            'kind': 'receiver_profile',
            'label': receiver_label,
            'generated_at': datetime.utcnow().isoformat(),
        },
    )
    db.session.add(asset)
    db.session.flush()
    return asset


def _upsert_project_receiver(project: Project, receiver_payload: dict):
    if project is None:
        return
    receiver_id = receiver_payload.get('id')
    if not receiver_id:
        return
    record = ProjectReceiver.query.filter_by(
        project_id=project.id,
        legacy_id=str(receiver_id),
    ).first()
    if not record:
        record = ProjectReceiver(project_id=project.id, legacy_id=str(receiver_id))
    record.label = receiver_payload.get('label') or record.label or receiver_id
    location = receiver_payload.get('location') or {}
    lat = receiver_payload.get('lat') or location.get('lat') or location.get('latitude')
    lon = (
        receiver_payload.get('lng')
        or receiver_payload.get('lon')
        or location.get('lng')
        or location.get('longitude')
    )
    try:
        record.latitude = float(lat) if lat is not None else None
    except (TypeError, ValueError):
        record.latitude = None
    try:
        record.longitude = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        record.longitude = None
    record.municipality = receiver_payload.get('municipality') or location.get('municipality') or location.get('city')
    record.state = receiver_payload.get('state') or location.get('state') or location.get('uf')
    record.summary = dict(receiver_payload)
    ibge_info = receiver_payload.get('ibge') or {}
    record.ibge_code = ibge_info.get('code') or receiver_payload.get('ibge_code')
    record.population = receiver_payload.get('population')
    record.population_year = receiver_payload.get('population_year')
    profile_asset_id = receiver_payload.get('profile_asset_id')
    record.profile_asset_id = profile_asset_id
    db.session.add(record)


def generate_coverage_image(lons, lats, _total_atten, radius_km, lon_center, lat_center):
    fig, ax = plt.subplots()
    atten_db = _total_atten.to(u.dB).value
    levels = np.linspace(atten_db.min(), atten_db.max(), 100)
    cim = ax.contourf(lons, lats, atten_db, levels=levels, cmap='rainbow')

    cax = fig.add_axes([0.85, 0.15, 0.05, 0.7])
    plt.colorbar(cim, cax=cax, orientation='vertical', label='Atenuação [dB]')

    earth_radius_km = 6371.0
    radius_degrees_lat = radius_km / (earth_radius_km * (np.pi/180))
    radius_degrees_lon = radius_km / (earth_radius_km * np.cos(np.radians(lat_center.to(u.deg).value)) * (np.pi/180))

    lon_center_adjusted = lon_center.to(u.deg).value
    lat_center_adjusted = lat_center.to(u.deg).value

    circle = plt.Circle((lon_center_adjusted, lat_center_adjusted),
                        max(radius_degrees_lon, radius_degrees_lat),
                        color='red', fill=False, linestyle='--')
    ax.add_artist(circle)

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', transparent=True)
    img_buffer.seek(0)
    plt.close(fig)
    return img_buffer

def calculate_bearing(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dLon = lng2 - lng1
    x = math.sin(dLon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dLon)
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing

def fresnel_zone_radius(d1, d2, wavelength):
    return np.sqrt(wavelength * d1 * d2 / (d1 + d2))


def _estimate_tile_zoom(bounds_payload):
    if not bounds_payload:
        return None, None
    try:
        north = float(bounds_payload.get('north'))
        south = float(bounds_payload.get('south'))
        east = float(bounds_payload.get('east'))
        west = float(bounds_payload.get('west'))
    except (TypeError, ValueError):
        return None, None
    lon_span = abs(east - west)
    lat_span = abs(north - south)
    span = max(lon_span, lat_span, 1e-6)
    approx_zoom = math.log2(360.0 / span)
    approx_zoom = max(0.0, min(18.0, approx_zoom))
    base_zoom = int(round(approx_zoom))
    min_zoom = max(0, base_zoom - 2)
    max_zoom = min(22, base_zoom + 4)
    if max_zoom < min_zoom:
        max_zoom = min_zoom
    return min_zoom, max_zoom


def _latlon_to_tile_indices(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(max(min(lat_deg, 85.05112878), -85.05112878))
    scale = 1 << zoom
    x = (lon_deg + 180.0) / 360.0 * scale
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * scale
    return int(math.floor(x)), int(math.floor(y))


def _build_tile_signal_stats(signal_level_dict, min_zoom, max_zoom):
    if not signal_level_dict or min_zoom is None or max_zoom is None:
        return {}
    stats = {}
    for key, value in signal_level_dict.items():
        try:
            lat_str, lon_str = key.strip()[1:-1].split(',')
            lat = float(lat_str)
            lon = float(lon_str)
            field_val = float(value)
        except (ValueError, IndexError, AttributeError, TypeError):
            continue
        for zoom in range(int(min_zoom), int(max_zoom) + 1):
            x_idx, y_idx = _latlon_to_tile_indices(lat, lon, zoom)
            bucket = stats.setdefault(str(zoom), {})
            tile_key = f"{x_idx}/{y_idx}"
            entry = bucket.setdefault(tile_key, {"sum": 0.0, "count": 0})
            entry["sum"] += field_val
            entry["count"] += 1
    summary = {}
    for zoom_key, tiles in stats.items():
        cleaned = {}
        for tile_key, payload in tiles.items():
            if payload["count"]:
                cleaned[tile_key] = round(payload["sum"] / payload["count"], 2)
        if cleaned:
            summary[zoom_key] = cleaned
    return summary

# -------- Perfil (TX → RX) COM CORREÇÃO DA CURVATURA --------

@bp.route('/gerar_img_perfil', methods=['POST'])
@login_required
def gerar_img_perfil():
    data = request.get_json()
    start_coords = data['path'][0]
    end_coords   = data['path'][1]
    path = data['path']
    project_slug = data.get('projectSlug') or data.get('project_slug')
    project = None
    if project_slug:
        project = _load_project_for_current_user(project_slug)
    receiver_id = data.get('receiverId') or data.get('receiver_id')
    receiver_label = data.get('receiverLabel') or data.get('receiver_label')
    receiver_summary = data.get('summary') if isinstance(data.get('summary'), dict) else {}

    # ========= parâmetros TX/RX =========
    Ptx_W         = max(float(current_user.transmission_power or 0.0), 1e-6)  # W
    G_tx_dBi_base = current_user.antenna_gain or 0.0                          # dBi pico nominal TX
    G_rx_dbi      = current_user.rx_gain or 0.0                                # dBi RX
    freq_mhz_user = current_user.frequencia or 100.0                           # MHz
    totalloss     = current_user.total_loss or 0.0                             # perdas sistêmicas (cabos etc.) dB
    pattern       = current_user.antenna_pattern
    direction     = current_user.antenna_direction
    tilt          = current_user.antenna_tilt

    # bearing TX->RX (graus azimute)
    direction_rx = calculate_bearing(
        start_coords['lat'], start_coords['lng'],
        end_coords['lat'],   end_coords['lng']
    )

    # ========= ganho TX efetivo incluindo padrão direcional =========
    delta_dir_dB  = 0.0
    delta_tilt_dB = 0.0
    horizontal_data = None
    vertical_data   = None

    if pattern is not None:
        file_content = pattern.decode('latin1', errors='ignore')

        # parse_pat retorna padrão horizontal/vertical em E/Emax (linear)
        horizontal_data, vertical_data, _meta = parse_pat(file_content)

        # Ajuste horizontal (azimute)
        if horizontal_data is not None:
            horiz = np.asarray(horizontal_data, dtype=float)
            # aplica rotação global da antena
            if direction is not None:
                rotation_index = int(direction / (360.0 / len(horiz)))
                horiz = np.roll(horiz, rotation_index)

            # valor E/Emax na direção real do RX
            e_emax = horiz[int(direction_rx) % 360]
            e_emax = max(e_emax, 1e-6)
            # 20log10(E/Emax) -> variação de ganho em dB
            delta_dir_dB = 20.0 * math.log10(e_emax)

        # Ajuste vertical (tilt mecânico/elétrico)
        if vertical_data is not None:
            vert = np.asarray(vertical_data, dtype=float)
            # aplica tilt (rolagem positiva = inclinar feixe)
            if tilt:
                vert = np.roll(vert, int(np.round(tilt)))

            # assumimos índice central = 0° elétrico
            idx_zero = len(vert) // 2
            e_vert = max(vert[idx_zero], 1e-6)
            delta_tilt_dB = 20.0 * math.log10(e_vert)

    G_tx_dBi = G_tx_dBi_base + delta_dir_dB + delta_tilt_dB  # dBi efetivo TX naquela direção

    # ========= frequência e ERP =========
    # limite inferior só pra não quebrar log10
    if freq_mhz_user < 100.0:
        # fora da faixa ideal do P.452 (< ~700 MHz), mas mantemos coerência interna
        freq_mhz_user = 100.0
    frequency = (freq_mhz_user / 1000.0) * u.GHz  # pycraf espera GHz

    # Potência TX em dBm
    P_dBm = 10.0 * math.log10(Ptx_W / 0.001)  # W → dBm
    # ERP naquela direção (já descontando perdas de cabo)
    erp = P_dBm + G_tx_dBi - totalloss

    # ========= coordenadas geográficas e perfil SRTM =========
    tx_coords = path[0]
    rx_coords = path[1]
    lon_tx, lat_tx = float(tx_coords['lng']) * u.deg, float(tx_coords['lat']) * u.deg
    lon_rx, lat_rx = float(rx_coords['lng']) * u.deg, float(rx_coords['lat']) * u.deg

    temperature   = 293.15 * u.K
    pressure      = 1013.0 * u.hPa
    time_percent  = 40.0 * u.percent
    zone_t, zone_r = pathprof.CLUTTER.UNKNOWN, pathprof.CLUTTER.UNKNOWN

    srtm_dir = str(global_srtm_dir())
    if project:
        summary_tx = ensure_geodata_availability(project, start_coords['lat'], start_coords['lng'], fetch_lulc=False) or {}
        summary_rx = ensure_geodata_availability(project, end_coords['lat'], end_coords['lng'], fetch_lulc=False) or {}
        dem_tx = summary_tx.get('dem_dir')
        dem_rx = summary_rx.get('dem_dir')
        srtm_dir = (dem_rx or dem_tx) or srtm_dir
    message_payload = None
    google_profile = _google_elevation_profile(tx_coords, rx_coords, samples=256)
    if google_profile:
        current_app.logger.info(
            'elevation.profile.using_google',
            extra={
                'samples': google_profile.get('samples'),
                'distance_km': google_profile['distance_m'] / 1000.0,
            },
        )
        elevations = np.array(google_profile['elevations_m'], dtype=float)
        total_distance = google_profile['distance_m']
        sample_count = len(elevations)
        distance_samples = np.linspace(0.0, total_distance, sample_count)
        # Ajusta o perfil para coincidir com o solo da torre RX/TX medido via SRTM
        tx_ground = _compute_site_elevation(tx_coords['lat'], tx_coords['lng'])
        rx_ground = _compute_site_elevation(rx_coords['lat'], rx_coords['lng'])
        if tx_ground is not None or rx_ground is not None:
            start_target = tx_ground if tx_ground is not None else elevations[0]
            end_target = rx_ground if rx_ground is not None else elevations[-1]
            start_delta = start_target - elevations[0]
            end_delta = end_target - elevations[-1]
            adjustments = np.linspace(start_delta, end_delta, sample_count)
            elevations = elevations + adjustments
        distances = (distance_samples * u.m)
        heights = (elevations * u.m)
        longitudes = np.array(google_profile['longitudes'])
        latitudes = np.array(google_profile['latitudes'])
        additional_data = {}
    else:
        message_payload = {
            "message": "Perfil usando SRTM local — aguarde alguns segundos a mais.",
            "warning": True,
        }
        current_app.logger.info('elevation.profile.using_srtm')
        profile_step = 30 * u.m  # SRTM1 tem resolução ≈30 m; evita amostragem excessiva
        try:
            with SrtmConf.set(srtm_dir=srtm_dir, download='none', server='viewpano'):
                profile = pathprof.srtm_height_profile(
                    lon_tx, lat_tx,
                    lon_rx, lat_rx,
                    step=profile_step
                )
        except Exception:
            with SrtmConf.set(srtm_dir=srtm_dir, download='missing', server='viewpano'):
                profile = pathprof.srtm_height_profile(
                    lon_tx, lat_tx,
                    lon_rx, lat_rx,
                    step=profile_step
                )
        longitudes, latitudes, total_distance, distances, heights, angle1, angle2, additional_data = profile

    # alturas das antenas acima do solo
    h_rg = (current_user.rx_height or 1.0) * u.m
    h_tg = (current_user.tower_height or 30.0) * u.m

    # ========= curvatura da Terra =========
    distances_m = distances.to(u.m).value
    heights_m   = heights.to(u.m).value

    # (opcional) alturas ajustadas por curvatura local da Terra
    # mantemos aqui se você quiser usar depois p/ debug:
    adjusted_heights = adjust_heights_for_curvature(
        distances_m,
        heights_m,
        h_tg.value,
        h_rg.value
    )

    # raio efetivo da Terra (k-factor). Guardamos pra uso futuro se quiser plotar info.
    effective_radius = calculate_effective_earth_radius()

    # distância total TX→RX
    rx_position_km = distances.to(u.km)[-1].value

    def _compute_losses(mode: str):
        with SrtmConf.set(srtm_dir=srtm_dir, download=mode, server='viewpano'):
            return pathprof.losses_complete(
                frequency,
                temperature,
                pressure,
                lon_tx, lat_tx,
                lon_rx, lat_rx,
                h_tg, h_rg,
                1 * u.m,
                time_percent,
                zone_t=zone_t,
                zone_r=zone_r,
            )

    results = None
    for mode in ('none', 'missing'):
        try:
            results = _compute_losses(mode)
            break
        except Exception as exc:
            current_app.logger.warning('losses_complete.retry', extra={'mode': mode, 'error': str(exc)})
    if results is None:
        raise RuntimeError('Não foi possível calcular as perdas P.452')

    _Lb_corr_obj = results.get('L_b_corr', None)
    if _Lb_corr_obj is None:
        _Lb_corr_obj = results.get('L_b', None)

    if hasattr(_Lb_corr_obj, 'value'):
        val = _Lb_corr_obj.value
        if isinstance(val, np.ndarray):
            Lb_corr = float(val[0])
        else:
            Lb_corr = float(val)
    else:
        Lb_corr = float(_Lb_corr_obj)

    # potência recebida estimada em dBm no RX:
    # Prx = ERP(dBm) + G_rx(dBi) - L_path(dB)
    sinal_recebido = erp + G_rx_dbi - Lb_corr  # dBm

    # ========= geometria básica pra plot =========
    distances_km = distances.to(u.km).value
    terrain_x    = distances_km  # eixo X em km

    tx_top = heights_m[0]    + h_tg.value
    rx_top = heights_m[-1]   + h_rg.value

    min_height = float(np.min(heights_m))

    # ========= figura / layout =========
    fig = plt.figure(figsize=(15, 8))
    gs  = fig.add_gridspec(
        2, 1,
        height_ratios=[4, 1],
        hspace=0.18
    )
    ax = fig.add_subplot(gs[0])

    # ===== Terreno base (areia/marrom) =====
    ax.fill_between(
        terrain_x,
        heights_m,
        color='#d8c9a7',
        alpha=0.85,
        label='Terreno'
    )
    ax.plot(
        terrain_x,
        heights_m,
        color='#564d33',
        linewidth=2
    )

    # ===== Curvatura da Terra (referência do feixe seguindo Terra efetiva) =====
    curvature_line = []
    for i, dist_km in enumerate(terrain_x):
        if i == 0:
            height_point = heights_m[i] + h_tg.value
        elif i == len(terrain_x) - 1:
            height_point = heights_m[i] + h_rg.value
        else:
            drop = earth_curvature_correction(dist_km)  # queda geométrica da curvatura
            height_point = heights_m[i] - drop
        curvature_line.append(height_point)

    ax.plot(
        terrain_x,
        curvature_line,
        color='#b71c1c',
        linestyle='--',
        linewidth=1.6,
        alpha=0.8,
        label='Curvatura da Terra'
    )

    # ===== Torres TX / RX (retângulos azuis/roxo) =====
    total_span_km = max(rx_position_km, 1e-3)
    tower_width   = max(total_span_km * 0.02, 0.06)

    tx_rect = Rectangle(
        (-tower_width / 2.0, heights_m[0]),
        tower_width,
        h_tg.value,
        facecolor='#0d6efd',
        edgecolor='#0a58ca',
        alpha=0.9,
        label='TX',
        zorder=6
    )
    rx_rect = Rectangle(
        (terrain_x[-1] - tower_width / 2.0, heights_m[-1]),
        tower_width,
        h_rg.value,
        facecolor='#6610f2',
        edgecolor='#520dc2',
        alpha=0.9,
        label='RX',
        zorder=6
    )
    ax.add_patch(tx_rect)
    ax.add_patch(rx_rect)

    # ===== Linha de visada ideal (reta, sem curvatura) =====
    ax.plot(
        [0.0, rx_position_km],
        [tx_top, rx_top],
        color='#ff9800',
        linestyle=':',
        linewidth=1.5,
        label='Linha Reta (sem curvatura)'
    )

    # ===== 1ª Zona de Fresnel =====
    # amostragem suave pra preencher área
    n_points = 200
    x_smooth = np.linspace(0.0, rx_position_km, n_points)

    # comprimento de onda
    c0 = c  # velocidade da luz já importada (m/s)
    wavelength = c0 / frequency.to(u.Hz).value  # m

    # raio da 1ª Fresnel para cada ponto x_smooth
    fresnel_radius = np.array([
        fresnel_zone_radius(
            xi * 1000.0,
            (rx_position_km - xi) * 1000.0,
            wavelength
        )
        for xi in x_smooth
    ])

    # linha base reta entre topos TX/RX
    direct_line = np.linspace(tx_top, rx_top, n_points)

    # correção da curvatura da Terra pra cada ponto
    curvature_adjustment = np.array([
        earth_curvature_correction(xi)
        for xi in x_smooth
    ], dtype=float)
    if curvature_adjustment.size:
        baseline = np.linspace(curvature_adjustment[0], curvature_adjustment[-1], curvature_adjustment.size)
        curvature_adjustment = curvature_adjustment - baseline

    # base ajustada = linha direta menos queda de curvatura
    adjusted_base = direct_line - curvature_adjustment

    fresnel_top    = adjusted_base + fresnel_radius
    fresnel_bottom = adjusted_base - fresnel_radius

    # preencher Fresnel
    ax.fill_between(
        x_smooth,
        fresnel_bottom,
        fresnel_top,
        color='#ffe082',
        alpha=0.45,
        label='1ª Zona Fresnel'
    )
    # contorno superior/inferior em roxo tracejado
    ax.plot(
        x_smooth,
        fresnel_top,
        color='#9c27b0',
        linestyle='--',
        linewidth=1.2,
        alpha=0.8
    )
    ax.plot(
        x_smooth,
        fresnel_bottom,
        color='#9c27b0',
        linestyle='--',
        linewidth=1.2,
        alpha=0.8
    )

    # ===== Bloqueio da Fresnel (regiões em vermelho no terreno) =====
    # calcular no grid do terreno (mesma resolução SRTM)
    base_line_profile = np.linspace(tx_top, rx_top, len(terrain_x))
    curvature_profile = np.array([
        earth_curvature_correction(dist)
        for dist in terrain_x
    ], dtype=float)
    if curvature_profile.size:
        baseline_profile = np.linspace(curvature_profile[0], curvature_profile[-1], curvature_profile.size)
        curvature_profile = curvature_profile - baseline_profile
    fresnel_radius_profile = np.array([
        fresnel_zone_radius(
            d * 1000.0,
            (rx_position_km - d) * 1000.0,
            wavelength
        )
        for d in terrain_x
    ])

    # limite inferior permitido (bottom da zona)
    fresnel_bottom_profile = (
        base_line_profile
        - curvature_profile
        - fresnel_radius_profile
    )

    # ponto obstruído se o terreno está acima da "fresnel_bottom_profile"
    obstruction_mask = heights_m >= fresnel_bottom_profile
    # não marcar as torres como obstáculo
    if len(obstruction_mask) >= 2:
        obstruction_mask[0]  = False
        obstruction_mask[-1] = False

    # sobrepinta solo onde tem obstrução (faixa vermelha mais grossa)
    if np.any(obstruction_mask):
        ax.fill_between(
            terrain_x,
            heights_m,
            where=obstruction_mask,
            color='#c62828',
            alpha=0.7,
            interpolate=True,
            label='Obstáculo na Fresnel'
        )

    # também marcar pontos discretos (marcadores vermelhos)
    obstacle_distances = terrain_x[obstruction_mask]
    if obstacle_distances.size:
        ax.scatter(
            obstacle_distances,
            heights_m[obstruction_mask],
            color='#c62828',
            s=32,
            zorder=10
        )

    # ===== Escalas de altura =====
    max_profile_height = max(
        float(np.max(heights_m)),
        float(np.max(fresnel_top)),
        tx_top,
        rx_top,
    )
    span = max(max_profile_height - min_height, 1.0)

    # baseline visual pra dar "respiro" embaixo
    if abs(min_height) < 1e-3:
        baseline = min_height - 0.3 * span
    else:
        baseline = min_height - abs(min_height) * 0.3

    y_min = min(baseline, min_height - 0.15 * span)
    y_max = max_profile_height + max(span * 0.12, 10.0)

    ax.set_ylim(y_min, y_max)
    ax.set_xlim(0.0, rx_position_km)

    # ========= Campo elétrico estimado no RX =========
    # fórmula já discutida: E(dBµV/m) = Prx(dBm) - Grx(dBi) + 77.2 + 20log10(fMHz)
    freq_for_field = max(float(freq_mhz_user), 0.1)
    field_rx_dbuv = (
        sinal_recebido
        - (G_rx_dbi or 0.0)
        + 77.2
        + 20.0 * math.log10(freq_for_field)
    )

    # ===== Lista de obstáculos (até 6 primeiras distâncias km) =====
    if obstacle_distances.size:
        obstacle_desc = ", ".join(f"{dist:.2f} km" for dist in obstacle_distances[:6])
    else:
        obstacle_desc = 'Nenhum'

    # ========= Subplot de informações =========
    ax_info = fig.add_subplot(gs[1])
    ax_info.axis('off')

    info_lines = [
        f"Distância TX→RX: {rx_position_km:.2f} km",
        f"Direção RX: {direction_rx:.2f}°",
        f"Ganho TX (base + ΔH + ΔV): {G_tx_dBi:.2f} dBi ({G_tx_dBi_base:.2f} + {delta_dir_dB:.2f} + {delta_tilt_dB:.2f})",
        f"ERP na direção: {erp:.2f} dBm",
        f"Perdas (P.452): {Lb_corr:.2f} dB",
        f"Ganho RX: {G_rx_dbi:.2f} dBi",
        f"Potência recebida estimada: {sinal_recebido:.2f} dBm",
        f"Campo estimado no RX: {field_rx_dbuv:.2f} dBµV/m",
        f"Obstáculos na 1ª Fresnel: {obstacle_desc}",
    ]

    ax_info.text(
        0.01, 0.95,
        "\n".join(info_lines),
        fontsize=10,
        ha='left',
        va='top'
    )

    # ========= rotulagem e legenda =========
    ax.set_xlabel('Distância (km)', fontsize=11)
    ax.set_ylabel('Elevação (m)', fontsize=11)
    ax.set_title('Perfil de Elevação com Curvatura e Fresnel', fontsize=13)

    ax.grid(True, which="both", ls="--", alpha=0.5)

    # legenda organizada
    ax.legend(
        loc='upper center',
        bbox_to_anchor=(0.5, 1.22),
        ncol=3,
        fontsize=9,
        frameon=True,
        framealpha=0.95
    )

    # ========= render final =========
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=120)
    img_buffer.seek(0)
    image_bytes = img_buffer.getvalue()

    lat_series = _to_degree_array(latitudes) if 'latitudes' in locals() else []
    lon_series = _to_degree_array(longitudes) if 'longitudes' in locals() else []
    profile_payload = {
        'source': google_profile.get('source', 'google') if google_profile else 'srtm',
        'distance_km': rx_position_km,
        'samples': len(heights_m),
        'elevations_m': _downsample_sequence(heights_m.tolist()),
        'latitudes': _downsample_sequence(lat_series.tolist() if hasattr(lat_series, 'tolist') else lat_series),
        'longitudes': _downsample_sequence(lon_series.tolist() if hasattr(lon_series, 'tolist') else lon_series),
    }

    current_user.perfil_img = image_bytes

    profile_asset = None
    receiver_record = None
    asset_url = None
    if project and receiver_id:
        try:
            profile_asset = _persist_receiver_profile_asset(project, receiver_label, image_bytes)
            asset_url = url_for('projects.asset_preview', slug=project.slug, asset_id=profile_asset.id)
            receiver_record = {
                'id': receiver_id,
                'label': receiver_label or receiver_id,
                'lat': float(end_coords['lat']),
                'lng': float(end_coords['lng']),
                'municipality': receiver_summary.get('municipality'),
                'field': receiver_summary.get('field') or receiver_summary.get('field_dbuv_m'),
                'elevation': receiver_summary.get('elevation') or receiver_summary.get('elevation_m'),
                'distance': receiver_summary.get('distance') or receiver_summary.get('distance_km'),
                'bearing': receiver_summary.get('bearing') or receiver_summary.get('bearing_deg'),
                'summary': receiver_summary,
                'profile': profile_payload,
                'profile_asset_id': str(profile_asset.id),
                'profile_asset_path': profile_asset.path,
                'profile_asset_url': asset_url,
                'profile_meta': {
                    'distance_km': rx_position_km,
                    'erp_dbm': erp,
                    'rx_power_dbm': sinal_recebido,
                    'field_dbuv_m': field_rx_dbuv,
                    'obstacles': obstacle_desc,
                },
                'profile_info': info_lines,
            }
            _upsert_project_receiver(project, receiver_record)
        except Exception as exc:
            current_app.logger.warning('receiver.profile.persist_failed', extra={'error': str(exc)})

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('receiver.profile.commit_failed', exc_info=exc)
        return jsonify({'error': 'Falha ao salvar o perfil gerado.'}), 500

    img_base64 = base64.b64encode(image_bytes).decode('utf-8')
    img_buffer.close()
    plt.close(fig)

    response_payload = {
        "image": img_base64,
        "info": info_lines,
        "distance_km": rx_position_km,
        "erp_dbm": erp,
        "rx_power_dbm": sinal_recebido,
        "rx_field_dbuvm": field_rx_dbuv,
        "obstacles": obstacle_desc,
        "profile": profile_payload,
    }
    if profile_asset:
        response_payload['asset_id'] = str(profile_asset.id)
        response_payload['asset_url'] = asset_url
    if receiver_record:
        response_payload['receiver'] = receiver_record
    if message_payload:
        response_payload.update(message_payload)

    return jsonify(response_payload)




# -------- Cobertura (mapa) --------

def create_attenuation_dict(lons, lats, attenuation):
    attenuation_dict = {}
    if attenuation.shape == (len(lats), len(lons)):
        for i in range(len(lats)):
            for j in range(len(lons)):
                key = f"({lats[i]}, {lons[j]})"
                attenuation_dict[key] = float(attenuation[i, j].value)
    else:
        raise ValueError("A dimensão do array de atenuação não corresponde ao número de latitudes e longitudes.")
    return attenuation_dict

def calculate_geodesic_bounds(lon, lat, radius_km):
    central_point = (lat, lon)
    north = geodesic(kilometers=radius_km).destination(central_point, bearing=0)
    south = geodesic(kilometers=radius_km).destination(central_point, bearing=180)
    east  = geodesic(kilometers=radius_km).destination(central_point, bearing=90)
    west  = geodesic(kilometers=radius_km).destination(central_point, bearing=270)
    return {"north": north.latitude, "south": south.latitude, "east": east.longitude, "west": west.longitude}

radii = np.array([20, 40, 60, 100, 300]).reshape(-1, 1)  # km
delta_lat = np.array([-0.002316, -0.002324, -0.005666, -0.011404, -0.034283])
delta_lon = np.array([0.006451, 0.013683, 0.018373, 0.030432, 0.090573])
model_lat = LinearRegression().fit(radii, delta_lat)
model_lon = LinearRegression().fit(radii, delta_lon)

def adjust_center(radius_km, center_lat, center_lon):
    adj_lat = model_lat.predict(np.array([[radius_km]]))[0]
    adj_lon = model_lon.predict(np.array([[radius_km]]))[0]
    scale_factor_lat = 1
    scale_factor_log = 1

    if radius_km in range(0, 21):
        scale_factor_lat = 1.9;  scale_factor_log = 0.95
    elif radius_km in range(21, 31):
        scale_factor_lat = 1.4;  scale_factor_log = 0.93
    elif radius_km in range(31, 41):
        scale_factor_lat = 1.28; scale_factor_log = 1.0
    elif radius_km in range(41, 51):
        scale_factor_lat = 1.21; scale_factor_log = 1.03
    elif radius_km in range(51, 61):
        scale_factor_lat = 1.19; scale_factor_log = .97
    elif radius_km in range(61, 71):
        scale_factor_lat = 1.17; scale_factor_log = 1.025
    elif radius_km in range(71, 101):
        scale_factor_lat = 1.1;  scale_factor_log = 1.027

    new_lat = center_lat - adj_lat*scale_factor_lat
    new_lon = center_lon - adj_lon*scale_factor_log
    return new_lat, new_lon

def determine_hgt_files(bounds):
    files_hgt = []
    lat_start = int(math.floor(bounds['south']))
    lat_end   = int(math.ceil(bounds['north']))
    lon_start = int(math.floor(bounds['west']))
    lon_end   = int(math.ceil(bounds['east']))
    for lat in range(lat_start, lat_end):
        for lon in range(lon_start, lon_end):
            lat_prefix = 'N' if lat >= 0 else 'S'
            lon_prefix = 'E' if lon >= 0 else 'W'
            filename = f"{lat_prefix}{abs(lat):02d}{lon_prefix}{abs(lon):03d}.hgt"
            files_hgt.append(filename)
    return files_hgt


def _load_antenna_patterns(user):
    if not user.antenna_pattern:
        return None, None
    file_content = user.antenna_pattern.decode('latin1', errors='ignore')
    horizontal_data, vertical_data, _ = parse_pat(file_content)
    return horizontal_data, vertical_data


def _compute_gain_components(user, hprof_cache):
    horizontal_linear, vertical_linear = _load_antenna_patterns(user)
    direction = float(user.antenna_direction or 0.0)
    tilt = float(user.antenna_tilt or 0.0)

    gain_data = {
        'horizontal_gain_grid_db': 0.0,
        'horizontal_pattern_db': None,
        'vertical_gain_grid_db': 0.0,
        'vertical_pattern_db': None,
        'vertical_horizon_db': 0.0,
    }

    if horizontal_linear is None or vertical_linear is None:
        return gain_data

    bearing_map = np.asarray(hprof_cache.get('bearing_map'))
    dist_map = np.asarray(hprof_cache.get('dist_map'))

    if bearing_map.size == 0 or dist_map.size == 0:
        return gain_data

    horizontal_linear = np.asarray(horizontal_linear, dtype=float)
    vertical_linear = np.asarray(vertical_linear, dtype=float)
    horizontal_linear = np.clip(horizontal_linear, 1e-6, None)
    vertical_linear = np.clip(vertical_linear, 1e-6, None)

    horizontal_db = 20.0 * np.log10(horizontal_linear)
    horizontal_db -= np.nanmax(horizontal_db)
    vertical_db_table = 20.0 * np.log10(vertical_linear)
    vertical_db_table -= np.nanmax(vertical_db_table)

    # -------- Horizontal pattern (azimute relativo) --------
    azimuth_deg = np.degrees(bearing_map) % 360.0
    relative_az = (azimuth_deg - direction) % 360.0
    relative_az_flat = relative_az.ravel()

    base_angles = np.arange(0, 360, dtype=float)
    horizontal_interp = np.interp(relative_az_flat, np.concatenate([base_angles, [360.0]]),
                                  np.concatenate([horizontal_db, [horizontal_db[0]]]))
    horizontal_interp = horizontal_interp.reshape(relative_az.shape)

    # -------- Vertical pattern (considera distância e tilt) --------
    # pathprof dist_map é em km
    dist_m = np.maximum(dist_map, 1e-3) * 1000.0
    tx_height_m = float(user.tower_height or 0.0)
    rx_height_m = float(user.rx_height or 0.0)
    vertical_angles = np.linspace(-90.0, 90.0, len(vertical_db_table), dtype=float)

    elevation = np.degrees(np.arctan2(rx_height_m - tx_height_m, dist_m))
    relative_el = np.clip(elevation - tilt, -90.0, 90.0)
    vertical_interp = np.interp(relative_el.ravel(), vertical_angles, vertical_db_table)
    vertical_gain_db = vertical_interp.reshape(relative_el.shape)
    horizon_delta_db = float(np.interp(np.clip(-tilt, -90.0, 90.0), vertical_angles, vertical_db_table))

    gain_data['horizontal_gain_grid_db'] = horizontal_interp
    abs_angles = np.arange(0, 360, dtype=float)
    rotated_db = np.interp((abs_angles - direction) % 360.0, base_angles, horizontal_db, period=360.0)

    gain_data['horizontal_pattern_db'] = rotated_db
    gain_data['vertical_gain_grid_db'] = vertical_gain_db
    gain_data['vertical_pattern_db'] = vertical_db_table
    gain_data['vertical_horizon_db'] = horizon_delta_db

    return gain_data


def _to_degree_array(values):
    if isinstance(values, np.ndarray):
        if hasattr(values, 'unit'):
            return values.to(u.deg).value
        return values
    arr = np.asarray([
        val.to(u.deg).value if hasattr(val, 'unit') else val
        for val in values
    ], dtype=float)
    return arr


def _determine_auto_scale(values, min_val, max_val):
    finite = np.isfinite(values)
    if not np.any(finite):
        return -110.0, -40.0

    data = values[finite]
    if min_val is None or max_val is None:
        p5, p95 = np.percentile(data, [5, 95])
        span = max(p95 - p5, 6.0)
        min_val = p5 - 0.1 * span
        max_val = p95 + 0.1 * span
    if min_val is None:
        min_val = float(np.nanmin(data))
    if max_val is None:
        max_val = float(np.nanmax(data))
    if min_val >= max_val:
        delta = abs(min_val) * 0.05 + 3.0
        min_val -= delta
        max_val += delta
    return float(min_val), float(max_val)


def _select_map_resolution(radius_km, min_arcsec=0.5, max_arcsec=20.0):
    radius_km = max(float(radius_km), 0.5)
    if radius_km <= 25:
        target_pixels = 640.0
    elif radius_km <= 80:
        target_pixels = 512.0
    else:
        target_pixels = 384.0

    km_per_degree = 111.32
    diameter_deg = max((2.0 * radius_km) / km_per_degree, 0.01)
    resolution_arcsec = (diameter_deg * 3600.0) / max(target_pixels, 256.0)
    resolution_arcsec = float(np.clip(resolution_arcsec, min_arcsec, max_arcsec))
    return resolution_arcsec * u.arcsec


def _compute_site_elevation(lat, lon):
    try:
        srtm_dir = str(global_srtm_dir())
        download_mode = 'none'
        try:
            with pathprof.SrtmConf.set(srtm_dir=srtm_dir, download=download_mode, server='viewpano'):
                _, _, height_map = pathprof.srtm_height_map(
                    lon * u.deg,
                    lat * u.deg,
                    0.02 * u.deg,
                    0.02 * u.deg,
                    map_resolution=3 * u.arcsec,
                )
        except Exception:
            with pathprof.SrtmConf.set(srtm_dir=srtm_dir, download='missing', server='viewpano'):
                _, _, height_map = pathprof.srtm_height_map(
                    lon * u.deg,
                    lat * u.deg,
                    0.02 * u.deg,
                    0.02 * u.deg,
                    map_resolution=3 * u.arcsec,
                )
        hm = np.asarray(height_map.to(u.m).value, dtype=float)
        if hm.size == 0:
            return None
        center_idx = hm.shape[0] // 2
        return float(hm[center_idx, center_idx])
    except Exception as exc:
        current_app.logger.warning('Falha ao obter elevação SRTM: %s', exc)
        return None


def _compute_haat_radials(
    lat_deg,
    lon_deg,
    tower_height_m,
    site_elevation_m=None,
    dem_directory=None,
    inner_km=3.0,
    outer_km=16.0,
    bearing_step_deg=45,
    profile_step_m=200.0,
):
    """Calcula HMNT/HAAT médio com radiais usando perfis SRTM."""
    try:
        lat = float(lat_deg)
        lon = float(lon_deg)
    except (TypeError, ValueError, RuntimeError):
        return [], None

    if bearing_step_deg <= 0:
        bearing_step_deg = 15
    if outer_km <= inner_km:
        outer_km = inner_km + 0.5

    tower_height = float(tower_height_m or 0.0)
    origin = (lat, lon)
    lon_tx = lon * u.deg
    lat_tx = lat * u.deg
    srtm_dir = dem_directory or str(global_srtm_dir())
    radials = []

    def _fetch_profile(lon_rx, lat_rx):
        try:
            with SrtmConf.set(srtm_dir=srtm_dir, download='none', server='viewpano'):
                return pathprof.srtm_height_profile(
                    lon_tx,
                    lat_tx,
                    lon_rx,
                    lat_rx,
                    step=max(50.0, profile_step_m) * u.m,
                )
        except Exception:
            with SrtmConf.set(srtm_dir=srtm_dir, download='missing', server='viewpano'):
                return pathprof.srtm_height_profile(
                    lon_tx,
                    lat_tx,
                    lon_rx,
                    lat_rx,
                    step=max(50.0, profile_step_m) * u.m,
                )

    for bearing in range(0, 360, int(bearing_step_deg)):
        destination = geodesic(kilometers=outer_km).destination(origin, bearing)
        lon_rx = float(destination.longitude) * u.deg
        lat_rx = float(destination.latitude) * u.deg
        try:
            profile = _fetch_profile(lon_rx, lat_rx)
        except Exception as exc:
            current_app.logger.warning('haat.profile_error', extra={'bearing': bearing, 'error': str(exc)})
            continue

        try:
            _, _, _, distances, heights, *_ = profile
        except (ValueError, TypeError):
            continue

        dist_km = np.asarray(distances.to(u.km).value, dtype=float)
        ground_m = np.asarray(heights.to(u.m).value, dtype=float)
        mask = (dist_km >= float(inner_km)) & (dist_km <= float(outer_km))
        if not np.any(mask):
            continue

        avg_ground = float(np.nanmean(ground_m[mask]))
        site_elevation = float(site_elevation_m) if site_elevation_m is not None else float(ground_m[0])
        haat_value = (site_elevation + tower_height) - avg_ground

        radial_entry = {
            'bearing_deg': float(bearing),
            'avg_terrain_m': round(avg_ground, 2),
            'haat_m': round(haat_value, 2),
            'hmnt_m': round(haat_value, 2),
        }
        radials.append(radial_entry)

    if not radials:
        return [], None

    haat_average = round(float(np.nanmean([item.get('hmnt_m') for item in radials])), 2)
    return radials, haat_average


def _lookup_municipality_details(lat, lon, include_ibge=False):
    try:
        detail = ibge_api.reverse_geocode_offline(lat, lon)
    except ibge_api.ReverseGeocoderUnavailable as exc:
        current_app.logger.warning('geocode.offline_unavailable', extra={'error': str(exc)})
        return None
    if not detail or not detail.get('name'):
        return None
    response = {
        'name': detail.get('name'),
        'state': detail.get('state'),
        'state_code': detail.get('state_code'),
        'country': detail.get('country') or 'BR',
        'provider': detail.get('provider') or 'reverse_geocoder',
    }
    code = None
    population = None
    population_year = None
    if include_ibge:
        state_hint = response.get('state_code') or response.get('state')
        code = ibge_api.resolve_municipality_code(response.get('name'), state_hint)
        if not code:
            entry = ibge_api.find_local_municipality(response.get('name'), state_hint)
            if entry:
                code = entry.get('code')
        if code:
            entry = ibge_api.get_local_municipality_entry(code)
            if entry:
                response['name'] = entry.get('name') or response['name']
                response['state'] = entry.get('state') or response['state']
                response['state_code'] = entry.get('state_code') or response['state_code']
                population = entry.get('population')
                population_year = entry.get('population_year')
            response['ibge_code'] = str(code)
    response['population'] = population
    response['population_year'] = population_year
    return response


def _lookup_municipality(lat, lon):
    details = _lookup_municipality_details(lat, lon, include_ibge=True)
    if not details:
        return None
    parts = [details.get('name'), details.get('state'), details.get('country')]
    formatted = ', '.join(part for part in parts if part)
    return {
        'label': formatted or None,
        'ibge_code': details.get('ibge_code'),
        'state': details.get('state'),
        'state_code': details.get('state_code'),
        'population': details.get('population'),
        'population_year': details.get('population_year'),
    }


def _downsample_sequence(values, max_points=256):
    if not values:
        return []
    sequence = []
    for value in values:
        try:
            sequence.append(float(value))
        except (TypeError, ValueError):
            continue
    length = len(sequence)
    if length <= max_points:
        return sequence
    step = max(1, length // max_points)
    downsampled = sequence[::step]
    if len(downsampled) > max_points:
        downsampled = downsampled[:max_points]
    return downsampled


def _build_receiver_profile(tx_coords, rx_coords):
    try:
        profile = _google_elevation_profile(tx_coords, rx_coords, samples=256)
    except Exception:
        profile = None
    if not profile:
        return None
    distance_km = None
    if profile.get('distance_m') is not None:
        distance_km = float(profile['distance_m']) / 1000.0
    return {
        'source': profile.get('source', 'google'),
        'samples': profile.get('samples'),
        'distance_km': distance_km,
        'elevations_m': _downsample_sequence(profile.get('elevations_m')),
        'latitudes': _downsample_sequence(profile.get('latitudes')),
        'longitudes': _downsample_sequence(profile.get('longitudes')),
    }


def _enrich_receivers_metadata(receivers, tx_object):
    if not receivers:
        return receivers
    enriched = []
    location_cache = {}
    demographics_cache = {}
    tx_coords = None
    if tx_object and tx_object.latitude is not None and tx_object.longitude is not None:
        tx_coords = {'lat': float(tx_object.latitude), 'lng': float(tx_object.longitude)}
    for receiver in receivers:
        rx_copy = dict(receiver)
        location = dict(rx_copy.get('location') or {})
        lat = _coerce_float(rx_copy.get('lat') or location.get('lat') or location.get('latitude'))
        lon = _coerce_float(rx_copy.get('lng') or rx_copy.get('lon') or location.get('lng') or location.get('lon') or location.get('longitude'))
        if lat is not None:
            location['lat'] = lat
        if lon is not None:
            location['lng'] = lon

        details = None
        if lat is not None and lon is not None:
            cache_key = (round(lat, 5), round(lon, 5))
            details = location_cache.get(cache_key)
            if details is None:
                details = _lookup_municipality_details(lat, lon, include_ibge=True)
                location_cache[cache_key] = details

        if details:
            municipality_name = details.get('name')
            state_label = details.get('state_code') or details.get('state')
            location['municipality'] = municipality_name
            location['state'] = details.get('state')
            location['state_code'] = details.get('state_code')
            location['country'] = details.get('country')
            rx_copy['location'] = location
            if municipality_name:
                rx_copy.setdefault('municipality', municipality_name)
            if state_label:
                rx_copy['state'] = state_label
            ibge_code = details.get('ibge_code')
            demographics = None
            if ibge_code:
                demographics = demographics_cache.get(ibge_code)
                if demographics is None:
                    demographics = ibge_api.fetch_demographics_by_code(ibge_code)
                    demographics_cache[ibge_code] = demographics
            ibge_payload = {
                'code': ibge_code,
                'name': municipality_name,
                'state': state_label,
                'demographics': demographics,
            }
            rx_copy['ibge'] = {k: v for k, v in ibge_payload.items() if v not in (None, '', {}, [])}

        if tx_coords and lat is not None and lon is not None and not rx_copy.get('profile'):
            profile = _build_receiver_profile(tx_coords, {'lat': lat, 'lng': lon})
            if profile:
                rx_copy['profile'] = profile

        enriched.append(rx_copy)
    return enriched



def _google_elevation_profile(start_coords, end_coords, samples=256):
    """
    Usa a Google Elevation API para obter o perfil de terreno ao longo
    do enlace TX→RX. Retorna None em caso de falha (para permitir fallback SRTM).
    """
    api_key = current_app.config.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        return None
    distance_km = geodesic(
        (start_coords['lat'], start_coords['lng']),
        (end_coords['lat'], end_coords['lng']),
    ).km
    samples = int(np.clip(distance_km * 10 + 50, 48, 512))
    params = {
        'path': f"{start_coords['lat']},{start_coords['lng']}|{end_coords['lat']},{end_coords['lng']}",
        'samples': samples,
        'key': api_key,
    }
    logger = current_app.logger
    try:
        logger.info(
            'elevation.google.request',
            extra={
                'samples': samples,
                'start': start_coords,
                'end': end_coords,
                'distance_km': distance_km,
            },
        )
        resp = requests.get('https://maps.googleapis.com/maps/api/elevation/json', params=params, timeout=20)
        raw_text = resp.text
        resp.raise_for_status()
        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning(
                'elevation.google.json_error: %s (body=%s)',
                exc,
                raw_text[:512],
            )
            return None
        if payload.get('status') != 'OK':
            logger.warning(
                'elevation.google.status_not_ok: %s (body=%s)',
                payload.get('status'),
                payload,
            )
            return None
        results = payload.get('results') or []
        if len(results) < 2:
            return None
        elevations = [float(item.get('elevation', 0.0)) for item in results]
        locations = [item.get('location') for item in results]
        if not all(locations):
            return None
        total_distance_m = distance_km * 1000.0
        lats = [loc.get('lat') for loc in locations]
        lngs = [loc.get('lng') for loc in locations]
        sample_count = len(elevations)
        logger.info('elevation.google.success', extra={'samples': sample_count, 'distance_km': total_distance_m / 1000.0})
        return {
            'elevations_m': elevations,
            'latitudes': lats,
            'longitudes': lngs,
            'distance_m': total_distance_m,
            'samples': sample_count,
            'source': 'google',
        }
    except Exception as exc:
        logger.warning(
            'elevation.google.error: %s',
            exc,
            exc_info=True,
        )
        return None


def _estimate_google_block_penalty(elevations: np.ndarray) -> float:
    if elevations.size < 12:
        return 0.0
    sigma = max(1.0, elevations.size / 80.0)
    smooth = gaussian_filter1d(elevations, sigma=sigma, mode='nearest')
    residual = elevations - smooth
    spikes = residual > 3.5
    if not np.any(spikes):
        return 0.0
    penalty = float(np.count_nonzero(spikes)) * 0.7
    return float(np.clip(penalty, 0.0, 30.0))


def _apply_rt3d_penalty(total_loss_db, lat_grid, lon_grid, lat_tx_deg, lon_tx_deg, radius_km, tx, data, scene=None):
    engine = (data.get('coverageEngine') or CoverageEngine.p1546.value).lower()
    if engine != CoverageEngine.rt3d.value:
        return total_loss_db, {}

    tx_ground = getattr(tx, 'tx_site_elevation', None)
    if tx_ground is None:
        tx_ground = _compute_site_elevation(lat_tx_deg, lon_tx_deg) or 0.0
    tx_height = getattr(tx, 'tower_height', None) or 30.0
    tx_altitude = tx_ground + tx_height
    rx_height = getattr(tx, 'rx_height', None) or 1.5

    occlusion_rate = float(_coerce_float(data.get('rt3dOcclusionPerMeter')) or 0.8)
    reflection_gain = float(_coerce_float(data.get('rt3dReflectionGain')) or 0.35)
    interference_rate = float(_coerce_float(data.get('rt3dInterferencePenalty')) or 0.25)
    reflection_cap = float(_coerce_float(data.get('rt3dReflectionCap')) or 12.0)
    minimum_clearance_m = float(_coerce_float(data.get('rt3dMinimumClearance'))
                                or getattr(tx, 'rt3dMinimumClearance', None)
                                or 2.0)
    diffraction_boost_db = float(_coerce_float(data.get('rt3dDiffractionBoost'))
                                 or getattr(tx, 'rt3dDiffractionBoost', None)
                                 or 1.5)

    diagnostics = {
        'occlusion_rate': occlusion_rate,
        'reflection_gain': reflection_gain,
        'interference_rate': interference_rate,
    }
    MAX_RAYS = 250
    rays: list[dict] = []
    meta = {
        'quality_map': None,
        'occlusion_map': None,
        'reflection_map': None,
        'multipath_map': None,
        'mode': None,
        'diagnostics': diagnostics,
        'rays': rays,
    }
    lat_axis = lat_grid[:, 0]
    lon_axis = lon_grid[0, :]

    scene_payload = scene or {}
    if scene_payload.get('points') is None:
        scene_payload['points'] = []
    points_payload = scene_payload.get('points')
    if points_payload:
        pts = np.array([[pt['lat'], pt['lon'], pt['height_m']] for pt in points_payload if pt.get('height_m') is not None], dtype=float)
        if pts.size >= 3:
            building_grid = griddata(
                (pts[:, 0], pts[:, 1]),
                pts[:, 2],
                (lat_grid, lon_grid),
                method='linear',
                fill_value=np.nan,
            )
            if np.isnan(building_grid).all():
                building_grid = np.zeros_like(lat_grid)
            else:
                building_grid = np.nan_to_num(building_grid, nan=np.nanmedian(pts[:, 2]))

            clearance = tx_altitude - building_grid
            occlusion_factor = np.clip(minimum_clearance_m - clearance, 0.0, None)
            occlusion_loss = occlusion_factor * occlusion_rate
            reflection_bonus = np.clip(building_grid - tx_altitude + rx_height, 0.0, None) * reflection_gain
            reflection_bonus = np.clip(reflection_bonus, 0.0, reflection_cap)

            diffraction_mask = np.logical_and(clearance < minimum_clearance_m, clearance > -minimum_clearance_m)
            reflection_bonus = reflection_bonus + (diffraction_mask.astype(float) * diffraction_boost_db)

            grad_lat, grad_lon = np.gradient(building_grid)
            multipath = np.sqrt(np.abs(grad_lat) + np.abs(grad_lon)) * interference_rate

            total_loss = total_loss_db + occlusion_loss + multipath - reflection_bonus
            quality_map = reflection_bonus - occlusion_loss - multipath

            diagnostics.update({
                'mode': 'scene',
                'points_used': int(len(points_payload)),
                'median_height': scene_payload.get('median_height'),
                'occlusion_mean': float(np.nanmean(occlusion_loss)),
                'reflection_mean': float(np.nanmean(reflection_bonus)),
                'multipath_mean': float(np.nanmean(multipath)),
            })
            meta.update({
                'quality_map': quality_map,
                'occlusion_map': occlusion_loss,
                'reflection_map': reflection_bonus,
                'multipath_map': multipath,
                'mode': 'scene',
            })
            scene_payload['diagnostics'] = diagnostics
            sample_count = min(len(points_payload), 200)
            stride = max(1, len(points_payload) // sample_count)
            for idx, pt in enumerate(points_payload):
                if idx % stride != 0:
                    continue
                lat_pt = float(pt.get('lat'))
                lon_pt = float(pt.get('lon'))
                height_pt = float(pt.get('height_m') or 0.0)
                clearance = tx_altitude - height_pt
                if clearance >= 5.0:
                    ray_mode = 'los'
                elif height_pt >= tx_altitude:
                    ray_mode = 'reflection'
                else:
                    ray_mode = 'obstruction'
                lat_idx = int(np.clip(np.searchsorted(lat_axis, lat_pt), 0, lat_axis.size - 1))
                lon_idx = int(np.clip(np.searchsorted(lon_axis, lon_pt), 0, lon_axis.size - 1))
                sample_quality = float(quality_map[lat_idx, lon_idx])
                rays.append({
                    'mode': ray_mode,
                    'path': [
                        {'lat': lat_tx_deg, 'lng': lon_tx_deg},
                        {'lat': lat_pt, 'lng': lon_pt},
                    ],
                    'height_m': height_pt,
                    'quality_db': sample_quality,
                })
            current_app.logger.info(
                'rt3d.penalty.applied',
                extra={'mode': 'scene', 'points': len(points_payload)},
            )
            return total_loss, meta

    api_key = current_app.config.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        current_app.logger.warning('rt3d.penalty.skip', extra={'reason': 'missing_assets'})
        return total_loss_db, meta

    num_rings = int(data.get('rt3dRings', 6))
    num_rays = int(data.get('rt3dRays', 32))
    num_rings = max(3, min(10, num_rings))
    num_rays = max(12, min(72, num_rays))

    samples_per_request = int(data.get('rt3dSamples', 180))
    samples_per_request = max(64, min(512, samples_per_request))

    collected_points: list[tuple[float, float]] = []
    penalties: list[float] = []

    for ring_idx in range(1, num_rings + 1):
        distance = radius_km * (ring_idx / num_rings)
        if distance < 0.1:
            continue
        for ray_idx in range(num_rays):
            bearing = (360.0 / num_rays) * ray_idx
            destination = geodesic(kilometers=distance).destination((lat_tx_deg, lon_tx_deg), bearing)
            rx_lat = destination.latitude
            rx_lon = destination.longitude
            profile = _google_elevation_profile(
                {'lat': lat_tx_deg, 'lng': lon_tx_deg},
                {'lat': rx_lat, 'lng': rx_lon},
                samples=max(samples_per_request, int(distance * 12)),
            )
            if not profile:
                continue
            elevations = np.asarray(profile['elevations_m'], dtype=float)
            penalty = _estimate_google_block_penalty(elevations)
            if penalty <= 0.3:
                continue
            collected_points.append((rx_lat, rx_lon))
            penalties.append(penalty)
            rays.append({
                'mode': 'profile',
                'path': [
                    {'lat': lat_tx_deg, 'lng': lon_tx_deg},
                    {'lat': rx_lat, 'lng': rx_lon},
                ],
                'quality_db': float(penalty) * -1.0,
            })

    if not penalties:
        current_app.logger.info('rt3d.penalty.skip', extra={'reason': 'no_penalties'})
        return total_loss_db, meta

    points = np.array(collected_points)
    values = np.array(penalties)
    penalty_surface = griddata(
        points,
        values,
        (lat_grid, lon_grid),
        method='linear',
        fill_value=0.0,
    )
    if np.isnan(penalty_surface).any():
        penalty_surface = np.nan_to_num(penalty_surface, nan=0.0)

    penalty_surface = np.clip(penalty_surface, 0.0, 36.0)
    diagnostics.update({'mode': 'profile', 'samples': len(penalties)})
    meta['mode'] = 'profile'
    if scene is not None:
        scene_payload['diagnostics'] = diagnostics
    if len(rays) > MAX_RAYS:
        stride = max(1, len(rays) // MAX_RAYS)
        rays[:] = rays[::stride]
    current_app.logger.info('rt3d.penalty.applied', extra={'samples': len(penalties), 'mode': 'profile'})
    return total_loss_db + penalty_surface, meta


def _render_field_strength_image(lons_deg, lats_deg, field_levels,
                                 radius_km, lon_center_deg, lat_center_deg,
                                 min_val, max_val, horizontal_pattern_db,
                                 dist_map_km=None, colorbar_label='Nível de Campo [dBµV/m]'):
    lon_grid, lat_grid = np.meshgrid(lons_deg, lats_deg)
    field_plot = np.array(field_levels, copy=True)

    if dist_map_km is not None:
        dist_km = np.asarray(dist_map_km, dtype=float)
        if dist_km.shape != field_plot.shape:
            raise ValueError('dist_map_km shape mismatch with field levels grid')
    else:
        dist = np.sqrt((lon_grid - lon_center_deg) ** 2 + (lat_grid - lat_center_deg) ** 2)
        earth_radius_km = 6371.0
        dist_km = dist * (np.pi / 180.0) * earth_radius_km
    field_plot[dist_km > radius_km] = np.nan

    masked_plot = np.ma.masked_invalid(field_plot)
    base_cmap = plt.cm.get_cmap('turbo')
    try:
        cmap = base_cmap.copy()
    except AttributeError:
        cmap = ListedColormap(base_cmap(np.linspace(0, 1, base_cmap.N)))
    cmap.set_bad(alpha=0.0)

    fig, ax = plt.subplots(figsize=(6, 6))

    feather_width = max(radius_km * 0.07, 0.5)
    transition = (radius_km - dist_km) / feather_width
    alpha_mask = np.clip(transition, 0.0, 1.0)
    alpha_mask[dist_km > radius_km] = 0.0
    if np.ma.isMaskedArray(masked_plot):
        alpha_mask = np.where(masked_plot.mask, 0.0, alpha_mask)

    mesh = ax.pcolormesh(
        lon_grid,
        lat_grid,
        masked_plot,
        cmap=cmap,
        shading='auto',
        vmin=min_val,
        vmax=max_val,
        alpha=alpha_mask,
    )

    if horizontal_pattern_db is not None:
        pattern_db = np.asarray(horizontal_pattern_db, dtype=float)
        if pattern_db.ndim == 0:
            pattern_db = np.array([pattern_db], dtype=float)
        pattern_db = np.nan_to_num(pattern_db, nan=-40.0)
        pattern_linear = np.clip(10.0 ** (pattern_db / 20.0), 1e-6, None)
        max_linear = float(np.nanmax(pattern_linear)) if np.isfinite(pattern_linear).any() else 1.0
        if max_linear <= 0:
            max_linear = 1.0
        pattern_linear = np.clip(pattern_linear / max_linear, 0.0, 1.0)
        polar_dimension = min(fig.get_size_inches()) * 0.35
        inset_left = (1.0 - polar_dimension / fig.get_size_inches()[0]) * 0.5
        inset_bottom = (1.0 - polar_dimension / fig.get_size_inches()[1]) * 0.5
        ax_inset = fig.add_axes(
            [inset_left, inset_bottom,
             polar_dimension / fig.get_size_inches()[0],
             polar_dimension / fig.get_size_inches()[1]],
            polar=True,
        )
        azimutes = np.linspace(0, 2 * np.pi, len(pattern_linear), endpoint=False)
        ax_inset.set_theta_zero_location('N')
        ax_inset.set_theta_direction(-1)
        ax_inset.plot(azimutes, pattern_linear, color='#0d47a1', linewidth=2)
        ax_inset.fill_between(azimutes, 0, pattern_linear, color='#0d47a1', alpha=0.12)
        ax_inset.set_xticks([])
        ax_inset.set_yticks([0.25, 0.5, 0.75, 1.0])
        ax_inset.set_yticklabels(['0.25', '0.50', '0.75', '1.00'], fontsize=7)
        ax_inset.set_ylim(0.0, 1.05)
        ax_inset.spines['polar'].set_visible(False)
        ax_inset.grid(True, linestyle='--', linewidth=0.6, alpha=0.4)
        ax_inset.set_title('Diagrama H (E/Emax)', fontsize=8)

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([]); ax.set_yticks([])
    ax.xaxis.label.set_visible(False); ax.yaxis.label.set_visible(False)

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', transparent=True)
    img_buffer.seek(0)
    image_base64 = base64.b64encode(img_buffer.read()).decode('utf-8')
    plt.close(fig)

    fig_cb, ax_cb = plt.subplots(figsize=(6, 1))
    norm = Normalize(vmin=min_val, vmax=max_val)
    scalar_map = ScalarMappable(norm=norm, cmap=cmap)
    scalar_map.set_array([])
    fig_cb.colorbar(scalar_map, cax=ax_cb, orientation='horizontal')
    ax_cb.set_title(colorbar_label)
    fig_cb.tight_layout()
    cb_buffer = io.BytesIO()
    fig_cb.savefig(cb_buffer, format='png', transparent=True)
    cb_buffer.seek(0)
    colorbar_base64 = base64.b64encode(cb_buffer.read()).decode('utf-8')
    plt.close(fig_cb)

    return image_base64, colorbar_base64


def _compute_rt3d_only_map(tx, data, include_arrays=False, label=None, rt3d_scene=None):
    def _coerce_optional(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        value_str = str(value).strip()
        if not value_str:
            return None
        try:
            return float(value_str)
        except ValueError:
            return None

    def _determine_auto_scale_local(arr, user_min, user_max, default_min=None, default_max=None):
        arr_np = np.asarray(arr, dtype=float)
        finite_vals = arr_np[np.isfinite(arr_np)]
        if finite_vals.size == 0:
            return (0.0, 1.0)
        auto_min = float(np.nanmin(finite_vals))
        auto_max = float(np.nanmax(finite_vals))
        vmin = float(user_min) if user_min is not None else (
            float(default_min) if default_min is not None else auto_min
        )
        vmax = float(user_max) if user_max is not None else (
            float(default_max) if default_max is not None else auto_max
        )
        if abs(vmax - vmin) < 1e-9:
            vmax = vmin + 1.0
        return (vmin, vmax)

    def _fill_holes(mask_in, arr_plot):
        vals = arr_plot[mask_in & np.isfinite(arr_plot)]
        if vals.size > 0:
            fill_val = float(np.nanmin(vals))
            hole_mask = mask_in & ~np.isfinite(arr_plot)
            arr_plot[hole_mask] = fill_val
        return arr_plot

    def _safe_value_at_center(arr, center_idx):
        arr_np = np.asarray(arr, dtype=float)
        try:
            return float(arr_np[center_idx])
        except Exception:
            if np.ndim(arr_np) == 0:
                return float(arr_np)
            finite_mask = np.isfinite(arr_np)
            if np.any(finite_mask):
                return float(np.nanmean(arr_np[finite_mask]))
            return None

    def _summarize_component(arr):
        arr_np = np.asarray(arr, dtype=float)
        finite_mask = np.isfinite(arr_np)
        if not np.any(finite_mask):
            return None
        return {
            'min': float(np.nanmin(arr_np)),
            'max': float(np.nanmax(arr_np)),
            'unit': 'dB',
            'center': float(np.nanmean(arr_np[finite_mask])),
        }

    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        lat1r = np.radians(lat1)
        lon1r = np.radians(lon1)
        lat2r = np.radians(lat2)
        lon2r = np.radians(lon2)
        dlat = lat2r - lat1r
        dlon = lon2r - lon1r
        a = (
            np.sin(dlat / 2.0) ** 2
            + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2.0) ** 2
        )
        return 2.0 * R * np.arcsin(np.sqrt(a))

    center_override = data.get('customCenter') or {}
    try:
        lon_tx_deg = float(center_override.get('lng', tx.longitude))
        lat_tx_deg = float(center_override.get('lat', tx.latitude))
    except (TypeError, ValueError):
        lon_tx_deg, lat_tx_deg = tx.longitude, tx.latitude

    haat_radials = []
    haat_average = None
    try:
        haat_radials, haat_average = _compute_haat_radials(
            lat_tx_deg,
            lon_tx_deg,
            getattr(tx, 'tower_height', None),
            getattr(tx, 'tx_site_elevation', None),
            dem_directory=dem_directory,
        )
    except Exception:
        haat_radials, haat_average = [], None

    radius_requested = _coerce_optional(data.get('radius'))
    if radius_requested is None or radius_requested <= 0:
        radius_requested = 10.0
    radius_km = float(radius_requested)

    freq_mhz = getattr(tx, 'frequencia', None) or 100.0
    if freq_mhz < 50.0:
        freq_mhz = 50.0
    frequency = freq_mhz

    loss_sys_db = tx.total_loss or 0.0
    Ptx_W = max(float(tx.transmission_power or 0.0), 1e-6)
    Gtx_peak_dBi = tx.antenna_gain or 0.0
    Grx_dBi = tx.rx_gain or 0.0

    building_source = (data.get('rt3dBuildingSource')
                       or getattr(tx, 'rt3dBuildingSource', None)
                       or 'auto').lower()
    ray_step_m = float(_coerce_optional(data.get('rt3dRayStep'))
                       or getattr(tx, 'rt3dRayStep', None)
                       or 25.0)
    diffraction_boost_db = float(_coerce_optional(data.get('rt3dDiffractionBoost'))
                                 or getattr(tx, 'rt3dDiffractionBoost', None)
                                 or 1.5)
    minimum_clearance_m = float(_coerce_optional(data.get('rt3dMinimumClearance'))
                                or getattr(tx, 'rt3dMinimumClearance', None)
                                or 2.0)

    grid_points = int(np.clip((radius_km * 1000.0) / max(ray_step_m, 5.0), 160, 360))
    lat_extent = radius_km / 111.32
    cos_lat = max(np.cos(np.radians(lat_tx_deg)), 0.25)
    lon_extent = radius_km / (111.32 * cos_lat)

    lats_deg = np.linspace(lat_tx_deg - lat_extent, lat_tx_deg + lat_extent, grid_points)
    lons_deg = np.linspace(lon_tx_deg - lon_extent, lon_tx_deg + lon_extent, grid_points)
    lon_grid, lat_grid = np.meshgrid(lons_deg, lats_deg)

    dist_km_grid = _haversine_km(lat_grid, lon_grid, lat_tx_deg, lon_tx_deg)
    inrange_mask = dist_km_grid <= radius_km

    base_loss_db = 32.45 + 20.0 * np.log10(frequency) + 20.0 * np.log10(np.clip(dist_km_grid, 0.05, None))
    base_loss_db = np.nan_to_num(base_loss_db, nan=0.0)

    total_path_loss_db, penalty_meta = _apply_rt3d_penalty(
        base_loss_db,
        lat_grid,
        lon_grid,
        lat_tx_deg,
        lon_tx_deg,
        radius_km,
        tx,
        data,
        scene=rt3d_scene,
    )

    Ptx_dBm = 10.0 * math.log10(Ptx_W / 0.001)
    Gtx_eff_db = float(Gtx_peak_dBi)

    Prx_dbm = (
        Ptx_dBm
        + Gtx_eff_db
        + float(Grx_dBi)
        - float(loss_sys_db)
        - total_path_loss_db
    )
    Prx_dbm = np.asarray(Prx_dbm, dtype=float)

    freq_mhz_safe = max(float(freq_mhz), 0.1)
    E_dbuv = (
        Prx_dbm
        - float(Grx_dBi)
        + 77.2
        + 20.0 * np.log10(freq_mhz_safe)
    )
    E_dbuv = np.asarray(E_dbuv, dtype=float)

    E_plot = np.full_like(E_dbuv, np.nan, dtype=float)
    Prx_plot = np.full_like(Prx_dbm, np.nan, dtype=float)
    E_plot[inrange_mask] = E_dbuv[inrange_mask]
    Prx_plot[inrange_mask] = Prx_dbm[inrange_mask]
    E_plot = _fill_holes(inrange_mask, E_plot)
    Prx_plot = _fill_holes(inrange_mask, Prx_plot)

    min_val, max_val = _determine_auto_scale_local(
        E_plot,
        _coerce_optional(data.get('minSignalLevel')),
        _coerce_optional(data.get('maxSignalLevel')),
        default_min=10.0,
        default_max=60.0,
    )
    power_min, power_max = _determine_auto_scale_local(Prx_plot, None, None)

    lat_diff = np.abs(lats_deg - lat_tx_deg)
    lon_diff = np.abs(lons_deg - lon_tx_deg)
    center_idx_lat = int(np.argmin(lat_diff))
    center_idx_lon = int(np.argmin(lon_diff))
    center_idx = (center_idx_lat, center_idx_lon)

    bounds = {
        'north': float(np.nanmax(lats_deg) + (lat_extent / grid_points)),
        'south': float(np.nanmin(lats_deg) - (lat_extent / grid_points)),
        'east': float(np.nanmax(lons_deg) + (lon_extent / grid_points)),
        'west': float(np.nanmin(lons_deg) - (lon_extent / grid_points)),
    }
    colorbar_bounds = {
        'north': bounds['north'],
        'south': bounds['north'] - max((bounds['north'] - bounds['south']) * 0.05, 1e-4),
        'east': bounds['east'],
        'west': bounds['west'],
    }

    loss_components_summary = {}
    summary_fspl = _summarize_component(base_loss_db)
    if summary_fspl:
        loss_components_summary['fspl'] = summary_fspl
    if penalty_meta.get('occlusion_map') is not None:
        summary_penalty = _summarize_component(
            penalty_meta['occlusion_map']
            + penalty_meta.get('multipath_map', 0.0)
            - penalty_meta.get('reflection_map', 0.0)
        )
        if summary_penalty:
            loss_components_summary['rt3d_penalty'] = summary_penalty

    center_metrics = {
        'combined_loss_center_db': _safe_value_at_center(total_path_loss_db, center_idx),
        'received_power_center_dbm': _safe_value_at_center(Prx_dbm, center_idx),
        'field_center_dbuv_m': _safe_value_at_center(E_dbuv, center_idx),
        'effective_gain_center_db': float(Gtx_eff_db),
        'tx_power_dbm': float(Ptx_dBm),
        'system_losses_db': float(loss_sys_db),
        'frequency_mhz': float(freq_mhz),
        'radius_km': float(radius_km),
    }

    try:
        center_metrics['distance_center_km'] = float(dist_km_grid[center_idx])
    except Exception:
        pass

    horizontal_pattern_db = gain_comp_raw.get('horizontal_pattern_db')

    img_dbuv_b64, colorbar_dbuv_b64 = _render_field_strength_image(
        lons_deg,
        lats_deg,
        E_plot,
        radius_km,
        lon_tx_deg,
        lat_tx_deg,
        min_val,
        max_val,
        horizontal_pattern_db,
        dist_map_km=dist_km_grid,
        colorbar_label='Campo elétrico [dBµV/m]'
    )

    img_dbm_b64, colorbar_dbm_b64 = _render_field_strength_image(
        lons_deg,
        lats_deg,
        Prx_plot,
        radius_km,
        lon_tx_deg,
        lat_tx_deg,
        power_min,
        power_max,
        None,
        dist_map_km=dist_km_grid,
        colorbar_label='Potência recebida [dBm]'
    )

    quality_map = penalty_meta.get('quality_map')
    if quality_map is not None:
        qmin, qmax = _determine_auto_scale_local(quality_map, None, None)
        img_rt3d_b64, colorbar_rt3d_b64 = _render_field_strength_image(
            lons_deg,
            lats_deg,
            quality_map,
            radius_km,
            lon_tx_deg,
            lat_tx_deg,
            qmin,
            qmax,
            None,
            dist_map_km=dist_km_grid,
            colorbar_label='Qualidade RT3D [dB]',
        )
        images_payload = {
            "rt3d": {
                "image": img_rt3d_b64,
                "colorbar": colorbar_rt3d_b64,
                "label": "Qualidade RT3D [dB]",
                "unit": "rt3d",
            },
        }
        scale_payload = {
            "default_unit": "rt3d",
            "min": qmin,
            "max": qmax,
            "units": {
                "rt3d": {"min": qmin, "max": qmax},
            },
        }
    else:
        images_payload = {
            "dbuv": {
                "image": img_dbuv_b64,
                "colorbar": colorbar_dbuv_b64,
                "label": "Campo elétrico [dBµV/m]",
                "unit": "dBµV/m",
            },
            "dbm": {
                "image": img_dbm_b64,
                "colorbar": colorbar_dbm_b64,
                "label": "Potência recebida [dBm]",
                "unit": "dBm",
            },
        }
        scale_payload = {
            "default_unit": "dbuv",
            "min": min_val,
            "max": max_val,
            "units": {
                "dbuv": {"min": min_val, "max": max_val},
                "dbm": {"min": power_min, "max": power_max},
            },
        }

    signal_level_dict = {}
    signal_level_dict_dbm = {}
    for i, lat_val in enumerate(lats_deg):
        for j, lon_val in enumerate(lons_deg):
            if not inrange_mask[i, j]:
                continue
            if np.isfinite(E_dbuv[i, j]):
                signal_level_dict[f"({lat_val}, {lon_val})"] = float(E_dbuv[i, j])
            if np.isfinite(Prx_dbm[i, j]):
                signal_level_dict_dbm[f"({lat_val}, {lon_val})"] = float(Prx_dbm[i, j])

    tile_min_zoom, tile_max_zoom = _estimate_tile_zoom(bounds)
    tile_stats_payload = _build_tile_signal_stats(signal_level_dict, tile_min_zoom, tile_max_zoom)

    payload = {
        "images": images_payload,
        "bounds": bounds,
        "colorbar_bounds": colorbar_bounds,
        "scale": scale_payload,
        "center": {"lat": float(lat_tx_deg), "lng": float(lon_tx_deg)},
        "requested_radius_km": radius_km,
        "radius": data.get('radius', radius_km),
        "location_status": getattr(tx, 'location_status', None),
        "tx_location_name": getattr(tx, 'tx_location_name', None),
        "tx_site_elevation": getattr(tx, 'tx_site_elevation', None),
        "antenna_direction": getattr(tx, 'antenna_direction', None),
        "tx_parameters": {
            "power_w": getattr(tx, 'transmission_power', None),
            "tower_height_m": getattr(tx, 'tower_height', None),
            "rx_height_m": getattr(tx, 'rx_height', None),
            "total_loss_db": getattr(tx, 'total_loss', None),
            "antenna_gain_dbi": getattr(tx, 'antenna_gain', None),
        },
        "gain_components": {
            "base_gain_dbi": float(Gtx_peak_dBi),
            "horizontal_adjustment_db_min": 0.0,
            "horizontal_adjustment_db_max": 0.0,
            "vertical_adjustment_db": 0.0,
            "horizontal_pattern_db": None,
            "vertical_pattern_db": None,
            "vertical_horizon_db": None,
        },
        "loss_components": loss_components_summary,
        "center_metrics": center_metrics,
        "signal_level_dict": signal_level_dict,
        "signal_level_dict_dbm": signal_level_dict_dbm,
        "rt3dDiagnostics": penalty_meta.get('diagnostics'),
        "rt3dSettings": {
            "building_source": building_source,
            "ray_step_m": ray_step_m,
            "diffraction_boost_db": diffraction_boost_db,
            "minimum_clearance_m": minimum_clearance_m,
        },
    }
    if haat_radials:
        payload['haat_radials'] = haat_radials
    if haat_average is not None:
        payload['haat_average_m'] = haat_average
    if haat_radials:
        payload['haat_radials'] = haat_radials
    if haat_average is not None:
        payload['haat_average_m'] = haat_average
    if tile_min_zoom is not None and tile_max_zoom is not None:
        payload["tile_zoom"] = {"min": int(tile_min_zoom), "max": int(tile_max_zoom)}
    if tile_stats_payload:
        payload["tile_stats"] = tile_stats_payload
    if penalty_meta.get('rays'):
        payload['rt3dRays'] = penalty_meta['rays']

    if label is not None:
        payload["label"] = str(label)

    return payload

def _normalize_bounds_payload(bounds):
    if not bounds:
        return None
    if isinstance(bounds, dict):
        north = _coerce_float(
            bounds.get('north')
            or bounds.get('max_lat')
            or bounds.get('maxLat')
            or (bounds.get('northEast') or {}).get('lat')
        )
        south = _coerce_float(
            bounds.get('south')
            or bounds.get('min_lat')
            or bounds.get('minLat')
            or (bounds.get('southWest') or {}).get('lat')
        )
        east = _coerce_float(
            bounds.get('east')
            or bounds.get('max_lng')
            or bounds.get('maxLon')
            or bounds.get('maxLon')
            or (bounds.get('northEast') or {}).get('lng')
            or (bounds.get('northEast') or {}).get('lon')
        )
        west = _coerce_float(
            bounds.get('west')
            or bounds.get('min_lng')
            or bounds.get('minLon')
            or (bounds.get('southWest') or {}).get('lng')
            or (bounds.get('southWest') or {}).get('lon')
        )
    elif isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
        south = _coerce_float(bounds[0])
        west = _coerce_float(bounds[1])
        north = _coerce_float(bounds[2])
        east = _coerce_float(bounds[3])
    else:
        return None

    if None in (north, south, east, west):
        return None
    # normaliza longitude para [-180, 180]
    if east - west > 360:
        east = west + 360
    def _normalize_lon(value):
        if value is None:
            return None
        value = float(value)
        while value > 180:
            value -= 360
        while value < -180:
            value += 360
        return value
    return {
        'north': float(north),
        'south': float(south),
        'east': _normalize_lon(east),
        'west': _normalize_lon(west),
    }


def _normalize_point_payload(point):
    if isinstance(point, dict):
        lat = _coerce_float(point.get('lat') or point.get('latitude'))
        lng = _coerce_float(point.get('lng') or point.get('lon') or point.get('longitude'))
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        lat = _coerce_float(point[0])
        lng = _coerce_float(point[1])
    else:
        lat = None
        lng = None
    if lat is None or lng is None:
        return (None, None)
    return (float(lat), float(lng))


def _latlon_to_pixel_xy(lat, lon, zoom, tile_size=256):
    siny = math.sin(math.radians(lat))
    siny = min(max(siny, -0.9999), 0.9999)
    scale = tile_size * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale
    return x, y


def _estimate_zoom_from_bounds(bounds, width_px, height_px):
    if not bounds:
        return 12

    def _lat_rad(lat):
        siny = math.sin(math.radians(lat))
        siny = min(max(siny, -0.9999), 0.9999)
        return math.log((1 + siny) / (1 - siny)) / 2

    lat_fraction = abs(_lat_rad(bounds['north']) - _lat_rad(bounds['south'])) / math.pi
    lon_diff = bounds['east'] - bounds['west']
    if lon_diff < 0:
        lon_diff += 360
    lon_fraction = abs(lon_diff) / 360

    if lat_fraction == 0:
        lat_fraction = 1e-6
    if lon_fraction == 0:
        lon_fraction = 1e-6

    lat_zoom = math.log(height_px / 256 / lat_fraction) / math.log(2)
    lon_zoom = math.log(width_px / 256 / lon_fraction) / math.log(2)
    zoom = min(lat_zoom, lon_zoom) - 0.5  # margem
    zoom = max(3, min(18, zoom))
    return int(round(zoom))


def _download_static_map_image(center_lat, center_lng, zoom, size_px, scale, api_key, project_slug=None):
    params = {
        'center': f'{center_lat},{center_lng}',
        'zoom': max(3, min(20, int(zoom))),
        'size': f'{size_px}x{size_px}',
        'scale': scale,
        'maptype': 'roadmap',
        'style': 'feature:poi|visibility:off',
        'key': api_key,
    }
    try:
        response = requests.get(
            'https://maps.googleapis.com/maps/api/staticmap',
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert('RGB')
    except Exception as exc:
        current_app.logger.warning(
            'coverage.map_snapshot.fetch_failed',
            extra={'project': project_slug or '-', 'error': str(exc)},
        )
        return None


def _fetch_osm_tile(session, zoom, x, y, user_agent=None):
    url = f'https://tile.openstreetmap.org/{zoom}/{x}/{y}.png'
    headers = {'User-Agent': user_agent or 'ATXCoverage/1.0 (+tile-capture)'}
    resp = session.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert('RGB')


def _build_osm_static_map(center_lat, center_lng, zoom, width_px, height_px, project_slug=None):
    tile_size = 256
    num_tiles = 2 ** zoom
    if num_tiles <= 0:
        return None
    center_x, center_y = _latlon_to_pixel_xy(center_lat, center_lng, zoom)
    top_left_x = center_x - (width_px / 2.0)
    top_left_y = center_y - (height_px / 2.0)
    bottom_right_x = center_x + (width_px / 2.0)
    bottom_right_y = center_y + (height_px / 2.0)

    tile_x_start = math.floor(top_left_x / tile_size)
    tile_y_start = math.floor(top_left_y / tile_size)
    tile_x_end = math.floor((bottom_right_x - 1) / tile_size)
    tile_y_end = math.floor((bottom_right_y - 1) / tile_size)

    mosaic_width_px = (tile_x_end - tile_x_start + 1) * tile_size
    mosaic_height_px = (tile_y_end - tile_y_start + 1) * tile_size
    if mosaic_width_px <= 0 or mosaic_height_px <= 0:
        return None

    session = requests.Session()
    mosaic = Image.new('RGB', (mosaic_width_px, mosaic_height_px), (240, 240, 240))
    for ix, tile_x in enumerate(range(tile_x_start, tile_x_end + 1)):
        wrapped_x = tile_x % num_tiles
        for iy, tile_y in enumerate(range(tile_y_start, tile_y_end + 1)):
            paste_x = ix * tile_size
            paste_y = iy * tile_size
            if tile_y < 0 or tile_y >= num_tiles:
                tile_img = Image.new('RGB', (tile_size, tile_size), (238, 238, 238))
            else:
                try:
                    tile_img = _fetch_osm_tile(session, zoom, wrapped_x, tile_y)
                except Exception as exc:
                    current_app.logger.warning(
                        'coverage.osm_tile_failed',
                        extra={'project': project_slug or '-', 'zoom': zoom, 'tile': f'{wrapped_x}/{tile_y}', 'error': str(exc)},
                    )
                    tile_img = Image.new('RGB', (tile_size, tile_size), (238, 238, 238))
            mosaic.paste(tile_img, (paste_x, paste_y))
            tile_img.close()

    crop_x = int(round(top_left_x - tile_x_start * tile_size))
    crop_y = int(round(top_left_y - tile_y_start * tile_size))
    crop_box = (
        max(0, crop_x),
        max(0, crop_y),
        min(mosaic.width, crop_x + width_px),
        min(mosaic.height, crop_y + height_px),
    )
    if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
        return mosaic
    cropped = mosaic.crop(crop_box)
    if cropped.size != (width_px, height_px):
        padded = Image.new('RGB', (width_px, height_px), (240, 240, 240))
        padded.paste(cropped, (0, 0))
        mosaic.close()
        return padded
    return cropped


def _overlay_heatmap_on_base(base_img, heatmap_path, bounds, center_lat, center_lng, zoom, opacity):
    try:
        overlay_img = Image.open(heatmap_path).convert('RGBA')
    except Exception:
        return base_img.convert('RGBA')

    width_px, height_px = base_img.size
    center_x, center_y = _latlon_to_pixel_xy(center_lat, center_lng, zoom)
    top_left_x = center_x - (width_px / 2)
    top_left_y = center_y - (height_px / 2)

    nw_x, nw_y = _latlon_to_pixel_xy(bounds['north'], bounds['west'], zoom)
    se_x, se_y = _latlon_to_pixel_xy(bounds['south'], bounds['east'], zoom)

    left = min(nw_x, se_x)
    right = max(nw_x, se_x)
    top = min(nw_y, se_y)
    bottom = max(nw_y, se_y)

    target_width = max(1, int(round(right - left)))
    target_height = max(1, int(round(bottom - top)))

    resized_overlay = overlay_img.resize((target_width, target_height), resample=Image.BILINEAR)

    alpha = resized_overlay.getchannel('A') if 'A' in resized_overlay.getbands() else Image.new('L', resized_overlay.size, color=255)
    alpha = alpha.point(lambda px: int(px * opacity))
    resized_overlay.putalpha(alpha)

    overlay_canvas = Image.new('RGBA', (width_px, height_px), (0, 0, 0, 0))
    paste_x = int(round(left - top_left_x))
    paste_y = int(round(top - top_left_y))

    crop_left = max(0, -paste_x)
    crop_top = max(0, -paste_y)
    crop_right = min(resized_overlay.width, overlay_canvas.width - paste_x + crop_left)
    crop_bottom = min(resized_overlay.height, overlay_canvas.height - paste_y + crop_top)

    if crop_right <= crop_left or crop_bottom <= crop_top:
        overlay_img.close()
        resized_overlay.close()
        return base_img.convert('RGBA')

    cropped = resized_overlay.crop((crop_left, crop_top, crop_right, crop_bottom))
    overlay_canvas.paste(cropped, (max(paste_x, 0), max(paste_y, 0)), cropped)

    base_rgba = base_img.convert('RGBA')
    combined = Image.alpha_composite(base_rgba, overlay_canvas)

    overlay_img.close()
    resized_overlay.close()
    cropped.close()
    return combined


def _append_colorbar_to_snapshot(base_img, colorbar_blob):
    if not colorbar_blob:
        return base_img
    try:
        colorbar_img = Image.open(io.BytesIO(colorbar_blob)).convert('RGBA')
    except Exception:
        return base_img

    max_width = max(50, base_img.width - 80)
    target_width = min(max_width, colorbar_img.width)
    ratio = target_width / float(colorbar_img.width)
    target_height = max(1, int(round(colorbar_img.height * ratio)))

    resized = colorbar_img.resize((target_width, target_height), resample=Image.LANCZOS)

    canvas = Image.new('RGBA', (base_img.width, base_img.height + target_height + 48), (255, 255, 255, 255))
    canvas.paste(base_img, (0, 0))
    text_position = (40, base_img.height + 10)
    draw = ImageDraw.Draw(canvas)
    draw.text(text_position, "Escala aplicada [dBµV/m]", fill=(31, 41, 55))

    canvas.paste(resized, ((canvas.width - target_width) // 2, base_img.height + 24), resized)

    colorbar_img.close()
    resized.close()
    base_img.close()
    return canvas


def _render_map_snapshot_image(
    heatmap_bytes: bytes | None,
    colorbar_bytes: bytes | None,
    bounds_payload,
    center_payload,
    project_slug=None,
    radius_km=None,
):
    bounds = _normalize_bounds_payload(bounds_payload)
    if not bounds or not heatmap_bytes:
        return None

    center_lat, center_lng = _normalize_point_payload(center_payload)
    if center_lat is None or center_lng is None:
        center_lat = (bounds['north'] + bounds['south']) / 2.0
        center_lng = (bounds['east'] + bounds['west']) / 2.0

    base_size = 640
    scale = 2
    width_px = base_size * scale
    height_px = base_size * scale
    zoom = _estimate_zoom_from_bounds(bounds, width_px, height_px)
    if radius_km and center_lat is not None:
        try:
            radius_value = max(float(radius_km), 1.0)
            diameter_m = radius_value * 2000.0
            target_pixels = width_px * 0.75
            cos_lat = max(math.cos(math.radians(center_lat)), 0.2)
            meters_per_pixel = max(diameter_m / target_pixels, 1.0)
            zoom_radius = math.log2((156543.03392 * cos_lat) / meters_per_pixel)
            zoom = min(18, max(zoom, zoom_radius))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    zoom = max(3.0, min(18.0, zoom))
    zoom = int(round(zoom))
    base_img = None
    google_key = get_google_maps_key()
    if google_key:
        base_img = _download_static_map_image(center_lat, center_lng, zoom, base_size, scale, google_key, project_slug=project_slug)
    if base_img is None:
        base_img = _build_osm_static_map(center_lat, center_lng, zoom, width_px, height_px, project_slug=project_slug)
    if base_img is None:
        return None

    try:
        composed = _overlay_heatmap_on_base(
            base_img,
            io.BytesIO(heatmap_bytes),
            bounds,
            center_lat,
            center_lng,
            zoom,
            opacity=0.78,
        )
        final_canvas = _append_colorbar_to_snapshot(composed, colorbar_bytes)
        final_rgb = final_canvas.convert('RGB')
        buffer = io.BytesIO()
        final_rgb.save(buffer, format='PNG', optimize=True)
        buffer.seek(0)
        snapshot_bytes = buffer.read()
        final_rgb.close()
        final_canvas.close()
        composed.close()
    finally:
        base_img.close()
    return zoom, snapshot_bytes



def _persist_coverage_artifacts(user, project, engine_value, request_payload, coverage_payload):
    if project is None:
        return None

    engine_enum = CoverageEngine(engine_value)
    timestamp = datetime.utcnow()
    timestamp_iso = timestamp.isoformat()
    base_name = f"coverage_{timestamp.strftime('%Y%m%d_%H%M%S')}_{engine_enum.value}"

    def _json_default(obj):
        if isinstance(obj, (np.floating, float)):
            return float(obj)
        if isinstance(obj, (np.integer, int)):
            return int(obj)
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        return str(obj)

    def _clean_json(data):
        if data is None:
            return None
        try:
            return json.loads(json.dumps(data, default=_json_default))
        except TypeError:
            return data

    def _decode_image_b64(data_str):
        if not data_str:
            return b''
        return base64.b64decode(data_str)

    def _encode_blob(blob):
        if not blob:
            return None
        return base64.b64encode(blob).decode('utf-8')

    images_payload = coverage_payload.get('images') or {}
    selected_image = None
    selected_unit = None
    for unit in ('rt3d', 'dbuv', 'dbm'):
        entry = images_payload.get(unit)
        if entry and entry.get('image'):
            selected_image = entry
            selected_unit = unit
            break

    heatmap_bytes = _decode_image_b64(selected_image.get('image') if selected_image else None)
    colorbar_bytes = _decode_image_b64(selected_image.get('colorbar') if selected_image else None)

    receivers_payload = coverage_payload.get('receivers') or request_payload.get('receivers')

    summary_payload = {
        "engine": engine_enum.value,
        "generated_at": timestamp_iso,
        "project_slug": project.slug,
        "request": _clean_json(request_payload),
        "center_metrics": _clean_json(coverage_payload.get('center_metrics')),
        "loss_components": _clean_json(coverage_payload.get('loss_components')),
        "bounds": _clean_json(coverage_payload.get('bounds')),
        "colorbar_bounds": _clean_json(coverage_payload.get('colorbar_bounds')),
        "scale": _clean_json(coverage_payload.get('scale')),
        "tx_location": _clean_json(coverage_payload.get('center')),
        "center": _clean_json(coverage_payload.get('center')),
        "requested_radius_km": _clean_json(coverage_payload.get('requested_radius_km')),
        "radius": _clean_json(coverage_payload.get('radius')),
        "gain_components": _clean_json(coverage_payload.get('gain_components')),
        "signal_level_dict": _clean_json(coverage_payload.get('signal_level_dict')),
        "signal_level_dict_dbm": _clean_json(coverage_payload.get('signal_level_dict_dbm')),
        "location_status": coverage_payload.get('location_status'),
        "tx_location_name": coverage_payload.get('tx_location_name'),
        "tx_site_elevation": coverage_payload.get('tx_site_elevation'),
        "tx_parameters": _clean_json(coverage_payload.get('tx_parameters')),
        "receivers": _clean_json(receivers_payload),
        "rt3d_scene": _clean_json(coverage_payload.get('rt3dScene')),
        "rt3d_diagnostics": _clean_json(coverage_payload.get('rt3dDiagnostics')),
        "rt3d_rays": _clean_json(coverage_payload.get('rt3dRays')),
        "rt3d_settings": _clean_json(coverage_payload.get('rt3dSettings')),
    }
    if coverage_payload.get('haat_radials'):
        summary_payload["haat_radials"] = _clean_json(coverage_payload.get('haat_radials'))
    if coverage_payload.get('haat_average_m') is not None:
        summary_payload["haat_average_m"] = _clean_json(coverage_payload.get('haat_average_m'))
    summary_payload["diagram_horizontal_b64"] = _encode_blob(getattr(user, 'antenna_pattern_img_dia_H', None))
    summary_payload["diagram_vertical_b64"] = _encode_blob(getattr(user, 'antenna_pattern_img_dia_V', None))

    def _create_asset_from_bytes(kind: str, extension: str, blob: bytes | None, asset_type: AssetType, meta: dict) -> Asset | None:
        if not blob:
            return None
        meta = dict(meta or {})
        mime = meta.pop('mime_type', None) or ('application/json' if extension.endswith('json') else 'image/png')
        asset = Asset(
            project_id=project.id,
            type=asset_type,
            path=inline_asset_path(kind, extension),
            mime_type=mime,
            byte_size=len(blob),
            data=blob,
            meta=meta,
        )
        db.session.add(asset)
        return asset

    heatmap_asset = _create_asset_from_bytes(
        'coverage',
        'png',
        heatmap_bytes,
        AssetType.heatmap,
        {
            "engine": engine_enum.value,
            "generated_at": timestamp_iso,
            "unit": selected_image.get('unit') if selected_image else None,
            "label": selected_image.get('label') if selected_image else None,
            "radius_km": coverage_payload.get('requested_radius_km'),
        },
    )

    colorbar_asset = _create_asset_from_bytes(
        'coverage',
        'png',
        colorbar_bytes,
        AssetType.png,
        {
            "engine": engine_enum.value,
            "generated_at": timestamp_iso,
            "label": selected_image.get('label') if selected_image else None,
        },
    ) if colorbar_bytes else None

    map_snapshot_asset = None
    map_snapshot_zoom = None
    bounds_payload = coverage_payload.get('bounds')
    if bounds_payload and heatmap_bytes:
        try:
            render_result = _render_map_snapshot_image(
                heatmap_bytes,
                colorbar_bytes,
                bounds_payload,
                coverage_payload.get('center') or coverage_payload.get('tx_location'),
                project_slug=project.slug,
                radius_km=coverage_payload.get('requested_radius_km') or coverage_payload.get('radius'),
            )
        except Exception as exc:
            current_app.logger.warning(
                'coverage.snapshot.render_failed',
                extra={'error': str(exc)},
            )
            render_result = None
        if render_result:
            map_snapshot_zoom, snapshot_blob = render_result
            map_snapshot_asset = _create_asset_from_bytes(
                'coverage',
                'png',
                snapshot_blob,
                AssetType.png,
                {
                    "engine": engine_enum.value,
                    "generated_at": timestamp_iso,
                    "zoom": map_snapshot_zoom,
                },
            )

    if heatmap_asset:
        summary_payload["asset_id"] = str(heatmap_asset.id)
        summary_payload["asset_path"] = heatmap_asset.path
    if colorbar_asset:
        summary_payload["colorbar_asset_id"] = str(colorbar_asset.id)
    if map_snapshot_asset:
        summary_payload["map_snapshot_asset_id"] = str(map_snapshot_asset.id)
        summary_payload["map_snapshot_path"] = map_snapshot_asset.path
        summary_payload["map_snapshot_zoom"] = map_snapshot_zoom

    ibge_registry = {}
    if receivers_payload:
        for receiver in receivers_payload:
            ibge_info = receiver.get('ibge') or {}
            code = ibge_info.get('code') or ibge_info.get('ibge_code')
            if not code:
                continue
            ibge_registry[str(code)] = _clean_json(ibge_info)
    if ibge_registry:
        summary_payload["ibge_registry"] = ibge_registry
    if not summary_payload.get('receivers'):
        summary_payload["receivers"] = _clean_json(_project_receivers_payload(project))
    if coverage_payload.get('rt3dScene'):
        summary_payload["rt3d_scene"] = _clean_json(coverage_payload.get('rt3dScene'))
    if coverage_payload.get('rt3dDiagnostics'):
        summary_payload["rt3d_diagnostics"] = _clean_json(coverage_payload.get('rt3dDiagnostics'))

    tile_metadata = None
    bounds_payload = coverage_payload.get('bounds')
    if bounds_payload and heatmap_asset:
        try:
            min_zoom = None
            max_zoom = None
            tile_zoom_payload = coverage_payload.get('tile_zoom') or {}
            if tile_zoom_payload:
                min_zoom = tile_zoom_payload.get('min')
                max_zoom = tile_zoom_payload.get('max')
            if min_zoom is None or max_zoom is None:
                min_zoom, max_zoom = _estimate_tile_zoom(bounds_payload)
            if min_zoom is not None and max_zoom is not None:
                base_tile_url = url_for(
                    'projects.coverage_tile',
                    slug=project.slug,
                    asset_id=str(heatmap_asset.id),
                    z=0,
                    x=0,
                    y=0,
                )
                template_url = base_tile_url.replace('/0/0/0.png', '/{z}/{x}/{y}.png')
                tile_metadata = {
                    "asset_id": str(heatmap_asset.id),
                    "url_template": template_url,
                    "min_zoom": int(min_zoom),
                    "max_zoom": int(max_zoom),
                    "bounds": _clean_json(bounds_payload),
                }
                tile_stats_payload = coverage_payload.get('tile_stats')
                if tile_stats_payload:
                    tile_metadata["stats"] = tile_stats_payload
        except Exception as exc:
            current_app.logger.warning(
                'coverage.tiles.metadata_failed',
                extra={'error': str(exc)},
            )

    summary_payload["image_unit"] = selected_unit
    if colorbar_asset:
        summary_payload["colorbar_asset_id"] = str(colorbar_asset.id)
    if map_snapshot_asset:
        summary_payload["map_snapshot_asset_id"] = str(map_snapshot_asset.id)
        summary_payload["map_snapshot_path"] = map_snapshot_asset.path
        summary_payload["map_snapshot_zoom"] = map_snapshot_zoom

    if ibge_registry:
        summary_payload.setdefault("ibge_registry", ibge_registry)
    if tile_metadata:
        sanitized_tiles = _sanitize_tiles_payload(project, tile_metadata)
        if sanitized_tiles:
            summary_payload["tiles"] = _clean_json(sanitized_tiles)
    updated_settings = dict(project.settings or {})
    updated_settings.pop('lastCoverage', None)
    updated_settings['lastCoverage'] = _clean_json(summary_payload)
    project.settings = updated_settings

    json_blob = json.dumps(summary_payload, ensure_ascii=False, indent=2, default=_json_default).encode('utf-8')
    json_asset = _create_asset_from_bytes(
        'coverage',
        'json',
        json_blob,
        AssetType.json,
        {
            "engine": engine_enum.value,
            "generated_at": timestamp_iso,
            "mime_type": "application/json",
        },
    )
    if json_asset:
        summary_payload["json_asset_id"] = str(json_asset.id)

    job = CoverageJob(
        project_id=project.id,
        status=CoverageStatus.succeeded,
        engine=engine_enum,
        inputs={
            "request": request_payload,
            "project_settings": _clean_json(project.settings),
        },
        metrics={
            "center_metrics": _clean_json(coverage_payload.get('center_metrics')),
            "loss_components": _clean_json(coverage_payload.get('loss_components')),
            "summary": _clean_json(summary_payload),
        },
        outputs_asset_id=heatmap_asset.id if heatmap_asset else None,
        started_at=timestamp,
        finished_at=timestamp,
    )
    db.session.add(job)

    _persist_project_coverage_record(
        project,
        summary_payload,
        heatmap_asset=heatmap_asset,
        colorbar_asset=colorbar_asset,
        map_snapshot_asset=map_snapshot_asset,
        summary_asset=json_asset,
    )

    return {
        "heatmap_asset": heatmap_asset,
        "json_asset": json_asset,
        "colorbar_asset": colorbar_asset,
        "map_snapshot_asset": map_snapshot_asset,
        "job": job,
        "timestamp": timestamp_iso,
        "tiles": tile_metadata,
    }



def _latest_coverage_snapshot(project: Project | None):
    if project is None:
        return None

    coverage_record = (
        ProjectCoverage.query.filter_by(project_id=project.id)
        .order_by(ProjectCoverage.generated_at.desc().nullslast(), ProjectCoverage.created_at.desc().nullslast())
        .first()
    )
    payload = _serialize_project_coverage(coverage_record)
    if payload:
        return payload

    job = (
        CoverageJob.query
        .filter_by(project_id=project.id, status=CoverageStatus.succeeded)
        .order_by(
            CoverageJob.finished_at.desc().nullslast(),
            CoverageJob.started_at.desc().nullslast(),
            CoverageJob.created_at.desc().nullslast(),
        )
        .first()
    )

    settings_snapshot = _sanitize_snapshot_assets(project, (project.settings or {}).get('lastCoverage') or {}) or {}

    if not job:
        if settings_snapshot:
            snapshot = dict(settings_snapshot)
            snapshot.setdefault('project_slug', project.slug)
            snapshot.setdefault('receivers', _project_receivers_payload(project))
            return snapshot
        return None

    metrics = job.metrics or {}
    summary = dict(metrics.get('summary') or {})

    for key, value in settings_snapshot.items():
        summary.setdefault(key, value)

    asset = None
    if job.outputs_asset_id:
        asset = Asset.query.filter_by(id=job.outputs_asset_id, project_id=project.id).first()

    if asset and not _asset_file_exists(asset):
        asset = None

    if not asset:
        return None

    summary['asset_id'] = str(summary.get('asset_id') or asset.id)
    summary['asset_path'] = summary.get('asset_path') or asset.path
    summary['engine'] = summary.get('engine') or (job.engine.value if job.engine else None)
    summary['generated_at'] = summary.get('generated_at') or (
        job.finished_at.isoformat() if job.finished_at else (
            job.started_at.isoformat() if job.started_at else None
        )
    )
    summary['project_slug'] = project.slug
    summary['receivers'] = summary.get('receivers') or _project_receivers_payload(project)

    if summary.get('json_asset_id') is not None:
        summary['json_asset_id'] = str(summary['json_asset_id'])
    if summary.get('colorbar_asset_id') is not None:
        summary['colorbar_asset_id'] = str(summary['colorbar_asset_id'])

    summary.setdefault('center_metrics', metrics.get('center_metrics'))
    summary.setdefault('loss_components', metrics.get('loss_components'))
    summary.setdefault('scale', summary.get('scale'))
    summary.setdefault('bounds', summary.get('bounds'))
    if 'center' not in summary or summary['center'] is None:
        summary['center'] = summary.get('tx_location')
    summary.setdefault('requested_radius_km', summary.get('requested_radius_km') or summary.get('radius_km'))
    summary.setdefault('receivers', summary.get('receivers') or [])

    return summary


@bp.route('/projects/<slug>/coverage.kml')
@login_required
def download_coverage_kml(slug):
    project = _load_project_for_current_user(slug)
    snapshot = _latest_coverage_snapshot(project)
    if not snapshot or not snapshot.get('asset_id'):
        return jsonify({'error': 'Nenhuma mancha disponível para exportação.'}), 404
    bounds = _normalize_bounds_payload(snapshot.get('bounds'))
    if not bounds:
        return jsonify({'error': 'Cobertura sem limites válidos para gerar KML.'}), 400

    overlay_bytes = _load_asset_bytes(snapshot.get('asset_id'), snapshot.get('asset_path'))
    if overlay_bytes:
        overlay_href = f"data:image/png;base64,{base64.b64encode(overlay_bytes).decode('ascii')}"
    else:
        overlay_href = url_for(
            'projects.asset_preview',
            slug=project.slug,
            asset_id=snapshot.get('asset_id'),
            _external=True,
        )
    center_lat, center_lng = _normalize_point_payload(snapshot.get('center') or snapshot.get('tx_location') or {})
    if center_lat is None or center_lng is None:
        center_lat = (bounds['north'] + bounds['south']) / 2.0
        center_lng = (bounds['east'] + bounds['west']) / 2.0
    receiver_placemarks = []
    for receiver in snapshot.get('receivers') or []:
        location = receiver.get('location') or {}
        lat = _safe_float(receiver.get('lat') or location.get('lat'))
        lng = _safe_float(receiver.get('lng') or location.get('lng'))
        if lat is None or lng is None:
            continue
        label = html.escape(receiver.get('label') or receiver.get('id') or 'Receptor')
        details = []
        for key, display in (('field', 'Campo'), ('elevation', 'Elevação'), ('distance', 'Distância'), ('municipality', 'Município')):
            value = receiver.get(key)
            if value:
                details.append(f"{display}: {value}")
        description = html.escape(" | ".join(details)) if details else ''
        receiver_placemarks.append(f"""
    <Placemark>
      <name>{label}</name>
      <description>{description}</description>
      <Point>
        <coordinates>{lng:.6f},{lat:.6f},0</coordinates>
      </Point>
    </Placemark>""")

    kml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{html.escape(project.name)} — Cobertura</name>
    <GroundOverlay>
      <name>Mancha de cobertura</name>
      <color>B2FFFFFF</color>
      <Icon>
        <href>{overlay_href}</href>
      </Icon>
      <LatLonBox>
        <north>{bounds['north']:.6f}</north>
        <south>{bounds['south']:.6f}</south>
        <east>{bounds['east']:.6f}</east>
        <west>{bounds['west']:.6f}</west>
      </LatLonBox>
    </GroundOverlay>
    <Placemark>
      <name>Transmissor</name>
      <Point>
        <coordinates>{center_lng:.6f},{center_lat:.6f},0</coordinates>
      </Point>
    </Placemark>
    {''.join(receiver_placemarks)}
  </Document>
</kml>'''
    filename = f"cobertura_{project.slug}.kml"
    return Response(
        kml_content,
        mimetype='application/vnd.google-earth.kml+xml',
        headers={
            'Content-Disposition': f'attachment; filename=\"{filename}\"'
        },
    )


@bp.route('/relatorios/<slug>')
@login_required
def report_editor(slug):
    project = _load_project_for_current_user(slug)
    snapshot = _latest_coverage_snapshot(project)
    if snapshot is None:
        flash('Gere uma cobertura antes de montar o relatório.', 'warning')
    return render_template(
        'relatorio.html',
        project=project,
        has_snapshot=bool(snapshot),
    )


@bp.route('/projects/<slug>/rt3d-scene.geojson')
@login_required
def download_rt3d_scene(slug):
    project = _load_project_for_current_user(slug)
    snapshot = _latest_coverage_snapshot(project) or {}
    scene_meta = snapshot.get('rt3d_scene') or {}
    blob = _load_asset_bytes(scene_meta.get('asset_id'), scene_meta.get('asset_path'))
    if not blob:
        return jsonify({'error': 'Nenhuma cena RT3D disponível para este projeto.'}), 404
    return send_file(
        io.BytesIO(blob),
        mimetype='application/geo+json',
        as_attachment=False,
        download_name=f"{project.slug}_rt3d.geojson",
    )


@bp.route('/projects/<slug>/rt3d-data')
@login_required
def rt3d_data(slug):
    project = _load_project_for_current_user(slug)
    snapshot = _latest_coverage_snapshot(project) or {}
    if not snapshot.get('rt3d_scene'):
        return jsonify({'error': 'Nenhuma cena RT3D disponível para este projeto.'}), 404

    scene_url = url_for('ui.download_rt3d_scene', slug=project.slug)
    return jsonify({
        'scene_url': scene_url,
        'settings': snapshot.get('rt3d_settings') or {},
        'diagnostics': snapshot.get('rt3d_diagnostics') or {},
        'rays': snapshot.get('rt3d_rays') or [],
        'project': {
            'slug': project.slug,
            'name': project.name,
        },
    })


@bp.route('/rt3d-viewer')
@login_required
def rt3d_viewer():
    slug = request.args.get('project')
    if not slug:
        flash('Selecione um projeto para visualizar em 3D.', 'warning')
        return redirect(url_for('ui.calcular_cobertura'))
    project = _load_project_for_current_user(slug)
    snapshot = _latest_coverage_snapshot(project)
    if not snapshot or not snapshot.get('rt3d_scene'):
        flash('Este projeto ainda não possui cena RT3D.', 'warning')
        return redirect(url_for('ui.calcular_cobertura', project=project.slug))
    cesium_token = current_app.config.get('CESIUM_ION_TOKEN')
    return render_template(
        'rt3d_viewer.html',
        project=project,
        cesium_token=cesium_token,
        scene_endpoint=url_for('ui.download_rt3d_scene', slug=project.slug),
        data_endpoint=url_for('ui.rt3d_data', slug=project.slug),
    )
def _compute_coverage_map(tx, data, include_arrays=False, label=None, dem_directory=None, rt3d_scene=None):
    """
    Gera todos os artefatos de cobertura (heatmap, barra de cores, metadados)
    em formato compatível com mapa.js / generateCoverage() / applyCoverageOverlay().

    tx   -> objeto "transmissor" (ex: current_user)
    data -> payload JSON vindo do front (radius, min/max escala, customCenter ...)
    """

    if data.get('coverageEngine') == CoverageEngine.rt3d.value:
        return _compute_rt3d_only_map(tx, data, include_arrays, label, rt3d_scene)

    haat_radials: list[dict] = []
    haat_average = None

    # -------------------------------------------------
    # helpers internos
    # -------------------------------------------------
    def _coerce_optional(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        value_str = str(value).strip()
        if not value_str:
            return None
        try:
            return float(value_str)
        except ValueError:
            return None

    def _determine_auto_scale_local(arr, user_min, user_max, default_min=None, default_max=None):
        """
        Decide faixa de cores.
        Se user_min/max vierem, respeita.
        Caso contrário usa min/max dos valores finitos.
        Garante que vmax > vmin.
        """
        arr_np = np.asarray(arr, dtype=float)
        finite_vals = arr_np[np.isfinite(arr_np)]
        if finite_vals.size == 0:
            # fallback qualquer para não quebrar
            return (0.0, 1.0)

        auto_min = float(np.nanmin(finite_vals))
        auto_max = float(np.nanmax(finite_vals))

        vmin = float(user_min) if user_min is not None else (
            float(default_min) if default_min is not None else auto_min
        )
        vmax = float(user_max) if user_max is not None else (
            float(default_max) if default_max is not None else auto_max
        )

        if abs(vmax - vmin) < 1e-9:
            vmax = vmin + 1.0

        return (vmin, vmax)

    def _safe_value_at_center(arr, center_idx):
        """
        Extrai um valor "representativo" de um array (2D normalmente),
        tentando pegar no índice [lat, lon] mais próximo do TX.
        """
        arr_np = np.asarray(arr, dtype=float)
        try:
            return float(arr_np[center_idx])
        except Exception:
            # se for escalar
            if np.ndim(arr_np) == 0:
                return float(arr_np)
            finite_mask = np.isfinite(arr_np)
            if np.any(finite_mask):
                return float(np.nanmean(arr_np[finite_mask]))
            return None

    def _summarize_component(arr, center_idx):
        """
        min, max e valor central (dB) de um mapa de perdas.
        """
        arr_np = np.asarray(arr, dtype=float)
        finite_mask = np.isfinite(arr_np)
        if not np.any(finite_mask):
            return None
        out = {
            'min': float(np.nanmin(arr_np)),
            'max': float(np.nanmax(arr_np)),
            'unit': 'dB',
        }
        try:
            out['center'] = float(arr_np[center_idx])
        except Exception:
            out['center'] = float(np.nanmean(arr_np[finite_mask]))
        return out

    def adjust_center(radius_km, center_lat, center_lon):
        """
        Corrige o centro do raster em função do raio, para compensar
        o desalinhamento visual do GroundOverlay no Google Maps.

        Usa dois modelos regressivos (model_lat, model_lon) que estimam
        o quanto precisamos deslocar em graus, e depois aplica fatores
        empíricos diferentes por faixa de raio.

        Se os modelos não existirem ou der erro -> fallback = sem ajuste.
        """
        # fallback (sem ajuste)
        base_adj_lat = 0.0
        base_adj_lon = 0.0

        try:
            # os modelos esperam [[raio]]
            base_adj_lat = float(model_lat.predict(np.array([[radius_km]], dtype=float))[0])
            base_adj_lon = float(model_lon.predict(np.array([[radius_km]], dtype=float))[0])
        except Exception:
            # sem modelo treinado disponível? não quebra.
            base_adj_lat = 0.0
            base_adj_lon = 0.0

        # fatores empíricos
        if radius_km < 21:
            scale_factor_lat = 1.90
            scale_factor_lon = 0.95
        elif radius_km < 31:
            scale_factor_lat = 1.40
            scale_factor_lon = 0.93
        elif radius_km < 41:
            scale_factor_lat = 1.28
            scale_factor_lon = 1.00
        elif radius_km < 51:
            scale_factor_lat = 1.21
            scale_factor_lon = 1.03
        elif radius_km < 61:
            scale_factor_lat = 1.19
            scale_factor_lon = 0.97
        elif radius_km < 71:
            scale_factor_lat = 1.17
            scale_factor_lon = 1.025
        elif radius_km < 101:
            scale_factor_lat = 1.10
            scale_factor_lon = 1.027
        else:
            # acima de 100 km ainda não calibrado -> último conhecido
            scale_factor_lat = 1.10
            scale_factor_lon = 1.027

        # aplica deslocamento (mesma lógica que você mandou: center - adj*scale)
        new_lat = center_lat - base_adj_lat * scale_factor_lat
        new_lon = center_lon - base_adj_lon * scale_factor_lon

        return float(new_lat), float(new_lon)

    if dem_directory is None:
        dem_directory = str(global_srtm_dir())

    # -------------------------------------------------
    # 1. PARÂMETROS DO TX / AMBIENTE / UI
    # -------------------------------------------------

    # perdas fixas sistêmicas (cabos, conectores) [dB]
    loss_sys_db = tx.total_loss or 0.0

    # potência transmitida [W] (evitar log(0))
    Ptx_W = max(float(tx.transmission_power or 0.0), 1e-6)

    # ganho nominal de pico da antena TX [dBi]
    Gtx_peak_dBi = tx.antenna_gain or 0.0

    # ganho da RX [dBi]
    # Em estudos ponto-área assumimos receptor isotrópico (0 dBi)
    Grx_dBi = 0.0

    # override de centro vindo do front (posição "real"/visível da TX)
    center_override = data.get('customCenter') or {}
    try:
        lon_tx_deg = float(center_override.get('lng', tx.longitude))
        lat_tx_deg = float(center_override.get('lat', tx.latitude))
    except (TypeError, ValueError):
        lon_tx_deg, lat_tx_deg = tx.longitude, tx.latitude

    # raio solicitado [km]
    radius_requested = _coerce_optional(data.get('radius'))
    if radius_requested is None or radius_requested <= 0:
        radius_requested = 10.0
    radius_km = float(radius_requested)

    # >>> AQUI entra o teu ajuste empírico
    lat_ref_deg, lon_ref_deg = adjust_center(radius_km, lat_tx_deg, lon_tx_deg)

    # esses são os centros que vamos usar pro cálculo SRTM / pycraf
    lat_ref = lat_ref_deg * u.deg
    lon_ref = lon_ref_deg * u.deg

    # frequência (MHz → GHz p/ pycraf)
    freq_mhz = getattr(tx, 'frequencia', None) or 100.0
    if freq_mhz < 100.0:
        freq_mhz = 100.0
    frequency = (freq_mhz / 1000.0) * u.GHz  # pycraf usa GHz

    # alturas TX / RX acima do solo
    # Para ponto-área fixamos a altura do receptor em 2 m
    rx_height_m = 2.0
    tx_height_m = tx.tower_height if tx.tower_height is not None else 30.0
    h_rx = rx_height_m * u.m
    h_tx = tx_height_m * u.m

    # porcentagem de tempo P.452 (p%)
    time_pct = tx.time_percentage if getattr(tx, 'time_percentage', None) else 40.0
    time_pct = max(0.001, min(float(time_pct), 50.0))
    timepercent = time_pct * u.percent  # unidade correta pro pycraf

    # polarização
    pol = (getattr(tx, 'polarization', None) or 'vertical').lower()
    polarization = 1 if pol == 'vertical' else 0

    # versão P.452
    version = getattr(tx, 'p452_version', None) or 16
    if version not in (14, 16):
        version = 16

    # escala manual de cores (UI)
    min_valu = _coerce_optional(data.get('minSignalLevel'))
    max_valu = _coerce_optional(data.get('maxSignalLevel'))

    # resolução do grid em função do raio
    map_resolution = _select_map_resolution(radius_km)

    # bounding box geodésico aproximado só pra recortar SRTM
    # IMPORTANTE: usamos o centro AJUSTADO aqui, pois é ele que vamos
    # realmente rasterizar.
    bounds_hint = calculate_geodesic_bounds(lon_ref_deg, lat_ref_deg, radius_km)

    def _span_deg(a, b):
        span = abs(float(a) - float(b))
        if span > 180.0:
            span = 360.0 - span
        return max(span, 1e-6)

    pad_factor = 1.05
    span_lon = _span_deg(bounds_hint['east'], bounds_hint['west']) * pad_factor
    span_lat = _span_deg(bounds_hint['north'], bounds_hint['south']) * pad_factor
    map_size_lon = span_lon * u.deg
    map_size_lat = span_lat * u.deg

    # condições atmosféricas
    temperature_k = tx.temperature_k if getattr(tx, 'temperature_k', None) else 293.15
    pressure_hpa  = tx.pressure_hpa  if getattr(tx, 'pressure_hpa', None)  else 1013.0
    water_density = tx.water_density if getattr(tx, 'water_density', None) else 7.5
    if water_density is None or (isinstance(water_density, float) and math.isnan(water_density)):
        water_density = 7.5

    temperature = temperature_k * u.K
    pressure    = pressure_hpa * u.hPa

    # tipo de clutter (ambiente)
    modelo = getattr(tx, 'propagation_model', None)
    if modelo == 'modelo1':
        zone_t, zone_r = pathprof.CLUTTER.URBAN, pathprof.CLUTTER.URBAN
    elif modelo == 'modelo2':
        zone_t, zone_r = pathprof.CLUTTER.SUBURBAN, pathprof.CLUTTER.SUBURBAN
    elif modelo == 'modelo3':
        zone_t, zone_r = pathprof.CLUTTER.TROPICAL_FOREST, pathprof.CLUTTER.TROPICAL_FOREST
    elif modelo == 'modelo4':
        zone_t, zone_r = pathprof.CLUTTER.CONIFEROUS_TREES, pathprof.CLUTTER.CONIFEROUS_TREES
    else:
        zone_t, zone_r = pathprof.CLUTTER.UNKNOWN, pathprof.CLUTTER.UNKNOWN

    # -------------------------------------------------
    # 2. GERA GRID DE TERRENO + ATENUAÇÃO P.452
    #     (usando o centro AJUSTADO!)
    # -------------------------------------------------
    srtm_dir = dem_directory or './SRTM'
    download_mode = 'none'
    try:
        with pathprof.SrtmConf.set(
            srtm_dir=srtm_dir,
            download=download_mode,
            server='viewpano'
        ):
            hprof_cache = pathprof.height_map_data(
                lon_ref,
                lat_ref,
                map_size_lon,
                map_size_lat,
                map_resolution=map_resolution,
                zone_t=zone_t,
                zone_r=zone_r,
            )
    except Exception:
        with pathprof.SrtmConf.set(
            srtm_dir=srtm_dir,
            download='missing',
            server='viewpano'
        ):
            hprof_cache = pathprof.height_map_data(
                lon_ref,
                lat_ref,
                map_size_lon,
                map_size_lat,
                map_resolution=map_resolution,
                zone_t=zone_t,
                zone_r=zone_r,
            )

    results = pathprof.atten_map_fast(
        freq=frequency,
        temperature=temperature,
        pressure=pressure,
        h_tg=h_tx,
        h_rg=h_rx,
        timepercent=timepercent,
        hprof_data=hprof_cache,
        polarization=polarization,
        version=version,
        base_water_density=(water_density if water_density is not None else 7.5) * u.g / u.m**3
    )

    # vetores 1D de coordenadas (centros de pixel) do RASTER AJUSTADO
    _lons = hprof_cache['xcoords']
    _lats = hprof_cache['ycoords']

    # -------------------------------------------------
    # 3. MAPAS DE PERDA
    # -------------------------------------------------
    loss_maps = {}
    for key in ('L_b0p', 'L_bd', 'L_bs', 'L_ba', 'L_b', 'L_b_corr'):
        if key in results and results[key] is not None:
            try:
                loss_maps[key] = results[key].to(u.dB).value
            except Exception:
                loss_maps[key] = np.asarray(results[key], dtype=float)

    if 'L_b_corr' in loss_maps:
        combined_loss_map = loss_maps['L_b_corr']
    elif 'L_b' in loss_maps:
        combined_loss_map = loss_maps['L_b']
    else:
        combined_loss_map = results['L_b'].to(u.dB).value

    total_path_loss_db = np.asarray(combined_loss_map, dtype=float)
    _total_atten = u.Quantity(total_path_loss_db, u.dB)

    # -------------------------------------------------
    # 4. AJUSTE DE DIMENSÃO (garante [nlat, nlon])
    # -------------------------------------------------
    lons_deg = np.asarray(_to_degree_array(_lons), dtype=float)
    lats_deg = np.asarray(_to_degree_array(_lats), dtype=float)

    if lons_deg.ndim == 2:
        lons_deg = lons_deg[0, :]
    else:
        lons_deg = lons_deg.ravel()
    if lats_deg.ndim == 2:
        lats_deg = lats_deg[:, 0]
    else:
        lats_deg = lats_deg.ravel()

    nlon = int(lons_deg.size)
    nlat = int(lats_deg.size)

    # Se a matriz veio (nlon, nlat), transpõe pra (nlat, nlon).
    if total_path_loss_db.shape == (nlon, nlat):
        total_path_loss_db = total_path_loss_db.T
        _total_atten = u.Quantity(total_path_loss_db, u.dB)
    elif total_path_loss_db.shape != (nlat, nlon):
        # fallback: tenta a transposta
        if total_path_loss_db.T.shape == (nlat, nlon):
            total_path_loss_db = total_path_loss_db.T
            _total_atten = u.Quantity(total_path_loss_db, u.dB)
        # se ainda não bateu, seguimos assim mesmo

    # tamanhos angulares médios por pixel (pra calcular bounds com meia célula)
    if lons_deg.size > 1:
        dlon = float(np.median(np.abs(np.diff(lons_deg))))
    else:
        dlon = 0.0
    if lats_deg.size > 1:
        dlat = float(np.median(np.abs(np.diff(lats_deg))))
    else:
        dlat = 0.0

    lon_grid, lat_grid = np.meshgrid(lons_deg, lats_deg)

    # -------------------------------------------------
    # 5. PADRÃO DE ANTENA / CORREÇÃO DE ORIENTAÇÃO (+90°)
    # -------------------------------------------------
    gain_comp_raw = _compute_gain_components(tx, hprof_cache)

    horiz_grid = gain_comp_raw['horizontal_gain_grid_db']
    vert_grid  = gain_comp_raw['vertical_gain_grid_db']

    def _coerce_gain_grid(arr, target_shape):
        """
        Garante que horiz_grid / vert_grid tenham shape (nlat, nlon)
        e possam somar com total_path_loss_db.
        """
        if np.isscalar(arr) or arr is None:
            return np.zeros(target_shape, dtype=float)

        a = np.asarray(arr, dtype=float)

        if a.shape == target_shape:
            return a

        if a.shape == (target_shape[1], target_shape[0]):
            return a.T

        if a.ndim == 1:
            # broadcast linha/coluna
            if a.size == target_shape[1]:
                return np.tile(a, (target_shape[0], 1))
            if a.size == target_shape[0]:
                return np.tile(a[:, None], (1, target_shape[1]))

        try:
            return a.reshape(target_shape)
        except Exception:
            return np.zeros(target_shape, dtype=float)

    horiz_grid = _coerce_gain_grid(horiz_grid, total_path_loss_db.shape)
    vert_grid  = _coerce_gain_grid(vert_grid,  total_path_loss_db.shape)

    # correção de +90° no padrão (offset entre diagrama e mapa):
    # rotaciona ambos 90° horário (np.rot90(..., k=3))
    if horiz_grid.shape == vert_grid.shape and horiz_grid.shape[0] == horiz_grid.shape[1]:
        horiz_grid = np.rot90(horiz_grid, k=3)
        vert_grid  = np.rot90(vert_grid,  k=3)

    # -------------------------------------------------
    # 6. LINK BUDGET (Friis em dB) E CAMPO ELÉTRICO
    # -------------------------------------------------
    # Ptx em dBm
    Ptx_dBm = 10.0 * math.log10(Ptx_W / 0.001)

    # ganho TX efetivo por pixel
    Gtx_eff_db = (
        float(Gtx_peak_dBi)
        + horiz_grid
        + vert_grid
    )

    # potência recebida estimada no conector RX [dBm]:
    # Prx = Ptx_dBm + Gtx_eff + Grx_dBi - loss_sys - L_path
    Prx_dbm = (
        Ptx_dBm
        + Gtx_eff_db
        + float(Grx_dBi)
        - float(loss_sys_db)
        - total_path_loss_db
    )
    Prx_dbm = np.asarray(Prx_dbm, dtype=float)

    # campo elétrico equivalente [dBµV/m]
    # E = Prx(dBm) - Grx(dBi) + 77.2 + 20log10(f_MHz)
    freq_mhz_safe = max(float(freq_mhz), 0.1)
    E_dbuv = (
        Prx_dbm
        - float(Grx_dBi)
        + 77.2
        + 20.0 * np.log10(freq_mhz_safe)
    )
    E_dbuv = np.asarray(E_dbuv, dtype=float)

    # -------------------------------------------------
    # 7. MÁSCARA CIRCULAR (preencher TODO o disco)
    #    IMPORTANTE: o círculo é em torno da TX REAL (lat_tx_deg/lon_tx_deg),
    #    não do centro ajustado.
    # -------------------------------------------------

    def _haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0  # km
        lat1r = np.radians(lat1)
        lon1r = np.radians(lon1)
        lat2r = np.radians(lat2)
        lon2r = np.radians(lon2)
        dlat  = lat2r - lat1r
        dlon  = lon2r - lon1r
        a = (
            np.sin(dlat / 2.0) ** 2
            + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2.0) ** 2
        )
        return 2.0 * R * np.arcsin(np.sqrt(a))

    dist_km_grid = _haversine_km(lat_grid, lon_grid, lat_tx_deg, lon_tx_deg)
    inrange_mask = dist_km_grid <= radius_km

    # inicializa com NaN
    E_plot   = np.full_like(E_dbuv, np.nan, dtype=float)
    Prx_plot = np.full_like(Prx_dbm, np.nan, dtype=float)

    # mantém apenas dentro do raio
    E_plot[inrange_mask]   = E_dbuv[inrange_mask]
    Prx_plot[inrange_mask] = Prx_dbm[inrange_mask]

    # preencher buracos NaN DENTRO do raio com valor mínimo dentro do raio
    def _fill_holes(mask_in, arr_plot):
        vals = arr_plot[mask_in & np.isfinite(arr_plot)]
        if vals.size > 0:
            fill_val = float(np.nanmin(vals))
            hole_mask = mask_in & ~np.isfinite(arr_plot)
            arr_plot[hole_mask] = fill_val
        return arr_plot

    E_plot   = _fill_holes(inrange_mask, E_plot)
    Prx_plot = _fill_holes(inrange_mask, Prx_plot)

    # -------------------------------------------------
    # 8. ESCALA DE CORES
    # -------------------------------------------------
    min_val, max_val = _determine_auto_scale_local(E_plot, min_valu, max_valu, default_min=10.0, default_max=60.0)
    if min_valu is None and max_valu is None:
        if (not np.isfinite(min_val)) or (not np.isfinite(max_val)):
            min_val, max_val = 10.0, 60.0

    power_min, power_max = _determine_auto_scale_local(Prx_plot, None, None)

    # -------------------------------------------------
    # 9. ENCONTRAR ÍNDICE DO PIXEL MAIS PRÓXIMO DO TX REAL
    #    (pra summaries e painéis do front)
    # -------------------------------------------------
    lat_diff = np.abs(lats_deg - lat_tx_deg)
    lon_diff = np.abs(lons_deg - lon_tx_deg)
    center_idx_lat = int(np.argmin(lat_diff))
    center_idx_lon = int(np.argmin(lon_diff))
    center_idx = (center_idx_lat, center_idx_lon)

    # -------------------------------------------------
    # 10. BOUNDS DO OVERLAY
    #     usamos a grade AJUSTADA (lat_ref_deg/lon_ref_deg),
    #     sem mais delta sub-pixel. Só expandimos meia célula.
    # -------------------------------------------------
    lat_min_center = float(np.nanmin(lats_deg))
    lat_max_center = float(np.nanmax(lats_deg))
    lon_min_center = float(np.nanmin(lons_deg))
    lon_max_center = float(np.nanmax(lons_deg))

    south_edge = lat_min_center - dlat / 2.0
    north_edge = lat_max_center + dlat / 2.0
    west_edge  = lon_min_center - dlon / 2.0
    east_edge  = lon_max_center + dlon / 2.0

    bounds = {
        'north': north_edge,
        'south': south_edge,
        'east':  east_edge,
        'west':  west_edge,
    }

    lat_span = max(bounds['north'] - bounds['south'], 1e-6)
    colorbar_height = max(lat_span * 0.05, 1e-4)
    colorbar_bounds = {
        'north': bounds['north'],
        'south': bounds['north'] - colorbar_height,
        'east':  bounds['east'],
        'west':  bounds['west'],
    }

    # -------------------------------------------------
    # 11. STATUS CLIMÁTICO / MÉTRICAS DO CENTRO
    # -------------------------------------------------
    climate_lat = getattr(tx, 'climate_lat', None)
    climate_lon = getattr(tx, 'climate_lon', None)
    location_changed = True
    if climate_lat is not None and climate_lon is not None:
        dlat_check = abs(float(climate_lat) - lat_tx_deg)
        dlon_check = abs(float(climate_lon) - lon_tx_deg)
        location_changed = max(dlat_check, dlon_check) > 1e-4

    if location_changed:
        location_status = (
            'A localização da TX mudou desde o último ajuste climático. '
            'Atualize as condições atmosféricas para refletir o novo município.'
        )
    else:
        if getattr(tx, 'climate_updated_at', None):
            location_status = (
                f"Localização inalterada desde "
                f"{tx.climate_updated_at.strftime('%d/%m/%Y %H:%M UTC')}"
            )
        else:
            location_status = (
                'Localização confirmada. Ajuste climático ainda não foi registrado para este ponto.'
            )

    # path_type (LoS, ducting etc.) no ponto central
    path_type_info = results.get('path_type')
    path_type_value = None
    if path_type_info is not None:
        try:
            if hasattr(path_type_info, '__getitem__'):
                ptv = path_type_info[center_idx]
            else:
                ptv = path_type_info
            if isinstance(ptv, bytes):
                ptv = ptv.decode('utf-8', errors='ignore')
            path_type_value = str(ptv)
        except Exception:
            path_type_value = None

    loss_components_summary = {}
    for k, arr in loss_maps.items():
        s = _summarize_component(arr, center_idx)
        if s:
            loss_components_summary[k] = s

    center_metrics = {
        'path_type': path_type_value,
        'combined_loss_center_db': _safe_value_at_center(total_path_loss_db, center_idx),
        'received_power_center_dbm': _safe_value_at_center(Prx_dbm, center_idx),
        'field_center_dbuv_m': _safe_value_at_center(E_dbuv, center_idx),
        'effective_gain_center_db': _safe_value_at_center(Gtx_eff_db, center_idx),
        'tx_power_dbm': float(Ptx_dBm),
        'system_losses_db': float(loss_sys_db),
        'frequency_mhz': float(freq_mhz),
        'radius_km': float(radius_km),
    }

    try:
        center_metrics['distance_center_km'] = float(dist_km_grid[center_idx])
    except Exception:
        pass

    # -------------------------------------------------
    # 12. IMAGENS (heatmap/base64) p/ overlay e barra
    # -------------------------------------------------
    img_dbuv_b64, colorbar_dbuv_b64 = _render_field_strength_image(
        lons_deg,
        lats_deg,
        E_plot,
        radius_km,
        lon_tx_deg,
        lat_tx_deg,
        min_val,
        max_val,
        gain_comp_raw['horizontal_pattern_db'],
        dist_map_km=dist_km_grid,
        colorbar_label='Campo elétrico [dBµV/m]'
    )

    img_dbm_b64, colorbar_dbm_b64 = _render_field_strength_image(
        lons_deg,
        lats_deg,
        Prx_plot,
        radius_km,
        lon_tx_deg,
        lat_tx_deg,
        power_min,
        power_max,
        gain_comp_raw['horizontal_pattern_db'],
        dist_map_km=dist_km_grid,
        colorbar_label='Potência recebida [dBm]'
    )

    images_payload = {
        "dbuv": {
            "image": img_dbuv_b64,
            "colorbar": colorbar_dbuv_b64,
            "label": "Campo elétrico [dBµV/m]",
            "unit": "dBµV/m",
        },
        "dbm": {
            "image": img_dbm_b64,
            "colorbar": colorbar_dbm_b64,
            "label": "Potência recebida [dBm]",
            "unit": "dBm",
        },
    }
    scale_payload = {
        "default_unit": "dbuv",
        "min": min_val,
        "max": max_val,
        "units": {
            "dbuv": {"min": min_val, "max": max_val},
            "dbm": {"min": power_min, "max": power_max},
        },
    }

    # -------------------------------------------------
    # 13. DICIONÁRIOS DE NÍVEL DE CAMPO (p/ clique RX)
    # -------------------------------------------------
    signal_level_dict = {}
    signal_level_dict_dbm = {}

    if E_plot.shape == (len(lats_deg), len(lons_deg)):
        for i, lat_val in enumerate(lats_deg):
            for j, lon_val in enumerate(lons_deg):
                if not inrange_mask[i, j]:
                    continue
                if np.isfinite(E_dbuv[i, j]):
                    signal_level_dict[f"({lat_val}, {lon_val})"] = float(E_dbuv[i, j])
                if np.isfinite(Prx_dbm[i, j]):
                    signal_level_dict_dbm[f"({lat_val}, {lon_val})"] = float(Prx_dbm[i, j])

    # -------------------------------------------------
    # 14. GAIN COMPONENTS (interface com updateGainSummary no front)
    # -------------------------------------------------
    if np.isfinite(horiz_grid).any():
        horiz_min = float(np.nanmin(horiz_grid))
        horiz_max = float(np.nanmax(horiz_grid))
    else:
        horiz_min = 0.0
        horiz_max = 0.0

    vert_center = _safe_value_at_center(vert_grid, center_idx)
    if vert_center is None or not np.isfinite(vert_center):
        vert_center = 0.0

    gain_components_payload = {
        "base_gain_dbi": float(Gtx_peak_dBi),
        "horizontal_adjustment_db_min": horiz_min,
        "horizontal_adjustment_db_max": horiz_max,
        "vertical_adjustment_db": float(vert_center),
        "horizontal_pattern_db": (
            gain_comp_raw['horizontal_pattern_db'].tolist()
            if gain_comp_raw.get('horizontal_pattern_db') is not None and include_arrays
            else None
        ),
        "vertical_pattern_db": (
            gain_comp_raw['vertical_pattern_db'].tolist()
            if gain_comp_raw.get('vertical_pattern_db') is not None and include_arrays
            else None
        ),
        "vertical_horizon_db": gain_comp_raw.get('vertical_horizon_db'),
    }

    # -------------------------------------------------
    # 15. OBJETO FINAL (compatível com mapa.js)
    # -------------------------------------------------
    payload = {
        "images": images_payload,

        # bounds agora vêm da malha calculada em torno do centro AJUSTADO
        # (ou seja, já com o deslocamento empírico por raio)
        "bounds": bounds,
        "colorbar_bounds": colorbar_bounds,

        "scale": scale_payload,

        # AQUI mandamos a posição REAL da TX (marcador vermelho),
        # pra desenhar o círculo azul e pra atualizar o painel.
        "center": {"lat": float(lat_tx_deg), "lng": float(lon_tx_deg)},
        "requested_radius_km": radius_km,
        "radius": data.get('radius', radius_km),

        "location_status": location_status,
        "location_changed": location_changed,
        "tx_location_name": getattr(tx, 'tx_location_name', None),
        "tx_site_elevation": getattr(tx, 'tx_site_elevation', None),
        "climate_updated_at": tx.climate_updated_at.isoformat()
            if getattr(tx, 'climate_updated_at', None) else None,

        # usado no front para desenhar a linha de azimute (refreshDirectionGuide)
        "antenna_direction": getattr(tx, 'antenna_direction', None),
        "tx_parameters": {
            "power_w": getattr(tx, 'transmission_power', None),
            "tower_height_m": getattr(tx, 'tower_height', None),
            "rx_height_m": rx_height_m,
            "total_loss_db": getattr(tx, 'total_loss', None),
            "antenna_gain_dbi": getattr(tx, 'antenna_gain', None),
        },

        # resumo de ganho usado em updateGainSummary()
        "gain_components": gain_components_payload,

        "loss_components": loss_components_summary,
        "center_metrics": center_metrics,

        # usado por computeReceiverSummary() pra estimar o nível no RX clicado
        "signal_level_dict": signal_level_dict,
        "signal_level_dict_dbm": signal_level_dict_dbm,
    }

    if haat_radials:
        payload['haat_radials'] = haat_radials
    if haat_average is not None:
        payload['haat_average_m'] = haat_average

    if label is not None:
        payload["label"] = str(label)

    return payload





@bp.route('/calculate-coverage', methods=['POST'])
@login_required
def calculate_coverage():
    data = request.get_json() or {}
    project_slug = data.get('projectSlug') or data.get('project_slug')
    project = None
    if project_slug:
        project = _load_project_for_current_user(project_slug)

    engine_value = data.get('coverageEngine') or CoverageEngine.p1546.value
    if engine_value not in {engine.value for engine in CoverageEngine}:
        engine_value = CoverageEngine.p1546.value

    receivers = data.get('receivers') or []

    # Prepare overrides from project settings
    project_overrides = {}
    if project and project.settings:
        settings = project.settings
        if "propagationModel" in settings:
            project_overrides["propagation_model"] = settings["propagationModel"]
        if "Total_loss" in settings:
            project_overrides["total_loss"] = settings["Total_loss"]
        if "antennaGain" in settings:
            project_overrides["antenna_gain"] = settings["antennaGain"]
        if "rxGain" in settings:
            project_overrides["rx_gain"] = settings["rxGain"]
        if "transmissionPower" in settings:
            project_overrides["transmission_power"] = settings["transmissionPower"]
        if "frequency" in settings:
            project_overrides["frequencia"] = settings["frequency"]
        if "towerHeight" in settings:
            project_overrides["tower_height"] = settings["towerHeight"]
        if "rxHeight" in settings:
            project_overrides["rx_height"] = settings["rxHeight"]
        if "antennaTilt" in settings:
            project_overrides["antenna_tilt"] = settings["antennaTilt"]
        if "antennaDirection" in settings:
            project_overrides["antenna_direction"] = settings["antennaDirection"]
        if "timePercentage" in settings:
            project_overrides["time_percentage"] = settings["timePercentage"]
        if "temperature" in settings:
            project_overrides["temperature_k"] = settings["temperature"] + 273.15 if settings["temperature"] is not None else None
        if "pressure" in settings:
            project_overrides["pressure_hpa"] = settings["pressure"]
        if "waterDensity" in settings:
            project_overrides["water_density"] = settings["waterDensity"]
        if "serviceType" in settings:
            project_overrides["servico"] = settings["serviceType"]
        if "polarization" in settings:
            project_overrides["polarization"] = settings["polarization"]
        if "p452Version" in settings:
            project_overrides["p452_version"] = settings["p452Version"]
        if "latitude" in settings:
            project_overrides["latitude"] = settings["latitude"]
        if "longitude" in settings:
            project_overrides["longitude"] = settings["longitude"]
        if "rt3dUrbanRadius" in settings:
            project_overrides["rt3dUrbanRadius"] = settings["rt3dUrbanRadius"]
        if "rt3dBuildingSource" in settings:
            project_overrides["rt3dBuildingSource"] = settings["rt3dBuildingSource"]
        if "rt3dRayStep" in settings:
            project_overrides["rt3dRayStep"] = settings["rt3dRayStep"]
        if "rt3dDiffractionBoost" in settings:
            project_overrides["rt3dDiffractionBoost"] = settings["rt3dDiffractionBoost"]
        if "rt3dMinimumClearance" in settings:
            project_overrides["rt3dMinimumClearance"] = settings["rt3dMinimumClearance"]

    # Prepare overrides from request data (real-time UI changes)
    request_overrides = {}
    if "latitude" in data:
        request_overrides["latitude"] = _coerce_float(data["latitude"])
    if "longitude" in data:
        request_overrides["longitude"] = _coerce_float(data["longitude"])
    if "frequency" in data:
        request_overrides["frequencia"] = _coerce_float(data["frequency"])
    if "direction" in data:
        request_overrides["antenna_direction"] = _normalize_direction_value(data["direction"])
    if "tilt" in data:
        request_overrides["antenna_tilt"] = _coerce_float(data["tilt"])
    if "tower_height" in data:
        request_overrides["tower_height"] = _coerce_float(data["tower_height"])
    if "rx_height" in data:
        request_overrides["rx_height"] = _coerce_float(data["rx_height"])
    if "transmission_power" in data:
        request_overrides["transmission_power"] = _coerce_float(data["transmission_power"])
    if "antenna_gain" in data:
        request_overrides["antenna_gain"] = _coerce_float(data["antenna_gain"])
    if "total_loss" in data:
        request_overrides["total_loss"] = _coerce_float(data["total_loss"])
    if "time_percentage" in data:
        request_overrides["time_percentage"] = _coerce_float(data["time_percentage"])
    if "polarization" in data:
        request_overrides["polarization"] = _coerce_str(data["polarization"])
    if "p452_version" in data:
        request_overrides["p452_version"] = _coerce_float(data["p452_version"])
    if "temperature_k" in data:
        request_overrides["temperature_k"] = _coerce_float(data["temperature_k"])
    if "pressure_hpa" in data:
        request_overrides["pressure_hpa"] = _coerce_float(data["pressure_hpa"])
    if "water_density" in data:
        request_overrides["water_density"] = _coerce_float(data["water_density"])
    if "propagation_model" in data:
        request_overrides["propagation_model"] = _coerce_str(data["propagation_model"])
    if "service" in data:
        request_overrides["servico"] = _coerce_str(data["service"])
    if "rt3dBuildingSource" in data:
        request_overrides["rt3dBuildingSource"] = _coerce_str(data["rt3dBuildingSource"])
    if "rt3dRayStep" in data:
        request_overrides["rt3dRayStep"] = _coerce_float(data["rt3dRayStep"])
    if "rt3dDiffractionBoost" in data:
        request_overrides["rt3dDiffractionBoost"] = _coerce_float(data["rt3dDiffractionBoost"])
    if "rt3dMinimumClearance" in data:
        request_overrides["rt3dMinimumClearance"] = _coerce_float(data["rt3dMinimumClearance"])

    # Combine overrides: request_overrides take precedence over project_overrides
    # which take precedence over current_user defaults.
    all_overrides = {**project_overrides, **request_overrides}

    # Construct the tx_object
    tx_object = _prepare_tx_object(current_user, overrides=all_overrides)

    if receivers:
        receivers = _enrich_receivers_metadata(receivers, tx_object)
        data['receivers'] = receivers

    dataset_summary = {}
    rt3d_scene_summary = None
    if project and tx_object.latitude is not None and tx_object.longitude is not None:
        if engine_value != CoverageEngine.rt3d.value:
            try:
                dataset_summary = ensure_geodata_availability(
                    project,
                    tx_object.latitude,
                    tx_object.longitude,
                    data.get('lulcYear'),
                )
            except Exception as exc:
                current_app.logger.warning('Falha ao preparar datasets base: %s', exc)
                dataset_summary = {}
        if engine_value == CoverageEngine.rt3d.value:
            try:
                rt3d_radius = _coerce_float(data.get('rt3dUrbanRadius'))
                if rt3d_radius is None:
                    rt3d_radius = max(_coerce_float(data.get('radius')) or 6.0, 1.0)
                rt3d_scene_summary = ensure_rt3d_scene(
                    project,
                    tx_object.latitude,
                    tx_object.longitude,
                    rt3d_radius,
                    current_app.config.get('GOOGLE_MAPS_API_KEY'),
                )
            except Exception as exc:
                current_app.logger.warning('rt3d.scene.failure', extra={'error': str(exc)})
                rt3d_scene_summary = None

    dem_directory = dataset_summary.get('dem_dir') if dataset_summary else None
    result = _compute_coverage_map(tx_object, data, dem_directory=dem_directory, rt3d_scene=rt3d_scene_summary)
    if receivers:
        result['receivers'] = receivers
    status_payload = result.get('datasetStatus') or {}
    if dataset_summary:
        dem_asset = dataset_summary.get('dem_asset')
        if dem_asset is not None:
            meta = dem_asset.meta or {}
            status_payload['demPath'] = dem_asset.path
            status_payload['demTile'] = meta.get('tile')
            status_payload['demResolution'] = meta.get('resolution')
            status_payload['demSource'] = meta.get('source')
        lulc_asset = dataset_summary.get('lulc_asset')
        if lulc_asset is not None:
            meta = lulc_asset.meta or {}
            status_payload['lulcPath'] = lulc_asset.path
            status_payload['lulcYear'] = meta.get('year') or dataset_summary.get('lulc_year')
            status_payload['lulcSource'] = meta.get('source')
        elif dataset_summary.get('lulc_year') is not None:
            status_payload['lulcYear'] = dataset_summary['lulc_year']
    if rt3d_scene_summary:
        status_payload['buildingsSource'] = rt3d_scene_summary.get('source')
        status_payload['buildingsCount'] = rt3d_scene_summary.get('feature_count')
        result['rt3dScene'] = rt3d_scene_summary
        if rt3d_scene_summary.get('diagnostics'):
            existing_diag = result.get('rt3dDiagnostics') or {}
            merged_diag = dict(existing_diag)
            merged_diag.update(rt3d_scene_summary['diagnostics'])
            result['rt3dDiagnostics'] = merged_diag
        if result.get('rt3dSettings') is None and tx_object:
            result['rt3dSettings'] = {
                'building_source': getattr(tx_object, 'rt3dBuildingSource', None),
                'ray_step_m': getattr(tx_object, 'rt3dRayStep', None),
                'diffraction_boost_db': getattr(tx_object, 'rt3dDiffractionBoost', None),
                'minimum_clearance_m': getattr(tx_object, 'rt3dMinimumClearance', None),
            }
    if status_payload:
        result['datasetStatus'] = status_payload

    result['receivers'] = receivers
    if project:
        result.setdefault('project_slug', project.slug)

    persisted = None
    try:
        persisted = _persist_coverage_artifacts(current_user, project, engine_value, data, result)
        db.session.commit()
    except Exception as exc:
        current_app.logger.exception('Falha ao persistir artefatos de cobertura: %s', exc)
        db.session.rollback()
    else:
        if persisted:
            result.setdefault('assets', {})['heatmap'] = {
                'id': str(persisted['heatmap_asset'].id),
                'path': persisted['heatmap_asset'].path,
            }
            if persisted.get('json_asset'):
                result['assets']['summary'] = {
                    'id': str(persisted['json_asset'].id),
                    'path': persisted['json_asset'].path,
                }
            if persisted.get('colorbar_asset'):
                result['assets']['colorbar'] = {
                    'id': str(persisted['colorbar_asset'].id),
                    'path': persisted['colorbar_asset'].path,
                }
            result['coverage_job_id'] = str(persisted['job'].id)
            result['generated_at'] = persisted['timestamp']
            if persisted.get('tiles'):
                sanitized_tiles = _sanitize_tiles_payload(project, persisted['tiles'])
                if sanitized_tiles:
                    result['tiles'] = sanitized_tiles
            if project:
                result['project_slug'] = project.slug
                result['lastCoverage'] = _latest_coverage_snapshot(project)
        elif project:
            result['lastCoverage'] = _latest_coverage_snapshot(project)

    if 'tiles' not in result:
        last_cov = result.get('lastCoverage') or {}
        tiles_meta = last_cov.get('tiles')
        if tiles_meta:
            sanitized_tiles = _sanitize_tiles_payload(project, tiles_meta) if project else None
            if sanitized_tiles:
                result['tiles'] = sanitized_tiles

    return jsonify(result)




@bp.route('/tx-location', methods=['POST'])
@login_required
def atualizar_localizacao_tx():
    try:
        db.session.rollback()
    except Exception:
        pass
    data = request.get_json() or {}
    try:
        lat = float(data.get('latitude'))
        lon = float(data.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Coordenadas inválidas.'}), 400

    project_slug = data.get('projectSlug') or data.get('project_slug')
    project = None
    if project_slug:
        try:
            project = _load_project_for_current_user(project_slug)
            _remember_active_project(project)
        except NotFound:
            project = None

    user = User.query.get(current_user.id)
    if not user:
        return jsonify({'error': 'Usuário não encontrado.'}), 404

    municipality_detail = _lookup_municipality(lat, lon)
    municipality_label = None
    if isinstance(municipality_detail, dict):
        municipality_label = municipality_detail.get('label')
    elif isinstance(municipality_detail, str):
        municipality_label = municipality_detail
    else:
        municipality_label = None
    elevation = _compute_site_elevation(lat, lon)

    if project:
        settings = dict(project.settings or {})
        settings['latitude'] = lat
        settings['longitude'] = lon
        if municipality_label:
            settings['txLocationName'] = municipality_label
            if isinstance(municipality_detail, dict):
                settings['txMunicipality'] = municipality_detail.get('label')
                settings['txIbgeCode'] = municipality_detail.get('ibge_code')
                settings['txPopulation'] = municipality_detail.get('population')
                settings['txPopulationYear'] = municipality_detail.get('population_year')
        if elevation is not None:
            settings['txElevation'] = elevation
        project.settings = settings
    else:
        user.latitude = lat
        user.longitude = lon
        if municipality_label:
            user.tx_location_name = municipality_label
        if elevation is not None:
            user.tx_site_elevation = elevation

    try:
        if project:
            db.session.add(project)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 500

    return jsonify({
        'municipality': municipality_label,
        'ibge_code': (municipality_detail or {}).get('ibge_code') if isinstance(municipality_detail, dict) else None,
        'population': (municipality_detail or {}).get('population') if isinstance(municipality_detail, dict) else None,
        'population_year': (municipality_detail or {}).get('population_year') if isinstance(municipality_detail, dict) else None,
        'elevation': elevation,
        'project': project.slug if project else None,
    }), 200


@bp.route('/projects/<slug>/receivers/<receiver_id>', methods=['DELETE'])
@login_required
def delete_project_receiver(slug, receiver_id):
    project = _load_project_for_current_user(slug)
    record = ProjectReceiver.query.filter_by(
        project_id=project.id,
        legacy_id=str(receiver_id),
    ).first()
    settings = dict(project.settings or {})
    if record:
        _delete_receiver_profile_assets(project, record.summary or {})
        db.session.delete(record)
    remaining = ProjectReceiver.query.filter_by(project_id=project.id).count()
    if remaining == 0:
        ProjectCoverage.query.filter_by(project_id=project.id).delete()
        _purge_project_asset_folders(project, ('profiles', 'coverage'))
        settings.pop('receiverBookmarks', None)
        settings.pop('lastCoverage', None)
    else:
        settings['receiverBookmarks'] = _project_receivers_payload(project, include_urls=True)
    project.settings = settings or None
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 500
    return jsonify({'removed': receiver_id, 'count': remaining}), 200


@bp.route('/projects/<slug>/receivers', methods=['DELETE'])
@login_required
def clear_project_receivers(slug):
    project = _load_project_for_current_user(slug)
    records = ProjectReceiver.query.filter_by(project_id=project.id).all()
    cleared = len(records)
    for record in records:
        _delete_receiver_profile_assets(project, record.summary or {})
        db.session.delete(record)
    ProjectCoverage.query.filter_by(project_id=project.id).delete()
    settings = dict(project.settings or {})
    settings.pop('receiverBookmarks', None)
    settings.pop('lastCoverage', None)
    project.settings = settings
    _purge_project_asset_folders(project, ('profiles', 'coverage'))
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 500
    return jsonify({'cleared': cleared, 'coverage_cleared': True}), 200

@bp.route('/clima-recomendado', methods=['GET'])
@login_required
def clima_recomendado():
    user = User.query.get(current_user.id)
    if not user:
        return jsonify({'error': 'Usuário não encontrado.'}), 404

    project_slug = request.args.get('project') or request.args.get('projectSlug')
    
    # 1. Tenta pegar da URL (prioridade para o que está na tela)
    lat_arg = request.args.get('latitude') or request.args.get('lat')
    lon_arg = request.args.get('longitude') or request.args.get('lon')
    
    lat = _coerce_float(lat_arg)
    lon = _coerce_float(lon_arg)

    project = None
    if project_slug:
        try:
            project = _load_project_for_current_user(project_slug)
        except Exception:
            project = None

    # 2. Se não veio na URL, tenta do projeto
    if (lat is None or lon is None) and project:
        lat = _coerce_float((project.settings or {}).get('latitude'))
        lon = _coerce_float((project.settings or {}).get('longitude'))
        if lat is None or lon is None:
            last_cov = _latest_coverage_snapshot(project)
            center = (last_cov or {}).get('center') or (last_cov or {}).get('tx_location')
            if center:
                lat = _coerce_float(center.get('lat') or center.get('latitude'))
                lon = _coerce_float(center.get('lng') or center.get('lon') or center.get('longitude'))

    # 3. Fallback para usuário
    if lat is None or lon is None:
        lat = _coerce_float(user.latitude)
        lon = _coerce_float(user.longitude)

    if lat is None or lon is None:
        return jsonify({'error': 'Latitude/longitude não definidos. Informe a posição da TX primeiro.'}), 400

    lat = float(lat)
    lon = float(lon)
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=360)

    params = {
        'latitude': lat,
        'longitude': lon,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'daily': 'temperature_2m_mean,relative_humidity_2m_mean,surface_pressure_mean',
        'timezone': 'UTC',
    }
    try:
        resp = requests.get('https://archive-api.open-meteo.com/v1/archive', params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        current_app.logger.warning('Falha ao consultar Open-Meteo: %s', exc)
        return jsonify({'error': 'Não foi possível obter dados climáticos.'}), 502

    daily = payload.get('daily', {})
    temps = daily.get('temperature_2m_mean') or []
    humidity = daily.get('relative_humidity_2m_mean') or []
    pressure = daily.get('surface_pressure_mean') or []

    def _safe_mean(seq):
        values = [float(v) for v in seq if v is not None]
        return float(np.mean(values)) if values else None

    avg_temp = _safe_mean(temps)
    avg_humidity = _safe_mean(humidity)
    avg_pressure = _safe_mean(pressure)

    if avg_temp is None:
        avg_temp = 20.0
    if avg_humidity is None:
        avg_humidity = 70.0
    if avg_pressure is None:
        avg_pressure = 1013.0

    temp_c = avg_temp
    rh = max(0.0, min(avg_humidity, 100.0))

    # densidade de vapor diário, depois média anual
    abs_samples = []
    for t, rel in zip(temps, humidity):
        if t is None or rel is None:
            continue
        rel = max(0.0, min(float(rel), 100.0))
        saturation_vapor_pressure = 6.112 * math.exp((17.67 * t) / (t + 243.5))
        actual_vapor_pressure = (rel / 100.0) * saturation_vapor_pressure
        abs_samples.append(216.7 * (actual_vapor_pressure / (t + 273.15)))

    if abs_samples:
        absolute_humidity = float(np.mean(abs_samples))
    else:
        saturation_vapor_pressure = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
        actual_vapor_pressure = (rh / 100.0) * saturation_vapor_pressure
        absolute_humidity = 216.7 * (actual_vapor_pressure / (temp_c + 273.15))

    # Save to Project if active, else User (legacy)
    climate_updated_at = datetime.utcnow()
    
    if project:
        settings = dict(project.settings or {})
        settings['temperature'] = temp_c
        settings['pressure'] = avg_pressure
        settings['waterDensity'] = absolute_humidity
        settings['climateLat'] = lat
        settings['climateLon'] = lon
        settings['climateUpdatedAt'] = climate_updated_at.isoformat()
        project.settings = settings
        db.session.add(project)
    else:
        user.temperature_k = temp_c + 273.15
        user.pressure_hpa = avg_pressure
        user.water_density = absolute_humidity
        user.climate_lat = lat
        user.climate_lon = lon
        user.climate_updated_at = climate_updated_at
        db.session.add(user)
        
    db.session.commit()

    return jsonify({
        'temperature': round(temp_c, 2),
        'pressure': round(avg_pressure, 1),
        'relativeHumidity': round(rh, 1),
        'waterDensity': round(max(0.0, absolute_humidity), 2),
        'daysSampled': len(temps),
        'municipality': (project.settings.get('txLocationName') if project and project.settings else user.tx_location_name),
        'climateUpdatedAt': climate_updated_at.isoformat(),
    })

@bp.route('/visualizar-dados-salvos')
@login_required
def visualizar_dados_salvos():
    # 1. Carrega usuário base
    user = current_user
    
    # 2. Tenta carregar projeto ativo se solicitado
    project_slug = request.args.get('project')
    project = None
    if project_slug:
        try:
            project = _load_project_for_current_user(project_slug)
        except Exception:
            project = None

    # 3. Monta objeto de exibição (prioridade: Projeto > Usuário)
    # Usamos um SimpleNamespace ou dict para emular o acesso por atributo do template
    from types import SimpleNamespace
    
    # Começa com os dados do usuário
    display_data = {
        'username': user.username,
        'email': user.email,
        'servico': user.servico,
        'propagation_model': user.propagation_model,
        'frequencia': user.frequencia,
        'transmission_power': user.transmission_power,
        'antenna_gain': user.antenna_gain,
        'total_loss': user.total_loss,
        'tx_site_elevation': user.tx_site_elevation,
        'latitude': user.latitude,
        'longitude': user.longitude,
        'polarization': user.polarization,
        'notes': user.notes,
        # Artefatos legados
        'perfil_img': user.perfil_img,
        'cobertura_img': user.cobertura_img,
        'antenna_pattern_img_dia_H': user.antenna_pattern_img_dia_H,
        'antenna_pattern_img_dia_V': user.antenna_pattern_img_dia_V,
        'projects': user.projects, # Query object
    }
    
    # Se tiver projeto, sobrescreve com settings
    if project:
        settings = project.settings or {}
        
        # Mapeamento de campos do settings para campos de exibição
        # settings key -> display key
        mapping = {
            'serviceType': 'servico',
            'propagationModel': 'propagation_model',
            'frequency': 'frequencia',
            'transmissionPower': 'transmission_power',
            'antennaGain': 'antenna_gain',
            'Total_loss': 'total_loss',
            'txElevation': 'tx_site_elevation',
            'latitude': 'latitude',
            'longitude': 'longitude',
            'polarization': 'polarization',
        }
        
        for set_key, disp_key in mapping.items():
            if set_key in settings and settings[set_key] is not None:
                display_data[disp_key] = settings[set_key]
                
        # Campos especiais
        if 'txLocationName' in settings:
            # Se quiser exibir nome do local, pode adicionar ao template depois
            pass

    # Converte para objeto para acesso via .atributo no template
    dados_salvos = SimpleNamespace(**display_data)

    image_data = {
        'perfil_img': base64.b64encode(dados_salvos.perfil_img).decode('utf-8') if dados_salvos.perfil_img else None,
        'cobertura_img': base64.b64encode(dados_salvos.cobertura_img).decode('utf-8') if dados_salvos.cobertura_img else None,
        'antenna_pattern_img_dia_H': base64.b64encode(dados_salvos.antenna_pattern_img_dia_H).decode('utf-8') if dados_salvos.antenna_pattern_img_dia_H else None,
        'antenna_pattern_img_dia_V': base64.b64encode(dados_salvos.antenna_pattern_img_dia_V).decode('utf-8') if dados_salvos.antenna_pattern_img_dia_V else None,
    }

    coverage_cards = []
    try:
        # Se projects for query object
        projects_list = dados_salvos.projects.order_by(Project.created_at.desc()).all()
    except Exception:
        projects_list = []

    for proj in projects_list:
        if project_slug and proj.slug != project_slug:
            continue

        snapshot = _latest_coverage_snapshot(proj)
        asset_id = snapshot.get('asset_id') if snapshot else None
        if snapshot and asset_id:
            try:
                preview_url = url_for('projects.asset_preview', slug=proj.slug, asset_id=asset_id)
            except Exception:
                preview_url = None
            if preview_url:
                coverage_cards.append({
                    'project': proj,
                    'engine': snapshot.get('engine'),
                    'generated_at': snapshot.get('generated_at'),
                    'radius_km': snapshot.get('radius_km') or snapshot.get('requested_radius_km'),
                    'center_metrics': snapshot.get('center_metrics'),
                    'preview_url': preview_url,
                    'detail_url': url_for('projects.view_project', slug=proj.slug),
                })

    return render_template('dados_salvos.html', dados_salvos=dados_salvos, image_data=image_data, coverage_cards=coverage_cards)

@bp.route('/update-tilt', methods=['POST'])
@login_required
def update_tilt():
    data = request.get_json() or {}
    tilt = data.get('tilt')
    direction = data.get('direction')
    try:
        user = User.query.get(current_user.id)
        if not user:
            return jsonify({'error': 'Usuário não encontrado.'}), 404
        if tilt is not None and str(tilt).strip() != '':
            tilt_val = _coerce_float(tilt)
            if tilt_val is not None:
                user.antenna_tilt = tilt_val
        if direction is not None:
            direction_str = str(direction).strip()
            if direction_str == '':
                user.antenna_direction = None
            else:
                try:
                    normalized_dir = float(direction_str)
                except ValueError:
                    raise ValueError('Direção inválida')
                normalized_dir = normalized_dir % 360.0
                if normalized_dir < 0:
                    normalized_dir += 360.0
                user.antenna_direction = float(normalized_dir)
        db.session.commit()
        return jsonify({
            'antennaTilt': user.antenna_tilt,
            'antennaDirection': user.antenna_direction
        }), 200
    except (ValueError, SQLAlchemyError) as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 400

@bp.route('/carregar-dados', methods=['GET'])
@login_required
def carregar_dados():
    try:
        user = User.query.get(current_user.id)
        if not user:
            return jsonify({'error': 'Usuário não encontrado.'}), 404
        project_slug = request.args.get('project')
        project = None
        project_settings_view = {}
        if project_slug:
            project = _load_project_for_current_user(project_slug)
            project_settings_view = _project_settings_with_dynamic(project)

        latest_snapshot = _latest_coverage_snapshot(project) if project else None

        base_settings = {}
        if project:
            base_settings = dict(project.settings or {})
            is_new_project = not latest_snapshot and _is_project_settings_empty(base_settings)
            user_data = _blank_project_payload(user, project)
            user_data = _apply_project_settings(user_data, base_settings)
            if latest_snapshot:
                user_data['lastCoverage'] = latest_snapshot
            user_data['projectSettings'] = project_settings_view
            user_data['projectSlug'] = project.slug
            user_data['projectName'] = project.name
            user_data['projectDescription'] = project.description
            user_data['projectLastSavedAt'] = base_settings.get('lastSavedAt')
            if is_new_project:
                user_data['lastCoverage'] = None
        else:
            user_data = {
                'username': user.username,
                'email': user.email,
                'propagationModel': user.propagation_model,
                'frequency': user.frequencia,
                'towerHeight': user.tower_height,
                'rxHeight': user.rx_height,
                'Total_loss': user.total_loss,
                'transmissionPower': user.transmission_power,
                'antennaGain': user.antenna_gain,
                'antennaTilt': user.antenna_tilt,
                'antennaDirection': user.antenna_direction,
                'rxGain': user.rx_gain,
                'latitude': user.latitude,
                'longitude': user.longitude,
                'serviceType': user.servico,
                'nomeUsuario': user.username,
                'timePercentage': user.time_percentage or 40.0,
                'polarization': (user.polarization or 'vertical').lower() if user.polarization else None,
                'p452Version': user.p452_version or 16,
                'temperature': (user.temperature_k - 273.15) if user.temperature_k else 20.0,
                'pressure': user.pressure_hpa or 1013.0,
                'waterDensity': user.water_density or 7.5,
                'txLocationName': user.tx_location_name,
                'txElevation': user.tx_site_elevation,
                'climateUpdatedAt': user.climate_updated_at.isoformat() if user.climate_updated_at else None,
                'climateLatitude': user.climate_lat,
                'climateLongitude': user.climate_lon,
                'coverageEngine': CoverageEngine.p1546.value,
                'projectSettings': {},
                'receiverBookmarks': [],
            }

        source_settings = base_settings if project else project_settings_view
        receiver_bookmarks = source_settings.get('receiverBookmarks') if isinstance(source_settings, dict) else None
        if isinstance(receiver_bookmarks, list):
            user_data['receiverBookmarks'] = receiver_bookmarks
        else:
            user_data['receiverBookmarks'] = []

        # O front já faz a conversão dBi -> dBd para exibição.
        # Não devemos converter aqui, senão ocorre dupla subtração.
        # if user_data.get('antennaGain') is not None:
        #     converted_gain = _gain_dbi_to_dbd(user_data['antennaGain'])
        #     user_data['antennaGain'] = converted_gain if converted_gain is not None else user_data['antennaGain']

        return jsonify(user_data), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('ui.index'))


@bp.route('/reverse-geocode')
@login_required
def reverse_geocode():
    try:
        lat = request.args.get('lat', type=float)
        lon = request.args.get('lon', type=float)
        if lat is None or lon is None:
            return jsonify({'error': 'Parâmetros inválidos.'}), 400
        municipality = _lookup_municipality(lat, lon)
        if not municipality:
            return jsonify({'municipality': '-'}), 200
        return jsonify({
            'municipality': municipality.get('label') or '-',
            'ibge_code': municipality.get('ibge_code'),
            'state': municipality.get('state'),
            'population': municipality.get('population'),
            'population_year': municipality.get('population_year'),
        }), 200
    except Exception as exc:
        current_app.logger.warning('reverse_geocode.failed: %s', exc)
        return jsonify({'error': 'Não foi possível determinar o município.'}), 500
