"""Importación de los datos de PixelChief v1 (issue #15).

Solo **lee** de la carpeta de la v1 (`%LOCALAPPDATA%\\PixelChief`); no la toca. La v1 tenía un único fichero
de ajustes para todas las cuentas: al importar, cada jugador recibe una copia. Un fichero ilegible se
informa y se sigue con lo demás. Los ajustes de ataque y muros se guardan en `from_v1` sin usarse todavía.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from ..paths import local_base
from .schema import (
    AccountProfile,
    ConfigProblem,
    V1Pending,
    WindowSelection,
    parse_profile,
)
from .store import ConfigStore

V1_DIR_NAME = "PixelChief"
DEFAULT_PROFILE_NAME = "default"  # la v1 sin lista de jugadores era una sola cuenta


def v1_data_dir(env: Mapping[str, str] | None = None) -> Path:
    return local_base(env) / V1_DIR_NAME


@dataclass
class V1Report:
    """Lo que hay en la carpeta de la v1 y cómo se convertiría."""

    directory: Path
    settings: AccountProfile | None = None  # ajustes convertidos (con el nombre vacío: se rellena por jugador)
    players: list[tuple[str, bool]] = field(default_factory=list)
    window: WindowSelection | None = None
    problems: list[str] = field(default_factory=list)  # ficheros ilegibles y valores corregidos
    found: list[str] = field(default_factory=list)  # nombres de los ficheros de v1 presentes

    @property
    def has_data(self) -> bool:
        return bool(self.found)

    def describe(self) -> list[str]:
        if not self.has_data:
            return [f"No hay datos de la v1 en {self.directory}."]
        lines = [f"Datos de la v1 en {self.directory}: {', '.join(self.found)}"]
        if self.settings is not None:
            u = self.settings.upgrade
            lines.append(
                f"· Ajustes: orden {u.order}, reserva de constructores {u.reserve_builders} "
                "(y los de ataque y muros, guardados sin usar todavía)"
            )
        if self.players:
            names = ", ".join(f"{n}{'' if on else ' (desactivado)'}" for n, on in self.players)
            lines.append(f"· Jugadores ({len(self.players)}): {names}")
        elif self.settings is not None:
            lines.append(f"· Sin lista de jugadores: se creará la cuenta «{DEFAULT_PROFILE_NAME}»")
        if self.window is not None and self.window.is_set():
            lines.append(f"· Ventana fijada: {self.window.title or self.window.child_class}")
        lines.extend(f"⚠ {p}" for p in self.problems)
        return lines


def _load_json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append(f"{path.name} no se puede leer ({type(e).__name__}); se ignora y se usan los valores por defecto")
        return None


def _convert_settings(raw: Any, problems: list[str]) -> AccountProfile | None:
    if not isinstance(raw, dict):
        problems.append("settings.json no contiene un objeto; se ignora")
        return None
    # Los nombres de la v1 y los de PyClash no coinciden: upgrade_order → upgrade.order, etc.
    body = {
        "upgrade": {
            "order": raw.get("upgrade_order", "priciest"),
            "reserve_builders": raw.get("reserve_builders", 1),
        },
        "from_v1": {k: raw[k] for k in V1Pending.__dataclass_fields__ if k in raw},
    }
    profile, issues = parse_profile(body, name_hint="-")
    problems.extend(f"settings.json → {p}" for p in issues)
    return replace(profile, name="")


def detect_v1(directory: Path | None = None) -> V1Report:
    directory = directory or v1_data_dir()
    report = V1Report(directory)
    path = directory / "settings.json"
    if path.is_file():
        report.found.append(path.name)
        report.settings = _convert_settings(_load_json(path, report.problems), report.problems)
    path = directory / "player_list.json"
    if path.is_file():
        report.found.append(path.name)
        raw = _load_json(path, report.problems)
        if isinstance(raw, list):
            seen: set[str] = set()
            for item in raw:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name.strip() and name.strip() not in seen:
                    seen.add(name.strip())
                    report.players.append((name.strip(), bool(item.get("enabled", True))))
        elif raw is not None:
            report.problems.append("player_list.json no contiene una lista; se ignora")
    path = directory / "window.json"
    if path.is_file():
        report.found.append(path.name)
        raw = _load_json(path, report.problems)
        if isinstance(raw, dict):
            text = lambda k: str(raw.get(k) or "").strip()  # noqa: E731
            report.window = WindowSelection(text("title"), text("top_class"), text("child_class"))
        elif raw is not None:
            report.problems.append("window.json no contiene un objeto; se ignora")
    return report


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # ya existían: no se pisan
    window_imported: bool = False
    problems: list[ConfigProblem] = field(default_factory=list)

    def describe(self) -> list[str]:
        lines = [f"Cuentas creadas: {', '.join(self.created) or 'ninguna'}"]
        if self.skipped:
            lines.append(f"Ya existían y no se tocaron: {', '.join(self.skipped)}")
        lines.append("Ventana importada." if self.window_imported else "Ventana: sin cambios.")
        return lines


def import_v1(report: V1Report, store: ConfigStore) -> ImportResult:
    """Aplica lo detectado al almacén nuevo. No pisa cuentas ni una ventana que ya existan."""
    result = ImportResult()
    base = report.settings or AccountProfile(name="")
    players = report.players or ([(DEFAULT_PROFILE_NAME, True)] if report.settings is not None else [])
    existing = {p.value.name for p in store.list_profiles()}
    for name, enabled in players:
        if name in existing:
            result.skipped.append(name)
            continue
        store.save_profile(replace(base, name=name, enabled=enabled))
        result.created.append(name)
        existing.add(name)
    if report.window is not None and report.window.is_set():
        app = store.load_app().value
        if not app.window.is_set():
            store.save_app(replace(app, window=report.window))
            result.window_imported = True
    return result
