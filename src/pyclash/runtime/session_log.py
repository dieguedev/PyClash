"""Registro de sesiones de botín: una línea JSON por sesión, escrita al cerrar (issue #12).

JSON Lines: se añade al final, así que una sesión que muere a medias no corrompe las anteriores.
Solo lleva cifras: ni nombres de jugadores ni de ventana (regla del repositorio sobre datos personales).
El reloj y la hora de pared se inyectan; aquí no se lee el tiempo real.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..paths import user_data_dir
from ..policy.loot import SessionStats
from .clock import Clock

SESSIONS_FILE = "sessions.jsonl"


def default_sessions_path() -> Path:
    return user_data_dir() / SESSIONS_FILE


def _wall_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append_session(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class LootSession:
    """`with LootSession(stats, clock, path):` guarda el total al salir, también si hubo una excepción."""

    def __init__(
        self,
        stats: SessionStats,
        clock: Clock,
        path: Path | None = None,
        wall_now: Callable[[], str] = _wall_now,
    ) -> None:
        self.stats = stats
        self.clock = clock
        self.path = path
        self.wall_now = wall_now
        self._wall_start = ""

    def __enter__(self) -> SessionStats:
        self.stats.started_at = self.clock.now()
        self._wall_start = self.wall_now()
        return self.stats

    def __exit__(self, *exc: object) -> None:
        record = self.stats.to_record(self.clock.now(), self._wall_start, self.wall_now())
        append_session(self.path or default_sessions_path(), record)


__all__ = ["LootSession", "SESSIONS_FILE", "append_session", "default_sessions_path"]
