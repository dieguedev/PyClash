"""Construir edificios nuevos (issue #10): política pura, lector de la fase de colocación y costura de
tarea sobre dispositivo falso con capturas reales anonimizadas.

Las capturas de colocación son reales (✓ verde, ✓ gris sin sitio); la aldea y el panel también. El contador
de constructores se inyecta (`FakeVillage`). No hay captura de la cadena Bomb ×2 ni del recoloque tras ✗:
ver docs/verificacion-manual-construir-nuevos.md.
"""

import random

import pytest

from pyclash.device.base import Capabilities, Rect
from pyclash.device.fake import FakeDevice, Transition
from pyclash.policy.build_new import (
    BuildSettings, BuildState, Reason, build_gate, by_cost, decide_build_new,
)
from pyclash.policy.upgrades import Order
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.runtime.events import CaptureSaved, ImprovementStarted, UpgradePlanned, Warning
from pyclash.screens.placement import read_placement
from pyclash.screens.upgrade_panel import PanelRow, PanelScan
from pyclash.tasks.base import Outcome, run_task
from pyclash.tasks.build_new import BuildNewConfig, BuildNewTask
from pyclash.tasks.recovery import RecoveryConfig
from pyclash.vision.base import MetadataVision

from helpers import fixture_frame
from test_upgrade_flow import CachingDialogs, FakeVillage, SinglePageScanner, collect, vstate
from test_upgrade_panel import CachingReader


def row(name, quantity=1, cost=100, resource="gold", free=False, new=True, duration=60, affordable=True,
        kind="new_building", y=100):
    return PanelRow(name, quantity, cost, resource, free, new, duration, affordable, kind, None, (1043, y), y)


LAB = row("Laboratory", 1, 5000, "elixir", y=92)
COLLECTOR = row("Elixir Collector", 1, 150, "gold", duration=5, y=184)
HUT = row("Builder's Hut", 1, 500, "gems", affordable=False, duration=None, y=241)
BOMB = row("Bomb", 2, 400, "gold", duration=None, y=378)
WALL_FREE = row("Wall", 24, None, None, free=True, duration=None, kind="wall", y=468)
UPGRADE = row("Army Camp", 2, 2000, "elixir", new=False, kind="upgrade", y=584)


# --------------------------------------------------------------------------- política (pura)

def decide(rows, resources=(11000, 12000, 0), free=3, cooling=(), **kw):
    return decide_build_new(BuildState(tuple(rows), resources, free, frozenset(cooling)), BuildSettings(**kw))


def test_the_priciest_affordable_new_building_is_chosen():
    d = decide([COLLECTOR, LAB, BOMB])
    assert d.chosen.name == "Laboratory"


def test_cheapest_order_and_an_injected_rank_change_the_choice_without_touching_the_rules():
    assert decide([COLLECTOR, LAB, BOMB], order=Order.CHEAPEST).chosen.name == "Elixir Collector"
    by_name = lambda rows: sorted(rows, key=lambda r: r.name)
    assert decide([LAB, BOMB], rank=by_name).chosen.name == "Bomb"
    assert decide([HUT, LAB], rank=by_name).chosen.name == "Laboratory"  # el rank nunca ve lo excluido


def test_gems_free_walls_red_prices_and_upgrades_are_never_eligible():  # R2
    d = decide([HUT, WALL_FREE, UPGRADE, row("Gold Mine", affordable=False)])
    assert d.chosen is None
    assert d.why_not("Builder's Hut") == [Reason.GEMS]
    assert d.why_not("Wall") == [Reason.FREE]
    assert d.why_not("Gold Mine") == [Reason.NOT_AFFORDABLE]
    assert d.why_not("Army Camp") == []  # una mejora no es un rechazo de esta tarea


def test_a_paid_wall_and_an_unknown_cost_are_left_out():
    paid_wall = row("Wall", 5, 1000, "gold", kind="wall")
    d = decide([paid_wall, row("Mortar", cost=None, resource=None)])
    assert d.chosen is None
    assert d.why_not("Wall") == [Reason.WALL] and d.why_not("Mortar") == [Reason.UNKNOWN_COST]


