"""Piezas comunes de las tareas que trabajan sobre el panel «Buildings & Heroes»: volver a la aldea,
abrir y escanear el panel, cooldowns por fila y capturas de lo que se aparta.

Cada tarea pone su propia cadena de seguridad encima; aquí no se confirma ni se gasta nada.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ..runtime.context import Context
from ..runtime.events import CaptureSaved
from ..screens.classifier import ScreenClassifier
from ..screens.panel_layout import PANEL_PROFILES
from ..screens.upgrade_panel import PanelReader, PanelRow, PanelScanner
from ..screens.village import VillageReader
from .base import Outcome, SessionView, ShouldRun, TaskResult
from .recovery import RecoverScreen

ENGLISH_HINT = " (el bot exige el juego en inglés)"


class PanelTask:
    """Base de las tareas del panel. `config` debe tener: menu, recovery, open_timeout_s, cooldown_s, capture_dir."""

    cooldown_prefix = "upgrade"  # clave de cooldown: «<prefijo>:<menú>:<nombre>»
    capture_prefix = "upgrade"  # nombre de las capturas guardadas
    priority = 100

    def _reserve(self, ctx: Context) -> int:
        """Constructores que se dejan libres. Sin muros que mejorar, la reserva para ellos no tiene sentido."""
        return 0 if ctx.walls_complete else self.config.settings.reserve_builders

    def should_run(self, ctx: Context, view: SessionView) -> ShouldRun:
        """Para mejoras y edificios nuevos: procede si hay más constructores libres que la reserva."""
        village = view.village
        free = village.free_builders if "builders" not in village.unreliable else None
        if free is None:
            return ShouldRun.no("constructores libres desconocidos")
        reserve = self._reserve(ctx)
        if free <= reserve:
            return ShouldRun.no(f"{free} constructores libres, reserva {reserve}")
        return ShouldRun.yes(f"{free} constructores libres (reserva {reserve})")

    def _setup_panel(
        self,
        config,
        classifier: ScreenClassifier | None,
        scanner: PanelScanner | None,
        village: VillageReader | None,
    ) -> None:
        self.config = config
        self.classifier = classifier or ScreenClassifier()
        menu = config.menu
        reader = PanelReader(PANEL_PROFILES[menu.panel], **({"categories": menu.categories} if menu.categories else {}))
        self.scanner = scanner or PanelScanner(reader)
        self.village = village or VillageReader()

    def _to_village(self, ctx: Context) -> TaskResult | None:
        """None si ya se está (o se vuelve a) la aldea; si no, el resultado de la recuperación."""
        result = RecoverScreen(self.classifier, self.config.recovery).run(ctx)
        return None if result.outcome in (Outcome.DONE, Outcome.NOTHING_TO_DO) else result

    def _close(self, ctx: Context) -> TaskResult | None:
        return self._to_village(ctx)

    def _open_and_scan(self, ctx: Context) -> list[PanelRow] | TaskResult:
        menu = self.config.menu
        frame = ctx.device.capture()
        button = menu.open_button.best(frame.image, self.classifier.finder)
        if button is None or button.confidence < menu.open_button.threshold:
            return TaskResult.needs_recovery(f"no se ve el botón «{menu.open_button.text}» para abrir el menú{ENGLISH_HINT}")
        ctx.device.tap(*button.center)

        def opened() -> bool:
            return self.classifier.classify(ctx.device.capture().image).screen is menu.screen

        if not ctx.wait_until(opened, self.config.open_timeout_s, 0.5):
            return TaskResult.needs_recovery(f"el menú «{menu.name}» no se abrió")
        self._after_open(ctx)
        self.last_scan = self.scanner.scan(ctx)  # la tarea puede mirar si el panel se recorrió entero
        ctx.progress()
        return list(self.last_scan.rows)

    def _after_open(self, ctx: Context) -> None:
        """Gancho: lo que una tarea quiera hacer con el panel recién abierto, antes de leer las filas."""

    def _key(self, name: str) -> str:
        return f"{self.cooldown_prefix}:{self.config.menu.name}:{name}"

    def _cooling(self, ctx: Context, rows: list[PanelRow]) -> frozenset[str]:
        return frozenset(r.name for r in rows if r.name and not ctx.cooldowns.ready(self._key(r.name)))

    def _set_aside(self, ctx: Context, name: str | None, image: np.ndarray) -> None:
        """Aparta la fila (cooldown) y guarda una captura de la pantalla que la hizo fallar."""
        if name:
            ctx.cooldowns.start(self._key(name), self.config.cooldown_s)
        path = self._save(ctx, image)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))

    def _save(self, ctx: Context, image: np.ndarray) -> Path | None:
        if self.config.capture_dir is None:
            return None
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.capture_dir / f"{self.capture_prefix}-{int(ctx.clock.now() * 1000)}.png"
        Image.fromarray(image).save(path)
        return path
