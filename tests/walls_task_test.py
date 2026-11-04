"""Muros por lotes (issue #13): costura de tarea sobre dispositivo falso.

Las pantallas son capturas reales anonimizadas (aldea, panel, barra del muro, modal «Upgrade Walls»,
gemas). La barra de lote cambia con cada pulsación y solo hay fixtures de contador 1 y 10, así que el
lector de la barra se guioniza por fotograma (`FakeBars`); `upgrade_more` y `modal` son los reales.
La visión de la barra tiene sus pruebas sobre las capturas en wall_batch_test.py.
"""

import random

from dataclasses import replace

import pytest

from pyclash.device.base import Capabilities, Rect
from pyclash.device.fake import FakeDevice, Transition
from pyclash.policy.walls import Preference, WallSettings, WallState, verified_in_panel
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

VILLAGE, PANEL, SELECT, B1, B2, B3, B4, MODAL, MISSING, SINGLE_MENU, SINGLE_DIALOG = range(11)
FRAMES = ["main_village", "upgrade_panel_filtered", "wall_select_bar", "wall_batch_one", "wall_batch_one",
          "wall_batch_one", "wall_batch_one", "wall_batch_modal", "missing_gems", "wall_single_menu", "wall_single_dialog"]

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
    """Una lectura del panel por llamada: `scan` da la de antes del lote y `confirm` mira la de después (el panel
    abierto se actualiza en directo, así que la verificación no recorre nada: se mira la fila en el sitio)."""

    def __init__(self, *readings):
        self.readings = list(readings)
        self.calls: list[str] = []

    def _next(self):
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

    def scan(self, ctx, **kw):
        self.calls.append("scan")
        rows, complete = self._next()
        return PanelScan(tuple(rows), 0, complete, None, "")

    def rewind(self, ctx, **kw):
        self.calls.append("rewind")

    def confirm(self, ctx, row, taken):
        self.calls.append("confirm")
        rows, complete = self._next()
        after = next((r for r in rows if r.name == "Wall" and r.kind == "wall" and not r.new and not r.free and r.cost == row.cost), None)
        return verified_in_panel(
            row.quantity, row.cost, taken,
            after.quantity if after else None, after.cost if after else None, after is not None, complete,
        )


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
        open_timeout_s=1.0, capture_dir=tmp_path, recovery=RecoveryConfig(settle_seconds=0.1),
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


def test_las_pulsaciones_de_anadir_van_a_un_ritmo_humano_y_no_fijo():
    """Entre pulsaciones seguidas pasan entre 0,24 y 0,34 s, sorteados en cada una."""
    task, ctx = make()  # lote de 4 muros: el contador está en 1 y se arma con tres «+1» seguidos
    run_task(task, ctx)
    adds = [a for a in ctx.device.actions if a.kind == "tap" and ADD1.contains(*a.args)]
    gaps = [round(b.at - a.at, 6) for a, b in zip(adds, adds[1:])]
    assert len(adds) == 3 and len(gaps) == 2
    assert all(0.24 - 1e-9 <= g <= 0.34 + 1e-9 for g in gaps), gaps
    assert gaps[0] != gaps[1]  # no es un valor fijo


def test_sin_boton_de_mas_diez_el_lote_se_arma_solo_con_mas_uno():
    """Con menos de 10 muros posibles el juego no ofrece «+10»: las pulsaciones calculadas van todas a «+1»."""
    def lone(n):
        return BatchBar(button(REMOVE), None, button(ADD1), (upgrade_button(UPGRADE, "gold"),), n)

    task, ctx = make(table={B1: lone(1), B2: lone(2), B3: lone(3), B4: lone(4)})
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and "4 muros" in result.reason
    assert len(tapped(ctx, ADD1)) == 3 and not tapped(ctx, ADD10)
    assert len(tapped(ctx, UPGRADE)) == 1 and len(tapped(ctx, OKAY)) == 1


def test_no_hay_pausas_fijas_solo_la_reaccion_humana_entre_lo_que_se_ve_y_lo_que_se_pulsa():
    """Entre pantalla y pulsación solo corre la reacción humana aleatoria (0,2-0,35 s): la visión no cuenta en el reloj
    simulado y no queda ninguna espera fija. Antes eran 0,8 s antes de «Upgrade» y 0,8 s antes de «Okay»."""
    task, ctx = make()
    run_task(task, ctx)
    at = {name: [a.at for a in ctx.device.actions if a.kind == "tap" and rect.contains(*a.args)]
          for name, rect in (("add", ADD1), ("upgrade", UPGRADE), ("okay", OKAY))}
    to_upgrade = at["upgrade"][0] - at["add"][-1]  # última pulsación de «+1» → «Upgrade»
    to_okay = at["okay"][0] - at["upgrade"][0]  # «Upgrade» → «Okay» (el modal sale al primer vistazo)
    assert 0.2 - 1e-9 <= to_upgrade <= 0.35 + 1e-9, to_upgrade
    assert 0.2 - 1e-9 <= to_okay <= 0.35 + 1e-9, to_okay
    assert to_upgrade != to_okay  # sorteadas por separado