def test_insufficient_resources_and_cooldown_are_skipped():
    assert decide([LAB], resources=(11000, 4000, 0)).why_not("Laboratory") == [Reason.INSUFFICIENT]
    assert decide([LAB, COLLECTOR], cooling=["Laboratory"]).chosen.name == "Elixir Collector"


def test_no_builder_above_the_reserve_means_nothing_is_chosen():  # R3
    assert decide([LAB], free=1).chosen is None and "reserva" in decide([LAB], free=1).reason
    assert decide([LAB], free=None).chosen is None
    assert decide([LAB], free=2).chosen is not None
    assert build_gate(2, 2) is not None and build_gate(3, 2) is None


def test_placements_are_bounded_by_quantity_money_and_the_cap():
    assert decide([BOMB]).placements == 2
    assert decide([BOMB], resources=(500, 0, 0)).placements == 1  # solo da para una
    assert decide([row("Cannon", 5, 100)], max_placements=3).placements == 3


# --------------------------------------------------------------------------- lector de colocación

@pytest.mark.parametrize("name, state", [
    ("placing_building", "ready"),
    ("placing_building_elixir_collector", "ready"),
    ("placing_building_no_space", "blocked"),
])
def test_the_placement_reader_tells_a_green_check_from_a_grey_one(name, state):
    placement = read_placement(fixture_frame(name).image)
    assert placement is not None and placement.state == state
    assert placement.confirm.x > placement.cancel.x  # el ✓ está a la derecha de la X


@pytest.mark.parametrize("name", ["main_village", "upgrade_panel", "upgrade_dialog", "battle", "wall_selected"])
def test_other_screens_are_not_a_placement(name):
    assert read_placement(fixture_frame(name).image) is None


def test_the_confirm_box_lands_on_the_check():
    ready = read_placement(fixture_frame("placing_building").image)
    assert READY_OK.contains(*ready.confirm.center) and READY_NO.contains(*ready.cancel.center)


# --------------------------------------------------------------------------- tarea

VILLAGE, PANEL, READY, READY2, BLOCKED, MISSING = range(6)
FRAMES = ["main_village", "upgrade_panel", "placing_building", "placing_building", "placing_building_no_space",
          "missing_gems"]
BUILD = Rect(1140, 640, 140, 80)
PANEL_X = Rect(1200, 0, 80, 70)
ROWS = Rect(941, 72, 339, 560)
READY_OK, READY_NO = Rect(790, 248, 34, 34), Rect(739, 248, 34, 34)
BLOCKED_OK, BLOCKED_NO = Rect(624, 302, 34, 34), Rect(573, 303, 34, 36)
MISSING_X = Rect(820, 205, 80, 70)


def route(source, target, region=None):
    return Transition(source, "tap", target, region)


BASE = [
    route(VILLAGE, PANEL, BUILD),
    route(PANEL, VILLAGE, PANEL_X),
    route(MISSING, VILLAGE, MISSING_X),
    route(READY, VILLAGE, READY_NO),
    route(READY2, VILLAGE, READY_NO),
    route(BLOCKED, VILLAGE, BLOCKED_NO),
]


def build_ctx(transitions, seed=1, device_cls=FakeDevice, **dev_kw):
    clock = FakeClock()
    device = device_cls([fixture_frame(n) for n in FRAMES], clock, transitions, Capabilities(swipe=True), **dev_kw)
    return Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(seed))


class Scans:
    """Cada escaneo del panel devuelve la siguiente lista (la última se repite)."""

    def __init__(self, *lists):
        self.lists, self.n = [list(l) for l in lists], 0

    def scan(self, ctx, **kw):
        rows = self.lists[min(self.n, len(self.lists) - 1)]
        self.n += 1
        return PanelScan(tuple(rows), 0, True, None, "")


def make_task(village, scans, tmp_path=None, settings=None, **kw):
    cfg = BuildNewConfig(
        settings=settings or BuildSettings(), settle_s=0.1, placement_timeout_s=1.0, game_sort=False,
        capture_dir=tmp_path, recovery=RecoveryConfig(settle_seconds=0.1), **kw,
    )
    return BuildNewTask(cfg, scanner=scans, dialogs=CachingDialogs(), village=village)


def taps(ctx):
    return [a.args for a in ctx.device.actions if a.kind == "tap"]


