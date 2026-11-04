"""Registro de entradas del jugador (issue #43): anota pulsaciones y gestos sobre la ventana del juego.

Lógica pura: no sabe nada de Windows ni de OBS. Recibe eventos de ratón ya capturados, el estado de la
ventana, un reloj y un destino de líneas, todo inyectado. De OBS solo recibe avisos ya interpretados
(`obs_event`, `set_video_offset`, ver `obs_link.py`). El pegamento con el sistema real vive en
`interface/input_recorder.py`.

Formato: JSON Lines, un evento por línea, escrito y volcado en cuanto ocurre. Las posiciones van
normalizadas (0-1) respecto al área cliente de la ventana.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from ..device.base import Rect
from .clock import Clock

MOVE_INTERVAL = 0.10  # s entre posiciones anotadas con el botón libre
DRAG_MOVE_INTERVAL = 0.03  # s entre posiciones anotadas con un botón pulsado


class InputLogError(Exception):
    """El registro no puede empezar (p. ej. no se encuentra la ventana del juego)."""


@dataclass(frozen=True)
class WindowState:
    """Área cliente de la ventana del juego en pantalla y si tiene el foco."""

    client: Rect
    focused: bool


class LineSink(Protocol):
    def write_line(self, line: str) -> None: ...
    def close(self) -> None: ...


class FileSink:
    """Escribe cada línea y la vuelca al instante: un cierre brusco no pierde lo anterior."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = path.open("a", encoding="utf-8")

    def write_line(self, line: str) -> None:
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def session_filename(when: datetime, town_hall: int) -> str:
    return f"input_{when:%Y-%m-%d_%H-%M-%S}_th{town_hall}.jsonl"


class InputLog:
    def __init__(
        self,
        clock: Clock,
        wall: Callable[[], datetime],
        window: Callable[[], WindowState | None],
        open_sink: Callable[[datetime, int], LineSink],
    ) -> None:
        self._clock = clock
        self._wall = wall
        self._window = window
        self._open_sink = open_sink
        self._lock = threading.RLock()
        self._sink: LineSink | None = None
        self._t0 = 0.0
        self._size: tuple[int, int] | None = None
        self._held: set[str] = set()  # botones con una pulsación anotada aún sin su suelta
        self._reference_done = False
        self._last_move = float("-inf")
        # Distancia entre el reloj local y el vídeo de OBS. Vive aunque el registro esté parado: OBS puede
        # empezar a grabar antes de que se pulse la tecla de empezar.
        self._video_offset: float | None = None

    @property
    def recording(self) -> bool:
        return self._sink is not None

    # --- OBS (issue #44) ------------------------------------------------------

    def set_video_offset(self, offset: float | None) -> None:
        """Fija la distancia reloj local - vídeo, o None si ya no se conoce (parar, pausar, reanudar)."""
        with self._lock:
            self._video_offset = offset

    def obs_event(self, kind: str) -> None:
        """Anota el aviso de inicio (`start`) o fin (`stop`) de grabación de OBS."""
        with self._lock:
            if self._sink is not None:
                self._emit(f"obs_{kind}")

    # --- control --------------------------------------------------------------

    def start(self, town_hall: int) -> None:
        with self._lock:
            if self._sink is not None:
                return
            state = self._window()
            if state is None:
                raise InputLogError("No se encuentra la ventana del juego.")
            now = self._wall()
            self._sink = self._open_sink(now, town_hall)
            self._t0 = self._clock.now()
            self._size = None
            self._held.clear()
            self._reference_done = False
            self._last_move = float("-inf")
            self._emit("start", town_hall=town_hall)
            self._note_size(state)

    def stop(self) -> None:
        with self._lock:
            if self._sink is None:
                return
            self._emit("end")
            self._sink.close()
            self._sink = None

    def toggle(self, town_hall: int) -> None:
        with self._lock:
            if self.recording:
                self.stop()
            else:
                self.start(town_hall)

    def mark(self) -> None:
        with self._lock:
            if self._sink is not None:
                self._emit("mark")

    # --- entradas de ratón ----------------------------------------------------

    def button(self, button: str, pressed: bool, x: int, y: int) -> None:
        with self._lock:
            if self._sink is None:
                return
            if pressed:
                state = self._active_state()
                if state is None or not self._inside(state, x, y):
                    return
                self._note_size(state)
                extra = {}
                if not self._reference_done:
                    self._reference_done = True
                    extra["reference"] = True
                self._held.add(button)
                self._emit("press", button=button, **self._pos(state, x, y), **extra)
            elif button in self._held:
                # La suelta de una pulsación anotada se registra siempre, aunque sea fuera o sin foco.
                self._held.discard(button)
                state = self._window()
                pos = self._pos(state, x, y) if state is not None else {}
                self._emit("release", button=button, **pos)

    def move(self, x: int, y: int) -> None:
        with self._lock:
            if self._sink is None:
                return
            state = self._window()
            if state is None:
                return
            if self._held:
                interval = DRAG_MOVE_INTERVAL
            else:
                if not (state.focused and self._inside(state, x, y)):
                    return
                interval = MOVE_INTERVAL
            now = self._clock.now()
            if now - self._last_move < interval:
                return
            self._last_move = now
            self._note_size(state)
            self._emit("move", **self._pos(state, x, y))

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        with self._lock:
            if self._sink is None:
                return
            state = self._active_state()
            if state is None or not self._inside(state, x, y):
                return
            self._note_size(state)
            self._emit("scroll", dx=dx, dy=dy, **self._pos(state, x, y))

    # --- internos -------------------------------------------------------------

    def _active_state(self) -> WindowState | None:
        state = self._window()
        return state if state is not None and state.focused else None

    @staticmethod
    def _inside(state: WindowState, x: int, y: int) -> bool:
        c = state.client
        return c.x <= x < c.x + c.w and c.y <= y < c.y + c.h

    @staticmethod
    def _pos(state: WindowState, x: int, y: int) -> dict:
        c = state.client
        return {"x": round((x - c.x) / c.w, 4), "y": round((y - c.y) / c.h, 4)}

    def _note_size(self, state: WindowState) -> None:
        size = (state.client.w, state.client.h)
        if size != self._size:
            self._size = size
            self._emit("window_size", w=size[0], h=size[1])

    def _emit(self, event: str, **fields) -> None:
        now = self._clock.now()
        record = {
            "event": event,
            "t": round(now - self._t0, 4),
            "wall": self._wall().isoformat(timespec="milliseconds"),
        }
        if self._video_offset is not None:
            record["video"] = round(now - self._video_offset, 4)
        record.update(fields)
        self._sink.write_line(json.dumps(record, ensure_ascii=False))
