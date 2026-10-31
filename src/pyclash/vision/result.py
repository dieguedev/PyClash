"""Resultados de visión: cada lectura lleva su confianza y su procedencia (región y método)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

import numpy as np

from ..device.base import Rect

T = TypeVar("T")


@dataclass(frozen=True)
class Provenance:
    region: Rect
    method: str  # p. ej. "template", "font:white:s29", "digits:font", "ocr:easyocr"


@dataclass(frozen=True)
class Match:
    """Una coincidencia localizada; `rect` está en coordenadas del fotograma completo."""

    rect: Rect
    confidence: float
    provenance: Provenance
    label: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        return self.rect.center


@dataclass(frozen=True)
class Reading(Generic[T]):
    value: T
    confidence: float
    provenance: Provenance


def full_region(image: np.ndarray) -> Rect:
    h, w = image.shape[:2]
    return Rect(0, 0, w, h)


def fraction_rect(image: np.ndarray, x1: float, y1: float, x2: float, y2: float) -> Rect:
    """Rect en píxeles a partir de fracciones (0..1) del ancho y el alto."""
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        raise ValueError(f"fracciones fuera de rango: {(x1, y1, x2, y2)}")
    h, w = image.shape[:2]
    left, top = int(w * x1), int(h * y1)
    right, bottom = max(left + 1, int(w * x2)), max(top + 1, int(h * y2))
    return Rect(left, top, right - left, bottom - top)


def crop(image: np.ndarray, region: Rect | None) -> tuple[np.ndarray, Rect]:
    """Recorta a `region` (limitada al fotograma); devuelve el recorte y la región efectiva."""
    h, w = image.shape[:2]
    if region is None:
        return image, Rect(0, 0, w, h)
    x0, y0 = max(0, region.x), max(0, region.y)
    x1, y1 = min(w, region.x + region.w), min(h, region.y + region.h)
    if x1 <= x0 or y1 <= y0:
        return image[0:0, 0:0], Rect(x0, y0, 0, 0)
    return image[y0:y1, x0:x1], Rect(x0, y0, x1 - x0, y1 - y0)
