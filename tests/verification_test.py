"""Lógica de la pantalla de verificación manual (#33): sin Qt, con dispositivo y reloj falsos."""

import dataclasses

import numpy as np

from pyclash.device.base import DeviceError, Frame
from pyclash.device.fake import FakeDevice
from pyclash.interface.verification import CHECKS, Check, CheckOutcome, check_by_key, run_check
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.stop import StopToken
from pyclash.tasks.base import Outcome
from tests.helpers import fixture_frame


def device(*frames):
    clock = FakeClock()
    return FakeDevice(list(frames), clock), clock


def test_every_check_has_unique_key_and_known_risk():
    keys = [c.key for c in CHECKS]
    assert len(keys) == len(set(keys))
    assert {c.risk for c in CHECKS} == {"read", "dry", "spend"}


def test_real_spending_checks_are_never_dry():
    # lo que gasta de verdad debe ser un botón aparte, marcado «spend», que la pantalla confirma
    assert {c.key for c in CHECKS if c.risk == "spend"} == {"upgrade", "walls", "build", "farm"}


def test_read_only_checks_never_tap(tmp_path):
    black = Frame(np.zeros((90, 160, 3), np.uint8), (160, 90), 0.0, {})
    for check in (c for c in CHECKS if c.risk == "read"):
        dev, clock = device(black)
        out = run_check(check, dev, clock=clock, capture_dir=tmp_path)
        assert dev.actions == [], check.key
        assert out.lines, check.key


def test_screen_check_names_the_screen():
    dev, clock = device(fixture_frame("main_village"))
    out = run_check(check_by_key("screen"), dev, clock=clock)
    assert any("main_village" in line for line in out.lines)


def test_capture_check_saves_a_png(tmp_path):
    dev, clock = device(fixture_frame("main_village"))
    run_check(check_by_key("capture"), dev, clock=clock, capture_dir=tmp_path)
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_measure_shift_finds_how_far_the_panel_moved():
    from pyclash.interface.verification import measure_shift
    from pyclash.screens.panel_layout import VILLAGE_PANEL

    image = fixture_frame("upgrade_panel").image
    h, w = image.shape[:2]
    x0, y0, x1, y1 = VILLAGE_PANEL.viewport(h, w)
    moved = image.copy()
    moved[y0:y1 - 60, x0:x1] = image[y0 + 60:y1, x0:x1]  # el contenido sube 60 px
    assert measure_shift(image, image) == 0
    assert abs(measure_shift(image, moved) - 60) <= 1
    assert abs(measure_shift(moved, image) + 60) <= 1 or measure_shift(moved, image) is None


def test_wheel_check_is_a_dry_check():
    assert check_by_key("wheel").risk == "dry"


def test_unexpected_error_is_reported_not_raised():
    def boom(ctx, directory):
        raise ValueError("fallo de prueba")

    dev, clock = device(fixture_frame("main_village"))
    out = run_check(Check("x", "x", "read", "", boom), dev, clock=clock)
    assert any("fallo de prueba" in line for line in out.lines)
    assert not out.ok


def test_device_error_is_reported():
    def lost(ctx, directory):
        raise DeviceError("No se encuentra la ventana.")

    dev, clock = device(fixture_frame("main_village"))
    out = run_check(Check("x", "x", "read", "", lost), dev, clock=clock)
    assert out.result is not None and out.result.outcome is Outcome.FAILED


def test_stop_before_a_task_ends_it_without_touching_anything():
    stop = StopToken()
    stop.request()
    dev, clock = device(fixture_frame("main_village"))
    out = run_check(check_by_key("upgrade_dry"), dev, stop=stop, clock=clock)
    assert dev.actions == []
    assert out.result is not None and out.result.outcome is Outcome.STOPPED


def _stub_walls_and_reading(monkeypatch):
    """Cambia la lectura del coste de búsqueda y la tarea de muros por stubs: se mira qué hace la comprobación."""
    from pyclash.interface import verification as v
    from pyclash.tasks.base import TaskResult

    calls = {"reads": 0, "floors": []}

    class Reading:
        name, priority = "read_search_cost", 5

        def run(self, ctx):
            calls["reads"] += 1
            ctx.gold_reserve.update(350, 350, ctx.clock.now())
            return TaskResult.done()

    class Walls:
        name, priority = "upgrade_walls", 30

        def __init__(self, config, inputs):
            self.inputs = inputs

        def run(self, ctx):
            calls["floors"].append(self.inputs(ctx).gold_floor)
            return TaskResult.done()

    monkeypatch.setattr(v, "ReadSearchCost", Reading)
    monkeypatch.setattr(v, "WallBatchTask", Walls)
    return calls


def test_walls_real_reads_the_search_cost_first_when_the_gold_reserve_is_missing(monkeypatch):
    calls = _stub_walls_and_reading(monkeypatch)
    dev, clock = device(fixture_frame("main_village"))
    out = run_check(check_by_key("walls"), dev, clock=clock)
    assert calls["reads"] == 1 and calls["floors"] == [26600]  # se leyó y los muros ya ven el colchón de oro
    assert any("se lee el coste de búsqueda" in line for line in out.lines)


