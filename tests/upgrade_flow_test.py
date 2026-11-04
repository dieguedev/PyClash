"""Flujo genérico de mejora (issue #9): costura de tarea sobre dispositivo falso y capturas reales.

El panel y los diálogos son capturas reales anonimizadas; los toques mueven un `FakeDevice` entre
ellas. El contador de constructores y el HUD se inyectan (`FakeVillage`): no hay captura de la aldea
justo después de confirmar. Faltan capturas de héroes y de una fila en rojo ya pulsada: ver
docs/verificacion-manual-flujo-mejora.md.
"""

import random
from dataclasses import replace

import pytest

from pyclash.device.base import Capabilities, Rect
from pyclash.device.fake import FakeDevice, Transition
from pyclash.policy.upgrades import Fallback, Mode, UpgradeSettings
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.runtime.events import CaptureSaved, ImprovementStarted, UpgradePlanned, Warning
from pyclash.screens.upgrade_dialog import DialogReader
from pyclash.screens.upgrade_panel import PanelReader, PanelRow, PanelScan, PanelScanner
from pyclash.screens.village import VillageState
from pyclash.tasks.base import Outcome, TaskResult, run_task
from pyclash.tasks.recovery import RecoveryConfig
from pyclash.tasks.upgrade import MenuDefinition, UpgradeConfig, UpgradeTask, VILLAGE_MENU
from pyclash.vision.base import MetadataVision

from helpers import fixture_frame
from upgrade_panel_test import CachingReader

VILLAGE, PANEL, DIALOG, MISSING, WALL_MENU, WALL_DIALOG, REBUILD = range(7)
FRAMES = ["main_village", "upgrade_panel_filtered", "upgrade_dialog", "missing_gems", "wall_selected",
          "wall_upgrade_dialog", "rebuild_clan_castle"]

BUILD = Rect(1140, 640, 140, 80)
PANEL_ROWS = Rect(941, 72, 339, 560)
PANEL_X = Rect(1200, 0, 80, 70)
DIALOG_X = Rect(1090, 20, 80, 70)
CONFIRM = Rect(797, 587, 202, 86)
WALL_UPGRADE = Rect(560, 570, 120, 80)
MISSING_X = Rect(820, 205, 80, 70)
REBUILD_X = Rect(960, 60, 90, 70)
ENTER_SHOP = Rect(545, 404, 198, 83)


class CachingDialogs(DialogReader):
    """Memoriza lecturas por contenido de la captura: las pruebas ven las mismas imágenes una y otra vez."""

    _memo: dict = {}

    def _cached(self, name, image, *args):
        key = (name, hash(image.tobytes()), args)
        if key not in self._memo:
            self._memo[key] = getattr(super(), name)(image, *args)
        return self._memo[key]

    def title(self, image, expected):
        return self._cached("title", image, expected)

    def missing(self, image):
        return self._cached("missing", image)

    def confirm_buttons(self, image):
        return self._cached("confirm_buttons", image)

    def wall_upgrade(self, image):
        return self._cached("wall_upgrade", image)


class SinglePageScanner(PanelScanner):
    """Lee la captura actual sin arrastrar (el recorrido ya tiene sus pruebas en test_upgrade_panel)."""

    def scan(self, ctx, **kw):
        return PanelScan(tuple(self.reader.read(ctx.device.capture().image).rows), 0, True, None, "")


class FakeVillage:
    def __init__(self, *states):
        self.states = list(states)
        self.reads = 0

    def read_state_stable(self, ctx, *a, **k):
        self.reads += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def vstate(free, resources=(11000, 12000, 0), unreliable=()):
    return VillageState((free, 2) if free is not None else None, None, None, None, resources, frozenset(unreliable))


def build_ctx(transitions, start=VILLAGE, seed=1):
    clock = FakeClock()
    device = FakeDevice([fixture_frame(n) for n in FRAMES], clock, transitions, Capabilities(swipe=True))
    device._index = start
    ctx = Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(seed))
    ctx.gold_reserve.update(1, 1, clock.now())  # reserva pequeña ya leída: estas pruebas no tratan de la reserva (#32)
    return ctx


def route(source, target, region=None):
    return Transition(source, "tap", target, region)


