"""Flujo genérico de mejora con cadena de seguridad: abrir menú -> leer filas -> decidir -> pulsar la
fila -> (menú del muro) -> confirmar -> verificar.

Un solo recorrido parametrizado por `MenuDefinition`; no hay una función por menú. La cadena de
seguridad vive aquí y en ningún otro sitio:

  1. nunca se confirma un coste en rojo ni un recurso indeterminado (lo descarta la política y,
     además, el botón de confirmar debe llevar un icono de oro, elixir o elixir oscuro y un coste
     leído igual al de la fila: un botón con gemas, sin icono o con otro coste no se pulsa);
  2. nunca se gastan gemas, ni se va a la tienda: un diálogo de gemas se cierra y la tarea se bloquea;
  3. el título del diálogo debe nombrar la fila elegida («Upgrade X to Level N?»);
  4. se verifica el efecto con la aldea cerrada (bajó el contador de constructores libres);
  5. lo no verificable se aparta (cooldown) y se guarda una captura;
  6. a lo sumo dos pulsaciones tras la fila (menú del muro -> diálogo), nunca una tercera a ciegas;
  7. en Dry run se registra lo que se haría y no se emite ningún toque de selección ni de confirmación.

El juego debe estar EN INGLÉS: los títulos de los diálogos, los nombres y los botones se leen con la
fuente y el vocabulario en inglés. En otro idioma nada coincide y el flujo se detiene sin pulsar
(mensajes con `ENGLISH_HINT`); soportar otros idiomas sería otra issue.

Excepción de flujo: «Rebuild Clan Castle» no usa constructor ni tiene tiempo, así que se verifica por
la bajada del elixir en lugar del contador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from ..device.base import Frame
from ..policy.upgrades import Decision, Mode, UpgradeSettings, UpgradeState, builder_gate, decide_upgrade
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, ImprovementStarted, UpgradePlanned, Warning
from ..screens.classifier import Screen, ScreenClassifier, TextIndicator
from ..screens.signatures import box
from ..screens.upgrade_dialog import GEMS, DialogReader, usable_confirm
from ..screens.panel_layout import PANEL_PROFILES
from ..screens.upgrade_panel import PanelReader, PanelRow, PanelScanner
from ..screens.village import VillageReader
from .base import Outcome, TaskResult
from .recovery import RecoverScreen, RecoveryConfig

ENGLISH_HINT = " (el bot exige el juego en inglés)"
_RESOURCE_INDEX = {"gold": 0, "elixir": 1, "dark_elixir": 2}


@dataclass(frozen=True)
class MenuDefinition:
    """Lo único que cambia entre menús: cómo se abre, qué variante de panel y qué vocabulario."""

    name: str
    open_button: TextIndicator
    panel: str = "village"  # clave de PANEL_PROFILES
    categories: tuple[str, ...] | None = None  # categorías del vocabulario de los nombres de fila
    screen: Screen = Screen.UPGRADE_PANEL  # pantalla que debe verse tras abrir


VILLAGE_MENU = MenuDefinition(
    name="village",
    open_button=TextIndicator("Build", box(0.90, 0.93, 1.0, 1.0), 28, threshold=0.7),
    categories=("heroes", "resource_buildings", "army_buildings", "defenses", "traps", "other_buildings"),
)


@dataclass(frozen=True)
class UpgradeConfig:
    settings: UpgradeSettings = field(default_factory=UpgradeSettings)
    menu: MenuDefinition = VILLAGE_MENU
    cooldown_s: float = 1800.0  # lo que descansa una fila que falló o no se pudo verificar
    settle_s: float = 1.0
    open_timeout_s: float = 6.0
    verify_polls: int = 4
    verify_interval_s: float = 1.5
    max_attempts: int = 6  # filas que se intentan en una ejecución
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


@dataclass(frozen=True)
class _Attempt:
    kind: str  # "started" | "skipped" | "blocked" | "failed"
    reason: str = ""
    rebuild: bool = False


class UpgradeTask:
    name = "upgrade_village"

    def __init__(
        self,
        config: UpgradeConfig | None = None,
        classifier: ScreenClassifier | None = None,
        scanner: PanelScanner | None = None,
        dialogs: DialogReader | None = None,
        village: VillageReader | None = None,
    ) -> None:
        self.config = config or UpgradeConfig()
        self.classifier = classifier or ScreenClassifier()
        menu = self.config.menu
        reader = PanelReader(PANEL_PROFILES[menu.panel], **({"categories": menu.categories} if menu.categories else {}))
        self.scanner = scanner or PanelScanner(reader)
        self.dialogs = dialogs or DialogReader()
        self.village = village or VillageReader()

    # ------------------------------------------------------------------------------ recorrido

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        back = self._to_village(ctx)
        if back is not None:
            return back
        started = attempts = 0
        last_reason = ""
        while attempts < cfg.max_attempts:
            ctx.check_stop()
            village = self.village.read_state_stable(ctx)
            free = village.free_builders if "builders" not in village.unreliable else None
            resources = village.resources if "resources" not in village.unreliable else None
            base = UpgradeState((), resources or (None, None, None), free, started)
            gate = builder_gate(base, cfg.settings)
            if gate:
                last_reason = gate
                break

            rows = self._open_and_scan(ctx)
            if isinstance(rows, TaskResult):
                return rows
            state = UpgradeState(
                tuple(rows), resources or (None, None, None), free, started, self._cooling(ctx, rows)
            )
            decision = decide_upgrade(state, cfg.settings, ctx.rng)
            last_reason = decision.reason
            if decision.chosen is None:
                self._close(ctx)
                break
            if cfg.settings.mode is Mode.DRY_RUN:
                ctx.events.publish(UpgradePlanned(decision.chosen.name or "?", decision.reason))
                back = self._close(ctx)
                return back or TaskResult(Outcome.DONE, f"dry run: {decision.reason}")

            attempts += 1
            result = self._attempt(ctx, decision, free, resources)
            if result.kind == "blocked":
                return TaskResult.blocked(result.reason)
            if result.kind == "failed":
                return TaskResult.failed(result.reason)
            if result.kind == "started":
                started += 1
                ctx.events.publish(ImprovementStarted(decision.chosen.name or "?"))
            else:
                ctx.events.publish(Warning(f"{decision.chosen.name}: {result.reason}"))
                last_reason = result.reason
        if started:
            return TaskResult(Outcome.DONE, f"{started} mejora(s) iniciada(s)")
        return TaskResult.nothing_to_do(last_reason)

    # ------------------------------------------------------------------------------ pasos

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
        return list(self.scanner.scan(ctx).rows)

    def _cooling(self, ctx: Context, rows: list[PanelRow]) -> frozenset[str]:
        return frozenset(r.name for r in rows if r.name and not ctx.cooldowns.ready(self._key(r.name)))

    def _key(self, name: str) -> str:
        return f"upgrade:{self.config.menu.name}:{name}"

    def _attempt(
        self, ctx: Context, decision: Decision, free_before: int | None, resources: tuple | None
    ) -> _Attempt:
        row = decision.chosen
        assert row is not None and row.name is not None
        confirmed = self._select_and_confirm(ctx, row)
        if confirmed.kind != "confirmed":
            return confirmed
        ctx.sleep(self.config.settle_s)
        back = self._close(ctx)
        if back is not None:
            return _Attempt("failed", back.reason)
        if self._verified(ctx, row, confirmed.rebuild, free_before, resources):
            return _Attempt("started", rebuild=confirmed.rebuild)
        self._skip(ctx, row, "no se pudo verificar que la mejora empezara", ctx.device.capture())
        return _Attempt("skipped", "no se pudo verificar que la mejora empezara")

    def _select_and_confirm(self, ctx: Context, row: PanelRow) -> _Attempt:
        """Pulsa la fila y recorre la cadena de pantallas. `kind == "confirmed"`: se pulsó Confirmar."""
        ctx.check_stop()
        ctx.device.tap(*row.center)
        wall_menu_used = False
        for _ in range(2):  # fila -> (menú del muro) -> diálogo: nunca una tercera pulsación a ciegas
            ctx.sleep(self.config.settle_s)
            frame = ctx.device.capture()
            image = frame.image

            missing = self.dialogs.missing(image)
            if missing is not None:
                if missing == GEMS:
                    self._close(ctx)
                    return _Attempt("blocked", "el juego ofrece pagar con gemas: no se gastan nunca")
                return self._abort(ctx, row, "faltan recursos que no se habían visto en rojo", frame)

            title = self.dialogs.title(image, row.name or "")
            if title is not None:
                button = usable_confirm(self.dialogs.confirm_buttons(image))
                if button is None:
                    return self._abort(ctx, row, "el diálogo no tiene un botón con oro, elixir o elixir oscuro", frame)
                if row.cost is not None and button.cost != row.cost:
                    return self._abort(ctx, row, f"el botón cuesta {button.cost} y la fila {row.cost}", frame)
                if row.resource is not None and button.resource != row.resource:
                    return self._abort(ctx, row, f"el botón gasta {button.resource} y la fila {row.resource}", frame)
                ctx.check_stop()
                ctx.device.tap(*button.center)
                return _Attempt("confirmed", rebuild=title.kind == "rebuild")

            wall = None if wall_menu_used else self.dialogs.wall_upgrade(image)
            if row.kind == "wall" and wall is not None:
                wall_menu_used = True
                ctx.check_stop()
                ctx.device.tap(*wall.center)
                continue
            return self._abort(ctx, row, f"el diálogo no nombra «{row.name}» o no se reconoce{ENGLISH_HINT}", frame)
        return self._abort(ctx, row, "la cadena de pantallas no llegó a un diálogo reconocido", ctx.device.capture())

    def _abort(self, ctx: Context, row: PanelRow, reason: str, frame: Frame) -> _Attempt:
        """Sale de la pantalla (botón de cerrar), aparta la fila y guarda una captura."""
        self._skip(ctx, row, reason, frame)
        back = self._close(ctx)
        if back is not None:
            return _Attempt("failed", back.reason)
        return _Attempt("skipped", reason)

    def _skip(self, ctx: Context, row: PanelRow, reason: str, frame: Frame) -> None:
        if row.name:
            ctx.cooldowns.start(self._key(row.name), self.config.cooldown_s)
        path = self._save(ctx, frame.image)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))

    def _save(self, ctx: Context, image: np.ndarray) -> Path | None:
        if self.config.capture_dir is None:
            return None
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.capture_dir / f"upgrade-{int(ctx.clock.now() * 1000)}.png"
        Image.fromarray(image).save(path)
        return path

    def _verified(
        self, ctx: Context, row: PanelRow, rebuild: bool, free_before: int | None, resources: tuple | None
    ) -> bool:
        """Con la aldea a la vista: bajó el contador de libres (o, en un Rebuild, el elixir)."""
        for poll in range(self.config.verify_polls):
            ctx.check_stop()
            state = self.village.read_state_stable(ctx)
            if rebuild:
                index = _RESOURCE_INDEX.get(row.resource or "")
                if index is not None and resources and state.resources and row.cost:
                    before, after = resources[index], state.resources[index]
                    if before is not None and "resources" not in state.unreliable and before - after >= row.cost * 0.5:
                        return True
            elif free_before is not None and "builders" not in state.unreliable:
                if state.free_builders is not None and state.free_builders < free_before:
                    return True
            if poll < self.config.verify_polls - 1:
                ctx.sleep(self.config.verify_interval_s)
        return False
