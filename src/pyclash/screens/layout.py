"""Dónde está cada indicador en la pantalla, como fracciones del fotograma.

PROVISIONAL: los recortes y tamaños de fuente vienen de PixelChief (medidos en una captura de
2560×1440) y NO están verificados contra la ventana de Google Play Games. Hay que calibrarlos con
capturas reales anonimizadas (herramienta de calibración de la issue de diagnóstico).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..device.base import Rect
from ..vision.result import fraction_rect

BASE_HEIGHT = 1440  # alto de referencia de los tamaños de fuente


@dataclass(frozen=True)
class RoiFraction:
    x: float
    y: float
    w: float
    h: float

    def rect(self, image: np.ndarray) -> Rect:
        return fraction_rect(image, self.x, self.y, min(1.0, self.x + self.w), min(1.0, self.y + self.h))


@dataclass(frozen=True)
class HomeLayout:
    builder_chip: RoiFraction = RoiFraction(0.492, 0.028, 0.048, 0.043)
    # Sin medir en el Builder Base: se supone la misma posición que en la aldea principal.
    builder_base_chip: RoiFraction = RoiFraction(0.492, 0.028, 0.048, 0.043)
    lab_chip: RoiFraction = RoiFraction(0.394, 0.026, 0.048, 0.036)
    # Esquina superior derecha (550×400 a 2560×1440): tres filas apiladas oro, elixir, oscuro.
    hud: RoiFraction = RoiFraction(0.785, 0.0, 0.215, 0.278)
    chip_font_px: int = 26  # a BASE_HEIGHT
    hud_font_px: int = 30
    max_home_builders: int = 6  # un chip con más es una lectura corrupta (p. ej. «8/8» de un diálogo)
    max_lab_slots: int = 2
    max_builder_base_builders: int = 2


DEFAULT_LAYOUT = HomeLayout()


def scaled_font_px(image: np.ndarray, base_px: int) -> int:
    """Tamaño de fuente para este fotograma, proporcional a su alto."""
    return max(1, round(base_px * image.shape[0] / BASE_HEIGHT))