def row_taps(ctx):
    return [t for t in taps(ctx) if ROWS.contains(*t)]


def hit(ctx, rect):
    return sum(1 for t in taps(ctx) if rect.contains(*t))


def happy_routes(placing=READY):
    return BASE + [route(PANEL, placing, ROWS), route(placing, VILLAGE, READY_OK)]


def test_a_new_building_is_placed_with_the_green_check_and_verified_by_the_list():  # E1R1
    ctx = build_ctx(happy_routes())
    built = collect(ctx, ImprovementStarted)
    village = FakeVillage(vstate(2), vstate(1))
    result = run_task(make_task(village, Scans([LAB, COLLECTOR], [COLLECTOR])), ctx)
    assert result.outcome is Outcome.DONE and [e.name for e in built] == ["Laboratory"]
    assert row_taps(ctx)[0] == LAB.center  # el más caro
    assert hit(ctx, READY_OK) == 1 and hit(ctx, READY_NO) == 0


def test_the_real_panel_capture_yields_the_laboratory_and_a_green_check():
    ctx = build_ctx(happy_routes())
    task = make_task(FakeVillage(vstate(2), vstate(1)), Scans([]))
    task.scanner = SinglePageScanner(CachingReader())
    # Primer escaneo: panel real. Tras colocar, el panel real sigue mostrando la fila: no se da por hecho.
    built = collect(ctx, ImprovementStarted)
    result = run_task(task, ctx)
    assert row_taps(ctx)[0] == (1043, 92)  # Laboratory, la más cara asequible
    assert hit(ctx, READY_OK) == 1
    assert built == [] and result.outcome is Outcome.NOTHING_TO_DO  # la lista no cambió: sin verificar


def test_a_grey_check_is_cancelled_never_confirmed_and_retried_three_times_before_cooldown(tmp_path):  # E2R1
    ctx = build_ctx(BASE + [route(PANEL, BLOCKED, ROWS)])
    saved = collect(ctx, CaptureSaved)
    result = run_task(make_task(FakeVillage(vstate(3)), Scans([LAB]), tmp_path), ctx)
    assert hit(ctx, BLOCKED_OK) == 0
    assert hit(ctx, BLOCKED_NO) == 3 and len(row_taps(ctx)) == 3
    assert not ctx.cooldowns.ready("build:village:Laboratory")
    assert saved and list(tmp_path.glob("build-*.png"))
    assert result.outcome is Outcome.NOTHING_TO_DO


def test_a_grey_check_that_gets_a_free_spot_on_the_retry_is_then_placed():
    class Sequence(FakeDevice):  # la primera vez no hay sitio; la segunda, sí
        presses = 0

        def tap(self, x, y):
            if self._index == PANEL and ROWS.contains(x, y):
                self.presses += 1
                self.actions.append(type("A", (), {"kind": "tap", "args": (x, y), "at": 0})())
                self._index = BLOCKED if self.presses == 1 else READY
                return
            super().tap(x, y)

    ctx = build_ctx(BASE + [route(READY, VILLAGE, READY_OK)], device_cls=Sequence)
    built = collect(ctx, ImprovementStarted)
    run_task(make_task(FakeVillage(vstate(3), vstate(3), vstate(2)), Scans([LAB], [LAB], [COLLECTOR]), max_attempts=2), ctx)
    assert hit(ctx, BLOCKED_OK) == 0 and hit(ctx, BLOCKED_NO) == 1 and hit(ctx, READY_OK) == 1
    assert [e.name for e in built] == ["Laboratory"]


