"""Rutas de datos del usuario (marcadores de restauración, configuración local).

La carpeta es la **local** de la máquina (`%LOCALAPPDATA%\\PyClash`), no la de itinerancia: guarda la
identidad de la ventana de este equipo y los secretos, que no deben viajar con el perfil de Windows.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping

APP_DIR_NAME = "PyClash"

# Lo que antes se guardaba en %APPDATA%\PyClash. Los marcadores de restauración van primero: si quedara uno
# pendiente en la carpeta vieja, el programa dejaría de verlo y la pantalla se quedaría sin restaurar.
LEGACY_FILES = ("display_restore.devmode", "hidden_window.json", "sessions.jsonl")


def local_base(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("LOCALAPPDATA") or Path.home() / ".local" / "share")


def legacy_data_dir(env: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    roaming = env.get("APPDATA")
    return Path(roaming) / APP_DIR_NAME if roaming else None


def adopt_legacy_files(new: Path, legacy: Path | None) -> list[str]:
    """Mueve a `new` lo que haya en la carpeta antigua y no exista ya allí; devuelve los nombres movidos.

    Nunca pisa un fichero de la carpeta nueva y deja en su sitio lo que no reconoce. Si la carpeta antigua
    queda vacía, la quita.
    """
    if legacy is None or not legacy.is_dir() or legacy.resolve() == new.resolve():
        return []
    moved: list[str] = []
    for name in LEGACY_FILES:
        old, target = legacy / name, new / name
        if old.is_file() and not target.exists():
            shutil.move(str(old), str(target))
            moved.append(name)
    try:
        legacy.rmdir()  # solo si está vacía
    except OSError:
        pass
    return moved


def user_data_dir() -> Path:
    path = local_base() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    adopt_legacy_files(path, legacy_data_dir())
    return path
