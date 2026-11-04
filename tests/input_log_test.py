"""Registro de entradas (#43): eventos de ratón y ventana simulados, sin sistema real."""

import json
from datetime import datetime, timedelta

import pytest

from pyclash.device.base import Rect
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.input_log import InputLog, InputLogError, WindowState, session_filename

WIN = Rect(100, 50, 800, 450)


class Harness:
    def __init__(self):
        self.clock = FakeClock(1000.0)
        self.state: WindowState | None = WindowState(WIN, True)
        self.lines: list[str] = []
        self.closed = False
        self.opened: tuple | None = None
        self.log = InputLog(self.clock, self._wall, lambda: self.state, self._open)

    def _wall(self):
        return datetime(2026, 11, 4, 12, 0, 0) + timedelta(seconds=self.clock.now() - 1000.0)

    def _open(self, when, th):
        self.opened = (when, th)
        h = self

        class Sink:
            def write_line(self, line):
                h.lines.append(line)

            def close(self):
                h.closed = True

        return Sink()

    @property
    def events(self):
        return [json.loads(l) for l in self.lines]

    def kinds(self):
        return [e["event"] for e in self.events]


@pytest.fixture
def h():
    return Harness()


def test_nothing_is_written_without_active_recording(h):
    h.log.button("left", True, 500, 250)
    h.log.move(500, 250)
    h.log.scroll(500, 250, 0, 1)
    h.log.mark()
    assert h.lines == []


def test_start_writes_header_with_town_hall_and_window_size(h):
    h.log.start(3)
    assert h.kinds() == ["start", "window_size"]
    assert h.events[0]["town_hall"] == 3
    assert (h.events[1]["w"], h.events[1]["h"]) == (800, 450)
    assert h.log.recording


def test_start_without_window_fails_and_does_not_record(h):
    h.state = None
    with pytest.raises(InputLogError):
        h.log.start(9)
    assert not h.log.recording and h.opened is None


def test_stop_writes_end_and_closes(h):
    h.log.start(9)
    h.log.stop()
    assert h.kinds()[-1] == "end" and h.closed and not h.log.recording


def test_toggle_starts_and_stops(h):
    h.log.toggle(9)
    assert h.log.recording
    h.log.toggle(9)
    assert not h.log.recording


def test_press_and_release_are_separate_events_with_normalized_position(h):
    h.log.start(9)
    h.log.button("left", True, 500, 275)
    h.log.button("left", False, 500, 275)
    press, release = h.events[-2:]
    assert (press["event"], press["button"], press["x"], press["y"]) == ("press", "left", 0.5, 0.5)
    assert (release["event"], release["x"], release["y"]) == ("release", 0.5, 0.5)


def test_position_is_relative_to_window_not_screen(h):
    h.log.start(9)
    h.log.button("left", True, 500, 275)
    h.state = WindowState(Rect(0, 0, 1600, 900), True)
    h.log.button("left", False, 800, 450)
    h.log.button("left", True, 800, 450)
    assert h.events[-1]["x"] == 0.5 and h.events[-1]["y"] == 0.5


def test_other_buttons_are_recorded_with_their_name(h):
    h.log.start(9)
    h.log.button("right", True, 500, 275)
    assert h.events[-1]["button"] == "right"


def test_click_outside_window_is_ignored(h):
    h.log.start(9)
    n = len(h.lines)
    h.log.button("left", True, 5, 5)
    h.log.button("left", False, 5, 5)
    assert len(h.lines) == n


def test_click_without_focus_is_ignored(h):
    h.log.start(9)
    h.state = WindowState(WIN, False)
    n = len(h.lines)
    h.log.button("left", True, 500, 275)
    h.log.button("left", False, 500, 275)
    assert len(h.lines) == n


def test_release_outside_window_is_recorded_after_valid_press(h):
    h.log.start(9)
    h.log.button("left", True, 500, 275)
    h.log.button("left", False, 5, 5)
    assert h.kinds()[-1] == "release"