def test_without_a_free_builder_above_the_reserve_the_panel_is_not_even_opened():  # E1R3 / E2R3
    ctx = build_ctx(happy_routes())
    result = run_task(make_task(FakeVillage(vstate(1)), Scans([LAB])), ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO and "reserva" in result.reason
    assert taps(ctx) == []


def test_gem_cards_and_free_walls_are_never_pressed():  # E1R2 / E2R2
    ctx = build_ctx(happy_routes())
    result = run_task(make_task(FakeVillage(vstate(3)), Scans([HUT, WALL_FREE, UPGRADE])), ctx)
    assert result.outcome is Outcome.NOTHING_TO_DO
    assert row_taps(ctx) == []


def test_a_red_price_is_never_pressed():  # E3R2
    ctx = build_ctx(happy_routes())
    run_task(make_task(FakeVillage(vstate(3)), Scans([row("Cannon", affordable=False)])), ctx)
    assert row_taps(ctx) == []


def test_a_list_that_still_shows_the_row_is_not_a_placement_and_the_row_cools_down(tmp_path):
    ctx = build_ctx(happy_routes())
    built, saved = collect(ctx, ImprovementStarted), collect(ctx, CaptureSaved)
    result = run_task(make_task(FakeVillage(vstate(3)), Scans([LAB]), tmp_path), ctx)
    assert built == [] and result.outcome is Outcome.NOTHING_TO_DO
    assert not ctx.cooldowns.ready("build:village:Laboratory")
    assert saved and list(tmp_path.glob("build-*.png"))


def test_an_unreadable_list_after_placing_is_not_proof_that_the_row_vanished():
    ctx = build_ctx(happy_routes())
    built = collect(ctx, ImprovementStarted)
    run_task(make_task(FakeVillage(vstate(3)), Scans([LAB], [])), ctx)
    assert built == []


def test_the_builder_counter_only_informs():
    ctx = build_ctx(happy_routes())
    built, warnings = collect(ctx, ImprovementStarted), collect(ctx, Warning)
    # La fila desaparece de la lista, pero el contador no bajó: se avisa y se da por colocada.
    run_task(make_task(FakeVillage(vstate(3)), Scans([LAB], [COLLECTOR])), ctx)
    assert [e.name for e in built] == ["Laboratory"]
    assert any("contador" in w.message for w in warnings)


CHAIN = BASE + [route(PANEL, READY, ROWS), route(READY, READY2, READY_OK), route(READY2, VILLAGE, READY_OK)]


def test_a_chain_places_each_unit_after_re_reading_the_screen_and_counts_what_the_list_confirms():
    ctx = build_ctx(CHAIN)
    built = collect(ctx, ImprovementStarted)
    run_task(make_task(FakeVillage(vstate(3)), Scans([BOMB], [COLLECTOR]), max_attempts=1), ctx)  # la fila Bomb desapareció
    assert hit(ctx, READY_OK) == 2
    assert [e.name for e in built] == ["Bomb", "Bomb"]


def test_a_chain_counts_only_the_drop_the_list_shows():
    ctx = build_ctx(CHAIN)
    built = collect(ctx, ImprovementStarted)
    one_left = row("Bomb", 1, 400, "gold", duration=None, y=378)
    run_task(make_task(FakeVillage(vstate(3)), Scans([BOMB], [one_left]), max_attempts=1), ctx)
    assert [e.name for e in built] == ["Bomb"]


def test_a_second_ghost_beyond_the_budget_is_cancelled_not_bought():
    ctx = build_ctx(BASE + [route(PANEL, READY, ROWS), route(READY, READY2, READY_OK), route(READY2, VILLAGE, READY_NO)])
    poor = FakeVillage(vstate(3, resources=(450, 0, 0)))  # da para una Bomb (400), no para dos
    run_task(make_task(poor, Scans([BOMB], [row("Bomb", 1, 400, "gold", duration=None, y=378)]), max_attempts=1), ctx)
    assert hit(ctx, READY_OK) == 1 and hit(ctx, READY_NO) >= 1


def test_a_gems_dialog_after_the_row_blocks_the_task_without_spending():
    ctx = build_ctx(BASE + [route(PANEL, MISSING, ROWS)])
    result = run_task(make_task(FakeVillage(vstate(3)), Scans([LAB])), ctx)
    assert result.outcome is Outcome.BLOCKED and "gemas" in result.reason
    assert ctx.device.frame_index == VILLAGE


def test_a_row_that_opens_nothing_recognisable_is_set_aside_and_nothing_is_confirmed(tmp_path):
    ctx = build_ctx(BASE)  # pulsar la fila no hace nada: se queda en el panel
    result = run_task(make_task(FakeVillage(vstate(3)), Scans([LAB]), tmp_path), ctx)
    assert hit(ctx, READY_OK) == 0
    assert not ctx.cooldowns.ready("build:village:Laboratory")
    assert result.outcome is Outcome.NOTHING_TO_DO


def test_dry_run_says_what_it_would_build_and_presses_no_row():
    ctx = build_ctx(happy_routes())
    planned = collect(ctx, UpgradePlanned)
    result = run_task(make_task(FakeVillage(vstate(3)), Scans([LAB]), settings=BuildSettings(dry_run=True)), ctx)
    assert result.outcome is Outcome.DONE and "dry run" in result.reason
    assert [p.name for p in planned] == ["Laboratory"]
    assert row_taps(ctx) == [] and hit(ctx, READY_OK) == 0


def test_stop_in_the_middle_presses_no_check():
    ctx = build_ctx(happy_routes())

    class StopAfterScan(Scans):
        def scan(self, c, **kw):
            result = super().scan(c, **kw)
            c.stop.request()
            return result

    result = run_task(make_task(FakeVillage(vstate(3)), StopAfterScan([LAB])), ctx)
    assert result.outcome is Outcome.STOPPED and row_taps(ctx) == [] and hit(ctx, READY_OK) == 0


def test_without_the_game_sort_nothing_is_built_and_the_task_says_why(monkeypatch):
    calls = []
    monkeypatch.setattr("pyclash.tasks.build_new.apply_sort", lambda ctx, order: calls.append(order) or False)
    ctx = build_ctx(happy_routes())
    warnings = collect(ctx, Warning)
    task = make_task(FakeVillage(vstate(3), vstate(2)), Scans([LAB], [], ))
    task.config = BuildNewConfig(**{**task.config.__dict__, "game_sort": True})
    result = run_task(task, ctx)
    assert calls and calls[0] == "cost_max"
    assert result.outcome is Outcome.BLOCKED and "filtro del juego" in result.reason
    assert row_taps(ctx) == []  # sin filtro no se pulsa ninguna fila
    assert any("filtro del juego" in w.message for w in warnings)


# --------------------------------------------------------------------------- propiedad

class AdversarialDevice(FakeDevice):
    """Pulsar una fila lleva a una pantalla cualquiera de las capturadas: el ✓ solo puede recibir un toque
    cuando la captura que se ve tiene el ✓ en verde."""

    SCREENS = (READY, BLOCKED, MISSING, VILLAGE, PANEL)

    def __init__(self, frames, clock, transitions, caps, rng=None):
        super().__init__(frames, clock, transitions, caps)
        self.rng, self.taps = rng, []

    def tap(self, x, y):
        self.taps.append((self._index, x, y))
        if self._index == PANEL and ROWS.contains(x, y):
            self.actions.append(type("A", (), {"kind": "tap", "args": (x, y), "at": 0})())
            self._index = self.rng.choice(self.SCREENS)
            return
        super().tap(x, y)


@pytest.mark.parametrize("seed", range(16))
def test_no_screen_makes_a_check_tap_unless_the_check_is_green(seed):
    ctx = build_ctx(BASE + [route(READY, VILLAGE, READY_OK)], device_cls=AdversarialDevice, rng=random.Random(seed))
    run_task(make_task(FakeVillage(vstate(3)), Scans([LAB]), max_attempts=4), ctx)
    assert ctx.device.taps
    for index, x, y in ctx.device.taps:
        assert not (index == BLOCKED and BLOCKED_OK.contains(x, y)), (index, x, y)
        assert not (index == MISSING and not MISSING_X.contains(x, y) and Rect(500, 400, 300, 200).contains(x, y))


def test_the_property_detects_a_reader_that_trusts_a_grey_check(monkeypatch):
    """Control de la propia prueba: si el lector diera por bueno cualquier ✓, algún sorteo sí lo pulsaría."""
    import dataclasses

    import pyclash.tasks.build_new as bn

    real = bn.read_placement
    monkeypatch.setattr(bn, "read_placement", lambda img: (lambda p: p and dataclasses.replace(p, state="ready"))(real(img)))
    caught = 0
    for seed in range(16):
        ctx = build_ctx(BASE, device_cls=AdversarialDevice, rng=random.Random(seed))
        run_task(make_task(FakeVillage(vstate(3)), Scans([LAB]), max_attempts=4), ctx)
        caught += any(i == BLOCKED and BLOCKED_OK.contains(x, y) for i, x, y in ctx.device.taps)
    assert caught > 0