BASE = [
    route(VILLAGE, PANEL, BUILD),
    route(PANEL, VILLAGE, PANEL_X),
    route(DIALOG, PANEL, DIALOG_X),
    route(MISSING, VILLAGE, MISSING_X),
    route(WALL_MENU, VILLAGE, Rect(0, 0, 1280, 60)),
    route(WALL_DIALOG, PANEL, DIALOG_X),
    route(REBUILD, PANEL, REBUILD_X),
]


def make_task(village, settings, tmp_path=None, rows=None, **kw):
    cfg = UpgradeConfig(
        settings=settings, game_sort=False, settle_s=0.1, verify_interval_s=0.1, capture_dir=tmp_path,
        recovery=RecoveryConfig(settle_seconds=0.1), **kw,
    )
    scanner = SinglePageScanner(CachingReader())
    if rows is not None:
        scanner = type("Fixed", (), {"scan": lambda self, ctx, **k: PanelScan(tuple(rows), 0, True, None, "")})()
    return UpgradeTask(cfg, scanner=scanner, dialogs=CachingDialogs(), village=village)


def taps(ctx):
    return [a.args for a in ctx.device.actions if a.kind == "tap"]


def settings(*names, **kw):
    kw.setdefault("fallback", Fallback.NONE)
    return UpgradeSettings(priorities=(tuple(names),) if names else (), **kw)


def collect(ctx, kind):
    seen = []
    ctx.events.subscribe(kind, seen.append)
    return seen


# --------------------------------------------------------------------------- feliz

def test_archer_tower_is_started_and_verified_by_the_builder_counter():  # E1R2
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    started = collect(ctx, ImprovementStarted)
    village = FakeVillage(vstate(2), vstate(2), vstate(1))
    result = run_task(make_task(village, settings("Archer Tower")), ctx)
    assert result.outcome is Outcome.DONE
    assert [e.name for e in started] == ["Archer Tower"]
    assert any(CONFIRM.contains(*t) for t in taps(ctx))


def test_the_confirm_tap_lands_on_the_green_button_not_on_the_magic_item_one():
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    run_task(make_task(FakeVillage(vstate(2), vstate(2), vstate(1)), settings("Archer Tower")), ctx)
    hammer = Rect(450, 585, 200, 88)  # «Confirm / Hammer of Building», a la izquierda del verde
    assert not any(hammer.contains(*t) for t in taps(ctx))


def test_nothing_to_do_when_there_is_no_free_builder_above_the_reserve():
    ctx = build_ctx(BASE)
    result = run_task(make_task(FakeVillage(vstate(1)), settings("Archer Tower")), ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "reserva" in result.reason
    assert taps(ctx) == []  # ni siquiera se abre el menú


# --------------------------------------------------------------------------- R1: gasto dudoso

def test_a_dialog_that_names_another_building_is_never_confirmed():  # E3R1
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS)])  # se pulsa «Cannon», sale «Archer Tower»
    warnings = collect(ctx, Warning)
    task = make_task(FakeVillage(vstate(2)), settings("Cannon"))
    result = run_task(task, ctx)
    assert not any(CONFIRM.contains(*t) for t in taps(ctx))
    assert result.outcome is Outcome.NOTHING_TO_DO
    assert not ctx.cooldowns.ready("upgrade:village:Cannon")
    assert any("Cannon" in w.message for w in warnings)


def test_a_gems_dialog_is_closed_and_blocks_the_task_without_going_to_the_shop():  # E2R1
    ctx = build_ctx(BASE + [route(PANEL, MISSING, PANEL_ROWS)])
    result = run_task(make_task(FakeVillage(vstate(2)), settings("Archer Tower")), ctx)
    assert result.outcome is Outcome.BLOCKED and "gemas" in result.reason
    assert not any(ENTER_SHOP.contains(*t) for t in taps(ctx))
    assert ctx.device.frame_index == VILLAGE


def test_a_button_whose_cost_differs_from_the_row_is_not_pressed():
    rows = [PanelRow("Wall", 26, 1500, "gold", False, False, None, True, "wall", None, (1000, 300), 300)]
    ctx = build_ctx(BASE + [route(PANEL, WALL_DIALOG, PANEL_ROWS)])
    run_task(make_task(FakeVillage(vstate(2)), settings("Wall"), rows=rows), ctx)
    assert not any(CONFIRM.contains(*t) for t in taps(ctx))


