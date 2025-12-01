from __future__ import annotations

import uuid


def inline_asset_path(kind: str, extension: str) -> str:
    """
    Generates a pseudo-path that identifies assets stored inline in the database.
    """
    identifier = uuid.uuid4().hex
    sanitized = (extension or "").lstrip(".") or "bin"
    return f"inline://{kind}/{identifier}.{sanitized}"
