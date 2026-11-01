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
PARTIAL_FILE = "session_partial.json"


def default_sessions_path() -> Path:
    return user_data_dir() / SESSIONS_FILE


def default_partial_path() -> Path:
    return user_data_dir() / PARTIAL_FILE


def _wall_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append_session(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_partial(path: Path, record: dict) -> None:
    """Sobrescribe el parcial de forma atómica: un corte a mitad no deja un fichero a medias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class LootSession:
    """`with LootSession(stats, clock, path):` guarda el total al salir, también si hubo una excepción.

    Además, `checkpoint()` deja un registro parcial (se sobrescribe, no se acumula). Si el proceso muere,
    el parcial que queda se vuelca como sesión «interrumpida» al abrir la siguiente: una noche de botín
    no se pierde por un cierre brusco.
    """

    def __init__(
        self,
        stats: SessionStats,
        clock: Clock,
        path: Path | None = None,
        wall_now: Callable[[], str] = _wall_now,
        partial_path: Path | None = None,
    ) -> None:
        self.stats = stats
        self.clock = clock
        self.path = path
        self.wall_now = wall_now
        # Con una ruta de sesiones propia el parcial vive a su lado, así las pruebas no tocan el del usuario.
        self.partial_path = partial_path or (path.with_name(PARTIAL_FILE) if path else default_partial_path())
        self._wall_start = ""

    def _record(self) -> dict:
        return self.stats.to_record(self.clock.now(), self._wall_start, self.wall_now())

    def recover_interrupted(self) -> bool:
        """Vuelca a las sesiones el parcial de una ejecución que murió sin cerrarse. True si había uno."""
        if not self.partial_path.exists():
            return False
        try:
            record = json.loads(self.partial_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = None
        if isinstance(record, dict):
            append_session(self.path or default_sessions_path(), {**record, "interrumpida": True})
        self.partial_path.unlink(missing_ok=True)
        return isinstance(record, dict)

    def checkpoint(self) -> None:
        _write_partial(self.partial_path, self._record())

    def __enter__(self) -> SessionStats:
        self.recover_interrupted()
        self.stats.started_at = self.clock.now()
        self._wall_start = self.wall_now()
        return self.stats

    def __exit__(self, *exc: object) -> None:
        append_session(self.path or default_sessions_path(), self._record())
        self.partial_path.unlink(missing_ok=True)


__all__ = ["LootSession", "PARTIAL_FILE", "SESSIONS_FILE", "append_session", "default_partial_path", "default_sessions_path"]
