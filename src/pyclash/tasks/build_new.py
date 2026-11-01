"""Construir edificios nuevos (issue #10): abrir panel -> leer filas -> elegir una fila «New» -> fase de
colocación (✓/✗) -> verificar releyendo la lista.

Hermana de `UpgradeTask` (comparte la base `PanelTask`) pero con su propia cadena de seguridad:

  1. R3: sin constructor libre por encima de la reserva ni siquiera se abre el panel;
  2. R2: nunca gemas, muro gratis del tutorial, precio en rojo o indeterminado (lo descarta la política);
  3. la identidad del edificio la da la fila (etiqueta «New» leída antes de pulsar): la pantalla de
     colocación no nombra nada ni muestra coste, así que solo se pulsa el ✓ si está VERDE;
  4. ✓ en gris (sin sitio) o desconocido: se cancela con ✗, que no gasta nada. En gris se reintenta la
     misma fila (el juego recoloca el fantasma en otro hueco) hasta `placement_retries`; luego, cooldown;
  5. cadenas (Bomb ×2): tras cada ✓ se recaptura y se repiten las comprobaciones, con un tope de
     unidades por fila (cantidad, recurso/coste y `max_placements`); el fantasma sobrante se cancela;
  6. se verifica con la aldea cerrada reabriendo el panel: la fila New desaparece o baja su cantidad.
     El contador de constructores solo informa (los edificios sin constructor no lo mueven);
  7. lo no verificable se aparta (cooldown) y se guarda una captura. Dry run: no se pulsa ninguna fila.

El juego debe estar EN INGLÉS (ver `UpgradeTask`). Los muros de pago y la «Builder's Hut» (gemas) no
entran: son otro flujo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..device.base import Frame
from ..policy.build_new import BuildDecision, BuildSettings, BuildState, build_gate, decide_build_new
from ..policy.upgrades import Order
from ..runtime.context import Context
from ..runtime.events import ImprovementStarted, UpgradePlanned, Warning
from ..screens.classifier import ScreenClassifier
from ..screens.placement import Placement, read_placement
from ..screens.upgrade_dialog import GEMS, DialogReader
from ..screens.upgrade_panel import PanelRow, PanelScanner, apply_sort
from ..screens.village import VillageReader
from .base import Outcome, TaskResult
from .panel import ENGLISH_HINT, PanelTask
from .recovery import RecoveryConfig
from .upgrade import VILLAGE_MENU, MenuDefinition


@dataclass(frozen=True)
class BuildNewConfig:
    settings: BuildSettings = field(default_factory=BuildSettings)
    menu: MenuDefinition = VILLAGE_MENU
    cooldown_s: float = 1800.0  # lo que descansa una fila que falló o no se pudo verificar
    settle_s: float = 1.0
    open_timeout_s: float = 6.0
    placement_timeout_s: float = 4.0  # espera a que aparezca la X/✓ tras pulsar la fila
    placement_retries: int = 3  # veces que se reintenta una fila cuyo ✓ sale en gris
    max_attempts: int = 8  # filas que se intentan en una ejecución
    game_sort: bool = True  # ordenar con el filtro del juego (Cost Max / Cost Min) al abrir el panel
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


@dataclass(frozen=True)
class _Attempt:
    kind: str  # "built" | "retry" | "skipped" | "blocked" | "failed"
    reason: str = ""
    units: int = 0


class BuildNewTask(PanelTask):
    name = "build_new"
    priority = 10
    cooldown_prefix = "build"
    capture_prefix = "build"

    def __init__(
        self,
        config: BuildNewConfig | None = None,
        classifier: ScreenClassifier | None = None,
        scanner: PanelScanner | None = None,
        dialogs: DialogReader | None = None,
        village: VillageReader | None = None,
    ) -> None:
        self._setup_panel(config or BuildNewConfig(), classifier, scanner, village)
        self.dialogs = dialogs or DialogReader()
        self._retries: dict[str, int] = {}
        self._sort_applied = False

    # ------------------------------------------------------------------------------ recorrido

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        settings = cfg.settings
        self._retries = {}
        back = self._to_village(ctx)
        if back is not None:
            return back
        built = attempts = 0
        last_reason = ""
        while attempts < cfg.max_attempts:
            ctx.check_stop()
            village = self.village.read_state_stable(ctx)
            free = village.free_builders if "builders" not in village.unreliable else None
            resources = village.resources if "resources" not in village.unreliable else None
            gate = build_gate(free, self._reserve(ctx))
            if gate:  # R3: ni se abre el panel
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
            state = BuildState(
                tuple(rows), resources or (None, None, None), free, self._cooling(ctx, rows), game_ordered=cfg.game_sort
            )
            decision = decide_build_new(state, settings)
            last_reason = decision.reason
            if decision.chosen is None:
                self._close(ctx)
                break
            if settings.dry_run:
                ctx.events.publish(UpgradePlanned(decision.chosen.name or "?", decision.reason))
                back = self._close(ctx)
                return back or TaskResult(Outcome.DONE, f"dry run: {decision.reason}")

            attempts += 1
            ctx.progress()
            result = self._attempt(ctx, decision, free)
            if result.kind == "blocked":
                return TaskResult.blocked(result.reason)
            if result.kind == "failed":
                return TaskResult.failed(result.reason)
            if result.kind == "built":
                built += result.units
                for _ in range(result.units):
                    ctx.events.publish(ImprovementStarted(decision.chosen.name or "?"))
            else:
                ctx.events.publish(Warning(f"{decision.chosen.name}: {result.reason}"))
                last_reason = result.reason
        if built:
            return TaskResult(Outcome.DONE, f"{built} edificio(s) nuevo(s) colocado(s)")
        return TaskResult.nothing_to_do(last_reason)

    # ------------------------------------------------------------------------------ pasos

    def _after_open(self, ctx: Context) -> None:
        self._sort_applied = False
        if self.config.game_sort:
            self._sort_applied = apply_sort(ctx, self._order())

    def _order(self) -> str:
        return "cost_min" if self.config.settings.order is Order.CHEAPEST else "cost_max"

    def _scan_options(self) -> dict:
        return {"expect_order": self._order()} if self._sort_applied else {}

    def _attempt(self, ctx: Context, decision: BuildDecision, free_before: int | None) -> _Attempt:
        row = decision.chosen
        assert row is not None and row.name is not None
        ctx.check_stop()
        seen = self._reveal(ctx, row)
        if seen is None:
            return self._abort(ctx, row, "la fila ya no se ve en el panel", ctx.device.capture())
        ctx.device.tap(*seen.center)
        placement, frame = self._await_placement(ctx)
        missing = self.dialogs.missing(frame.image)  # antes que nada: un diálogo también trae una «X» roja
        if missing is not None:
            if missing == GEMS:
                self._close(ctx)
                return _Attempt("blocked", "el juego ofrece pagar con gemas: no se gastan nunca")
            return self._abort(ctx, row, "faltan recursos que no se habían visto en rojo", frame)
        if placement is None:
            return self._abort(ctx, row, f"tras pulsar la fila no apareció la colocación (✓/✗){ENGLISH_HINT}", frame)

        pressed = 0
        while True:
            if placement.state != "ready":
                frame = ctx.device.capture()
                ctx.device.tap(*placement.cancel.center)  # ✗: no gasta nada
                if pressed:
                    break  # el siguiente fantasma no tiene sitio: se queda lo ya colocado
                if placement.state == "blocked":
                    return self._retry(ctx, row, frame)
                return self._abort(ctx, row, "el ✓ no está claramente verde ni gris", frame)
            if pressed >= decision.placements:
                ctx.device.tap(*placement.cancel.center)  # fantasma sobrante: se cancela, no se compra
                break
            ctx.check_stop()
            ctx.device.tap(*placement.confirm.center)
            pressed += 1
            ctx.sleep(self.config.settle_s)
            placement = read_placement(ctx.device.capture().image)
            if placement is None:
                break  # volvió a la aldea: no hay más fantasmas

        ctx.sleep(self.config.settle_s)
        back = self._close(ctx)
        if back is not None:
            return _Attempt("failed", back.reason)
        return self._verify(ctx, row, free_before)

    def _await_placement(self, ctx: Context) -> tuple[Placement | None, Frame]:
        """Espera a que la X/✓ se vea y el ✓ deje de estar en transición (o a que salga un diálogo de recursos).
        Devuelve la lectura y el fotograma en que se hizo; la lectura es None si nunca aparece."""

        def settled() -> bool:
            image = ctx.device.capture().image
            found = read_placement(image)
            return (found is not None and found.state != "unknown") or self.dialogs.missing(image) is not None

        ctx.sleep(self.config.settle_s)
        ctx.wait_until(settled, self.config.placement_timeout_s, 0.5)
        frame = ctx.device.capture()
        return read_placement(frame.image), frame

    def _retry(self, ctx: Context, row: PanelRow, frame: Frame) -> _Attempt:
        """✓ en gris (sin sitio): ya se canceló con ✗. El juego recoloca el foco en otro hueco al volver a elegir."""
        name = row.name or ""
        self._retries[name] = self._retries.get(name, 0) + 1
        back = self._close(ctx)
        if back is not None:
            return _Attempt("failed", back.reason)
        if self._retries[name] >= self.config.placement_retries:
            self._set_aside(ctx, name, frame.image)
            return _Attempt("skipped", f"sin sitio tras {self._retries[name]} intentos")
        return _Attempt("retry", f"sin sitio (intento {self._retries[name]}/{self.config.placement_retries})")

    def _abort(self, ctx: Context, row: PanelRow, reason: str, frame: Frame) -> _Attempt:
        self._set_aside(ctx, row.name, frame.image)
        back = self._close(ctx)
        if back is not None:
            return _Attempt("failed", back.reason)
        return _Attempt("skipped", reason)

    def _verify(self, ctx: Context, row: PanelRow, free_before: int | None) -> _Attempt:
        """Con la aldea a la vista: informa del contador y relee la lista (la fuente de verdad)."""
        village = self.village.read_state_stable(ctx)
        if (
            row.duration is not None
            and free_before is not None
            and "builders" not in village.unreliable
            and village.free_builders is not None
            and village.free_builders >= free_before
        ):
            ctx.events.publish(Warning(f"{row.name}: el contador de constructores libres no bajó"))
        rows = self._open_and_scan(ctx)
        if isinstance(rows, TaskResult):
            return _Attempt("failed", rows.reason)
        frame = ctx.device.capture()
        back = self._close(ctx)
        if back is not None:
            return _Attempt("failed", back.reason)
        units = _units_built(row, rows)
        if units > 0:
            return _Attempt("built", units=units)
        self._set_aside(ctx, row.name, frame.image)
        return _Attempt("skipped", "no se pudo verificar que el edificio se colocara")


def _units_built(row: PanelRow, rows_after: list[PanelRow]) -> int:
    """Cuántas unidades menos de la fila New hay ahora: desaparecida = toda la cantidad; si no, la diferencia."""
    if not rows_after:
        return 0  # una lectura vacía no demuestra que la fila desapareciera
    same = [r for r in rows_after if r.name == row.name and r.kind in ("new_building", "wall") and r.new]
    if not same:
        return row.quantity
    return max(0, row.quantity - min(r.quantity for r in same))
