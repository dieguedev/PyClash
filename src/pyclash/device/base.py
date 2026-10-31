"""Interfaz mínima de dispositivo y tipos asociados."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import numpy as np


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    viewport: tuple[int, int]
    timestamp: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


class DeviceError(Exception):
    """Error de dispositivo que el usuario puede entender y corregir."""


class WindowNotFound(DeviceError):
    pass


class WindowMinimized(DeviceError):
    pass


class BlackCapture(DeviceError):
    pass


class CaptureFailed(DeviceError):
    pass


@dataclass(frozen=True)
class Capabilities:
    hold: bool = True
    swipe: bool = True
    scroll: bool = True


class Device(Protocol):
    @property
    def capabilities(self) -> Capabilities: ...
    def capture(self) -> Frame: ...
    def tap(self, x: int, y: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None: ...
    def scroll(self, x: int, y: int, amount: int) -> None: ...
    def press(self, x: int, y: int) -> None: ...
    def release(self) -> None: ...
    @property
    def held(self) -> bool: ...
