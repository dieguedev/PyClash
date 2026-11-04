"""Salidas seguras de una pantalla que no se conoce: botón de cerrar y terreno vacío.

Son las únicas dos cosas que se pulsan en una pantalla desconocida (nunca un «OK»). Calibrado con
capturas reales de la ventana de Google Play Games; los umbrales están medidos, no supuestos.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

import cv2
import numpy as np
from PIL import Image

from ..device.base import Rect
from .result import Match, Provenance, crop

# El botón de cerrar es un cuadrado rojo con una «X» blanca. Con un diálogo encima el rojo se
# oscurece y desatura y la «X» se vuelve gris: por eso se mide la baja saturación, no el blanco.
# El color solo propone candidatos (un tejado rojo o una bomba con calavera también lo cumplen): los
# confirma la forma, comparando con un recorte real del botón (`assets/icons/close_x.png`, a 720 de alto).
_RED_LOW = ((0, 120, 90), (8, 255, 255))
_RED_HIGH = ((172, 120, 90), (180, 255, 255))
_MIN_SIDE, _MAX_SIDE = 0.03, 0.10  # lado del botón, como fracción del alto del fotograma
_MIN_FILL = 0.40  # un tejado rojo rellena menos de 0.3 de su caja; el botón, más de 0.45
_MIN_GLYPH = 0.08  # fracción de píxeles poco saturados (la «X») dentro de la caja
_X_MIN_SCORE = 0.7  # medido: las «X» reales (mapa y diálogos oscurecidos) dan 0.84-1.0; tejados, bombas y otros 0.16-0.54
_X_SCALE_SWEEP = (0.85, 0.92, 1.0, 1.08, 1.15)  # relativo al lado de la caja del candidato
_X_WINDOW_PAD = 0.25  # margen alrededor del candidato (su caja puede incluir rojo de más, p. ej. el de un edificio pegado)

# Hierba del juego: matiz verde amarillento, saturación media, brillo alto y casi plano. Un botón
# verde es más brillante y menos saturado; el terreno bajo un diálogo oscurecido es mucho más oscuro.
_GRASS_HUE = (26, 45)  # el verde oscurecido de un menú abierto deriva hacia 28
_GRASS_SAT = (110, 200)
_GRASS_VAL = (120, 230)
_GRASS_MIN_FRACTION = 0.95
_GRASS_MAX_VAL_STD = 14.0


@lru_cache(maxsize=1)
def _x_template() -> np.ndarray:
    with (resources.files("pyclash") / "assets" / "icons" / "close_x.png").open("rb") as handle, Image.open(handle) as im:
        return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2GRAY)


def x_shape_score(image: np.ndarray, rect: Rect) -> float:
    """Parecido (correlación normalizada, -1..1) entre el candidato y el botón «X» real, buscando en su entorno.

    Se busca con un barrido de escala en una ventana algo mayor que el candidato: su caja de color puede
    salir descentrada o con rojo de más. La correlación ignora el brillo, así que vale también con el
    botón oscurecido bajo un diálogo.
    """
    template = _x_template()
    side = max(rect.w, rect.h)
    pad = int(side * _X_WINDOW_PAD)
    x0, y0 = max(0, rect.x - pad), max(0, rect.y - pad)
    window = cv2.cvtColor(image[y0 : rect.y + rect.h + pad, x0 : rect.x + rect.w + pad], cv2.COLOR_RGB2GRAY)
    best = -1.0
    for k in _X_SCALE_SWEEP:
        f = side * k / template.shape[1]
        scaled = cv2.resize(template, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
        if window.shape[0] < scaled.shape[0] or window.shape[1] < scaled.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(window, scaled, cv2.TM_CCOEFF_NORMED).max()))
    return best


def find_close_buttons(image: np.ndarray, region: Rect | None = None) -> list[Match]:
    """Botones de cerrar: candidatos por color (rojo con «X») confirmados por la forma de la «X» real.

    Ordenados de mayor a menor confianza (relleno de la caja).
    """
    section, eff = crop(image, region)
    if section.size == 0:
        return []
    height = image.shape[0]
    hsv = cv2.cvtColor(section, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, *_RED_LOW) | cv2.inRange(hsv, *_RED_HIGH)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    found: list[Match] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (_MIN_SIDE * height <= w <= _MAX_SIDE * height and 0.8 <= w / h <= 1.25):
            continue
        fill = area / (w * h)
        glyph = float((hsv[y:y + h, x:x + w, 1] < 80).mean())
        if fill < _MIN_FILL or glyph < _MIN_GLYPH:
            continue
        rect = Rect(eff.x + x, eff.y + y, w, h)
        if x_shape_score(image, rect) < _X_MIN_SCORE:
            continue
        found.append(Match(rect, float(fill), Provenance(eff, "color:close-button")))
    return sorted(found, key=lambda m: m.confidence, reverse=True)


def find_empty_terrain(
    image: np.ndarray,
    region: Rect | None = None,
    patch: float = 0.05,
    min_value: int = _GRASS_VAL[0],
) -> Match | None:
    """Un cuadrado de hierba lisa dentro de `region`, el más cercano a su centro; None si no hay.

    `patch` es el lado como fracción del alto. Hierba oscurecida (un diálogo encima), un botón
    verde o un edificio no cuentan: pulsar ahí no sería pulsar terreno vacío. Con `min_value` bajo
    se acepta terreno oscurecido: solo para pantallas ya identificadas, cuyo menú atenúa el mundo.
    """
    section, eff = crop(image, region)
    side = max(2, int(image.shape[0] * patch))
    if section.shape[0] < side or section.shape[1] < side:
        return None
    hsv = cv2.cvtColor(section, cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    grass = (
        (hue >= _GRASS_HUE[0]) & (hue <= _GRASS_HUE[1])
        & (sat >= _GRASS_SAT[0]) & (sat <= _GRASS_SAT[1])
        & (val >= min_value) & (val <= _GRASS_VAL[1])
    )
    cy, cx = section.shape[0] / 2, section.shape[1] / 2
    best: tuple[float, Match] | None = None
    for y in range(0, section.shape[0] - side + 1, side // 2):
        for x in range(0, section.shape[1] - side + 1, side // 2):
            fraction = float(grass[y:y + side, x:x + side].mean())
            if fraction < _GRASS_MIN_FRACTION or float(val[y:y + side, x:x + side].std()) > _GRASS_MAX_VAL_STD:
                continue
            dist = (y + side / 2 - cy) ** 2 + (x + side / 2 - cx) ** 2
            match = Match(Rect(eff.x + x, eff.y + y, side, side), fraction, Provenance(eff, "color:terrain"))
            if best is None or dist < best[0]:
                best = (dist, match)
    return best[1] if best else None


def is_blank(image: np.ndarray, max_mean: float = 6.0, max_std: float = 6.0) -> bool:
    """Captura negra o sin contenido: casi todo el fotograma es del mismo tono oscuro."""
    if image.size == 0:
        return True
    gray = image.mean(axis=2) if image.ndim == 3 else image
    return float(gray.mean()) <= max_mean and float(gray.std()) <= max_std
