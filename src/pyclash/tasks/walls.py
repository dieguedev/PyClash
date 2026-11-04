"""Muros por lotes (issue #13): abrir el panel, recorrer las filas «Wall», armar cada lote, confirmar y verificar.

Una sesión abre el panel UNA vez, hace todos los muros que el dinero permita (todos los niveles: una fila por
nivel, de más caro a más barato, con el saldo que va quedando) y lo cierra al final. Dentro de una pasada no se
comprueba nada fila a fila: se pulsa y se sigue, dando por hecho que lo calculado se mejoró. Si tras una pasada ya se
ven en pantalla filas nuevas (los muros mejorados salen en otra fila al subir de nivel), se trabajan sin subir. Solo
cuando a la vista no queda nada se vuelve al principio y se relee el panel UNA vez: ahí se verifica cada fila
(descontando los muros que le llegaron después de la fila del nivel anterior) y se ve lo que no se veía.

La decisión (cuándo, con qué recurso, cuántos) es la política pura `policy/walls.py`; aquí solo está el
recorrido y su cadena de seguridad:

  1. nunca se gastan gemas ni se abre la tienda (un diálogo de gemas se cierra y la tarea se bloquea);
  2. el lote se arma con «+10» / «+1» solo cuando están ACTIVOS (etiqueta blanca, no roja) y tras cada
     pulsación el contador «x N» debe haber subido: si no, se aborta sin confirmar;
  3. el coste total del botón «Upgrade» no se lee con dígitos: se confirma solo si el contador vale lo
     calculado (`N × coste unitario` de la fila) y el coste NO sale en rojo (se quita un muro, hasta 3 veces);
  4. «Upgrade» abre el modal «Upgrade Walls»; solo se pulsa «Okay» si el título y el «Cancel» lo
     identifican (el importe va en otra fuente y no se lee: lo fija el juego a partir de N);
  5. se verifica en el panel (fuente de la verdad) al acabar la pasada: misma fila con los muros que tocan, o la fila
     ya no aparece cuando el lote cubría todo el nivel. Nunca se reintenta un lote «a ciegas»;
  6. en modo de prueba se publica lo que se haría y no se pulsa nada de selección ni de confirmación;
  7. una fila de UN solo muro no tiene lote («Upgrade More» sale apagado): se mejora con el «Upgrade» directo de su menú y
     el «Confirm» del diálogo «Upgrade Wall to Level N?», solo si el botón cuesta lo que dice la fila y paga con un
     recurso gastable. Esas filas van las últimas: el juego puede cerrar el panel al confirmar.

El juego debe estar EN INGLÉS (ver `UpgradeTask`). Paga con oro o con elixir: la barra de lote trae un botón «Upgrade» por recurso (moneda o gota) y se pulsa
el del recurso que decide la política. Si el juego no ofrece ese botón (para ese nivel de muro) se sigue con el otro
recurso en la misma barra, y no se reintenta con ese recurso y nivel en una hora.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, TypeVar

from ..policy.walls import (
    WallSettings,
    WallState,
    batch_size,
    ELIXIR_MIN_LEVEL,
    decide_walls,
    gold_only_level,
    next_add,
    plan_clicks,
    spendable,
    verified_after_pass,
    wall_gate,
)
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, WallBatchPlanned, WallBatchStep, Warning
from ..screens.classifier import ScreenClassifier
from ..screens.upgrade_dialog import GEMS, DialogReader, usable_confirm
from ..screens.upgrade_panel import PanelRow, PanelScanner
from ..screens.village import VillageReader
from ..screens.wall_batch import BatchBar, BatchBarReader, UpgradeButton
from .base import Outcome, SessionView, ShouldRun, TaskResult
from .panel import ENGLISH_HINT, PanelTask
from .recovery import RecoveryConfig
from .upgrade import VILLAGE_MENU, MenuDefinition

T = TypeVar("T")
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
    # Ritmo (ADR 0005). Las esperas son por sondeo: se mira la pantalla cada `poll_s` y se sigue en cuanto sale lo esperado,
    # sin pausas fijas. Cada pulsación que responde a un cambio de pantalla lleva además una reacción humana aleatoria.
    poll_s: float = 0.1
    ui_timeout_s: float = 4.0  # lo máximo que se espera a que el juego muestre lo esperado antes de abortar sin pulsar
    reaction_min_s: float = 0.2  # reacción visual simple humana: ~0,2-0,27 s
    reaction_max_s: float = 0.35
    # Pausa entre las pulsaciones calculadas de «+10» / «+1»: aleatoria en [min, max] en cada una, para que no sea un ritmo fijo.
    tap_gap_min_s: float = 0.24
    tap_gap_max_s: float = 0.34
    open_timeout_s: float = 6.0
    max_passes: int = 5  # pasadas por todas las filas en una sesión: lo mejorado puede dejar muros nuevos en la fila de arriba
    max_red_removals: int = 3  # «−1» como máximo si el coste sale en rojo
    verify_attempts: int = 2  # lecturas del panel tras confirmar (se relee una vez)
    cooldown_s: float = 0.0  # sin cooldown: una fila dudosa se reintenta en la siguiente ventana
    unavailable_cooldown_s: float = 3600.0  # si el juego no ofrece pagar con un recurso, no se vuelve a intentar en una hora
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


@dataclass(frozen=True)
class _RowWork:
    """Lo que dio una fila de muros: el saldo que queda, lo hecho y cómo sigue la sesión."""

    state: WallState
    done: list[tuple[str, int]] = field(default_factory=list)  # (recurso, muros) confirmados (en simulacro: planeados)
    skip: str = ""  # por qué no se hizo nada con la fila
    halted: str | None = None  # un lote se abortó: el panel está cerrado y la sesión acaba
    override: TaskResult | None = None  # resultado final que manda sobre todo (bloqueo, lote sin verificar)
    checked: bool = False  # ya verificada en el sitio (el muro suelto): no entra en la verificación de la pasada
    live: PanelRow | None = None  # la fila como estaba justo antes de trabajarla (su cantidad ya incluye lo que le llegó)


@dataclass(frozen=True)
class _Taken:
    """Una fila trabajada: cómo estaba justo antes de trabajarla y cuántos muros se mejoraron."""

    row: PanelRow
    count: int
    checked: bool  # ya verificada en el sitio


@dataclass(frozen=True)
class _Batch:
    ok: bool
    count: int = 0
    reason: str = ""
    blocked: bool = False
    resource: str = ""  # con qué recurso se pagó (puede no ser el planeado si el juego no ofrecía su botón)
    rest: tuple[tuple[str, int], ...] | None = None  # lotes que quedan si hubo que cambiar de recurso; None: sin cambio


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
        settings = cfg.settings
        state = WallState(resources, free, given.gold_floor, given.upgrades_pending)
        decision = decide_walls(state, settings)
        if decision.resource is None:
            return TaskResult.nothing_to_do(decision.reason)

        # Una sola sesión: el panel se abre una vez, se hacen TODOS los muros que el dinero permita (todas las filas, cada
        # nivel de muro es una) y se cierra al final. El panel abierto se actualiza en directo: no hace falta cerrarlo
        # para ver el resultado de cada lote.
        rows = self._open_and_scan(ctx)
        if isinstance(rows, TaskResult):
            return rows
        if not self._wall_rows(rows):
            self._close(ctx)
            # Con el panel recorrido entero, que no haya fila «Wall» es que no queda ningún muro por mejorar.
            return TaskResult.nothing_to_do(f"no hay fila «{WALL}» en el panel", exhausted=bool(self.last_scan.reached_end))

        done: list[tuple[str, int]] = []  # (recurso, muros) de toda la sesión
        skipped: list[str] = []  # por qué se saltó cada fila (para explicar un «no hizo nada»)
        halted: str | None = None  # motivo de un aborto: el panel ya está cerrado
        pending: list[_Taken] = []  # lo hecho desde la última revisión: se verifica al releer el panel entero
        for _ in range(cfg.max_passes):  # una pasada más mientras lo mejorado deje muros nuevos y siga habiendo dinero
            did = False
            for row in self._wall_rows(rows):
                work = self._work_row(ctx, row, state, settings)
                state = work.state
                done += work.done
                count = sum(n for _, n in work.done)
                if count and not cfg.dry_run:
                    did = True
                    pending.append(_Taken(work.live or row, count, work.checked))
                if work.skip:
                    skipped.append(work.skip)
                if work.override is not None:  # bloqueo o muro suelto sin verificar: se acaba aquí
                    self._close(ctx)
                    return work.override
                if work.halted is not None:
                    halted = work.halted
                    break
            if cfg.dry_run:
                break
            more_money = halted is None and did and decide_walls(state, settings).resource is not None
            if more_money:
                # Lo mejorado deja muros nuevos al subir de nivel: si ya están a la vista no hace falta subir a buscarlos.
                near = self._visible_work(ctx, state, settings)
                if near:
                    rows = near
                    continue
            if not pending:
                break
            # La «revisión»: se vuelve arriba y se lee el panel entero una vez. Verifica todo lo hecho y trae los muros que
            # no se veían (precios y cantidades nuevos). Tras un aborto el panel está cerrado y se reabre.
            read = self._review(ctx, pending, panel_open=halted is None)
            if isinstance(read, TaskResult):
                self._close(ctx)
                return read
            pending, rows = [], read
            if not more_money:
                break
        if pending:  # se acabaron las pasadas con lo último sin verificar
            read = self._review(ctx, pending, panel_open=halted is None)
            if isinstance(read, TaskResult):
                self._close(ctx)
                return read

        back = self._close(ctx)
        if not done:
            return TaskResult.nothing_to_do(halted or (skipped[0] if skipped else "no hay muros que mejorar"))
        if back is not None:
            return back
        by_resource: dict[str, int] = {}
        for resource, n in done:
            by_resource[resource] = by_resource.get(resource, 0) + n
        total = sum(by_resource.values())
        if cfg.dry_run:
            return TaskResult(Outcome.DONE, "dry run: " + "; ".join(f"{n} muros con {r}" for r, n in by_resource.items()))
        if len(by_resource) == 1:
            return TaskResult(Outcome.DONE, f"{total} muros mejorados con {next(iter(by_resource))}")
        return TaskResult(Outcome.DONE, f"{total} muros mejorados (" + "; ".join(f"{n} con {r}" for r, n in by_resource.items()) + ")")

    def _work_row(self, ctx: Context, row: PanelRow, state: WallState, settings: WallSettings) -> _RowWork:
        """Todo lo que se puede hacer con UNA fila de muros: planear con lo que queda, armar y confirmar sus lotes y
        comprobar en el panel abierto que la fila bajó. Devuelve el saldo que queda para las filas siguientes."""
        cfg = self.config
        # Los muros mejorados antes en esta sesión suben de nivel y se suman a esta fila: su cantidad ya no es la del
        # escaneo (un «suelto» puede ser ya un lote). Se localiza por coste y se planea con lo que hay AHORA.
        live = row if cfg.dry_run else self._locate(ctx, row)
        if live is None:
            return _RowWork(state, skip="la fila de muros ya no se ve en el panel")
        row = live
        # El coste unitario de la fila vale para los dos recursos (en las capturas, 100 000 en oro y en elixir).
        if row.resource not in ("gold", "elixir") or not row.cost:
            return _RowWork(state, skip=f"la fila de muros no tiene recurso o coste legibles ({row.resource}, {row.cost})")
        # «Sin botón» se apunta por recurso y nivel de muro (el coste unitario lo identifica): que los muros de nivel 2-4
        # no ofrezcan elixir no impide pagar con elixir los de nivel alto de la misma sesión.
        unavailable = frozenset(r for r in ("gold", "elixir") if not ctx.cooldowns.ready(self._no_button_key(r, row.cost)))
        here = replace(settings, enabled=settings.enabled - unavailable)
        plan = self._plan(state, here, row)
        if not plan:
            return _RowWork(state, skip=self._why_no_plan(state, here, row))
        for resource, count in plan:
            have = spendable(state, resource)
            ctx.events.publish(WallBatchPlanned(resource, count, row.cost, f"{resource}: gastable {have}, caben {count} de {row.cost}"))
        if cfg.dry_run:
            return _RowWork(self._after_spending(state, plan, row.cost), done=list(plan))
        if row.quantity < 2:
            return self._work_single(ctx, row, state, here)

        # Los lotes de una fila se hacen seguidos: ya se sabe cuántos caben (`plan`). Tras cada uno la fila tiene
        # `quantity − mejorados` muros: así se vuelve a localizar.
        row_done: list[tuple[str, int]] = []
        current = row
        queue = list(plan)

        def replan(missing: str) -> list[tuple[str, int]]:
            """Mismo cálculo sobre la misma fila sin el recurso que el juego no ofrece: no hace falta releer nada."""
            return self._plan(state, replace(here, enabled=here.enabled - {missing}), row)

        halted: str | None = None
        while queue:
            resource, target = queue.pop(0)
            # Solo el primer lote puede cambiar de recurso: después la fila ya ha cambiado y el plan se hizo con ella.
            batch = self._build_and_confirm(ctx, current, target, resource, None if row_done else replan)
            if batch.blocked:
                return _RowWork(self._after_spending(state, row_done, row.cost), done=row_done, override=TaskResult.blocked(batch.reason))
            if not batch.ok:
                ctx.events.publish(Warning(f"muros: {batch.reason}"))
                halted = batch.reason  # `_abort` ya cerró el panel
                break
            row_done.append((batch.resource, batch.count))
            current = replace(current, quantity=current.quantity - batch.count)
            if batch.rest is not None:  # se cambió de recurso: lo que queda es lo que el nuevo plan reparte
                queue = list(batch.rest)

        # Sin comprobar aquí: se da por hecho lo calculado y se verifica al releer el panel al acabar la pasada.
        return _RowWork(self._after_spending(state, row_done, row.cost), done=row_done, halted=halted, live=row)

    def _locate(self, ctx: Context, row: PanelRow) -> PanelRow | None:
        """La fila tal y como está ahora en el panel abierto (posición y cantidad actuales), buscada por su coste."""
        reveal = getattr(self.scanner, "reveal", None)
        return row if reveal is None else reveal(ctx, row, any_quantity=True)

    def _work_single(self, ctx: Context, row: PanelRow, state: WallState, settings: WallSettings) -> _RowWork:
        """Un solo muro: el juego no ofrece lote («Upgrade More» sale apagado). Se mejora con el «Upgrade» directo de su menú,
        que abre el diálogo «Upgrade Wall to Level N?» con su «Confirm» (el mismo que el de cualquier mejora)."""
        allowed = {r for r in ("gold", "elixir") if r in settings.enabled and (spendable(state, r) or 0) >= row.cost}
        batch = self._upgrade_single(ctx, row, allowed)
        if batch.blocked:
            return _RowWork(state, override=TaskResult.blocked(batch.reason))
        if not batch.ok:
            ctx.events.publish(Warning(f"muros: {batch.reason}"))
            return _RowWork(state, halted=batch.reason)  # `_abort` ya cerró el panel
        done = [(batch.resource, 1)]
        after = self._after_spending(state, done, row.cost)
        # Con el panel abierto se mira la fila en el sitio; si el juego lo cerró al confirmar, se ve en la aldea que bajó el saldo.
        panel_open = self.classifier.classify(ctx.device.capture().image).screen is self.config.menu.screen
        verified = self.scanner.confirm(ctx, row, 1) if panel_open else self._single_paid(ctx, row, batch.resource, state)
        if not verified:
            ctx.events.publish(Warning("muros: no se pudo verificar el muro suelto"))
            return _RowWork(after, done=done, override=TaskResult.nothing_to_do("no se pudo verificar el muro suelto"))
        return _RowWork(
            after, done=done, checked=True, live=row, halted=None if panel_open else "el juego cerró el panel tras mejorar un muro suelto"
        )

    def _upgrade_single(self, ctx: Context, row: PanelRow, allowed: set[str]) -> _Batch:
        ctx.check_stop()
        seen = self._reveal(ctx, row)
        if seen is None:
            return self._abort(ctx, "la fila de muros ya no se ve en el panel")
        ctx.device.tap(*seen.center)
        ctx.events.publish(WallBatchStep("select_row"))
        wall = self._poll(ctx, lambda: self.dialogs.wall_upgrade(ctx.device.capture().image), lambda w: w is not None)
        if wall is None:
            return self._abort(ctx, f"la fila no abrió el menú del muro{ENGLISH_HINT}")
        self._react(ctx)
        ctx.device.tap(*wall.center)
        ctx.events.publish(WallBatchStep("upgrade_single"))

        def screen():
            frame = ctx.device.capture()
            return frame, self.dialogs.missing(frame.image), self.dialogs.title(frame.image, WALL)

        frame, missing, title = self._poll(ctx, screen, lambda s: s[1] is not None or s[2] is not None)
        if missing == GEMS:
            self._close(ctx)
            return _Batch(False, reason="el juego ofrece pagar con gemas: no se gastan nunca", blocked=True)
        if missing is not None:
            return self._abort(ctx, "faltan recursos que no se habían visto en rojo", frame.image)
        if title is None:
            return self._abort(ctx, f"el diálogo no nombra «{WALL}» o no se reconoce{ENGLISH_HINT}", frame.image)
        button = usable_confirm(self.dialogs.confirm_buttons(frame.image))
        if button is None:
            return self._abort(ctx, "el diálogo no tiene un botón con oro o elixir", frame.image)
        if button.cost != row.cost:
            return self._abort(ctx, f"el botón cuesta {button.cost} y la fila {row.cost}", frame.image)
        if button.resource not in allowed:
            return self._abort(ctx, f"el botón gasta {button.resource}, que no se puede gastar ahora", frame.image)
        self._react(ctx)
        ctx.check_stop()
        ctx.device.tap(*button.center)
        ctx.events.publish(WallBatchStep("confirm_single", 1, button.resource))
        self._poll(ctx, lambda: self.dialogs.title(ctx.device.capture().image, WALL), lambda t: t is None)  # hasta que se cierra
        return _Batch(True, 1, resource=button.resource)

    def _single_paid(self, ctx: Context, row: PanelRow, resource: str, before: WallState) -> bool:
        """Con la aldea a la vista: el recurso bajó al menos la mitad de lo que costaba el muro (como la comprobación de
        un Rebuild). `before` es el saldo de antes de pagar."""
        index = {"gold": 0, "elixir": 1}[resource]
        was = before.resources[index]
        now = self.village.read_state_stable(ctx)
        if was is None or row.cost is None or "resources" in now.unreliable or not now.resources or now.resources[index] is None:
            return False
        return was - now.resources[index] >= row.cost * 0.5

    def _why_no_plan(self, state: WallState, settings: WallSettings, row: PanelRow) -> str:
        decision = decide_walls(state, settings)
        if decision.resource is None:
            return decision.reason
        if decision.resource == "elixir" and gold_only_level(row.level):
            # Los muros que suben al nivel 2, 3 o 4 solo se pagan con oro.
            without = decide_walls(state, replace(settings, enabled=settings.enabled - {"elixir"}))
            if without.resource is None:
                rest = "; ".join(r for r in without.skipped if not r.startswith("elixir:")) or without.reason
                return f"los muros suben al nivel {row.level} y el elixir sirve desde el {ELIXIR_MIN_LEVEL}: {rest}"
        return f"con {decision.spendable} gastable no cabe un muro de {row.cost}"

    @staticmethod
    def _after_spending(state: WallState, spent: list[tuple[str, int]], unit_cost: int) -> WallState:
        """El saldo tras gastar `spent`: el juego cobra `muros × coste unitario` de la fila."""
        gold, elixir, dark = state.resources
        for resource, n in spent:
            if resource == "gold" and gold is not None:
                gold -= n * unit_cost
            elif resource == "elixir" and elixir is not None:
                elixir -= n * unit_cost
        return replace(state, resources=(gold, elixir, dark))

    def _review(self, ctx: Context, pending: list[_Taken], panel_open: bool) -> list[PanelRow] | TaskResult:
        """Relee el panel entero y comprueba todo lo hecho desde la última revisión. Devuelve las filas leídas (para la
        pasada siguiente) o el resultado final si no se pudo verificar. Se relee una vez más si no cuadra."""
        for attempt in range(self.config.verify_attempts):
            ctx.check_stop()
            read = self._rescan(ctx) if panel_open or attempt else self._open_and_scan(ctx)
            if isinstance(read, TaskResult):
                return read
            if all(self._row_matches(read, pending, i) for i in range(len(pending))):
                return read
            ctx.events.publish(WallBatchStep("verify_failed", sum(t.count for t in pending), f"intento {attempt + 1}"))
        ctx.events.publish(Warning("muros: no se pudo verificar el lote en el panel"))
        return TaskResult.nothing_to_do("no se pudo verificar el lote en el panel")

    def _row_matches(self, rows: list[PanelRow], pending: list[_Taken], index: int) -> bool:
        """¿La fila relevida cuadra con lo mejorado? Se parte de su cantidad justo antes de trabajarla y solo cuentan los
        muros que le llegaron DESPUÉS, de filas trabajadas más tarde: los de la fila del nivel anterior (nivel − 1); los de
        filas con nivel ilegible solo ponen el techo del margen."""
        mine = pending[index]
        row = mine.row
        later = pending[index + 1:]
        if mine.checked or any(t.row.cost == row.cost for t in later):  # ya verificada, o la sustituye una lectura posterior
            return True
        known = unknown = 0
        for other in later:
            if row.level is not None and other.row.level is not None:
                known += other.count if other.row.level == row.level - 1 else 0
            else:
                unknown += other.count
        after = self._same_wall_row(rows, row)
        return verified_after_pass(
            row.quantity, row.cost, mine.count, known, known + unknown,
            after.quantity if after else None, after.cost if after else None,
            after is not None, bool(self.last_scan.reached_end),
        )

    def _visible_work(self, ctx: Context, state: WallState, settings: WallSettings) -> list[PanelRow]:
        """Filas de muros que ya se ven en el panel (sin subir ni bajar) y que el saldo permite mejorar."""
        visible = getattr(self.scanner, "visible", None)
        if visible is None:
            return []
        return [r for r in self._wall_rows(visible(ctx)) if r.cost and self._plan(state, settings, r)]

    def _rescan(self, ctx: Context) -> list[PanelRow] | TaskResult:
        """Vuelve al principio del panel abierto y lo lee entero."""
        rewind = getattr(self.scanner, "rewind", None)
        if rewind is not None:
            rewind(ctx)
        self.last_scan = self.scanner.scan(ctx, **self._scan_options())
        ctx.progress()
        return list(self.last_scan.rows)

    @staticmethod
    def _plan(state: WallState, settings: WallSettings, row: PanelRow) -> list[tuple[str, int]]:
        """Lotes de esta ventana, en orden de preferencia (elixir primero): (recurso, muros).

        Cada recurso se gasta hasta donde llega y lo que queda de la fila pasa al siguiente. El coste unitario de la
        fila vale para oro y elixir. El elixir no sirve para los niveles 2-4.
        """
        plan: list[tuple[str, int]] = []
        remaining = row.quantity
        for resource in settings.preference.order:
            if resource not in settings.enabled or (resource == "elixir" and gold_only_level(row.level)):
                continue
            have = spendable(state, resource)
            if have is None or have <= 0 or have < settings.threshold:
                continue
            count = batch_size(have, row.cost, remaining)
            if count >= 1:
                plan.append((resource, count))
                remaining -= count
        return plan

    @staticmethod
    def _no_button_key(resource: str, unit_cost: int | None) -> str:
        return f"walls:no_button:{resource}:{unit_cost}"

    @staticmethod
    def _wall_rows(rows: list[PanelRow]) -> list[PanelRow]:
        # Solo las mejoras: la fila «Wall ×N · gratis» de edificios nuevos (muros por colocar) no es un lote de mejora.
        # Una fila por nivel de muro, en el orden del panel (el del filtro del juego: de más caro a más barato). Los muros
        # sueltos van los últimos: su diálogo puede cerrar el panel y no deben cortar las filas con lote.
        walls = [r for r in rows if r.name == WALL and r.kind == "wall" and not r.new and not r.free]
        return sorted(walls, key=lambda r: r.quantity < 2)

    @staticmethod
    def _same_wall_row(rows: list[PanelRow], before: PanelRow) -> PanelRow | None:
        """La fila de muros de ANTES, tras el lote. Hay una fila por nivel de muro (`Wall ×100 · 25 000`, `Wall ×25 · 500`…) y
        el coste unitario las distingue: si la del lote se agotó, no se debe tomar la siguiente fila «Wall» por ella."""
        return next(
            (r for r in rows if r.name == WALL and r.kind == "wall" and not r.new and not r.free and r.cost == before.cost),
            None,
        )

    # ------------------------------------------------------------------------------ armar y confirmar

    def _build_and_confirm(
        self,
        ctx: Context,
        row: PanelRow,
        target: int,
        resource: str,
        replan: Callable[[str], list[tuple[str, int]]] | None = None,
    ) -> _Batch:
        ctx.check_stop()
        rest: tuple[tuple[str, int], ...] | None = None
        seen = self._reveal(ctx, row)
        if seen is None:
            return self._abort(ctx, "la fila de muros ya no se ve en el panel")
        ctx.device.tap(*seen.center)
        ctx.events.publish(WallBatchStep("select_row"))
        point = self._poll(ctx, lambda: self.bars.upgrade_more(ctx.device.capture().image), lambda p: p is not None)
        if point is None:
            return self._abort(ctx, f"la fila no abrió la barra del muro{ENGLISH_HINT}")
        self._react(ctx)
        ctx.device.tap(*point)
        ctx.events.publish(WallBatchStep("upgrade_more"))

        bar = self._read_bar(ctx)
        if bar is None or bar.count is None:
            return self._abort(ctx, "no se ve la barra de lote o su contador")
        self._react(ctx)
        if bar.upgrade_for(resource) is None:  # antes de armar nada: ¿ofrece el juego pagar con este recurso?
            ctx.cooldowns.start(self._no_button_key(resource, row.cost), self.config.unavailable_cooldown_s)
            # Los muros no cambian: se rehace la cuenta con el otro recurso y se sigue en esta misma barra.
            alternative = replan(resource) if replan is not None else []
            switched = next(((r, n) for r, n in alternative if bar.upgrade_for(r) is not None), None)
            if switched is None:
                return self._abort(ctx, f"el juego no ofrece un botón «Upgrade» de {resource} para estos muros")
            old, (resource, target) = resource, switched
            rest = tuple(alternative[alternative.index(switched) + 1:])
            ctx.events.publish(Warning(f"muros: sin botón de {old}; se sigue con {resource} en la misma barra"))
            ctx.events.publish(WallBatchPlanned(resource, target, row.cost or 0, f"{resource}: sin botón de {old}, caben {target} de {row.cost}"))
        count = bar.count
        if count > target:
            return self._abort(ctx, f"el lote ya vale {count}, más que lo calculado ({target})")

        # Pulsaciones calculadas de antemano: «+10» × k y «+1» × r seguidas (los botones no se mueven mientras sube el
        # contador) y UNA lectura al final, que debe valer el lote. Una pulsación perdida la corrige el bucle de abajo.
        n10, n1 = plan_clicks(target - count)
        if bar.add10 is None and bar.add1 is not None:  # sin «+10» (menos de 10 muros posibles): todo con «+1»
            n10, n1 = 0, n10 * 10 + n1
        planned = ((bar.add10, 10, n10), (bar.add1, 1, n1))
        if all(button is not None and button.active for button, _, n in planned if n):
            first = True
            for button, gain, n in planned:
                if not n:
                    continue
                ctx.events.publish(WallBatchStep(f"add{gain}", count, f"×{n}"))
                for _ in range(n):
                    ctx.check_stop()
                    if not first:  # la pausa va ENTRE pulsaciones: tras la última solo se espera a ver el contador
                        ctx.sleep(ctx.rng.uniform(self.config.tap_gap_min_s, self.config.tap_gap_max_s))
                    first = False
                    ctx.device.tap(*button.center)
            after = self._read_bar(ctx, lambda b: b.count == target)  # hasta que el contador llegue al lote
            if after is None or after.count is None:
                return self._abort(ctx, "no se pudo leer el contador tras las pulsaciones de «Add Wall»")
            if after.count > target or after.count < count:
                return self._abort(ctx, f"el contador pasó de {count} a {after.count} (el lote es de {target})")
            count, bar = after.count, after

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
            after = self._read_bar(ctx, lambda b: b.count != count)
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
            after = self._read_bar(ctx, lambda b: b.count != count)
            if after is None or after.count is None or after.count != count - 1:
                return self._abort(ctx, "no se pudo reducir el lote en rojo")
            count, bar = after.count, after
        if (bar.upgrade_for(resource) or _NO_BUTTON).affordable is not True:
            return self._abort(ctx, "no se pudo comprobar que el coste del lote no esté en rojo")

        # Última comprobación con la barra recién leída: el contador debe valer lo que se va a gastar.
        self._react(ctx)
        last = self._read_bar(ctx)
        pay = last.upgrade_for(resource) if last is not None else None
        if last is None or last.count != count or pay is None or pay.affordable is not True:
            return self._abort(ctx, "el contador, el botón o el coste cambiaron justo antes de confirmar")
        ctx.check_stop()
        ctx.device.tap(*pay.center)
        ctx.events.publish(WallBatchStep("upgrade", count, resource))

        def screen():
            frame = ctx.device.capture()
            return frame, self.dialogs.missing(frame.image) == GEMS, self.bars.modal(frame.image)

        frame, gems, modal = self._poll(ctx, screen, lambda s: s[1] or s[2] is not None)  # el modal, o el aviso de gemas
        if gems:
            self._close(ctx)
            return _Batch(False, reason="el juego ofrece pagar con gemas: no se gastan nunca", blocked=True)
        if modal is None:
            return self._abort(ctx, "tras «Upgrade» no salió el modal «Upgrade Walls»", frame.image)
        self._react(ctx)
        ctx.check_stop()
        ctx.device.tap(*modal.okay)
        ctx.events.publish(WallBatchStep("okay", count))
        self._poll(ctx, lambda: self.bars.modal(ctx.device.capture().image), lambda m: m is None)  # hasta que el modal se cierra
        return _Batch(True, count, resource=resource, rest=rest)

    def _poll(self, ctx: Context, read: Callable[[], T], accept: Callable[[T], bool]) -> T:
        """Mira la pantalla cada `poll_s` hasta que `accept(lectura)` o se agota `ui_timeout_s`; devuelve la última lectura.

        Es lo que sustituye a las pausas fijas: la espera dura lo que tarda el juego en responder y la visión en leerlo.
        """
        deadline = ctx.clock.now() + self.config.ui_timeout_s
        while True:
            ctx.check_stop()
            value = read()
            if accept(value) or ctx.clock.now() >= deadline:
                return value
            ctx.sleep(self.config.poll_s)

    def _react(self, ctx: Context) -> None:
        """Pausa de reacción humana, aleatoria, antes de pulsar algo que acaba de aparecer o cambiar (ADR 0005)."""
        ctx.sleep(ctx.rng.uniform(self.config.reaction_min_s, self.config.reaction_max_s))

    def _read_bar(self, ctx: Context, accept: Callable[[BatchBar], bool] = lambda bar: True) -> BatchBar | None:
        """Lee la barra con su contador hasta que `accept` lo da por bueno; devuelve la última lectura (o None si nunca salió)."""
        return self._poll(
            ctx,
            lambda: self.bars.read(ctx.device.capture().image),
            lambda b: b is not None and b.count is not None and accept(b),
        )

    def _abort(self, ctx: Context, reason: str, image=None) -> _Batch:
        """Sale sin confirmar: guarda una captura y vuelve a la aldea."""
        frame = image if image is not None else ctx.device.capture().image
        path = self._save(ctx, frame)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))
        ctx.events.publish(WallBatchStep("abort", None, reason))
        self._close(ctx)
        return _Batch(False, reason=reason)


__all__ = ["WALL", "WallBatchTask", "WallConfig", "WallInputs"]