def test_si_el_modal_tarda_la_espera_dura_lo_que_tarda_y_se_pulsa_okay_tras_reaccionar():
    """Con el modal retrasado, no se pulsa nada hasta que sale; luego, la reacción humana."""
    task, ctx = make()
    dev = ctx.device
    real_capture = dev.capture
    seen = {"polls": 0}

    def slow_capture():
        if dev.frame_index == MODAL:  # el juego tarda en mostrar el modal: tres capturas con la pantalla anterior
            seen["polls"] += 1
            if seen["polls"] <= 3:
                dev._index = B4
                frame = real_capture()
                dev._index = MODAL
                return frame
        return real_capture()

    dev.capture = slow_capture
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and len(tapped(ctx, OKAY)) == 1
    upgrade_at = [a.at for a in dev.actions if a.kind == "tap" and UPGRADE.contains(*a.args)][0]
    okay_at = [a.at for a in dev.actions if a.kind == "tap" and OKAY.contains(*a.args)][0]
    assert okay_at - upgrade_at >= 3 * 0.1 + 0.2 - 1e-9  # 3 sondeos de 0,1 s y la reacción mínima


def _wall(quantity, cost, y=300):
    return PanelRow("Wall", quantity, cost, "gold", False, False, None, True, "wall", None, (1000, y), y)


def test_una_sesion_hace_todas_las_filas_de_muros_sin_cerrar_el_panel():
    """Dos niveles de muro: se abre el panel una vez, se gasta el saldo que va quedando fila a fila y se cierra al final."""
    row1, row2 = _wall(4, 1000), _wall(10, 500, y=360)
    task, ctx = make(
        table=both_buttons(),
        # tras «Okay» el panel sigue abierto (se actualiza en directo); con un solo muro el lote ya está armado
        transitions=[route(B1, MODAL, UPGRADE_E), route(MODAL, PANEL, OKAY)] + BASE,
        village=FakeVillage(vstate(gold=3000, elixir=4500)),
        # antes; fila 1 sin aparecer (se mejoró entera); fila 2 con 5 muros menos
        readings=[([row1, row2], True), ([row2], True), ([replace(row2, quantity=5)], True)],
    )
    planned = collect(ctx, WallBatchPlanned)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and result.reason == "9 muros mejorados (5 con elixir; 4 con gold)", result.reason
    # fila 1: elixir 4 (de 4500); fila 2 con lo que queda: elixir 500 // 500 = 1 y oro (3000 - colchón 1000) // 500 = 4
    assert [(p.resource, p.count, p.unit_cost) for p in planned] == [
        ("elixir", 4, 1000), ("elixir", 1, 500), ("gold", 4, 500),
    ]
    assert len(tapped(ctx, BUILD)) == 1  # el panel se abrió una vez
    assert len(tapped(ctx, UPGRADE_E)) == 2 and len(tapped(ctx, UPGRADE)) == 1 and len(tapped(ctx, OKAY)) == 3
    assert task.scanner.calls == ["scan", "confirm", "confirm"]  # una lectura al principio y una comprobación por fila


SINGLE_UPGRADE = Rect(580, 570, 90, 70)  # «Upgrade» directo del menú de un muro suelto (a 720 de alto)
CONFIRM = Rect(797, 576, 204, 97)  # «Confirm» del diálogo «Upgrade Wall to Level N?»
SINGLE = [
    route(SINGLE_MENU, SINGLE_DIALOG, SINGLE_UPGRADE), route(SINGLE_DIALOG, PANEL, CONFIRM),
    route(PANEL, SINGLE_MENU, PANEL_ROWS), route(VILLAGE, PANEL, BUILD), route(PANEL, VILLAGE, PANEL_X),
]