def test_a_button_spending_another_resource_is_not_pressed():
    rows = [PanelRow("Archer Tower", 1, 2000, "elixir", False, False, None, True, "upgrade", None, (1000, 300), 300)]
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS)])
    run_task(make_task(FakeVillage(vstate(2)), settings("Archer Tower"), rows=rows), ctx)
    assert not any(CONFIRM.contains(*t) for t in taps(ctx))


def test_red_rows_are_never_selected():
    rows = [PanelRow("Archer Tower", 1, 2000, "gold", False, False, None, False, "upgrade", None, (1000, 300), 300)]
    ctx = build_ctx(BASE)
    result = run_task(make_task(FakeVillage(vstate(2)), settings("Archer Tower"), rows=rows), ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO
    assert not any(PANEL_ROWS.contains(*t) for t in taps(ctx))


# --------------------------------------------------------------------------- R2: verificación

def test_counter_unchanged_is_not_a_start_and_the_row_cools_down_with_a_capture(tmp_path):  # E2R2
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    started, saved = collect(ctx, ImprovementStarted), collect(ctx, CaptureSaved)
    result = run_task(make_task(FakeVillage(vstate(2)), settings("Archer Tower"), tmp_path), ctx)
    assert started == [] and result.outcome is Outcome.NOTHING_TO_DO
    assert not ctx.cooldowns.ready("upgrade:village:Archer Tower")
    assert saved and list(tmp_path.glob("upgrade-*.png"))


def test_unreliable_counter_does_not_verify():
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    village = FakeVillage(vstate(2), vstate(2), vstate(1, unreliable=("builders",)))
    started = collect(ctx, ImprovementStarted)
    run_task(make_task(village, settings("Archer Tower")), ctx)
    assert started == []


def test_clan_castle_rebuild_is_verified_by_the_elixir_not_by_the_builder_counter():
    ctx = build_ctx(BASE + [route(PANEL, REBUILD, PANEL_ROWS), route(REBUILD, VILLAGE, Rect(740, 535, 199, 82))])
    started = collect(ctx, ImprovementStarted)
    village = FakeVillage(vstate(2, (11000, 12000, 0)), vstate(2, (11000, 12000, 0)), vstate(2, (11000, 1500, 0)))
    result = run_task(make_task(village, settings("Clan Castle")), ctx)
    assert result.outcome is Outcome.DONE and [e.name for e in started] == ["Clan Castle"]


def test_clan_castle_rebuild_without_an_elixir_drop_is_not_registered():
    ctx = build_ctx(BASE + [route(PANEL, REBUILD, PANEL_ROWS), route(REBUILD, VILLAGE, Rect(740, 535, 199, 82))])
    started = collect(ctx, ImprovementStarted)
    run_task(make_task(FakeVillage(vstate(2, (11000, 12000, 0))), settings("Clan Castle")), ctx)
    assert started == []


# --------------------------------------------------------------------------- R3: Dry run

def test_dry_run_says_what_it_would_do_and_touches_neither_row_nor_confirm():  # E1R3
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    planned = collect(ctx, UpgradePlanned)
    result = run_task(make_task(FakeVillage(vstate(2)), settings("Archer Tower", mode=Mode.DRY_RUN)), ctx)
    assert result.outcome is Outcome.DONE and "dry run" in result.reason
    assert [p.name for p in planned] == ["Archer Tower"] and "prioridad 1" in planned[0].reason
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))
    assert ctx.device.frame_index == VILLAGE


# --------------------------------------------------------------------------- R4: un solo flujo

def test_wall_goes_through_the_bottom_menu_then_the_detail_dialog():  # E2R4
    ctx = build_ctx(BASE + [
        route(PANEL, WALL_MENU, PANEL_ROWS),
        route(WALL_MENU, WALL_DIALOG, WALL_UPGRADE),
        route(WALL_DIALOG, VILLAGE, CONFIRM),
    ])
    started = collect(ctx, ImprovementStarted)
    result = run_task(make_task(FakeVillage(vstate(2), vstate(2), vstate(1)), settings("Wall")), ctx)
    assert result.outcome is Outcome.DONE and [e.name for e in started] == ["Wall"]
    sequence = [("wall", t) for t in taps(ctx) if WALL_UPGRADE.contains(*t)]
    assert len(sequence) == 1  # «Upgrade», no «Upgrade More»


