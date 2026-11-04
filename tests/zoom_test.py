"""Zoom-out máximo (#37): cierra el menú abierto y aleja hasta el tope."""

import numpy as np

from pyclash.device.base import Frame
from pyclash.runtime.events import CaptureSaved, Error
from pyclash.tasks.base import Outcome, run_task
from pyclash.tasks.zoom import ZoomOut, ZoomOutConfig, frame_difference
from tests.helpers import fixture_frame, make_scripted_ctx


def shifted(frm, dx):
    image = np.roll(frm.image, dx, axis=1)
    return Frame(image, frm.viewport, 0.0, {})


def recorder(ctx):
    seen = []
    ctx.events.subscribe(CaptureSaved, seen.append)
    ctx.events.subscribe(Error, seen.append)
    return seen


def test_closes_the_open_menu_with_its_x_and_then_zooms_out():
    panel, village = fixture_frame("upgrade_panel"), fixture_frame("main_village")
    # recuperación: panel (pulsa la X) y aldea; luego captura base y giros: cambia y se estabiliza
    ctx = make_scripted_ctx([panel, village, village, shifted(village, 40), shifted(village, 40)])
    result = run_task(ZoomOut(), ctx)
    assert result.outcome is Outcome.DONE
    assert len(ctx.device.taps) == 1
    x, y = ctx.device.taps[0]
    h, w = panel.image.shape[:2]
    assert x > 0.9 * w and y < 0.12 * h  # la X del menú, arriba a la derecha


def test_already_in_the_village_taps_nothing_and_scrolls():
    a = fixture_frame("main_village")
    ctx = make_scripted_ctx([a])  # todas las capturas iguales: tope al primer giro
    assert run_task(ZoomOut(), ctx).outcome is Outcome.DONE
    assert ctx.device.taps == []


def test_scrolls_outwards():
    a = fixture_frame("main_village")
    ctx = make_scripted_ctx([a])
    calls = []
    ctx.device.scroll = lambda *args, **kw: calls.append(args)
    run_task(ZoomOut(), ctx)
    assert calls and calls[0][2] < 0  # alejar


def test_no_village_in_sight_does_not_scroll_and_taps_nothing():
    black = Frame(np.zeros((90, 160, 3), np.uint8), (160, 90), 0.0, {})
    ctx = make_scripted_ctx([black])
    calls = []
    ctx.device.scroll = lambda *args, **kw: calls.append(args)
    result = run_task(ZoomOut(), ctx)
    assert result.outcome is not Outcome.DONE
    assert ctx.device.taps == [] and calls == []


def test_fails_with_capture_if_the_zoom_never_settles(tmp_path):
    a = fixture_frame("main_village")
    frames = [a, a] + [shifted(a, 20 * i) for i in range(1, 12)]
    ctx = make_scripted_ctx(frames)
    seen = recorder(ctx)
    result = run_task(ZoomOut(ZoomOutConfig(max_scrolls=4, capture_dir=tmp_path)), ctx)
    assert result.outcome is Outcome.FAILED and "zoom" in result.reason
    assert any(isinstance(e, CaptureSaved) for e in seen)
    assert any(isinstance(e, Error) for e in seen)


def test_frame_difference():
    z = np.zeros((4, 4, 3), np.uint8)
    assert frame_difference(z, z) == 0
    assert frame_difference(z, np.full((4, 4, 3), 10, np.uint8)) == 10
    assert frame_difference(z, np.zeros((5, 4, 3), np.uint8)) == float("inf")
