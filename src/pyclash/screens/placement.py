"""Fase de colocación de un edificio nuevo: la «X» roja y el «✓» sobre el terreno.

La pantalla no nombra el edificio ni muestra el coste: quien llama debe haber leído antes la fila.
Aquí solo se responde a «¿se puede confirmar ahora?»:

  * `ready`: el ✓ está verde, el juego acepta el sitio que propuso.
  * `blocked`: el ✓ está en gris (sin sitio libre) y el fantasma sale rayado en rojo.
  * `unknown`: no se encuentra una única X, o el ✓ no es claramente ni verde ni gris.

Solo `ready` autoriza pulsar el ✓. Todo lo demás se cancela con la X, que no gasta nada.
Calibrado con capturas reales (1280×720): el ✓ queda a ~1,64 anchos de la X, verde ≈0,48 / gris ≈0,84.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from ..device.base import Rect
from ..vision.buttons import find_close_buttons
from .signatures import box

State = Literal["ready", "blocked", "unknown"]

# Zona del mapa: excluye el HUD y las esquinas (el buzón de abajo a la derecha tiene un círculo rojo).
MAP_REGION = box(0.10, 0.10, 0.95, 0.78)
_CHECK_OFFSET = 1.64  # distancia entre los centros de la X y el ✓, en anchos de la X
_CHECK_SIDE = 0.8  # lado de la muestra sobre el ✓, en anchos de la X
_GREEN = ((35, 110, 110), (85, 255, 255))
_GREY_MAX_SAT, _GREY_VAL = 50, (110, 240)
_READY_GREEN, _READY_MAX_GREY = 0.30, 0.20
_BLOCKED_GREY, _BLOCKED_MAX_GREEN = 0.50, 0.05


@dataclass(frozen=True)
class Placement:
    cancel: Rect  # la X
    confirm: Rect  # dónde está (o estaría) el ✓
    state: State
    green: float
    grey: float


def read_placement(image: np.ndarray) -> Placement | None:
    """None si no hay una única «X» roja en la zona del mapa (no es la fase de colocación)."""
    found = find_close_buttons(image, MAP_REGION.rect(image))
    if len(found) != 1:
        return None
    cancel = found[0].rect
    cx = cancel.x + cancel.w / 2 + _CHECK_OFFSET * cancel.w
    cy = cancel.y + cancel.h / 2
    half = _CHECK_SIDE * cancel.w / 2
    height, width = image.shape[:2]
    x0, x1 = max(0, int(cx - half)), min(width, int(cx + half))
    y0, y1 = max(0, int(cy - half)), min(height, int(cy + half))
    confirm = Rect(int(cx - cancel.w / 2), int(cy - cancel.h / 2), cancel.w, cancel.h)
    if x1 <= x0 or y1 <= y0:
        return Placement(cancel, confirm, "unknown", 0.0, 0.0)
    hsv = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_RGB2HSV)
    green = float(cv2.inRange(hsv, *_GREEN).astype(bool).mean())
    sat, val = hsv[..., 1], hsv[..., 2]
    grey = float(((sat <= _GREY_MAX_SAT) & (val >= _GREY_VAL[0]) & (val <= _GREY_VAL[1])).mean())
    state: State = "unknown"
    if green >= _READY_GREEN and grey < _READY_MAX_GREY:
        state = "ready"
    elif grey >= _BLOCKED_GREY and green < _BLOCKED_MAX_GREEN:
        state = "blocked"
    return Placement(cancel, confirm, state, green, grey)
