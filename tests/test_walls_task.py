"""Muros por lotes (issue #13): costura de tarea sobre dispositivo falso.

Las pantallas son capturas reales anonimizadas (aldea, panel, barra del muro, modal «Upgrade Walls»,
gemas). La barra de lote cambia con cada pulsación y solo hay fixtures de contador 1 y 10, así que el
lector de la barra se guioniza por fotograma (`FakeBars`); `upgrade_more` y `modal` son los reales.
La visión de la barra tiene sus pruebas sobre las capturas en test_wall_batch.py.
"""

import random

import pytest

from pyclash.device.base import Capabilities, Rect
from pyclash.device.fake import FakeDevice, Transition
from pyclash.policy.walls import Preference, WallSettings
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.runtime.events import WallBatchPlanned, WallBatchStep, Warning
from pyclash.screens.upgrade_panel import PanelRow, PanelScan
from pyclash.screens.village import VillageState
from pyclash.screens.wall_batch import BatchBar, BatchBarReader, BatchButton, UpgradeButton
from pyclash.tasks.base import Outcome, run_task
from pyclash.tasks.recovery import RecoveryConfig
from pyclash.tasks.walls import WallBatchTask, WallConfig, WallInputs
from pyclash.vision.base import MetadataVision

from helpers import fixture_frame

VILLAGE, PANEL, SELECT, B1, B2, B3, B4, MODAL, MISSING = range(9)
FRAMES = ["main_village", "upgrade_panel_filtered", "wall_select_bar", "wall_batch_one", "wall_batch_one",
          "wall_batch_one", "wall_batch_one", "wall_batch_modal", "missing_gems"]

BUILD = Rect(1140, 640, 140, 80)
PANEL_ROWS = Rect(941, 72, 339, 560)
PANEL_X = Rect(1200, 0, 80, 70)
UPGRADE_MORE = Rect(650, 520, 90, 80)
UPGRADE_E = Rect(700, 580, 90, 50)  # botón de elixir, a la derecha del de oro
ADD10, ADD1, REMOVE, UPGRADE = Rect(380, 580, 80, 50), Rect(480, 580, 80, 50), Rect(280, 575, 70, 50), Rect(580, 580, 90, 50)
OKAY = Rect(657, 409, 199, 82)
MISSING_X = Rect(820, 205, 80, 70)
ENTER_SHOP = Rect(545, 404, 198, 83)

WALL_ROW = PanelRow("Wall", 26, 1000, "gold", False, False, None, True, "wall", None, (1000, 300), 300)


