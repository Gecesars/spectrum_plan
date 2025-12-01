from __future__ import annotations

from pathlib import Path

from flask import current_app

from extensions import db
from app_core.storage import inline_asset_path


def legacy_storage_root() -> Path | None:
    """
    Returns the legacy storage root (if configured and present on disk).
    """
    root = current_app.config.get('LEGACY_STORAGE_ROOT')
    if root:
        path = Path(root)
    else:
        default_path = Path(current_app.root_path).parent / 'storage'
        path = default_path if default_path.exists() else None
    if path is None:
        return None
    if not path.exists():
        return None
    return path


def _resolve_file_path(legacy_path: str) -> Path | None:
    if not legacy_path or str(legacy_path).startswith('inline://'):
        return None
    
    # Strip file:// prefix if present
    clean_path = str(legacy_path)
    if clean_path.startswith('file://'):
        clean_path = clean_path[7:]
        
    root = legacy_storage_root()
    if not root:
        return None
        
    # Prevent directory traversal
    try:
        file_path = (root / clean_path).resolve()
        if not str(file_path).startswith(str(root.resolve())):
            return None
    except Exception:
        return None
        
    return file_path if file_path.exists() else None

def read_asset_data(asset) -> bytes | None:
    """
    Reads asset data from DB or filesystem without modifying the asset.
    """
    if not asset:
        return None
    
    # 1. Try DB data
    if getattr(asset, 'data', None):
        return bytes(asset.data)
        
    # 2. Try filesystem
    legacy_path = getattr(asset, 'path', None)
    file_path = _resolve_file_path(legacy_path)
    if file_path:
        try:
            return file_path.read_bytes()
        except OSError:
            pass
            
    return None

def rehydrate_asset_data(asset, *, kind: str = 'legacy') -> bytes | None:
    """
    Loads legacy data from the filesystem into the provided Asset row, storing it inline.
    Returns the raw bytes when rehydration succeeds.
    """
    if not asset or getattr(asset, 'data', None):
        return None
        
    legacy_path = getattr(asset, 'path', None)
    file_path = _resolve_file_path(legacy_path)
    
    if not file_path:
        return None

    try:
        payload = file_path.read_bytes()
    except OSError:
        return None

    suffix = Path(legacy_path).suffix or '.bin'
    asset.data = payload
    asset.byte_size = len(payload)
    asset.path = inline_asset_path(kind, suffix)
    meta = dict(getattr(asset, 'meta', {}) or {})
    meta.setdefault('legacy_path', str(legacy_path))
    asset.meta = meta
    db.session.add(asset)
    db.session.commit()

    try:
        file_path.unlink()
    except OSError:
        pass
    return payload