def test_un_muro_suelto_se_mejora_con_su_upgrade_directo_y_el_confirm_del_dialogo():
    """Con un solo muro no hay lote («Upgrade More» apagado): fila → «Upgrade» del menú → «Upgrade Wall to Level 3?» → Confirm."""
    task, ctx = make(
        transitions=SINGLE, village=FakeVillage(vstate(gold=5000, elixir=0)),  # colchón 1000: caben 4000 de oro
        readings=[([_wall(1, 2500)], True), ([], True)],  # el muro suelto se mejora y su fila desaparece
    )
    steps, planned = collect(ctx, WallBatchStep), collect(ctx, WallBatchPlanned)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and result.reason == "1 muros mejorados con gold", result.reason
    assert [(p.resource, p.count, p.unit_cost) for p in planned] == [("gold", 1, 2500)]
    assert [s.action for s in steps] == ["select_row", "upgrade_single", "confirm_single"]
    assert tapped(ctx, SINGLE_UPGRADE) and tapped(ctx, CONFIRM)
    assert not tapped(ctx, UPGRADE_MORE) and not tapped(ctx, ADD1)  # no hay lote
    assert len(tapped(ctx, BUILD)) == 1  # el panel se abrió una vez


def test_los_muros_sueltos_van_los_ultimos_para_no_cortar_las_filas_con_lote():
    single, normal = _wall(1, 2500), _wall(4, 1000, y=360)  # en el panel el suelto sale antes, pero se hace después
    assert [r.quantity for r in WallBatchTask._wall_rows([single, normal])] == [4, 1]


def test_el_muro_suelto_no_se_confirma_si_el_boton_cuesta_otra_cosa_que_la_fila():
    task, ctx = make(
        transitions=SINGLE, village=FakeVillage(vstate(gold=50000, elixir=0)),
        readings=[([_wall(1, 1000)], True)],  # la fila dice 1000 y el diálogo, 2500
    )
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "cuesta 2500" in result.reason
    assert not tapped(ctx, CONFIRM)


def test_el_muro_suelto_sin_dinero_no_se_pulsa():
    task, ctx = make(
        transitions=SINGLE, village=FakeVillage(vstate(gold=3000, elixir=0)),  # tras el colchón (1000) quedan 2000 < 2500
        readings=[([_wall(1, 2500)], True)],
    )
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and not tapped(ctx, PANEL_ROWS)


def test_si_sobra_dinero_tras_la_pasada_se_relee_el_panel_y_se_sigue():
    """Lo mejorado puede dejar muros nuevos en una fila de arriba: una segunda pasada los recoge."""
    row = _wall(2, 1000)
    task, ctx = make(
        table=both_buttons(), transitions=[route(B1, MODAL, UPGRADE_E), route(MODAL, PANEL, OKAY)] + BASE,
        village=FakeVillage(vstate(gold=100000, elixir=100000)),
        readings=[([row], True), ([], True), ([], True)],  # tras el lote la fila desaparece; en la relectura no hay más muros
    )
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and "2 muros mejorados" in result.reason
    assert task.scanner.calls == ["scan", "confirm", "rewind", "scan"]  # sobra dinero: vuelve al principio y relee
    assert len(tapped(ctx, BUILD)) == 1


def test_sin_dinero_para_mas_no_se_relee_el_panel():
    row = _wall(4, 1000)
    task, ctx = make(
        table=both_buttons(), transitions=[route(B1, MODAL, UPGRADE_E), route(MODAL, PANEL, OKAY)] + BASE,
        village=FakeVillage(vstate(gold=1000, elixir=4000)),  # el elixir da justo para los 4 y el oro no pasa del colchón
        readings=[([row], True), ([], True)],
    )
    run_task(task, ctx)
    assert task.scanner.calls == ["scan", "confirm"]


def test_el_lote_nunca_supera_lo_calculado():
    task, ctx = make(village=FakeVillage(vstate(gold=3500)))  # (3500 - 1000) // 1000 = 2
    result = run_task(task, ctx)
    assert len(tapped(ctx, ADD1)) == 1  # de 1 a 2
    assert result.outcome is Outcome.NOTHING_TO_DO  # el panel «después» (22) no cuadra con un lote de 2
    assert len(tapped(ctx, OKAY)) == 1


def test_modo_de_prueba_decide_y_no_pulsa_nada_de_seleccion():
    cfg = WallConfig(dry_run=True, recovery=RecoveryConfig(settle_seconds=0.1))
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
    config = WallConfig(
        settings=WallSettings(reserve_builder_for_walls=False),
        open_timeout_s=1.0, recovery=RecoveryConfig(settle_seconds=0.1),
    )
    task, ctx = make(inputs=lambda c: WallInputs(gold_floor=1000, upgrades_pending=True), config=config)
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
                     recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(config=cfg)
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO and taps(ctx) == []


# --------------------------------------------------------------------------- R2: botones activos


