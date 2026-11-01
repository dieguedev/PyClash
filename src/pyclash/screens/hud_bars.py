"""Barras de oro y elixir del HUD: cuánto de la barra está relleno (issue #13, «almacén lleno»).

Medido sobre capturas reales (juego en inglés): cada recurso es una barra que se llena de DERECHA a
IZQUIERDA desde el icono (la moneda, la gota). La parte vacía lleva un brillo grisáceo y las cifras
ocupan el centro, así que se mide en una fila baja de la barra, por debajo de las cifras. No hace falta
saber la capacidad del almacén: un almacén lleno es una barra rellena hasta el borde izquierdo.

Si el icono del recurso no está a la derecha de la barra (HUD tapado por un panel, otra pantalla), el
resultado es `None`: «no se sabe» nunca cuenta como lleno ni como vacío.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

from ..device.base import Rect
from ..vision.result import crop
from .layout import RoiFraction

Resource = Literal["gold", "elixir"]

# Fracciones del fotograma (medidas a 1280×720; el HUD escala con la ventana).
BARS: dict[str, RoiFraction] = {
    "gold": RoiFraction(0.8164, 0.0264, 0.1500, 0.0375),
    "elixir": RoiFraction(0.8164, 0.0833, 0.1500, 0.0375),
}
ICON_WIDTH = 0.018  # a la derecha de la barra, como fracción del ancho
SAMPLE_ROW = 0.84  # fila de la barra (0 arriba, 1 abajo) donde se mide: debajo de las cifras

# Rangos HSV de OpenCV (H 0-179) del relleno y del icono.
_FILL = {
    "gold": ((16, 34), 120, 150),
    "elixir": ((130, 170), 70, 100),
}


def _mask(hsv: np.ndarray, resource: str) -> np.ndarray:
    (h0, h1), s_min, v_min = _FILL[resource]
    return (hsv[..., 0] >= h0) & (hsv[..., 0] <= h1) & (hsv[..., 1] >= s_min) & (hsv[..., 2] >= v_min)


def read_fill(image: np.ndarray, resource: Resource) -> float | None:
    """Fracción (0-1) de la barra de `resource` rellena, o None si no se ve la barra con su icono."""
    bar = BARS[resource].rect(image)
    w = image.shape[1]
    icon = Rect(bar.x + bar.w, bar.y - bar.h // 2, max(4, int(ICON_WIDTH * w)), bar.h * 2)
    icon_px, _ = crop(image, icon)
    bar_px, _ = crop(image, bar)
    if icon_px.size == 0 or bar_px.size == 0:
        return None
    icon_mask = _mask(cv2.cvtColor(icon_px, cv2.COLOR_RGB2HSV), resource)
    if float(icon_mask.mean()) < 0.25:
        return None  # sin icono a la derecha: el HUD no está a la vista
    row = min(bar_px.shape[0] - 1, int(SAMPLE_ROW * bar_px.shape[0]))
    hsv = cv2.cvtColor(bar_px[row : row + 1], cv2.COLOR_RGB2HSV)
    filled = _mask(hsv, resource)[0]
    if not filled.any():
        return 0.0
    # Se recorre desde la derecha: el relleno es continuo hasta su borde izquierdo.
    run = 0
    for i in range(len(filled) - 1, -1, -1):
        if filled[i]:
            run += 1
        elif run and (len(filled) - 1 - i) - run <= 3:
            continue  # hueco de un píxel o dos (antialiasing, cifras): no corta el relleno
        else:
            break
    return max(0.0, min(1.0, run / len(filled)))


__all__ = ["BARS", "read_fill"]
