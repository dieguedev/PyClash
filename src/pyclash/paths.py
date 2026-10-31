"""Rutas de datos del usuario (marcadores de restauración, configuración local)."""

from __future__ import annotations

import os
from pathlib import Path


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "PyClash"
    path.mkdir(parents=True, exist_ok=True)
    return path