def test_a_wall_menu_that_never_reaches_a_dialog_stops_after_two_presses():
    ctx = build_ctx(BASE + [route(PANEL, WALL_MENU, PANEL_ROWS)])  # el botón no hace nada
    run_task(make_task(FakeVillage(vstate(2)), settings("Wall")), ctx)
    assert sum(1 for t in taps(ctx) if WALL_UPGRADE.contains(*t)) == 1
    assert not any(CONFIRM.contains(*t) for t in taps(ctx))


def test_a_second_menu_is_only_configuration_of_the_same_flow():  # E1R4
    other = MenuDefinition("lab", VILLAGE_MENU.open_button, panel="lab", categories=("army_buildings",))
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    task = make_task(FakeVillage(vstate(2), vstate(2), vstate(1)), settings("Archer Tower"), menu=other)
    assert task.config.menu.name == "lab"
    assert run_task(task, ctx).outcome is Outcome.DONE
    assert "upgrade:lab:" not in "".join(ctx.cooldowns._until)


# --------------------------------------------------------------------------- parada

def test_stop_in_the_middle_of_the_flow_presses_nothing_more():
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])

    class StopAfterScan(SinglePageScanner):
        def scan(self, c, **kw):
            result = super().scan(c, **kw)
            c.stop.request()
            return result

    task = make_task(FakeVillage(vstate(2)), settings("Archer Tower"))
    task.scanner = StopAfterScan(CachingReader())
    result = run_task(task, ctx)
    assert result.outcome is Outcome.STOPPED
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))


# --------------------------------------------------------------------------- propiedad

class AdversarialDevice(FakeDevice):
    """Sigue el flujo real, pero cada pulsación de fila (o del menú del muro) abre un diálogo cualquiera
    de los capturados, incluidos los que no corresponden a la fila: así se prueba la cadena de seguridad
    con todo lo que el juego puede poner delante, no solo con lo esperado."""

    SCREENS = (DIALOG, MISSING, WALL_MENU, WALL_DIALOG, REBUILD, PANEL, VILLAGE)

    def __init__(self, frames, clock, rng, transitions):
        super().__init__(frames, clock, transitions, Capabilities(swipe=True))
        self._rng = rng
        self.taps = []

    def tap(self, x, y):
        self.taps.append((self._index, x, y))
        opens = (self._index == PANEL and PANEL_ROWS.contains(x, y)) or self._index == WALL_MENU
        if opens and not any(r.contains(x, y) for r in (PANEL_X,)):
            self.actions.append(type("A", (), {"kind": "tap", "args": (x, y), "at": 0})())
            self._index = self._rng.choice(self.SCREENS)
            return
        super().tap(x, y)


FORBIDDEN = {DIALOG: [CONFIRM], WALL_DIALOG: [CONFIRM], REBUILD: [Rect(740, 535, 199, 82)],
             MISSING: [ENTER_SHOP], WALL_MENU: [WALL_UPGRADE]}


def run_adversarial(seed, *names, rows=None):
    clock = FakeClock()
    rng = random.Random(seed)
    device = AdversarialDevice([fixture_frame(n) for n in FRAMES], clock, rng, BASE)
    ctx = Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(seed))
    ctx.gold_reserve.update(1, 1, clock.now())
    task = make_task(FakeVillage(vstate(2)), settings(*names), rows=rows, max_attempts=4)
    run_task(task, ctx)
    return device


@pytest.mark.parametrize("seed", range(16))
def test_no_dialog_sequence_makes_a_forbidden_confirm_tap_for_a_row_nobody_names(seed):
    """Se elige «Cannon»: ningún diálogo capturado lo nombra, así que ningún botón verde, la tienda ni el
    «Upgrade» del muro pueden recibir un toque, abra el juego lo que abra tras pulsar la fila."""
    device = run_adversarial(seed, "Cannon")
    assert device.taps, "la prueba no llegó a pulsar nada: no prueba nada"
    for index, x, y in device.taps:
        assert not any(r.contains(x, y) for r in FORBIDDEN.get(index, [])), (index, x, y)