def test_botones_de_anadir_apagados_no_cambian_nada_y_el_lote_queda_en_el_maximo():  # E1R2
    """Las pulsaciones se calculan de antemano (3 × «+1»): con solo 2 muros posibles, el botón se apaga tras la
    primera y las demás no hacen nada (en el juego real un botón apagado no responde). Se lee, se ve el máximo
    y se confirma con 2; el «+10» apagado no se pulsa nunca."""
    table = {B1: bar(1), B2: bar(2, add10=False, add1=False)}
    no_third = [t for t in BASE if not (t.source == B2 and t.target == B3)]  # el «+1» apagado no pasa a B3
    task, ctx = make(table=table, transitions=no_third, readings=[([WALL_ROW], True), ([_row(24)], True)])
    result = run_task(task, ctx)
    assert not tapped(ctx, ADD10) and len(tapped(ctx, ADD1)) == 3  # 3 calculadas, solo la primera surte efecto
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


def test_si_la_fila_se_agota_otra_fila_wall_de_otro_nivel_no_se_toma_por_ella():
    """En el panel real hay una fila «Wall» por nivel (`×100 · 25 000`, `×25 · 500`): al agotarse la primera,
    la siguiente no es «la misma fila con menos muros»."""
    four = _row(4)
    other_level = PanelRow("Wall", 25, 500, "gold", False, False, None, True, "wall", None, (1000, 420), 420)
    task, ctx = make(readings=[([four], True), ([other_level], True)])
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and "4 muros" in result.reason


