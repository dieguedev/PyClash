"""Muros por lotes (issue #13): abrir el panel, localizar la fila «Wall», armar el lote, confirmar y verificar.

La decisión (cuándo, con qué recurso, cuántos) es la política pura `policy/walls.py`; aquí solo está el
recorrido y su cadena de seguridad:

  1. nunca se gastan gemas ni se abre la tienda (un diálogo de gemas se cierra y la tarea se bloquea);
  2. el lote se arma con «+10» / «+1» solo cuando están ACTIVOS (etiqueta blanca, no roja) y tras cada
     pulsación el contador «x N» debe haber subido: si no, se aborta sin confirmar;
  3. el coste total del botón «Upgrade» no se lee con dígitos: se confirma solo si el contador vale lo
     calculado (`N × coste unitario` de la fila) y el coste NO sale en rojo (se quita un muro, hasta 3 veces);
  4. «Upgrade» abre el modal «Upgrade Walls»; solo se pulsa «Okay» si el título y el «Cancel» lo
     identifican (el importe va en otra fuente y no se lee: lo fija el juego a partir de N);
  5. se verifica en el panel (fuente de la verdad): misma fila con menos muros, o la fila ya no aparece
     cuando el lote cubría todo el nivel. Nunca se reintenta un lote «a ciegas»;
  6. en modo de prueba se publica lo que se haría y no se pulsa nada de selección ni de confirmación.

El juego debe estar EN INGLÉS (ver `UpgradeTask`). Paga con oro o con elixir: la barra de lote trae un botón «Upgrade» por recurso (moneda o gota) y se pulsa
el del recurso que decide la política. Si el juego no ofrece ese botón no se pulsa nada y no se reintenta en una hora.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from ..policy.walls import (
    WallSettings,
    WallState,
    batch_size,
    ELIXIR_MIN_LEVEL,
    decide_walls,
    gold_only_level,
    next_add,
    verified_in_panel,
    wall_gate,
)
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, WallBatchPlanned, WallBatchStep, Warning
from ..screens.classifier import ScreenClassifier
from ..screens.upgrade_dialog import GEMS, DialogReader
from ..screens.upgrade_panel import PanelRow, PanelScanner
from ..screens.village import VillageReader
from ..screens.wall_batch import BatchBar, BatchBarReader, UpgradeButton
from .base import Outcome, SessionView, ShouldRun, TaskResult
from .panel import ENGLISH_HINT, PanelTask
from .recovery import RecoveryConfig
from .upgrade import VILLAGE_MENU, MenuDefinition

WALL = "Wall"
_NO_BUTTON = UpgradeButton((0, 0), None, None)  # marcador: «no hay botón» (affordable None)


@dataclass(frozen=True)
class WallInputs:
    """Lo que la tarea no puede saber sola: lo da quien la lanza (el orquestador)."""

    gold_floor: int | None = None  # colchón de oro (`gold_floor`), None: desconocido, no se gasta oro
    upgrades_pending: bool | None = None  # ¿quedan mejoras de aldea por hacer?


@dataclass(frozen=True)
class WallConfig:
    settings: WallSettings = field(default_factory=WallSettings)
    menu: MenuDefinition = VILLAGE_MENU
    dry_run: bool = False
    settle_s: float = 0.8
    open_timeout_s: float = 6.0
    bar_polls: int = 6  # intentos de ver la barra de lote tras «Upgrade More»
    max_red_removals: int = 3  # «−1» como máximo si el coste sale en rojo
    verify_attempts: int = 2  # lecturas del panel tras confirmar (se relee una vez)
    cooldown_s: float = 0.0  # sin cooldown: una fila dudosa se reintenta en la siguiente ventana
    unavailable_cooldown_s: float = 3600.0  # si el juego no ofrece pagar con un recurso, no se vuelve a intentar en una hora
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


@dataclass(frozen=True)
class _Batch:
    ok: bool
    count: int = 0
    reason: str = ""
    blocked: bool = False


class WallBatchTask(PanelTask):
    name = "upgrade_walls"
    priority = 30
    cooldown_prefix = "walls"
    capture_prefix = "walls"

    def __init__(
        self,
        config: WallConfig | None = None,
        inputs: Callable[[Context], WallInputs] | None = None,
        classifier: ScreenClassifier | None = None,
        scanner: PanelScanner | None = None,
        bars: BatchBarReader | None = None,
        dialogs: DialogReader | None = None,
        village: VillageReader | None = None,
    ) -> None:
        self._setup_panel(config or WallConfig(), classifier, scanner, village)
        self.bars = bars or BatchBarReader()
        self.dialogs = dialogs or DialogReader()
        self.inputs = inputs or (lambda ctx: WallInputs())

    def should_run(self, ctx: Context, view: SessionView) -> ShouldRun:
        """Procede con la puerta de constructores de la política: los muros usan el último constructor libre."""
        village = view.village
        free = village.free_builders if "builders" not in village.unreliable else None
        blocked = wall_gate(free, self.config.settings.reserve_builder_for_walls, None)
        return ShouldRun.no(blocked) if blocked else ShouldRun.yes(f"{free} constructores libres")

    # ------------------------------------------------------------------------------ recorrido

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        back = self._to_village(ctx)
        if back is not None:
            return back
        village = self.village.read_state_stable(ctx)
        free = village.free_builders if "builders" not in village.unreliable else None
        resources = village.resources if "resources" not in village.unreliable and village.resources else (None, None, None)
        given = self.inputs(ctx)
        unavailable = frozenset(r for r in ("gold", "elixir") if not ctx.cooldowns.ready(self._no_button_key(r)))
        settings = replace(cfg.settings, enabled=cfg.settings.enabled - unavailable)
        state = WallState(resources, free, given.gold_floor, given.upgrades_pending)
        decision = decide_walls(state, settings)
        if decision.resource is None:
            return TaskResult.nothing_to_do(decision.reason)

        rows = self._open_and_scan(ctx)
        if isinstance(rows, TaskResult):
            return rows
        row = self._wall_row(rows)
        if row is None:
            self._close(ctx)
            # Con el panel recorrido entero, que no haya fila «Wall» es que no queda ningún muro por mejorar.
            done = bool(self.last_scan.reached_end)
            return TaskResult.nothing_to_do(f"no hay fila «{WALL}» en el panel", exhausted=done)
        if decision.resource == "elixir" and gold_only_level(row.level):
            # Los muros que suben al nivel 2, 3 o 4 solo se pagan con oro: se decide de nuevo sin elixir.
            decision = decide_walls(state, replace(settings, enabled=settings.enabled - {"elixir"}))
            if decision.resource is None:
                self._close(ctx)
                rest = "; ".join(r for r in decision.skipped if not r.startswith("elixir:")) or decision.reason
                return TaskResult.nothing_to_do(
                    f"los muros suben al nivel {row.level} y el elixir sirve desde el {ELIXIR_MIN_LEVEL}: {rest}"
                )
        # El coste unitario de la fila vale para los dos recursos (en las capturas, 100 000 en oro y en elixir).
        if row.resource not in ("gold", "elixir") or not row.cost:
            self._close(ctx)
            return TaskResult.nothing_to_do(f"la fila de muros no tiene recurso o coste legibles ({row.resource}, {row.cost})")
        target = batch_size(decision.spendable, row.cost, row.quantity)
        if target < 1:
            self._close(ctx)
            return TaskResult.nothing_to_do(f"con {decision.spendable} gastable no cabe un muro de {row.cost}")
        ctx.events.publish(WallBatchPlanned(decision.resource, target, row.cost, decision.reason))
        if cfg.dry_run:
            back = self._close(ctx)
            return back or TaskResult(Outcome.DONE, f"dry run: {target} muros con {decision.resource}")

        batch = self._build_and_confirm(ctx, row, target, decision.resource)
        if batch.blocked:
            return TaskResult.blocked(batch.reason)
        if not batch.ok:
            ctx.events.publish(Warning(f"muros: {batch.reason}"))
            return TaskResult.nothing_to_do(batch.reason)

        if self._verified(ctx, row, batch.count):
            return TaskResult(Outcome.DONE, f"{batch.count} muros mejorados con {decision.resource}")
        ctx.events.publish(Warning("muros: no se pudo verificar el lote en el panel"))
        return TaskResult.nothing_to_do("no se pudo verificar el lote en el panel")

    @staticmethod
    def _no_button_key(resource: str) -> str:
        return f"walls:no_button:{resource}"

    @staticmethod
    def _wall_row(rows: list[PanelRow]) -> PanelRow | None:
        # Solo las mejoras: la fila «Wall ×N · gratis» de edificios nuevos (muros por colocar) no es un lote de mejora.
        return next((r for r in rows if r.name == WALL and r.kind == "wall" and not r.new and not r.free), None)

    @staticmethod
    def _same_wall_row(rows: list[PanelRow], before: PanelRow) -> PanelRow | None:
        """La fila de muros de ANTES, tras el lote. Hay una fila por nivel de muro (`Wall ×100 · 25 000`, `Wall ×25 · 500`…) y
        el coste unitario las distingue: si la del lote se agotó, no se debe tomar la siguiente fila «Wall» por ella."""
        return next(
            (r for r in rows if r.name == WALL and r.kind == "wall" and not r.new and not r.free and r.cost == before.cost),
            None,
        )

    # ------------------------------------------------------------------------------ armar y confirmar

    def _build_and_confirm(self, ctx: Context, row: PanelRow, target: int, resource: str) -> _Batch:
        ctx.check_stop()
        seen = self._reveal(ctx, row)
        if seen is None:
            return self._abort(ctx, "la fila de muros ya no se ve en el panel")
        ctx.device.tap(*seen.center)
        ctx.events.publish(WallBatchStep("select_row"))
        ctx.sleep(self.config.settle_s)
        image = ctx.device.capture().image
        point = self.bars.upgrade_more(image)
        if point is None:
            return self._abort(ctx, f"la fila no abrió la barra del muro{ENGLISH_HINT}")
        ctx.device.tap(*point)
        ctx.events.publish(WallBatchStep("upgrade_more"))

        bar = self._wait_bar(ctx)
        if bar is None or bar.count is None:
            return self._abort(ctx, "no se ve la barra de lote o su contador")
        if bar.upgrade_for(resource) is None:  # antes de armar nada: ¿ofrece el juego pagar con este recurso?
            ctx.cooldowns.start(self._no_button_key(resource), self.config.unavailable_cooldown_s)
            return self._abort(ctx, f"el juego no ofrece un botón «Upgrade» de {resource} para estos muros")
        count = bar.count
        if count > target:
            return self._abort(ctx, f"el lote ya vale {count}, más que lo calculado ({target})")

        clicks = 0
        while count < target:
            clicks += 1
            if clicks > target + 5:
                return self._abort(ctx, "demasiadas pulsaciones para armar el lote")
            step = next_add(count, target)
            if step == 10 and bar.add10 is not None and bar.add10.active:
                button, gain = bar.add10, 10
            elif bar.add1 is not None and bar.add1.active:
                button, gain = bar.add1, 1
            else:
                break  # los botones de añadir están apagados: no quedan más muros de ese nivel
            ctx.check_stop()
            ctx.device.tap(*button.center)
            ctx.events.publish(WallBatchStep(f"add{gain}", count))
            ctx.sleep(self.config.settle_s)
            after = self._read_bar(ctx)
            if after is None or after.count is None:
                return self._abort(ctx, "no se pudo leer el contador tras pulsar «Add Wall»")
            if after.count <= count or after.count > count + gain:
                return self._abort(ctx, f"el contador pasó de {count} a {after.count} tras pulsar +{gain}")
            count, bar = after.count, after

        removals = 0
        while (bar.upgrade_for(resource) or _NO_BUTTON).affordable is False:
            if removals >= self.config.max_red_removals or bar.remove is None or not bar.remove.active or count <= 1:
                return self._abort(ctx, "el coste del lote sale en rojo")
            removals += 1
            ctx.check_stop()
            ctx.device.tap(*bar.remove.center)
            ctx.events.publish(WallBatchStep("remove1", count, "coste en rojo"))
            ctx.sleep(self.config.settle_s)
            after = self._read_bar(ctx)
            if after is None or after.count is None or after.count != count - 1:
                return self._abort(ctx, "no se pudo reducir el lote en rojo")
            count, bar = after.count, after
        if (bar.upgrade_for(resource) or _NO_BUTTON).affordable is not True:
            return self._abort(ctx, "no se pudo comprobar que el coste del lote no esté en rojo")

        # Última comprobación con la barra recién leída: el contador debe valer lo que se va a gastar.
        last = self._read_bar(ctx)
        pay = last.upgrade_for(resource) if last is not None else None
        if last is None or last.count != count or pay is None or pay.affordable is not True:
            return self._abort(ctx, "el contador, el botón o el coste cambiaron justo antes de confirmar")
        ctx.check_stop()
        ctx.device.tap(*pay.center)
        ctx.events.publish(WallBatchStep("upgrade", count, resource))
        ctx.sleep(self.config.settle_s)

        frame = ctx.device.capture()
        if self.dialogs.missing(frame.image) == GEMS:
            self._close(ctx)
            return _Batch(False, reason="el juego ofrece pagar con gemas: no se gastan nunca", blocked=True)
        modal = self.bars.modal(frame.image)
        if modal is None:
            return self._abort(ctx, "tras «Upgrade» no salió el modal «Upgrade Walls»", frame.image)
        ctx.check_stop()
        ctx.device.tap(*modal.okay)
        ctx.events.publish(WallBatchStep("okay", count))
        ctx.sleep(self.config.settle_s)
        return _Batch(True, count)

    def _wait_bar(self, ctx: Context) -> BatchBar | None:
        for poll in range(self.config.bar_polls):
            ctx.check_stop()
            ctx.sleep(self.config.settle_s)
            bar = self.bars.read(ctx.device.capture().image)
            if bar is not None:
                return bar
        return None

    def _read_bar(self, ctx: Context) -> BatchBar | None:
        """Lee la barra; si falla se relee una vez (el juego redibuja unos instantes tras cada pulsación)."""
        for attempt in range(2):
            bar = self.bars.read(ctx.device.capture().image)
            if bar is not None and bar.count is not None:
                return bar
            if attempt == 0:
                ctx.sleep(self.config.settle_s)
        return None

    def _abort(self, ctx: Context, reason: str, image=None) -> _Batch:
        """Sale sin confirmar: guarda una captura y vuelve a la aldea."""
        frame = image if image is not None else ctx.device.capture().image
        path = self._save(ctx, frame)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))
        ctx.events.publish(WallBatchStep("abort", None, reason))
        self._close(ctx)
        return _Batch(False, reason=reason)

    # ------------------------------------------------------------------------------ verificar

    def _verified(self, ctx: Context, before: PanelRow, count: int) -> bool:
        """Reabre el panel (fuente de la verdad) y comprueba que el lote se aplicó. Se relee una vez."""
        back = self._close(ctx)
        if back is not None:
            return False
        for attempt in range(self.config.verify_attempts):
            ctx.check_stop()
            rows = self._open_and_scan(ctx)
            if isinstance(rows, TaskResult):
                return False
            after = self._same_wall_row(rows, before)
            complete = bool(self.last_scan.reached_end)
            verified = verified_in_panel(
                before.quantity, before.cost, count,
                after.quantity if after else None, after.cost if after else None,
                after is not None, complete,
            )
            self._close(ctx)
            if verified:
                return True
            ctx.events.publish(WallBatchStep("verify_failed", count, f"intento {attempt + 1}"))
        return False


__all__ = ["WALL", "WallBatchTask", "WallConfig", "WallInputs"]
