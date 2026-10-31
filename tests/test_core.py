import pytest

from helpers import frame, make_ctx
from pyclash.device.base import Capabilities, Rect
from pyclash.device.fake import FakeDevice, Transition
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.events import Event, EventBus, TaskFinished, TaskStarted, Warning
from pyclash.runtime.stop import StopRequested, StopToken
from pyclash.tasks.base import Outcome, TaskResult, run_task
from pyclash.tasks.example import TapButtonTask

BTN = Rect(10, 10, 20, 10)


def test_same_seed_same_choice():
    picks = [make_ctx([frame()], seed=42).rng.choice([0, 1, 2]) for _ in range(2)]
    assert picks[0] == picks[1]


def test_wait_does_not_really_wait():
    ctx = make_ctx([frame()])
    assert ctx.wait_until(lambda: False, 30) is False
    assert ctx.clock.now() == 30


def test_stop_during_sleep():
    ctx = make_ctx([frame()])
    orig = ctx.clock.sleep

    def sleep(s):
        orig(s)
        if ctx.clock.now() >= 3:
            ctx.stop.request()

    ctx.clock.sleep = sleep

    class Waiter:
        name = "wait"

        def run(self, c):
            c.sleep(60)
            return TaskResult.done()

    result = run_task(Waiter(), ctx)
    assert result.outcome is Outcome.STOPPED
    assert ctx.clock.now() == 3


def test_stop_releases_held_input():
    ctx = make_ctx([frame()])
    ctx.device.press(5, 5)
    assert ctx.device.held
    ctx.stop.request()
    with pytest.raises(StopRequested):
        ctx.check_stop()
    assert not ctx.device.held
    assert [a.kind for a in ctx.device.actions] == ["press", "release"]


def test_task_taps_button_and_is_done():
    ctx = make_ctx([frame({"ok": BTN}), frame()], [Transition(0, "tap", 1, BTN)])
    result = run_task(TapButtonTask("ok"), ctx)
    assert result.outcome is Outcome.DONE
    assert [(a.kind, a.args) for a in ctx.device.actions] == [("tap", BTN.center)]
    assert ctx.device.frame_index == 1


def test_task_blocked_without_button():
    ctx = make_ctx([frame()])
    result = run_task(TapButtonTask("ok", timeout=2), ctx)
    assert result.outcome is Outcome.BLOCKED and result.reason
    assert ctx.device.actions == []


def test_task_publishes_events():
    ctx = make_ctx([frame({"ok": BTN})])
    seen = []
    ctx.events.subscribe(Event, seen.append)
    run_task(TapButtonTask("ok"), ctx)
    assert [type(e) for e in seen] == [TaskStarted, TaskFinished]
    assert seen[1].outcome == "done"


def test_event_bus_filters_by_type():
    bus, got = EventBus(), []
    bus.subscribe(Warning, got.append)
    bus.publish(TaskStarted("x"))
    bus.publish(Warning("w"))
    assert [e.message for e in got] == ["w"]


def test_cooldowns_use_clock():
    ctx = make_ctx([frame()])
    ctx.cooldowns.start("k", 10)
    assert not ctx.cooldowns.ready("k")
    ctx.clock.sleep(10)
    assert ctx.cooldowns.ready("k")


def test_fake_device_capabilities_and_timestamp():
    clock = FakeClock(5.0)
    dev = FakeDevice([frame()], clock, capabilities=Capabilities(hold=False, swipe=False))
    assert dev.capture().timestamp == 5.0
    with pytest.raises(NotImplementedError):
        dev.press(1, 1)
    with pytest.raises(NotImplementedError):
        dev.swipe(0, 0, 1, 1)


def test_stop_token():
    t = StopToken()
    assert not t.requested
    t.request()
    assert t.requested
