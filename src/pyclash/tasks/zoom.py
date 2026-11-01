"""Zoom-out máximo de la aldea (issue #37): el estado normal de la aldea para leer y fotografiar a una escala fija.

Con un menú abierto el juego no deja alejar (tras confirmar una mejora queda abierto el menú «Buildings & Heroes»),
así que primero se vuelve a la aldea con `RecoverScreen`, que cierra cada pantalla por su vía de escape (la X del
menú) y nunca pulsa a ciegas. Después se gira la rueda hacia fuera hasta que dos capturas seguidas son casi idénticas
(el tope). Si tras `max_scrolls` giros la imagen sigue cambiando, falla con captura: las tareas que dependen de la
escala no deben correr.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from PIL import Image

from ..device.base import Frame
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, Error
from ..screens.classifier import ScreenClassifier
from .base import Outcome, TaskResult
from .recovery import RecoverScreen, RecoveryConfig


@dataclass(frozen=True)
class ZoomOutConfig:
    scroll_amount: int = -5  # muescas por giro; el signo que aleja está PROVISIONAL (sin medir en el juego real)
    max_scrolls: int = 6
    stable_diff: float = 1.0  # diferencia media por canal (0-255) por debajo de la cual el zoom no cambia
    settle_seconds: float = 0.6  # espera tras cada giro para que termine la animación
    capture_dir: Path | None = None  # dónde guardar la captura del fallo (None: no se guarda)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


def frame_difference(a: np.ndarray, b: np.ndarray) -> float:
    """Diferencia media absoluta por canal entre dos fotogramas; infinita si no tienen el mismo tamaño."""
    if a.shape != b.shape:
        return float("inf")
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


class ZoomOut:
    name = "zoom_out"

    def __init__(self, config: ZoomOutConfig | None = None, classifier: ScreenClassifier | None = None) -> None:
        self.config = config or ZoomOutConfig()
        self.classifier = classifier or ScreenClassifier()

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        ctx.check_stop()
        recovery = replace(cfg.recovery, capture_dir=cfg.recovery.capture_dir or cfg.capture_dir)
        back = RecoverScreen(self.classifier, recovery).run(ctx)
        if back.outcome not in (Outcome.DONE, Outcome.NOTHING_TO_DO):
            return back  # sin aldea a la vista no se gira la rueda

        frame = ctx.device.capture()
        height, width = frame.image.shape[:2]
        previous = frame.image
        for _ in range(cfg.max_scrolls):
            ctx.check_stop()
            ctx.device.scroll(width // 2, height // 2, cfg.scroll_amount)
            ctx.sleep(cfg.settle_seconds)
            frame = ctx.device.capture()
            if frame_difference(previous, frame.image) < cfg.stable_diff:
                return TaskResult.done()
            previous = frame.image
        return self._fail(ctx, frame, f"el zoom no llegó al máximo tras {cfg.max_scrolls} giros de rueda")

    def _fail(self, ctx: Context, frame: Frame, reason: str) -> TaskResult:
        path = self._save(ctx, frame)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))
        ctx.events.publish(Error(reason))
        return TaskResult.failed(reason)

    def _save(self, ctx: Context, frame: Frame) -> Path | None:
        if self.config.capture_dir is None:
            return None
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.capture_dir / f"zoom-{int(ctx.clock.now() * 1000)}.png"
        Image.fromarray(frame.image).save(path)
        return path
