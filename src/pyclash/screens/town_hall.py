"""¿La tarjeta del Ayuntamiento del panel ofrece subir a TH10? Solo dice que la cuenta está en TH9.

El número de la insignia de una fila es el nivel AL QUE SE MEJORA, así que un Ayuntamiento 9 muestra la tarjeta del
TH10 (el icono con lava). Esto NO es la condición de parada: el MVP termina cuando, estando en TH9, no queda
ninguna otra mejora posible salvo esta tarjeta. Esa decisión es de la política; aquí solo se detecta el nivel.
En TH9 la tarjeta NUNCA se pulsa. El 10 tiene dos dígitos y no cabe en la ventana de un dígito de `level_badge`,
por eso se reconoce por el icono: un recorte real (`assets/icons/town_hall_10.png`) buscado en el panel.

Está calibrado a 1766×993 (medido: 1,0 en la tarjeta del TH10 y ≤ 0,51 en las demás filas). A otra escala devuelve
None («no sé»), nunca False: quien lo use debe tratar la duda como «no pulsar».
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_ICON = Path(__file__).resolve().parent.parent / "assets" / "icons" / "town_hall_10.png"
_CALIBRATED = (1766, 993)  # ancho, alto
_PANEL_X = 0.73  # el panel ocupa el costado derecho; a la izquierda solo hay aldea
MIN_SCORE = 0.85


@lru_cache(maxsize=1)
def _template() -> np.ndarray:
    with Image.open(_ICON) as im:
        return np.array(im.convert("RGB"))


def offers_town_hall_10(image: np.ndarray) -> bool | None:
    """True si el panel muestra la tarjeta del TH10, False si no, None si no se puede saber (otra escala)."""
    h, w = image.shape[:2]
    if (w, h) != _CALIBRATED:
        return None
    panel = image[:, int(_PANEL_X * w) :]
    score = float(cv2.matchTemplate(panel, _template(), cv2.TM_CCOEFF_NORMED).max())
    return score >= MIN_SCORE