def button(rect, active=True):
    return BatchButton((rect.x + rect.w // 2, rect.y + rect.h // 2), active, 0.9)


def upgrade_button(rect, resource, affordable=True):
    return UpgradeButton((rect.x + rect.w // 2, rect.y + rect.h // 2), resource, affordable)


def bar(count, *, add10=True, add1=True, remove=True, affordable=True, elixir=False, gold=True):
    """Barra de lote guionizada. `gold` / `elixir`: qué botones «Upgrade» ofrece el juego."""
    upgrades = []
    if gold:
        upgrades.append(upgrade_button(UPGRADE, "gold", affordable))
    if elixir:
        upgrades.append(upgrade_button(UPGRADE_E, "elixir", affordable))
    return BatchBar(button(REMOVE, remove), button(ADD10, add10), button(ADD1, add1), tuple(upgrades), count)


class FakeBars(BatchBarReader):
    """`read` según el fotograma actual del dispositivo; `upgrade_more` y `modal` leen la imagen de verdad."""

    def __init__(self, table):
        super().__init__()
        self.table = table
        self.device = None

    def read(self, image):
        return self.table.get(self.device.frame_index)


class SeqScanner:
    """Devuelve una lectura del panel por llamada: antes y después del lote."""

    def __init__(self, *readings):
        self.readings = list(readings)

    def scan(self, ctx, **kw):
        rows, complete = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        return PanelScan(tuple(rows), 0, complete, None, "")


class FakeVillage:
    def __init__(self, state):
        self.state = state

    def read_state_stable(self, ctx, *a, **k):
        return self.state


def vstate(free=1, gold=5000, elixir=0):
    return VillageState((free, 2) if free is not None else None, None, None, None, (gold, elixir, 0), frozenset())


def route(source, target, region=None):
    return Transition(source, "tap", target, region)


BASE = [
    route(VILLAGE, PANEL, BUILD),
    route(PANEL, VILLAGE, PANEL_X),
    route(PANEL, SELECT, PANEL_ROWS),
    route(SELECT, B1, UPGRADE_MORE),
    route(B1, B2, ADD1), route(B2, B3, ADD1), route(B3, B4, ADD1),
    route(B1, B1, ADD10),
    route(B4, MODAL, UPGRADE), route(B3, MODAL, UPGRADE), route(B2, MODAL, UPGRADE),
    route(B4, MODAL, UPGRADE_E), route(B3, MODAL, UPGRADE_E), route(B2, MODAL, UPGRADE_E),
    route(MODAL, VILLAGE, OKAY),
    route(MISSING, VILLAGE, MISSING_X),
]


def make(*, table=None, transitions=None, village=None, readings=None, inputs=None, config=None, tmp_path=None):
    clock = FakeClock()
    device = FakeDevice([fixture_frame(n) for n in FRAMES], clock, transitions if transitions is not None else BASE,
                        Capabilities(swipe=True))
    ctx = Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(1))
    bars = FakeBars(table or {B1: bar(1), B2: bar(2), B3: bar(3), B4: bar(4)})
    bars.device = device
    cfg = config or WallConfig(
        settle_s=0.1, open_timeout_s=1.0, capture_dir=tmp_path, recovery=RecoveryConfig(settle_seconds=0.1),
    )
    scanner = SeqScanner(*(readings or [([WALL_ROW], True), ([_row(22)], True)]))
    task = WallBatchTask(
        cfg, inputs or (lambda c: WallInputs(gold_floor=1000, upgrades_pending=False)),
        scanner=scanner, bars=bars, village=village or FakeVillage(vstate()),
    )
    return task, ctx


def _row(quantity):
    return PanelRow("Wall", quantity, 1000, "gold", False, False, None, True, "wall", None, (1000, 300), 300)


def taps(ctx):
    return [a.args for a in ctx.device.actions if a.kind == "tap"]


def tapped(ctx, rect):
    return [t for t in taps(ctx) if rect.contains(*t)]


def collect(ctx, kind):
    seen = []
    ctx.events.subscribe(kind, seen.append)
    return seen


# --------------------------------------------------------------------------- recorrido completo


def test_lote_completo_abre_panel_arma_confirma_y_verifica_en_el_panel():
    task, ctx = make()
    planned, steps = collect(ctx, WallBatchPlanned), collect(ctx, WallBatchStep)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and "4 muros" in result.reason
    assert planned[0].count == 4 and planned[0].unit_cost == 1000  # (5000 - colchón 1000) // 1000
    assert len(tapped(ctx, ADD1)) == 3 and not tapped(ctx, ADD10)  # faltan 3: solo «+1»
    assert len(tapped(ctx, UPGRADE)) == 1 and len(tapped(ctx, OKAY)) == 1
    assert [s.action for s in steps][:2] == ["select_row", "upgrade_more"]
    assert ctx.device.frame_index == VILLAGE


def test_el_lote_nunca_supera_lo_calculado():
    task, ctx = make(village=FakeVillage(vstate(gold=3500)))  # (3500 - 1000) // 1000 = 2
    result = run_task(task, ctx)
    assert len(tapped(ctx, ADD1)) == 1  # de 1 a 2
    assert result.outcome is Outcome.NOTHING_TO_DO  # el panel «después» (22) no cuadra con un lote de 2
    assert len(tapped(ctx, OKAY)) == 1


def test_modo_de_prueba_decide_y_no_pulsa_nada_de_seleccion():
    cfg = WallConfig(dry_run=True, settle_s=0.1, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(config=cfg)
    planned = collect(ctx, WallBatchPlanned)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and "dry run" in result.reason
    assert planned and not tapped(ctx, PANEL_ROWS) and not tapped(ctx, UPGRADE) and not tapped(ctx, OKAY)


# --------------------------------------------------------------------------- R1: cuándo


def test_sin_constructor_libre_ni_se_abre_el_panel():
    task, ctx = make(village=FakeVillage(vstate(free=0)))
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and taps(ctx) == []


def test_con_las_mejoras_pendientes_los_muros_esperan():
    task, ctx = make(inputs=lambda c: WallInputs(gold_floor=1000, upgrades_pending=True))
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "mejoras" in result.reason and taps(ctx) == []


def test_oro_justo_bajo_el_colchon_no_se_gasta():  # E3R1
    task, ctx = make(village=FakeVillage(vstate(gold=900)))
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO and taps(ctx) == []


def test_colchon_desconocido_no_gasta_oro():
    task, ctx = make(inputs=lambda c: WallInputs(gold_floor=None, upgrades_pending=False))
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO and taps(ctx) == []


def test_solo_oro_habilitado_con_preferencia_de_solo_elixir_no_hace_nada():
    cfg = WallConfig(settings=WallSettings(preference=Preference.ELIXIR_ONLY, enabled=frozenset({"gold"})),
                     settle_s=0.1, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(config=cfg)
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO and taps(ctx) == []


# --------------------------------------------------------------------------- R2: botones activos


def test_botones_de_anadir_apagados_no_se_pulsan_y_el_lote_queda_en_el_maximo():  # E1R2
    table = {B1: bar(1), B2: bar(2, add10=False, add1=False)}
    task, ctx = make(table=table, readings=[([WALL_ROW], True), ([_row(24)], True)])
    result = run_task(task, ctx)
    assert len(tapped(ctx, ADD1)) == 1 and not tapped(ctx, ADD10)  # a B2 y ahí se para
    assert result.outcome is Outcome.DONE and "2 muros" in result.reason


def test_si_el_contador_no_sube_se_aborta_sin_confirmar(tmp_path):
    table = {B1: bar(1), B2: bar(1), B3: bar(1), B4: bar(1)}
    task, ctx = make(table=table, tmp_path=tmp_path)
    warnings = collect(ctx, Warning)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO
    assert not tapped(ctx, UPGRADE) and not tapped(ctx, OKAY)
    assert warnings and list(tmp_path.glob("walls-*.png"))


# --------------------------------------------------------------------------- R3: nunca se gasta mal


def test_coste_en_rojo_quita_un_muro_antes_de_confirmar():  # E1R3
    table = {B1: bar(1), B2: bar(2), B3: bar(3), B4: bar(4, affordable=False)}
    table[B3] = bar(3)
    trans = [t for t in BASE] + [route(B4, B3, REMOVE)]
    task, ctx = make(table=table, transitions=trans, readings=[([WALL_ROW], True), ([_row(23)], True)])
    steps = collect(ctx, WallBatchStep)
    result = run_task(task, ctx)
    assert tapped(ctx, REMOVE) and any(s.action == "remove1" for s in steps)
    assert len(tapped(ctx, UPGRADE)) == 1 and result.outcome is Outcome.DONE and "3 muros" in result.reason


def test_coste_en_rojo_sin_poder_reducir_cancela(tmp_path):
    table = {B1: bar(1), B2: bar(2), B3: bar(3), B4: bar(4, affordable=False, remove=False)}
    task, ctx = make(table=table, tmp_path=tmp_path)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "rojo" in result.reason
    assert not tapped(ctx, UPGRADE) and not tapped(ctx, OKAY)


def test_sin_modal_de_muros_no_se_pulsa_okay():
    trans = [t for t in BASE if not (t.target == MODAL)]  # «Upgrade» no cambia de pantalla
    task, ctx = make(transitions=trans)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "modal" in result.reason
    assert not tapped(ctx, OKAY)


def test_un_dialogo_de_gemas_se_cierra_y_bloquea_sin_ir_a_la_tienda():
    trans = [t for t in BASE if not (t.target == MODAL)] + [route(B4, MISSING, UPGRADE)]
    task, ctx = make(transitions=trans)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.BLOCKED and "gemas" in result.reason
    assert not tapped(ctx, ENTER_SHOP) and not tapped(ctx, OKAY)


# --------------------------------------------------------------------------- R4: verificación en el panel


def test_panel_sin_cambios_no_cuenta_como_verificado():  # E3R4
    task, ctx = make(readings=[([WALL_ROW], True), ([WALL_ROW], True)])
    warnings = collect(ctx, Warning)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "verificar" in result.reason
    assert any("verificar" in w.message for w in warnings)


def test_fila_que_desaparece_con_un_lote_que_cubre_todo_el_nivel():  # E2R4
    four = _row(4)
    task, ctx = make(readings=[([four], True), ([], True)])
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and "4 muros" in result.reason


def test_fila_ausente_con_el_panel_sin_recorrer_entero_no_verifica():
    four = _row(4)
    task, ctx = make(readings=[([four], True), ([], False)])
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO


def test_fila_ausente_con_un_lote_parcial_no_verifica():
    task, ctx = make(readings=[([WALL_ROW], True), ([], True)])
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO


# --------------------------------------------------------------------------- elixir: el botón del recurso decidido


def both_buttons(**kw):
    return {n: bar(k, elixir=True, **kw) for n, k in ((B1, 1), (B2, 2), (B3, 3), (B4, 4))}


def test_elixir_primero_se_pulsa_el_boton_de_elixir_no_el_de_oro():
    task, ctx = make(table=both_buttons(), village=FakeVillage(vstate(gold=1500, elixir=4500)))
    result = run_task(task, ctx)  # elixir gastable 4500 -> 4 muros; el oro no llega a nada sobre el colchón
    assert result.outcome is Outcome.DONE
    assert len(tapped(ctx, UPGRADE_E)) == 1 and not tapped(ctx, UPGRADE)


def test_con_oro_y_elixir_disponibles_y_elixir_primero_gana_el_elixir():
    task, ctx = make(table=both_buttons(), village=FakeVillage(vstate(gold=5000, elixir=4500)))
    run_task(task, ctx)
    assert tapped(ctx, UPGRADE_E) and not tapped(ctx, UPGRADE)


def test_oro_primero_pulsa_el_boton_de_oro_aunque_haya_elixir():
    cfg = WallConfig(settings=WallSettings(preference=Preference.GOLD_FIRST), settle_s=0.1,
                     recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(table=both_buttons(), village=FakeVillage(vstate(gold=5000, elixir=4500)), config=cfg)
    run_task(task, ctx)
    assert tapped(ctx, UPGRADE) and not tapped(ctx, UPGRADE_E)


def test_si_el_juego_no_ofrece_boton_de_elixir_no_se_arma_el_lote_y_se_apunta():
    # Muros de nivel bajo: solo oro. Con elixir primero se intenta una vez y no se vuelve en una hora.
    task, ctx = make(village=FakeVillage(vstate(gold=1500, elixir=4500)))
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "elixir" in result.reason
    assert not tapped(ctx, ADD1) and not tapped(ctx, ADD10)  # no se armó nada
    assert not tapped(ctx, UPGRADE) and not tapped(ctx, UPGRADE_E) and not tapped(ctx, OKAY)
    assert not ctx.cooldowns.ready("walls:no_button:elixir")


def test_tras_apuntar_que_no_hay_elixir_la_siguiente_ventana_usa_el_oro():
    task, ctx = make(village=FakeVillage(vstate(gold=5000, elixir=4500)))
    run_task(task, ctx)  # primera ventana: elixir no disponible
    ctx.device._index = VILLAGE
    ctx.device.actions.clear()
    task.scanner = SeqScanner(([WALL_ROW], True), ([_row(22)], True))
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and tapped(ctx, UPGRADE) and not tapped(ctx, UPGRADE_E)


def test_coste_en_rojo_solo_cuenta_el_del_boton_del_recurso_elegido():
    # El oro sale en rojo pero se paga con elixir: no es motivo para quitar muros.
    def mixed(n):
        b = bar(n, elixir=True)
        return BatchBar(b.remove, b.add10, b.add1,
                        (upgrade_button(UPGRADE, "gold", False), upgrade_button(UPGRADE_E, "elixir", True)), n)

    task, ctx = make(table={B1: mixed(1), B2: mixed(2), B3: mixed(3), B4: mixed(4)},
                     village=FakeVillage(vstate(gold=1500, elixir=4500)))
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and not tapped(ctx, REMOVE) and tapped(ctx, UPGRADE_E)


# --------------------------------------------------------------------------- nivel: el elixir solo desde el 5


def _wall_at(level, cost=5000):
    return PanelRow("Wall", 28, cost, "gold", False, False, None, True, "wall", None, (1000, 300), 300, level=level)


def _dry_with_elixir_rich(row):
    cfg = WallConfig(dry_run=True, settle_s=0.1, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(
        config=cfg, village=FakeVillage(vstate(gold=30000, elixir=100000)), readings=[([row], True)],
        inputs=lambda c: WallInputs(gold_floor=0, upgrades_pending=False),
    )
    planned = collect(ctx, WallBatchPlanned)
    return run_task(task, ctx), planned


@pytest.mark.parametrize("level", [2, 3, 4])
def test_muros_que_suben_a_nivel_4_o_menos_se_pagan_solo_con_oro(level):
    result, planned = _dry_with_elixir_rich(_wall_at(level))
    assert result.outcome is Outcome.DONE
    assert [p.resource for p in planned][-1] == "gold"  # con 100 000 de elixir, la política prefiere elixir: se veta


def test_muros_de_nivel_5_o_mas_pueden_pagarse_con_elixir():
    _, planned = _dry_with_elixir_rich(_wall_at(5, cost=20000))
    assert planned[-1].resource == "elixir"


def test_con_nivel_desconocido_no_se_veta_el_elixir():
    """Sin lectura del nivel manda el botón del juego: el veto nunca se aplica por dudas."""
    _, planned = _dry_with_elixir_rich(_wall_at(None))
    assert planned[-1].resource == "elixir"


def test_nivel_bajo_sin_oro_gastable_no_hace_nada_y_lo_explica():
    cfg = WallConfig(dry_run=True, settle_s=0.1, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(
        config=cfg, village=FakeVillage(vstate(gold=30000, elixir=100000)), readings=[([_wall_at(3)], True)],
        inputs=lambda c: WallInputs(gold_floor=None, upgrades_pending=False),  # oro sin colchón conocido: no se gasta
    )
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "nivel 3" in result.reason
