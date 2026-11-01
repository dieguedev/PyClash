"""Almacén de configuración en disco: `config.json` y un fichero por cuenta en `profiles/` (issue #15).

Leer nunca lanza ni corrige en silencio: devuelve los valores válidos y los problemas. Guardar rechaza
cualquier valor inválido (`ConfigError`, con el rango válido) y escribe de forma atómica.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypeVar

from .files import atomic_write_text, remove_stale_temps, set_aside_copy
from .schema import (
    SCHEMA_VERSION,
    AccountProfile,
    AppConfig,
    ConfigError,
    ConfigProblem,
    Migration,
    app_to_dict,
    migrate,
    parse_app,
    parse_profile,
    profile_to_dict,
    validate_app,
    validate_profile,
)

APP_FILE = "config.json"
PROFILES_DIR = "profiles"
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

T = TypeVar("T")


@dataclass
class Loaded(Generic[T]):
    value: T
    problems: list[ConfigProblem] = field(default_factory=list)
    path: Path | None = None
    newer_version: bool = False  # el fichero es de una versión más nueva: se lee, pero no se sobrescribe
    set_aside: Path | None = None  # copia `.bad` si el fichero era ilegible


def profile_filename(name: str) -> str:
    """Nombre de fichero seguro para una cuenta; si hubo que cambiar algo, se añade un sufijo para que no choque."""
    stem = re.sub(r"[^\w\- ]", "_", name, flags=re.UNICODE).strip(" .")[:40]
    if not stem or stem != name or stem.upper() in _RESERVED:
        stem = f"{stem or 'cuenta'}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:6]}"
    return f"{stem}.json"


class ConfigStore:
    def __init__(self, directory: Path, migrations: Mapping[str, Mapping[int, Migration]] | None = None):
        self.directory = Path(directory)
        self._migrations = migrations
        self._lock = threading.RLock()  # la interfaz y el bot pueden guardar desde hilos distintos

    @property
    def app_path(self) -> Path:
        return self.directory / APP_FILE

    @property
    def profiles_dir(self) -> Path:
        return self.directory / PROFILES_DIR

    # ------------------------------------------------------------ lectura

    def _read(self, path: Path, kind: str) -> tuple[dict[str, Any] | None, int, list[ConfigProblem], Path | None]:
        """JSON migrado, versión hallada, problemas y copia `.bad` (si no se pudo leer)."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            copy = set_aside_copy(path)
            where = f"; copia en {copy.name}" if copy else ""
            return None, SCHEMA_VERSION, [ConfigProblem(path.name, f"no se puede leer ({type(e).__name__}){where}; se usan los valores por defecto")], copy
        if not isinstance(raw, dict):
            copy = set_aside_copy(path)
            where = f"; copia en {copy.name}" if copy else ""
            return None, SCHEMA_VERSION, [ConfigProblem(path.name, f"debe contener un objeto JSON{where}; se usan los valores por defecto")], copy
        migrations = None if self._migrations is None else self._migrations.get(kind)
        data, found, problems = migrate(raw, kind, migrations=migrations)
        return data, found, problems, None

    def load_app(self) -> Loaded[AppConfig]:
        remove_stale_temps(self.directory)
        path = self.app_path
        if not path.is_file():
            return Loaded(AppConfig(), path=path)
        data, found, problems, aside = self._read(path, "app")
        if data is None:
            return Loaded(AppConfig(), problems, path, set_aside=aside)
        app, more = parse_app(data)
        return Loaded(app, problems + more, path, newer_version=found > SCHEMA_VERSION)

    def _load_profile_file(self, path: Path) -> Loaded[AccountProfile]:
        data, found, problems, aside = self._read(path, "profile")
        if data is None:
            profile, _ = parse_profile({}, path.stem)
            return Loaded(profile, problems, path, set_aside=aside)
        profile, more = parse_profile(data, path.stem)
        return Loaded(profile, problems + more, path, newer_version=found > SCHEMA_VERSION)

    def list_profiles(self) -> list[Loaded[AccountProfile]]:
        remove_stale_temps(self.profiles_dir)
        if not self.profiles_dir.is_dir():
            return []
        found = [self._load_profile_file(p) for p in sorted(self.profiles_dir.glob("*.json"))]
        return sorted(found, key=lambda loaded: loaded.value.name.lower())

    def load_profile(self, name: str) -> Loaded[AccountProfile] | None:
        return next((p for p in self.list_profiles() if p.value.name == name), None)

    # ------------------------------------------------------------ escritura

    def _refuse_newer(self, path: Path, kind: str) -> None:
        if not path.is_file():
            return
        try:
            version = json.loads(path.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            return  # ilegible: guardar encima es justo lo que se quiere (queda la copia .bad al leerlo)
        if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
            raise ConfigError(
                f"{path.name} es de una versión más nueva ({version}) que la de este programa ({SCHEMA_VERSION}); "
                "no se sobrescribe para no perder sus ajustes"
            )

    def _write(self, path: Path, payload: Mapping[str, Any], kind: str) -> None:
        self._refuse_newer(path, kind)
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def save_app(self, app: AppConfig) -> None:
        problems = validate_app(app)
        if problems:
            raise ConfigError(problems)
        with self._lock:
            self._write(self.app_path, app_to_dict(app), "app")

    def save_profile(self, profile: AccountProfile) -> Path:
        problems = validate_profile(profile)
        if problems:
            raise ConfigError(problems)
        with self._lock:
            existing = self.load_profile(profile.name)
            path = existing.path if existing and existing.path else self.profiles_dir / profile_filename(profile.name)
            self._write(path, profile_to_dict(profile), "profile")
            return path

    def delete_profile(self, name: str) -> bool:
        with self._lock:
            existing = self.load_profile(name)
            if existing is None or existing.path is None:
                return False
            existing.path.unlink()
            return True


__all__ = ["ConfigStore", "Loaded", "profile_filename", "APP_FILE", "PROFILES_DIR"]
