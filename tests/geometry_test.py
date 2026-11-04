import numpy as np
import pytest

from pyclash.device import geometry
from pyclash.device.base import BlackCapture, Rect, WindowMinimized, WindowNotFound
from pyclash.device.probe import ProbeStatus, probe


def _img(w, h, bar_left=0, bar_right=0, bar_top=0, bar_bottom=0):
    im = np.zeros((h, w, 3), np.uint8)
    im[bar_top : h - bar_bottom, bar_left : w - bar_right] = (90, 140, 60)
    return im


def test_aspect_16_9():
    assert geometry.is_16_9(1920, 1080)
    assert geometry.is_16_9(1280, 720)
    assert not geometry.is_16_9(2560, 1080)
    assert not geometry.is_16_9(0, 0)
    assert geometry.aspect_label(1920, 1080) == "16:9"
    assert geometry.aspect_label(2560, 1080) == "2.37:1"


def test_play_area_with_side_bars():
    area = geometry.play_area(_img(1000, 500, bar_left=40, bar_right=60))
    assert area == Rect(40, 0, 900, 500)


def test_play_area_without_bars_is_full_image():
    assert geometry.play_area(_img(320, 180)) == Rect(0, 0, 320, 180)


def test_play_area_with_top_and_bottom_bars():
    assert geometry.play_area(_img(320, 200, bar_top=10, bar_bottom=10)) == Rect(0, 10, 320, 180)


def test_black_image():
    assert geometry.play_area(np.zeros((90, 160, 3), np.uint8)) is None
    assert geometry.is_black(np.zeros((90, 160, 3), np.uint8))
    assert not geometry.is_black(_img(160, 90))


def test_fraction_center_lands_in_center_of_cropped_area():
    # E1R2: con barra lateral, la fracción (0.5, 0.5) del área recortada es su centro
    area = geometry.play_area(_img(1000, 500, bar_left=100))
    assert geometry.fraction_to_pixel(0.5, 0.5, (area.w, area.h)) == (450, 250)
    # el píxel de la ventana correspondiente: centro del área + desplazamiento del recorte
    assert 450 + area.x == 550


def test_fraction_without_bars():
    assert geometry.fraction_to_pixel(0.5, 0.5, (1920, 1080)) == (960, 540)


def test_fraction_is_clamped():
    assert geometry.fraction_to_pixel(1.0, 1.0, (100, 50)) == (99, 49)
    assert geometry.fraction_to_pixel(-1, -1, (100, 50)) == (0, 0)


def test_pixel_fraction_roundtrip():
    fx, fy = geometry.pixel_to_fraction(480, 270, (1920, 1080))
    assert (fx, fy) == (0.25, 0.25)
    assert geometry.fraction_to_pixel(fx, fy, (1920, 1080)) == (480, 270)


def test_fit_16_9_in_work_area():
    # superficie 1000x500 en ventana con marco de 16x39; área de trabajo 1920x1040
    x, y, ow, oh = geometry.fit_16_9((1000, 500), (100, 100, 1116, 639), (0, 0, 1920, 1040))
    sw, sh = ow - 16, oh - 39
    assert geometry.is_16_9(sw, sh, tolerance=0.005)
    assert ow <= 1920 and oh <= 1040
    assert x >= 0 and y >= 0 and x + ow <= 1920 and y + oh <= 1040


def test_fit_16_9_limited_by_width():
    _, _, ow, oh = geometry.fit_16_9((800, 600), (0, 0, 800, 600), (0, 0, 1000, 2000))
    assert (ow, oh) == (1000, 562)


def test_best_16_9_mode():
    from pyclash.device.display import best_16_9_mode

    modes = {(2560, 1080), (1920, 1080), (1280, 720), (1024, 768)}
    assert best_16_9_mode(modes) == (1920, 1080)
    assert best_16_9_mode({(2560, 1080)}) is None


def test_hidden_marker_roundtrip(tmp_path):
    from pyclash.device.gpg import read_hidden_marker, write_hidden_marker

    path = tmp_path / "hidden.json"
    write_hidden_marker(path, -50, 120)
    assert read_hidden_marker(path) == (-50, 120)
    path.write_text("no es json")
    assert read_hidden_marker(path) == (0, 0)


class _Device:
    def __init__(self, exc=None, frame=None):
        self.exc, self.frame = exc, frame

    def capture(self):
        if self.exc:
            raise self.exc
        return self.frame


@pytest.mark.parametrize(
    "exc,status",
    [
        (WindowNotFound("x"), ProbeStatus.NOT_FOUND),
        (WindowMinimized("x"), ProbeStatus.MINIMIZED),
        (BlackCapture("x"), ProbeStatus.BLACK),
    ],
)
def test_probe_reports_problems(exc, status):
    r = probe(_Device(exc))
    assert r.status is status and not r.ok and r.frame is None and r.message


def test_probe_shows_size_and_aspect():
    from pyclash.device.base import Frame

    f = Frame(np.zeros((1080, 1920, 3), np.uint8), (1920, 1080), 0.0)
    r = probe(_Device(frame=f))
    assert r.ok
    assert "1920×1080" in r.message and "16:9" in r.message