@pytest.mark.parametrize("seed", range(8))
def test_for_a_row_that_is_named_only_its_own_dialog_is_confirmed(seed):
    device = run_adversarial(seed, "Archer Tower")
    allowed = {DIALOG}
    for index, x, y in device.taps:
        if index not in allowed:
            assert not any(r.contains(x, y) for r in FORBIDDEN.get(index, [])), (index, x, y)


def test_the_adversarial_property_detects_a_broken_title_check(monkeypatch):
    """Control de la propia prueba: sin comprobar el título, algún diálogo ajeno sí recibiría el toque."""
    from pyclash.screens.upgrade_dialog import DialogTitle

    monkeypatch.setattr(CachingDialogs, "title", lambda self, image, expected: DialogTitle("upgrade", expected, 1.0))
    caught = 0
    for seed in range(16):
        device = run_adversarial(seed, "Cannon")
        caught += any(any(r.contains(x, y) for r in FORBIDDEN.get(i, [])) for i, x, y in device.taps)
    assert caught > 0


# --------------------------------------------------------------------------- reserva de oro (#32)

class StubCostReader:
    """Sustituye a ReadSearchCost: anota las llamadas y, si `cost`, deja la reserva leída."""

    def __init__(self, cost=None):
        self.cost, self.calls = cost, 0

    def run(self, ctx):
        self.calls += 1
        if self.cost is None:
            return TaskResult.failed("sin lectura")
        ctx.gold_reserve.update(self.cost, self.cost, ctx.clock.now())
        return TaskResult.done()


def archer_tower_task(reader):
    task = make_task(FakeVillage(vstate(2), vstate(2), vstate(1)), settings("Archer Tower"))
    task.cost_reader = reader
    return task


def test_sesion_nueva_lee_el_coste_antes_de_decidir_sobre_oro():  # R2
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    ctx.gold_reserve.costs = None
    reader = StubCostReader(cost=1)
    result = run_task(archer_tower_task(reader), ctx)
    assert reader.calls == 1 and result.outcome is Outcome.DONE


def test_coste_con_mas_de_60_minutos_se_vuelve_a_leer():  # R2
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    ctx.clock.sleep(3601)
    reader = StubCostReader(cost=1)
    run_task(archer_tower_task(reader), ctx)
    assert reader.calls == 1


def test_coste_reciente_no_se_vuelve_a_leer():
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    reader = StubCostReader(cost=1)
    run_task(archer_tower_task(reader), ctx)
    assert reader.calls == 0


def test_sin_constructor_libre_no_se_lee_el_coste():
    ctx = build_ctx(BASE)
    ctx.gold_reserve.costs = None
    reader = StubCostReader(cost=1)
    task = make_task(FakeVillage(vstate(1)), settings("Archer Tower"))
    task.cost_reader = reader
    run_task(task, ctx)
    assert reader.calls == 0 and taps(ctx) == []


def test_si_la_lectura_falla_no_se_gasta_oro():  # R2
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    ctx.gold_reserve.costs = None
    reader = StubCostReader(cost=None)
    started = collect(ctx, ImprovementStarted)
    result = run_task(archer_tower_task(reader), ctx)
    assert reader.calls == 1  # un solo intento por pasada
    assert result.outcome is not Outcome.DONE and started == []


def test_la_reserva_alta_impide_una_mejora_de_oro_que_cabria_sin_ella():  # R1
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    ctx.gold_reserve.update(10_000, 10_000, ctx.clock.now())  # umbral 760 000: no cabe con 11 000 de oro
    started = collect(ctx, ImprovementStarted)
    result = run_task(archer_tower_task(StubCostReader()), ctx)
    assert started == [] and result.outcome is not Outcome.DONE


# --------------------------------------------------------------------------- solo el filtro del juego

def test_without_the_game_filter_nothing_is_started(monkeypatch):
    monkeypatch.setattr("pyclash.tasks.upgrade.apply_sort", lambda ctx, order: False)
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    task = make_task(FakeVillage(vstate(2)), settings("Archer Tower"))
    task.config = replace(task.config, game_sort=True)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.BLOCKED and "filtro del juego" in result.reason
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))


