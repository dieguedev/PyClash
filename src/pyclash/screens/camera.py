"""Cuánto se movió el mapa entre dos capturas (correlación de fase sobre el terreno visible).

Sirve para detectar un trazo que arrastró el mapa en vez de soltar tropas: la zona roja leída en la
exploración queda desplazada ese mismo vector y hay que corregirla antes de volver a intentarlo.
"""

from __future__ import annotations

import cv2
import numpy as np

MIN_RESPONSE = 0.05  # confianza mínima de la correlación; por debajo se dice «no sé»
MIN_SHIFT_PX = 1.5  # menos que esto es ruido (animaciones, tropas recién soltadas)


def estimate_shift(before: np.ndarray, after: np.ndarray) -> tuple[int, int] | None:
    """(dx, dy) en píxeles que se desplazó el contenido de `before` hasta `after`; (0, 0) si no se movió;
    None si no se puede saber."""
    if before.shape != after.shape or before.ndim != 3:
        return None
    h, w = before.shape[:2]
    rows, cols = slice(int(0.12 * h), int(0.80 * h)), slice(int(0.15 * w), int(0.85 * w))
    a = cv2.cvtColor(before[rows, cols], cv2.COLOR_RGB2GRAY).astype(np.float32)
    b = cv2.cvtColor(after[rows, cols], cv2.COLOR_RGB2GRAY).astype(np.float32)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    if response < MIN_RESPONSE:
        return None
    if abs(dx) < MIN_SHIFT_PX and abs(dy) < MIN_SHIFT_PX:
        return 0, 0
    return round(dx), round(dy)


__all__ = ["estimate_shift"]
