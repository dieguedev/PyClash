"""Nivel del Ayuntamiento y tarjeta del TH10: las dos lecturas que gobiernan si el bot puede subir el Ayuntamiento.

Regla de oro: ante cualquier duda, `None`, y quien decida trata `None` como «el Ayuntamiento no se sube».

`read_town_hall_level`: el perfil de arriba a la izquierda (solo se dibuja con la aldea a la vista) muestra el icono del
Ayuntamiento de la cuenta. Se compara con un recorte real por nivel (`assets/town_halls/th<N>.png`); solo se devuelve un
nivel si una plantilla encaja claramente y las demás quedan lejos. Los niveles sin plantilla (hoy TH7 y TH8) y todo lo
dudoso dan `None`: no se confunde «otro nivel» con «icono tapado».

`offers_town_hall_10`: el número de la insignia de una fila es el nivel AL QUE SE MEJORA, así que un Ayuntamiento 9
muestra la tarjeta del TH10 (el icono con lava). El 10 tiene dos dígitos y no cabe en la ventana de un dígito de
`level_badge`, por eso se reconoce por el icono: un recorte real (`assets/icons/town_hall_10.png`) buscado en el panel.
En TH9 la tarjeta NUNCA se pulsa.

La ventana del juego no siempre mide lo mismo (entre 1762 y 1766 de ancho y 989 y 993 de alto según la captura): se tolera
un par de píxeles respecto a 1766×993, no otra escala. A otra escala ambas devuelven None.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_ICON = _ASSETS / "icons" / "town_hall_10.png"
_LEVELS_DIR = _ASSETS / "town_halls"
_CALIBRATED = (1766, 993)  # ancho, alto
_SIZE_TOLERANCE = 5  # píxeles de diferencia con el tamaño calibrado que se aceptan
_PANEL_X = 0.73  # el panel ocupa el costado derecho; a la izquierda solo hay aldea
MIN_SCORE = 0.85
NOT_OFFERED_MAX = 0.65  # medido: ≤ 0,51 en las filas que no son la tarjeta del TH10; lo intermedio es duda

_HUD_REGION = (0.30, 0.20)  # fracción del ancho y del alto de la esquina superior izquierda donde está el perfil
LEVEL_MIN_SCORE = 0.90  # medido: ≥ 0,98 con el nivel correcto en el HUD; ≤ 0,5 sin HUD
LEVEL_MIN_MARGIN = 0.10  # lo que debe sacarle la mejor plantilla a la segunda (TH3 y TH4 llegan a 0,82 entre sí)


def _calibrated_size(image: np.ndarray) -> bool:
    h, w = image.shape[:2]
    return abs(w - _CALIBRATED[0]) <= _SIZE_TOLERANCE and abs(h - _CALIBRATED[1]) <= _SIZE_TOLERANCE


@lru_cache(maxsize=1)
def _template() -> np.ndarray:
    with Image.open(_ICON) as im:
        return np.array(im.convert("RGB"))


@lru_cache(maxsize=1)
def _level_templates() -> dict[int, np.ndarray]:
    """Una plantilla por nivel disponible en `assets/town_halls/th<N>.png`."""
    found: dict[int, np.ndarray] = {}
    for path in sorted(_LEVELS_DIR.glob("th*.png")):
        digits = path.stem[2:]
        if digits.isdigit():
            with Image.open(path) as im:
                found[int(digits)] = np.array(im.convert("RGB"))
    return found


def read_town_hall_level(image: np.ndarray) -> int | None:
    """Nivel del Ayuntamiento que muestra el perfil de la aldea, o None ante cualquier duda.

    None si: otra escala, el HUD no está a la vista (menú abierto), ninguna plantilla encaja con claridad, dos encajan
    parecido, o el nivel no tiene plantilla. Nunca se devuelve un nivel «por descarte».
    """
    if not _calibrated_size(image):
        return None
    h, w = image.shape[:2]
    corner = image[: int(_HUD_REGION[1] * h), : int(_HUD_REGION[0] * w)]
    scores: dict[int, float] = {}
    for level, template in _level_templates().items():
        if corner.shape[0] < template.shape[0] or corner.shape[1] < template.shape[1]:
            continue
        scores[level] = float(cv2.matchTemplate(corner, template, cv2.TM_CCOEFF_NORMED).max())
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_level, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best < LEVEL_MIN_SCORE or best - second < LEVEL_MIN_MARGIN:
        return None
    return best_level


def offers_town_hall_10(image: np.ndarray) -> bool | None:
    """True si el panel muestra la tarjeta del TH10, False si seguro que no, None ante cualquier duda.

    Duda: otra escala o una puntuación entre `NOT_OFFERED_MAX` y `MIN_SCORE` (algo parecido a la tarjeta que no llega a
    reconocerse). Solo con una puntuación claramente baja se dice False: es lo que deja subir el Ayuntamiento.
    """
    if not _calibrated_size(image):
        return None
    w = image.shape[1]
    panel = image[:, int(_PANEL_X * w) :]
    score = float(cv2.matchTemplate(panel, _template(), cv2.TM_CCOEFF_NORMED).max())
    if score >= MIN_SCORE:
        return True
    return False if score <= NOT_OFFERED_MAX else None