def test_an_unverified_game_order_also_stops_the_task(monkeypatch):
    """El filtro «se pulsó» pero el panel no quedó ordenado por coste: tampoco se actúa."""
    monkeypatch.setattr("pyclash.tasks.upgrade.apply_sort", lambda ctx, order: True)
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS), route(DIALOG, VILLAGE, CONFIRM)])
    task = make_task(FakeVillage(vstate(2)), settings("Archer Tower"))
    task.config = replace(task.config, game_sort=True)
    task.scanner = type("Unsorted", (SinglePageScanner,), {"scan": lambda self, c, **k: PanelScan((), 0, True, False, "desordenado")})(CachingReader())
    result = run_task(task, ctx)
    assert result.outcome is Outcome.BLOCKED and "desordenado" in result.reason


def test_with_the_game_order_the_first_row_wins_and_the_town_hall_never_does():
    from pyclash.policy.upgrades import Fallback, UpgradeState, decide_upgrade

    hall = PanelRow("Town Hall", 1, 75000, "gold", False, False, 60, True, "upgrade", None, (10, 10), 10)
    first = replace(hall, name="Barracks", cost=60000, resource="elixir", center=(10, 60), y=60)
    other = replace(hall, name="Cannon", cost=900, center=(10, 110), y=110)
    st = UpgradeState((hall, first, other), (99999, 99999, 0), 2, 0, game_ordered=True)
    # sin prioridades ni alternativa: manda el orden del juego
    got = decide_upgrade(st, UpgradeSettings(fallback=Fallback.NONE), random.Random(0))
    assert got.chosen is first
    # aunque haya una prioridad que apunte a otra fila
    got = decide_upgrade(st, UpgradeSettings(priorities=(("Cannon",),)), random.Random(0))
    assert got.chosen is first


def test_with_the_game_filter_only_the_first_page_is_read_and_nothing_scrolls(monkeypatch):
    monkeypatch.setattr("pyclash.tasks.upgrade.apply_sort", lambda ctx, order: True)
    task = make_task(FakeVillage(vstate(2)), settings("Archer Tower"))
    task.config = replace(task.config, game_sort=True)
    assert task._scan_options() == {} # aún sin aplicar
    task._sort_applied = True
    assert task._scan_options()["max_steps"] == 0


# --------------------------------------------------------------------------- #36: Maxer/Rusher y TH9 maxeado

def _th_row(name="Town Hall", cost=1000, **kw):
    from upgrade_policy_test import row

    return row(name, cost, **kw)  # el Ayuntamiento lleva por defecto una insignia «5» legible


def test_dry_run_reports_every_rejection_with_its_reason_and_the_th_readings(monkeypatch):
    from pyclash.runtime.events import UpgradeDecided

    monkeypatch.setattr("pyclash.tasks.upgrade.read_town_hall_level", lambda image: 6)
    monkeypatch.setattr("pyclash.tasks.upgrade.offers_town_hall_10", lambda image: False)
    ctx = build_ctx(BASE)
    decided = collect(ctx, UpgradeDecided)
    rows = [_th_row("Cannon", 500), _th_row("Army Camp", 400)]
    task = make_task(FakeVillage(vstate(2)), UpgradeSettings(mode=Mode.RUSHER, dry_run=True), rows=rows)
    result = run_task(task, ctx)
    assert result.outcome is Outcome.DONE
    assert decided[0].chosen == "Army Camp" and decided[0].th_level == 6 and decided[0].offers_th10 is False
    assert ("Cannon", "fuera de la lista del Rusher") in decided[0].rejected
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))


def test_th9_maxed_publishes_account_finished_and_presses_nothing(monkeypatch):
    from pyclash.runtime.events import AccountFinished

    monkeypatch.setattr("pyclash.tasks.upgrade.read_town_hall_level", lambda image: 9)
    monkeypatch.setattr("pyclash.tasks.upgrade.offers_town_hall_10", lambda image: True)
    ctx = build_ctx(BASE)
    finished = collect(ctx, AccountFinished)
    result = run_task(make_task(FakeVillage(vstate(2)), UpgradeSettings(mode=Mode.RUSHER), rows=[_th_row("Town Hall", 9_000_000)]), ctx)
    assert len(finished) == 1 and "TH9 maxeado" in finished[0].reason
    assert result.outcome is Outcome.NOTHING_TO_DO and result.exhausted
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))


