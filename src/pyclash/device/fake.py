"""Dispositivo falso: reproduce una secuencia de fotogramas guiada por las acciones recibidas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..runtime.clock import Clock
from .base import Capabilities, Frame, Rect


@dataclass(frozen=True)
class Action:
    kind: str  # "tap" | "swipe" | "scroll" | "press" | "release"
    args: tuple
    at: float


@dataclass(frozen=True)
class Transition:
    """Al recibir `kind` (dentro de `region`, si se indica) desde el fotograma `source`, pasa a `target`."""

    source: int
    kind: str
    target: int
    region: Optional[Rect] = None


class FakeDevice:
    def __init__(
        self,
        frames: list[Frame],
        clock: Clock,
        transitions: list[Transition] | None = None,
        capabilities: Capabilities | None = None,
    ) -> None:
        if not frames:
            raise ValueError("se necesita al menos un fotograma")
        self._frames = frames
        self._clock = clock
        self._transitions = transitions or []
        self._caps = capabilities or Capabilities()
        self._index = 0
        self._held = False
        self.actions: list[Action] = []

    @property
    def capabilities(self) -> Capabilities:
        return self._caps

    @property
    def held(self) -> bool:
        return self._held

    @property
    def frame_index(self) -> int:
        return self._index

    def capture(self) -> Frame:
        f = self._frames[self._index]
        return Frame(f.image, f.viewport, self._clock.now(), f.metadata)

    def tap(self, x: int, y: int) -> None:
        self._act("tap", (x, y), x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        if not self._caps.swipe:
            raise NotImplementedError("swipe no disponible en este dispositivo")
        self._act("swipe", (x1, y1, x2, y2, duration), x1, y1)

    def scroll(self, x: int, y: int, amount: int) -> None:
        if not self._caps.scroll:
            raise NotImplementedError("scroll no disponible en este dispositivo")
        self._act("scroll", (x, y, amount), x, y)

    def press(self, x: int, y: int) -> None:
        if not self._caps.hold:
            raise NotImplementedError("pulsación sostenida no disponible")
        self._held = True
        self._act("press", (x, y), x, y)

    def release(self) -> None:
        self._held = False
        self._act("release", (), None, None)

    def _act(self, kind: str, args: tuple, x: int | None, y: int | None) -> None:
        self.actions.append(Action(kind, args, self._clock.now()))
        for t in self._transitions:
            if t.source != self._index or t.kind != kind:
                continue
            if t.region is not None and (x is None or y is None or not t.region.contains(x, y)):
                continue
            self._index = t.target
            return