def test_move_without_button_is_sampled(h):
    h.log.start(9)
    for _ in range(10):
        h.log.move(500, 275)
        h.clock.sleep(0.01)
    moves = [e for e in h.events if e["event"] == "move"]
    assert 1 <= len(moves) < 10


def test_drag_samples_denser_than_free_move(h):
    free = Harness()
    free.log.start(9)
    for _ in range(100):
        free.log.move(500, 275)
        free.clock.sleep(0.01)
    drag = Harness()
    drag.log.start(9)
    drag.log.button("left", True, 500, 275)
    for _ in range(100):
        drag.log.move(500, 275)
        drag.clock.sleep(0.01)
    assert drag.kinds().count("move") > free.kinds().count("move")


def test_free_move_outside_window_is_ignored(h):
    h.log.start(9)
    n = len(h.lines)
    h.log.move(5, 5)
    assert len(h.lines) == n


def test_free_move_without_focus_is_ignored(h):
    h.log.start(9)
    h.state = WindowState(WIN, False)
    n = len(h.lines)
    h.log.move(500, 275)
    assert len(h.lines) == n


def test_scroll_records_position_and_delta(h):
    h.log.start(9)
    h.log.scroll(500, 275, 0, -2)
    e = h.events[-1]
    assert (e["event"], e["dy"], e["x"], e["y"]) == ("scroll", -2, 0.5, 0.5)


def test_window_size_is_noted_once_per_change(h):
    h.log.start(9)
    h.state = WindowState(Rect(100, 50, 1600, 900), True)
    h.log.button("left", True, 500, 275)
    h.log.button("left", False, 500, 275)
    h.log.button("left", True, 500, 275)
    sizes = [e for e in h.events if e["event"] == "window_size"]
    assert [(e["w"], e["h"]) for e in sizes] == [(800, 450), (1600, 900)]


def test_mark_only_while_recording(h):
    h.log.mark()
    assert h.lines == []
    h.log.start(9)
    h.log.mark()
    assert h.kinds()[-1] == "mark"


def test_first_click_inside_is_reference_and_only_it(h):
    h.log.start(9)
    h.log.button("left", True, 5, 5)  # fuera: no cuenta
    h.log.button("left", True, 500, 275)
    h.log.button("left", False, 500, 275)
    h.log.button("left", True, 500, 275)
    presses = [e for e in h.events if e["event"] == "press"]
    assert presses[0].get("reference") is True
    assert "reference" not in presses[1]


def test_reference_restarts_with_each_recording(h):
    h.log.start(9)
    h.log.button("left", True, 500, 275)
    h.log.stop()
    h.lines.clear()
    h.log.start(9)
    h.log.button("left", True, 500, 275)
    assert [e for e in h.events if e["event"] == "press"][0].get("reference") is True


def test_events_carry_time_since_start_and_wall_time(h):
    h.log.start(9)
    h.clock.sleep(2.5)
    h.log.mark()
    e = h.events[-1]
    assert e["t"] == 2.5
    assert e["wall"].startswith("2026-11-04T12:00:02.5")


def test_each_line_is_written_as_it_happens(h):
    h.log.start(9)
    before = len(h.lines)
    h.log.mark()
    assert len(h.lines) == before + 1  # sin esperar a stop()


def test_file_sink_flushes_each_line(tmp_path):
    from pyclash.runtime.input_log import FileSink

    sink = FileSink(tmp_path / "sub" / "a.jsonl")
    sink.write_line('{"a": 1}')
    assert (tmp_path / "sub" / "a.jsonl").read_text(encoding="utf-8") == '{"a": 1}\n'  # antes de cerrar
    sink.close()


def test_filename_has_date_time_and_town_hall():
    assert session_filename(datetime(2026, 11, 4, 9, 5, 7), 3) == "input_2026-11-04_09-05-07_th3.jsonl"