def test_unreadable_town_hall_level_never_finishes_the_account_nor_presses_the_town_hall():
    from pyclash.runtime.events import AccountFinished

    ctx = build_ctx(BASE)  # las capturas son de 1280×720: ni TH9 ni la tarjeta TH10 se pueden leer
    finished = collect(ctx, AccountFinished)
    run_task(make_task(FakeVillage(vstate(2)), UpgradeSettings(mode=Mode.RUSHER), rows=[_th_row("Town Hall", 9_000_000)]), ctx)
    assert finished == []
    assert not any(PANEL_ROWS.contains(*t) for t in taps(ctx))


def test_first_screen_only_is_confirmed_with_a_full_scan_before_finishing(monkeypatch):
    """Con el filtro del juego se lee una sola pantalla: antes de dar la cuenta por terminada se recorre el panel entero."""
    from pyclash.runtime.events import AccountFinished

    monkeypatch.setattr("pyclash.tasks.upgrade.read_town_hall_level", lambda image: 9)
    monkeypatch.setattr("pyclash.tasks.upgrade.offers_town_hall_10", lambda image: True)
    calls = []

    class Scanner:
        def scan(self, ctx, **kw):
            calls.append(kw)
            first = len(calls) == 1  # la primera lectura es de una pantalla; la segunda recorre el panel y no halla más
            return PanelScan((_th_row("Town Hall", 9_000_000),), 0 if first else 3, not first, None, "")

    ctx = build_ctx(BASE)
    finished = collect(ctx, AccountFinished)
    cfg = UpgradeConfig(settings=UpgradeSettings(mode=Mode.RUSHER), game_sort=False, settle_s=0.1,
                        recovery=RecoveryConfig(settle_seconds=0.1))
    task = UpgradeTask(cfg, scanner=Scanner(), dialogs=CachingDialogs(), village=FakeVillage(vstate(2)))
    run_task(task, ctx)
    assert len(calls) == 2 and "max_steps" not in calls[1]  # la segunda lectura recorre el panel entero
    assert len(finished) == 1


def test_town_hall_row_is_not_pressed_if_its_number_is_not_recognized_when_revealed(monkeypatch):
    """Antes de pulsar, la fila localizada de nuevo debe llevar un número reconocido (2-9); si no, es duda."""
    from dataclasses import replace

    monkeypatch.setattr("pyclash.tasks.upgrade.read_town_hall_level", lambda image: None)
    monkeypatch.setattr("pyclash.tasks.upgrade.offers_town_hall_10", lambda image: False)
    row = _th_row("Town Hall", 9_000_000)

    class Scanner:
        def scan(self, ctx, **kw):
            return PanelScan((row,), 0, True, None, "")

        def reveal(self, ctx, r):
            return replace(r, level=None)  # al volver a localizarla, el número ya no se reconoce

    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS)])
    cfg = UpgradeConfig(settings=UpgradeSettings(mode=Mode.RUSHER), game_sort=False, settle_s=0.1,
                        recovery=RecoveryConfig(settle_seconds=0.1))
    task = UpgradeTask(cfg, scanner=Scanner(), dialogs=CachingDialogs(), village=FakeVillage(vstate(2)))
    result = run_task(task, ctx)
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))
    assert result.outcome is not Outcome.DONE


def test_town_hall_row_without_a_recognized_number_is_never_chosen(monkeypatch):
    monkeypatch.setattr("pyclash.tasks.upgrade.read_town_hall_level", lambda image: 6)
    monkeypatch.setattr("pyclash.tasks.upgrade.offers_town_hall_10", lambda image: False)
    ctx = build_ctx(BASE + [route(PANEL, DIALOG, PANEL_ROWS)])
    run_task(make_task(FakeVillage(vstate(2)), UpgradeSettings(mode=Mode.RUSHER), rows=[_th_row("Town Hall", 9_000_000, unreadable=True)]), ctx)
    assert not any(PANEL_ROWS.contains(*t) or CONFIRM.contains(*t) for t in taps(ctx))
