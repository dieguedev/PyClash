"""Utilidades de color: fracción de color, coincidencia en LAB y botón verde por blob."""

from __future__ import annotations

import cv2
import numpy as np

from ..device.base import Rect
from .result import Match, Provenance, crop

RGB = tuple[int, int, int]

# Verde de los botones de confirmación (Mejorar, Atacar...).
BUTTON_GREEN: RGB = (110, 200, 30)
# Rojo del coste cuando no hay recursos suficientes.
INSUFFICIENT_RED: RGB = (255, 123, 114)


def _to_lab(image: np.ndarray) -> np.ndarray:
    # En float32 OpenCV da L en 0..100 y a/b en -127..127 (distancias CIE76 interpretables).
    return cv2.cvtColor(image.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def lab_mask(image: np.ndarray, color: RGB, tolerance: float = 20.0) -> np.ndarray:
    """Máscara booleana de los píxeles a distancia LAB <= `tolerance` de `color`."""
    target = _to_lab(np.array([[color]], dtype=np.uint8))[0, 0]
    return np.linalg.norm(_to_lab(image) - target, axis=2) <= tolerance


def color_fraction(image: np.ndarray, color: RGB, tolerance: float = 20.0) -> float:
    """Fracción de píxeles de `image` parecidos a `color` (0..1)."""
    if image.size == 0:
        return 0.0
    return float(lab_mask(image, color, tolerance).mean())


def has_color(image: np.ndarray, color: RGB, tolerance: float = 20.0, min_fraction: float = 0.001) -> bool:
    return color_fraction(image, color, tolerance) >= min_fraction


def isolate_color(image: np.ndarray, color: RGB, tolerance: float = 20.0) -> np.ndarray:
    """Copia de `image` con todo lo que no se parece a `color` puesto en negro."""
    out = image.copy()
    out[~lab_mask(image, color, tolerance)] = 0
    return out


def find_green_buttons(
    image: np.ndarray,
    region: Rect | None = None,
    min_area: int = 400,
    min_fill: float = 0.6,
) -> list[Match]:
    """Botones verdes: blobs de matiz verde, saturados y rellenos, de mayor a menor área.

    `confidence` es la fracción de relleno de la caja (un botón es casi un rectángulo).
    """
    section, eff = crop(image, region)
    if section.size == 0:
        return []
    hsv = cv2.cvtColor(section, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, (30, 120, 120), (85, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    found = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        fill = area / (w * h)
        if area < min_area or fill < min_fill:
            continue
        rect = Rect(eff.x + x, eff.y + y, w, h)
        found.append((area, Match(rect, float(fill), Provenance(eff, "color:green-button"))))
    return [m for _, m in sorted(found, key=lambda t: t[0], reverse=True)]