def test_si_otra_fila_wall_tiene_el_mismo_coste_que_la_del_lote_no_hay_verificacion_falsa():
    four = _row(4)
    same_cost_other_qty = PanelRow("Wall", 25, 1000, "gold", False, False, None, True, "wall", None, (1000, 420), 420)
    task, ctx = make(readings=[([four], True), ([same_cost_other_qty], True)])
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO  # misma fila, 25 ≠ 4 − 4: no se da por verificada


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
    cfg = WallConfig(settings=WallSettings(preference=Preference.GOLD_FIRST),
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
    assert not ctx.cooldowns.ready("walls:no_button:elixir:1000")  # por recurso y nivel de muro (coste unitario)


def test_sin_boton_de_elixir_se_sigue_con_oro_en_la_misma_barra_sin_releer_el_panel():
    # Hay oro por encima del colchón: los muros no han cambiado, solo se rehace la cuenta con el oro.
    task, ctx = make(village=FakeVillage(vstate(gold=5000, elixir=4500)))
    warnings, planned = collect(ctx, Warning), collect(ctx, WallBatchPlanned)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and result.reason == "4 muros mejorados con gold"
    assert len(tapped(ctx, UPGRADE)) == 1 and not tapped(ctx, UPGRADE_E) and len(tapped(ctx, OKAY)) == 1
    # El panel se abre UNA vez y se verifica en el sitio, sin cerrarlo ni reabrirlo; una sola fila pulsada.
    assert len(tapped(ctx, BUILD)) == 1 and len(tapped(ctx, PANEL_ROWS)) == 1
    assert len(tapped(ctx, UPGRADE_MORE)) == 1  # y la barra no se cerró ni se volvió a abrir
    assert any("sin botón de elixir" in w.message for w in warnings)
    assert planned[-1].resource == "gold" and planned[-1].count == 4  # (5000 − colchón 1000) // 1000
    assert not ctx.cooldowns.ready("walls:no_button:elixir:1000")  # y se apunta, para este nivel de muro, en las siguientes ventanas


def test_el_cambio_a_oro_respeta_lo_que_el_plan_con_oro_repartia():
    # Con elixir y oro, el plan original era [elixir, oro]; sin elixir, todo el lote va a oro y no se repite el segundo.
    task, ctx = make(village=FakeVillage(vstate(gold=5000, elixir=4500)))
    run_task(task, ctx)
    assert len(tapped(ctx, UPGRADE)) == 1 and len(tapped(ctx, OKAY)) == 1


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


def _wall_at(level, cost=5000, quantity=28):
    return PanelRow("Wall", quantity, cost, "gold", False, False, None, True, "wall", None, (1000, 300), 300, level=level)


def _dry_with_elixir_rich(row):
    cfg = WallConfig(dry_run=True, recovery=RecoveryConfig(settle_seconds=0.1))
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
    assert planned[0].resource == "elixir"  # primero el elixir; el oro que sobre va en un segundo lote


def test_la_fila_de_muros_nuevos_gratis_no_se_toma_por_el_lote_de_mejora():
    """En el panel real «Wall ×50 · gratis» (edificios nuevos) sale ANTES que las mejoras: no es un lote."""
    free_new = PanelRow("Wall", 50, None, None, True, True, None, True, "wall", "other", (1000, 200), 200)
    result, planned = _dry_with_elixir_rich_rows([free_new, _wall_at(5, cost=20000)])
    assert result.outcome is Outcome.DONE and planned[-1].unit_cost == 20000


def _dry_with_elixir_rich_rows(rows):
    cfg = WallConfig(dry_run=True, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(
        config=cfg, village=FakeVillage(vstate(gold=30000, elixir=100000)), readings=[(rows, True)],
        inputs=lambda c: WallInputs(gold_floor=0, upgrades_pending=False),
    )
    planned = collect(ctx, WallBatchPlanned)
    return run_task(task, ctx), planned


def _wall_state(gold, elixir, floor=26_600):
    return WallState((gold, elixir, 0), 1, floor, False)


def test_el_plan_gasta_primero_elixir_y_luego_oro_con_lo_que_queda():
    plan = WallBatchTask._plan(_wall_state(2_000_000, 100_000), WallSettings(), _wall_at(7, cost=25_000, quantity=100))
    assert plan == [("elixir", 4), ("gold", 78)]  # oro: (2 000 000 − 26 600) // 25 000 = 78 (caben en los 96 que quedan)


def test_el_plan_no_pasa_de_los_muros_que_hay_en_la_fila():
    plan = WallBatchTask._plan(_wall_state(2_000_000, 100_000), WallSettings(), _wall_at(7, cost=25_000, quantity=5))
    assert plan == [("elixir", 4), ("gold", 1)]
    assert sum(n for _, n in plan) <= 5


def test_el_plan_salta_el_recurso_que_no_da_para_un_muro():
    plan = WallBatchTask._plan(_wall_state(2_000_000, 11_783), WallSettings(), _wall_at(7, cost=25_000, quantity=100))
    assert plan == [("gold", 78)]


def test_el_plan_de_niveles_2_a_4_es_solo_oro():
    plan = WallBatchTask._plan(_wall_state(2_000_000, 500_000), WallSettings(), _wall_at(3, cost=25_000, quantity=100))
    assert plan == [("gold", 78)]  # con 500 000 de elixir de sobra, el elixir no sirve para estos niveles


def test_el_plan_sin_colchon_de_oro_solo_usa_elixir():
    plan = WallBatchTask._plan(_wall_state(2_000_000, 100_000, floor=None), WallSettings(), _wall_at(7, cost=25_000))
    assert [r for r, _ in plan] == ["elixir"]


def test_si_el_elixir_no_da_para_un_muro_se_prueba_con_el_oro():
    """Elixir primero con 11 783 y un muro de 25 000: la política lo elige (> 0) pero no cabe ni uno; el oro sí."""
    cfg = WallConfig(dry_run=True, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(
        config=cfg, village=FakeVillage(vstate(gold=2_000_000, elixir=11_783)), readings=[([_wall_at(7, cost=25_000)], True)],
        inputs=lambda c: WallInputs(gold_floor=26_600, upgrades_pending=False),
    )
    planned = collect(ctx, WallBatchPlanned)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE and planned[-1].resource == "gold" and planned[-1].count >= 1


def test_si_ningun_recurso_da_para_un_muro_no_hace_nada():
    cfg = WallConfig(dry_run=True, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(
        config=cfg, village=FakeVillage(vstate(gold=30_000, elixir=11_783)), readings=[([_wall_at(7, cost=25_000)], True)],
        inputs=lambda c: WallInputs(gold_floor=26_600, upgrades_pending=False),  # oro gastable: 3 400
    )
    assert run_task(task, ctx).outcome is Outcome.NOTHING_TO_DO


def test_con_nivel_desconocido_no_se_veta_el_elixir():
    """Sin lectura del nivel manda el botón del juego: el veto nunca se aplica por dudas."""
    _, planned = _dry_with_elixir_rich(_wall_at(None))
    assert planned[0].resource == "elixir"


def test_nivel_bajo_sin_oro_gastable_no_hace_nada_y_lo_explica():
    cfg = WallConfig(dry_run=True, recovery=RecoveryConfig(settle_seconds=0.1))
    task, ctx = make(
        config=cfg, village=FakeVillage(vstate(gold=30000, elixir=100000)), readings=[([_wall_at(3)], True)],
        inputs=lambda c: WallInputs(gold_floor=None, upgrades_pending=False),  # oro sin colchón conocido: no se gasta
    )
    result = run_task(task, ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "nivel 3" in result.reason
