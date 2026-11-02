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

from dataclasses import dataclass, field, replace
from pathlib import Path

from ..device.base import Frame
from ..policy.upgrades import (
    MAX_TOWN_HALL,
    TOWN_HALL,
    Decision,
    Mode,
    Order,
    UpgradeSettings,
    UpgradeState,
    builder_gate,
    decide_upgrade,
)
from ..runtime.context import Context
from ..runtime.events import AccountFinished, ImprovementStarted, UpgradeDecided, UpgradePlanned, Warning
from ..screens.classifier import Screen, ScreenClassifier, TextIndicator
from ..screens.signatures import box
from ..screens.town_hall import offers_town_hall_10, read_town_hall_level
from ..screens.upgrade_dialog import GEMS, DialogReader, usable_confirm
from ..screens.upgrade_panel import PanelRow, PanelScanner, apply_sort
from ..screens.village import VillageReader
from .base import Outcome, TaskResult
from .panel import ENGLISH_HINT, PanelTask
from .recovery import RecoveryConfig

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
    # Orden SOLO con el filtro del juego (Cost Max / Cost Min): si no se aplica o no se verifica, la tarea no hace
    # nada. False solo para pruebas y simulacros sin juego: entonces ordena el bot.
    game_sort: bool = True
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


@dataclass(frozen=True)
class _Attempt:
    kind: str  # "started" | "skipped" | "blocked" | "failed"
    reason: str = ""
    rebuild: bool = False


class UpgradeTask(PanelTask):
    name = "upgrade_village"
    priority = 20

    def __init__(
        self,
        config: UpgradeConfig | None = None,
        classifier: ScreenClassifier | None = None,
        scanner: PanelScanner | None = None,
        dialogs: DialogReader | None = None,
        village: VillageReader | None = None,
    ) -> None:
        self._setup_panel(config or UpgradeConfig(), classifier, scanner, village)
        self.dialogs = dialogs or DialogReader()
        self._sort_applied = False

    # ------------------------------------------------------------------------------ recorrido

    def _live_settings(self, ctx: Context) -> UpgradeSettings:
        """Ajustes de la tarea con lo que el usuario cambió en vivo y la reserva ya relajada."""
        base = self.config.settings
        return replace(base, reserve_builders=self._reserve(ctx), excluded=base.excluded | ctx.settings.excluded)

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        settings = self._live_settings(ctx)
        back = self._to_village(ctx)
        if back is not None:
            return back
        started = attempts = 0
        last_reason = ""
        # El HUD solo se dibuja con la aldea a la vista: el nivel del Ayuntamiento se lee antes de abrir el panel.
        th_level = read_town_hall_level(ctx.device.capture().image)
        while attempts < cfg.max_attempts:
            ctx.check_stop()
            village = self.village.read_state_stable(ctx)
            free = village.free_builders if "builders" not in village.unreliable else None
            resources = village.resources if "resources" not in village.unreliable else None
            base = UpgradeState((), resources or (None, None, None), free, started)
            gate = builder_gate(base, settings)
            if gate:
                last_reason = gate
                break

            rows = self._open_and_scan(ctx)
            if isinstance(rows, TaskResult):
                return rows
            if cfg.game_sort and not (self._sort_applied and self.last_scan.order_ok is True):
                why = self.last_scan.order_reason if self._sort_applied else "no se pudo aplicar"
                reason = f"el filtro del juego no se aplicó o no se verificó ({why}): no se hace nada"
                ctx.events.publish(Warning(reason))
                self._close(ctx)
                return TaskResult.blocked(reason)
            offers = offers_town_hall_10(ctx.device.capture().image)
            scan_complete = self.last_scan.reached_end
            if not scan_complete and th_level == MAX_TOWN_HALL and offers is True and rows and all(r.name == TOWN_HALL for r in rows):
                # Con el filtro del juego solo se lee la primera pantalla. Si parece TH9 maxeado, antes de darlo por
                # terminado se recorre el panel entero: no puede quedar nada más por debajo.
                full = self.scanner.scan(ctx, from_top=True)
                rows, scan_complete = list(full.rows), full.reached_end
            state = UpgradeState(
                tuple(rows), resources or (None, None, None), free, started, self._cooling(ctx, rows),
                game_ordered=cfg.game_sort,  # el orden del panel ES el del juego: el bot no reordena
                th_level=th_level,
                offers_th10=offers,
                scan_complete=scan_complete,
            )
            decision = decide_upgrade(state, settings, ctx.rng)
            last_reason = decision.reason
            if settings.is_dry:
                ctx.events.publish(
                    UpgradeDecided(
                        decision.chosen.name if decision.chosen else None,
                        decision.reason,
                        tuple((j.row.name or "?", j.reason.value) for j in decision.rejected),
                        state.th_level,
                        state.offers_th10,
                    )
                )
            if decision.finished:
                ctx.events.publish(AccountFinished(decision.reason))
                back = self._close(ctx)
                return back or TaskResult.nothing_to_do(decision.reason, exhausted=True)
            if decision.chosen is None:
                self._close(ctx)
                break
            if settings.is_dry:
                ctx.events.publish(UpgradePlanned(decision.chosen.name or "?", decision.reason))
                back = self._close(ctx)
                return back or TaskResult(Outcome.DONE, f"dry run: {decision.reason}")

            attempts += 1
            ctx.progress()
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

    def _order(self) -> str:
        return "cost_min" if self.config.settings.order is Order.CHEAPEST else "cost_max"

    def _scan_options(self) -> dict:
        # Con el filtro del juego aplicado la mejor fila es la primera: se lee la primera pantalla y no se desplaza.
        return {"expect_order": self._order(), "max_steps": 0} if self._sort_applied else {}

    def _after_open(self, ctx: Context) -> None:
        self._sort_applied = False
        if not self.config.game_sort:
            return
        self._sort_applied = apply_sort(ctx, self._order())

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
        seen = self._reveal(ctx, row)
        if seen is None:
            return self._abort(ctx, row, "la fila ya no se ve en el panel", ctx.device.capture())
        if row.name == TOWN_HALL and not (seen.level is not None and 2 <= seen.level <= MAX_TOWN_HALL):
            # La fila localizada ahora debe llevar un número reconocido (2-9). Si no se lee, es duda: no se pulsa.
            return self._abort(
                ctx, row, "no se reconoce el número de la fila del Ayuntamiento: no se sube", ctx.device.capture()
            )
        ctx.device.tap(*seen.center)
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
        self._set_aside(ctx, row.name, frame.image)

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
