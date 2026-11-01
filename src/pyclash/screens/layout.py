"""Dónde está cada indicador en la pantalla, como fracciones del fotograma.

Calibrado con capturas reales de Google Play Games (16:9, 1763×991 y 1280×720) en inglés: el chip de
constructores está abajo a la derecha, encima de «Build», y el HUD de recursos arriba a la derecha.
Los tamaños de fuente son a `BASE_HEIGHT` y se barren ±1 al leer. Si el juego cambia su interfaz, la
pestaña «Verificación» → «Leer la aldea» dice qué campo dejó de leerse.
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
    # Franja de chips sobre «Build»: de izquierda a derecha, constructores y después el segundo chip
    # (laboratorio). Su posición depende de cuántos chips tenga la cuenta: se lee la franja entera y se
    # separan los chips por los huecos entre sus cifras.
    chip_strip: RoiFraction = RoiFraction(0.895, 0.793, 0.100, 0.036)
    # Sin medir en el Builder Base: se supone la misma franja que en la aldea principal.
    builder_base_strip: RoiFraction = RoiFraction(0.895, 0.793, 0.100, 0.036)
    # Esquina superior derecha: una barra por recurso (oro, elixir). El elixir oscuro es la tercera
    # barra solo en las cuentas que lo tienen y en otras esa fila son las gemas: no se lee por posición.
    hud: RoiFraction = RoiFraction(0.785, 0.0, 0.215, 0.278)  # esquina entera, para buscar iconos de almacén
    gold_bar: RoiFraction = RoiFraction(0.800, 0.020, 0.170, 0.050)
    elixir_bar: RoiFraction = RoiFraction(0.800, 0.077, 0.170, 0.050)
    chip_font_px: int = 31  # a BASE_HEIGHT
    hud_font_px: int = 26
    max_home_builders: int = 6  # un chip con más es una lectura corrupta (p. ej. «8/8» de un diálogo)
    max_lab_slots: int = 2
    max_builder_base_builders: int = 2


DEFAULT_LAYOUT = HomeLayout()


def scaled_font_px(image: np.ndarray, base_px: int) -> int:
    """Tamaño de fuente para este fotograma, proporcional a su alto."""
    return max(1, round(base_px * image.shape[0] / BASE_HEIGHT))
