from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List


def load_homologations(path: str) -> List[Dict[str, str]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    if file_path.suffix.lower() == '.json':
        return json.loads(file_path.read_text())
    rows: List[Dict[str, str]] = []
    with file_path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append({k.lower(): v for k, v in row.items()})
    return rows