def test_walls_real_reuses_a_fresh_gold_reserve_without_reading_again(monkeypatch):
    from pyclash.policy.gold_reserve import GoldReserve

    calls = _stub_walls_and_reading(monkeypatch)
    reserve = GoldReserve()
    dev, clock = device(fixture_frame("main_village"))
    reserve.update(350, 350, clock.now())
    run_check(check_by_key("walls"), dev, clock=clock, gold_reserve=reserve)
    assert calls["reads"] == 0 and calls["floors"] == [26600]


def test_walls_dry_run_never_reads_the_search_cost(monkeypatch):
    calls = _stub_walls_and_reading(monkeypatch)
    dev, clock = device(fixture_frame("main_village"))
    run_check(check_by_key("walls_dry"), dev, clock=clock)
    assert calls["reads"] == 0 and calls["floors"] == [0]  # el simulacro usa colchón 0 y no abre Battle


def test_the_gold_reserve_is_shared_between_checks_of_a_session():
    """Cada comprobación tiene su `Context`: la reserva que lee una debe llegar a la siguiente (Muros REAL)."""
    from pyclash.policy.gold_reserve import GoldReserve

    reserve = GoldReserve()

    def reads(ctx, directory):
        ctx.gold_reserve.update(350, 350, ctx.clock.now())
        return CheckOutcome(["leído"])

    def uses(ctx, directory):
        return CheckOutcome([f"colchón {ctx.gold_reserve.floor(ctx.clock.now())}"])

    dev, clock = device(fixture_frame("main_village"))
    run_check(Check("a", "a", "read", "", reads), dev, clock=clock, gold_reserve=reserve)
    out = run_check(Check("b", "b", "read", "", uses), dev, clock=clock, gold_reserve=reserve)
    assert out.lines == ["colchón 26600"]
    alone = run_check(Check("c", "c", "read", "", uses), dev, clock=clock)
    assert alone.lines == ["colchón None"]  # sin compartirla, cada comprobación parte de cero


def test_held_input_is_released_after_a_check():
    def holds(ctx, directory):
        ctx.device.press(5, 5)
        raise ValueError("a medias")

    dev, clock = device(fixture_frame("main_village"))
    run_check(Check("x", "x", "read", "", holds), dev, clock=clock)
    assert not dev.held


def test_events_stream_live_when_a_listener_is_given():
    seen = []
    dev, clock = device(fixture_frame("main_village"))
    out = run_check(check_by_key("upgrade_dry"), dev, clock=clock, on_line=seen.append)
    assert any("TaskStarted" in line for line in seen)
    assert not any("TaskStarted" in line for line in out.lines)  # no se duplican


def test_outcome_ok_rules():
    from pyclash.tasks.base import TaskResult

    assert CheckOutcome(result=TaskResult.nothing_to_do("sin constructores")).ok
    assert not CheckOutcome(result=TaskResult.failed("x")).ok
    assert not CheckOutcome(result=TaskResult.needs_recovery("x")).ok
    assert dataclasses.is_dataclass(CheckOutcome)


def test_card_tap_check_taps_once_and_never_holds_or_swipes(tmp_path):
    dev, clock = device(fixture_frame("two_troops_bar"))
    out = run_check(check_by_key("card_tap"), dev, clock=clock, capture_dir=tmp_path)
    assert [a.kind for a in dev.actions] == ["tap"]  # una pulsación sobre la carta: nada de despliegue
    assert not dev.held and out.ok
    assert len(list(tmp_path.glob("card-*.png"))) == 2  # antes y después


def test_zoom_out_check_is_a_dry_run_that_never_spends(tmp_path):
    assert check_by_key("zoom_out").risk == "dry"
    dev, clock = device(fixture_frame("main_village"))
    out = run_check(check_by_key("zoom_out"), dev, clock=clock, capture_dir=tmp_path)
    assert out.ok
    assert {a.kind for a in dev.actions} <= {"tap", "scroll"}
    assert len(list(tmp_path.glob("zoom-final-*.png"))) == 1


def test_inactivity_check_reads_the_popup_without_tapping():
    dev, clock = device(fixture_frame("inactivity_popup"))
    out = run_check(check_by_key("inactivity"), dev, clock=clock)
    assert any("SÍ" in line for line in out.lines) and any("Pulsaría en" in line for line in out.lines)
    assert dev.actions == []


def test_upgrade_policy_dry_checks_never_tap_a_row(tmp_path):
    for key in ("upgrade_dry", "upgrade_rusher_dry"):
        assert check_by_key(key).risk == "dry"
        dev, clock = device(fixture_frame("main_village"))
        out = run_check(check_by_key(key), dev, clock=clock, capture_dir=tmp_path)
        assert not any(a.kind == "tap" and a.args[1] < 640 and a.args[0] > 941 for a in dev.actions), key
        assert out.lines, key
