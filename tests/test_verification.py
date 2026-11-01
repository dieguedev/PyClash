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
