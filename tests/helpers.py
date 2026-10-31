import random

import numpy as np

from pyclash.device.base import Frame
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
