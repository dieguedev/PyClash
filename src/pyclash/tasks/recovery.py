"""Recuperación de pantalla: vuelve a una pantalla objetivo sin pulsar nunca a ciegas.

Por capas, en cada vuelta: pantalla conocida -> su vía de escape; desconocida -> botón de cerrar
localizado o terreno vacío; sin salida segura -> no se toca nada, se guarda una captura y falla.
Cada toque cuenta como un intento y tras `max_attempts` la tarea falla con motivo y evento de error.
Detecta ventana perdida, captura negra, juego cerrado e inicio de sesión de Supercell (no se toca).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from ..device.base import BlackCapture, CaptureFailed, Frame, WindowMinimized, WindowNotFound
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, Error, StateChanged, Warning
from ..screens.classifier import Classification, Screen, ScreenClassifier
from ..screens.escapes import find_escape
from .base import TaskResult


@dataclass(frozen=True)
class RecoveryConfig:
    goal: frozenset[Screen] = frozenset({Screen.MAIN_VILLAGE})
    max_attempts: int = 5
    settle_seconds: float = 1.0  # espera tras cada toque para que la pantalla cambie
    allow_surrender: bool = False  # rendirse tira la batalla: solo con permiso explícito
    terrain_fallback: bool = True
    black_patience: int = 3  # capturas negras seguidas antes de dar el juego por cerrado
    relaunch_wait: float = 20.0
    reload_wait: float = 30.0  # espera tras «RELOAD GAME» (aviso de desconexión por inactividad)
    capture_dir: Path | None = None  # dónde guardar la captura del fallo (None: no se guarda)


@dataclass(frozen=True)
class RecoveryHooks:
    """Capacidades opcionales del dispositivo: sin ellas, el fallo se comunica y la tarea se detiene."""

    relaunch: Callable[[], bool] | None = None  # reabre el juego; True si lo intentó
    restore_window: Callable[[], None] | None = None  # saca la ventana de minimizada


class RecoverScreen:
    name = "recover_screen"

    def __init__(
        self,
        classifier: ScreenClassifier | None = None,
        config: RecoveryConfig | None = None,
        hooks: RecoveryHooks | None = None,
    ) -> None:
        self.classifier = classifier or ScreenClassifier()
        self.config = config or RecoveryConfig()
        self.hooks = hooks or RecoveryHooks()

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        attempts = blanks = 0
        last: Screen | None = None
        while True:
            ctx.check_stop()
            try:
                frame = ctx.device.capture()
                cls = self.classifier.classify(frame.image)
            except WindowMinimized:
                if self.hooks.restore_window is None:
                    return self._stop(ctx, "la ventana del juego está minimizada y no hay forma de restaurarla")
                self.hooks.restore_window()
                frame, cls = None, Classification(Screen.BLANK, 1.0, reason="ventana minimizada")
            except WindowNotFound:
                if self.hooks.relaunch is None:
                    return self._stop(ctx, "se perdió la ventana del juego y el dispositivo no puede reabrirlo")
                frame, cls = None, Classification(Screen.BLANK, 1.0, reason="ventana perdida")
                blanks = cfg.black_patience  # sin ventana no hay nada que esperar
            except (BlackCapture, CaptureFailed) as exc:
                frame, cls = None, Classification(Screen.BLANK, 1.0, reason=str(exc) or "captura fallida")

            if cls.screen != last:
                ctx.events.publish(StateChanged(cls.screen.value))
                last = cls.screen
            if cls.screen in cfg.goal:
                return TaskResult.done() if attempts else TaskResult.nothing_to_do("ya en la pantalla objetivo")
            if cls.screen is Screen.LOGIN:
                # Sin captura: el formulario muestra credenciales.
                return self._stop(ctx, "inicio de sesión de Supercell: no se interactúa con él")
            if attempts >= cfg.max_attempts:
                return self._fail(ctx, frame, f"no se salió de «{cls.screen.value}» tras {attempts} intentos")
            attempts += 1

            if cls.screen is Screen.BLANK:
                blanks += 1
                if blanks >= cfg.black_patience:
                    if self.hooks.relaunch is None:
                        return self._stop(ctx, f"el juego parece cerrado ({cls.reason}) y no se puede reabrir")
                    ctx.events.publish(Warning(f"{cls.reason}: reabriendo el juego"))
                    self.hooks.relaunch()
                    blanks = 0
                    ctx.sleep(cfg.relaunch_wait)
                else:
                    ctx.sleep(cfg.settle_seconds)
                continue
            blanks = 0

            assert frame is not None
            tap = find_escape(frame.image, cls.screen, self.classifier.finder, cfg.allow_surrender, cfg.terrain_fallback)
            if tap is None:
                if cls.screen is Screen.BATTLE:
                    return self._stop(ctx, "hay una batalla en curso y no se permite rendirse")
                return self._fail(ctx, frame, f"sin salida segura desde «{cls.screen.value}» ({cls.reason or 'sin botón de cerrar ni terreno vacío'})")
            if cls.screen is Screen.UNKNOWN:
                self._record_unknown(ctx, frame, cls, tap.why)
            ctx.events.publish(Warning(f"recuperación: {cls.screen.value} -> {tap.why} en ({tap.x}, {tap.y})"))
            ctx.device.tap(tap.x, tap.y)
            # Recargar el juego tarda: sin esperar, la pantalla de carga (desconocida) gastaría intentos.
            ctx.sleep(cfg.reload_wait if cls.screen is Screen.INACTIVITY_POPUP else cfg.settle_seconds)

    def _stop(self, ctx: Context, reason: str) -> TaskResult:
        ctx.events.publish(Error(reason))
        return TaskResult.blocked(reason)

    def _fail(self, ctx: Context, frame: Frame | None, reason: str) -> TaskResult:
        path = self._save(ctx, frame)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))
        ctx.events.publish(Error(reason))
        return TaskResult.failed(reason)

    def _record_unknown(self, ctx: Context, frame: Frame, cls: Classification, why: str) -> None:
        """Una pantalla que no se reconoce se cierra, pero antes se guarda su captura (y se dice por
        qué no se reconoció): son las que luego se anonimizan para añadir su firma."""
        path = self._save(ctx, frame, "unknown")
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))
        shown = f"captura {path.name}" if path is not None else "sin carpeta de capturas configurada"
        candidates = ", ".join(f"{s.value} {c:.2f}" for s, c in cls.candidates) or "ninguna"
        ctx.events.publish(
            Warning(f"pantalla desconocida ({cls.reason}; firmas cumplidas: {candidates}); se cierra con {why}; {shown}")
        )

    def _save(self, ctx: Context, frame: Frame | None, prefix: str = "recovery") -> Path | None:
        if frame is None or self.config.capture_dir is None:
            return None
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.capture_dir / f"{prefix}-{int(ctx.clock.now() * 1000)}.png"
        Image.fromarray(frame.image).save(path)
        return path
