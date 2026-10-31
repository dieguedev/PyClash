import random
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from pyclash.device.base import Capabilities, Frame
from pyclash.device.fake import FakeDevice
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.context import Context
from pyclash.vision.base import MetadataVision


def frame(elements=None):
    return Frame(np.zeros((10, 10, 3), np.uint8), (10, 10), 0.0, {"elements": elements or {}})


def make_ctx(frames, transitions=None, seed=0, capabilities=None):
    clock = FakeClock()
    device = FakeDevice(frames, clock, transitions, capabilities)
    return Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(seed))


FIXTURES = Path(__file__).parent / "fixtures" / "screens"


@lru_cache(maxsize=None)
def _fixture_image(name):
    with Image.open(FIXTURES / f"{name}.png") as im:
        image = np.array(im.convert("RGB"))
    image.flags.writeable = False
    return image


def fixture_frame(name):
    """Fotograma real anonimizado de tests/fixtures/screens/<name>.png."""
    image = _fixture_image(name)
    return Frame(image, (image.shape[1], image.shape[0]), 0.0, {})


class ScriptedDevice:
    """Dispositivo cuyas capturas siguen un guion (fotogramas o excepciones) y que anota los toques."""

    def __init__(self, script):
        self._script = list(script)
        self._i = 0
        self.taps = []
        self.held = False
        self.capabilities = Capabilities()

    def capture(self):
        item = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, *a, **k): ...
    def scroll(self, *a, **k): ...
    def press(self, *a, **k): ...
    def move(self, *a, **k): ...
    def release(self): ...


def make_scripted_ctx(script, seed=0):
    clock = FakeClock()
    device = ScriptedDevice(script)
    return Context(device=device, vision=MetadataVision(), clock=clock, rng=random.Random(seed))
