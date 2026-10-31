"""Geometría pura del área de juego: recorte, ajuste a 16:9 y conversión fracción ↔ píxel."""

from __future__ import annotations

import numpy as np

from .base import Rect

ASPECT_16_9 = 16 / 9
ASPECT_TOLERANCE = 0.03


def is_16_9(width: int, height: int, tolerance: float = ASPECT_TOLERANCE) -> bool:
    return width > 0 and height > 0 and abs(width / height - ASPECT_16_9) <= tolerance


def aspect_label(width: int, height: int) -> str:
    if is_16_9(width, height):
        return "16:9"
    return f"{width / height:.2f}:1" if width > 0 and height > 0 else "?"


def play_area(image: np.ndarray, threshold: int = 8) -> Rect | None:
    """Rectángulo mínimo que contiene todo lo que no es negro; None si la imagen es toda negra.

    Excluye las barras negras laterales o superiores/inferiores de la ventana.
    """
    lit = image.max(axis=2) > threshold
    cols = np.flatnonzero(lit.any(axis=0))
    if cols.size == 0:
        return None
    rows = np.flatnonzero(lit.any(axis=1))
    x0, x1 = int(cols[0]), int(cols[-1])
    y0, y1 = int(rows[0]), int(rows[-1])
    return Rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def is_black(image: np.ndarray, threshold: int = 8) -> bool:
    return play_area(image, threshold) is None


def fraction_to_pixel(fx: float, fy: float, size: tuple[int, int]) -> tuple[int, int]:
    """Convierte una fracción (0..1) del área de juego a píxel, dentro de [0, tamaño-1]."""
    w, h = size
    return min(max(int(fx * w), 0), w - 1), min(max(int(fy * h), 0), h - 1)


def pixel_to_fraction(x: int, y: int, size: tuple[int, int]) -> tuple[float, float]:
    w, h = size
    return x / w, y / h


def fit_16_9(
    surface: tuple[int, int],
    outer: tuple[int, int, int, int],
    work: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Rectángulo exterior (x, y, ancho, alto) de la ventana cuya superficie de juego es el mayor 16:9 que cabe.

    `surface` es (ancho, alto) de la superficie, `outer` y `work` son (izq, arriba, der, abajo).
    La diferencia entre `outer` y `surface` es el marco de la ventana, que no cambia al redimensionar.
    """
    dw = outer[2] - outer[0] - surface[0]
    dh = outer[3] - outer[1] - surface[1]
    work_w, work_h = work[2] - work[0], work[3] - work[1]
    w = int(min(work_w - dw, (work_h - dh) * 16 / 9))
    h = round(w * 9 / 16)
    ow, oh = w + dw, h + dh
    x = max(work[0], min(outer[0], work[2] - ow))
    y = max(work[1], min(outer[1], work[3] - oh))
    return x, y, ow, oh
