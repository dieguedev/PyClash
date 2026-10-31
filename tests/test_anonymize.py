import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from pyclash.anonymize import anonymize, is_anonymized

ROOT = Path(__file__).resolve().parents[1]


def _make(path, color=(200, 10, 10)):
    Image.new("RGB", (40, 30), color).save(path)


def test_region_is_blanked_and_marked(tmp_path):
    src, dst = tmp_path / "a.png", tmp_path / "out" / "b.png"
    _make(src)
    anonymize(src, dst, [(5, 5, 10, 10)])
    assert is_anonymized(dst)
    with Image.open(dst) as im:
        assert im.getpixel((6, 6)) == (0, 0, 0)
        assert im.getpixel((30, 20)) == (200, 10, 10)


def test_original_untouched_and_not_marked(tmp_path):
    src = tmp_path / "a.png"
    _make(src)
    anonymize(src, tmp_path / "b.png", [])
    assert not is_anonymized(src)


def test_same_path_rejected(tmp_path):
    src = tmp_path / "a.png"
    _make(src)
    with pytest.raises(ValueError):
        anonymize(src, src, [])


def test_bad_region_rejected(tmp_path):
    src = tmp_path / "a.png"
    _make(src)
    with pytest.raises(ValueError):
        anonymize(src, tmp_path / "b.png", [(35, 0, 10, 10)])


def test_check_fixtures_fails_on_unmarked_image():
    raw = ROOT / "tests" / "fixtures" / "_unmarked_tmp.png"
    _make(raw)
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_fixtures.py")], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 1
        assert "_unmarked_tmp.png" in r.stdout
    finally:
        raw.unlink()


def test_check_fixtures_passes_on_marked_image():
    ok = ROOT / "tests" / "fixtures" / "_marked_tmp.png"
    src = ROOT / "tests" / "fixtures" / "_src_tmp.png"
    _make(src)
    try:
        anonymize(src, ok, [(0, 0, 4, 4)])
        src.unlink()
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_fixtures.py")], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stdout
    finally:
        ok.unlink(missing_ok=True)
        src.unlink(missing_ok=True)
