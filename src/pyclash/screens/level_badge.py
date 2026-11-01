"""Nivel de una fila del panel, leído de la insignia (cuadrado oscuro con un dígito blanco) junto al icono.

En las filas de muros ese número es el NIVEL AL QUE SE MEJORA, y el elixir no sirve hasta el 5: los niveles
2, 3 y 4 se pagan solo con oro. El dígito mide unos 5×8 píxeles, así que no se lee con la fuente dibujada
(confundía el 3 con el 8): se compara con RECORTES REALES del juego (`assets/level_badges/<dígito>.png`,
promedio de varias muestras). Se compara en gris, en una ventana fija centrada en el dígito con remuestreo
subpíxel, porque cada fila del panel cae en un subpíxel distinto y la caja del dígito varía en 1 px: así el mismo
dígito puntúa ≥ 0,93 y uno distinto ≤ 0,72 (medido sobre 9 muestras de dos capturas).

Es una PISTA SEGURA, no una garantía: devuelve `None` ante cualquier duda (otra escala de ventana, un
dígito sin plantilla, una coincidencia que no gana con margen), y quien la usa solo la aprovecha para
PROHIBIR elixir, nunca para permitirlo. La garantía final es que el juego ofrezca el botón de elixir.
Para añadir un dígito, basta con su recorte en `assets/level_badges/`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

_DIR = Path(__file__).resolve().parent.parent / "assets" / "level_badges"
# Zona de la insignia respecto al nombre de la fila (medida a 1766×993): fracciones del ancho y del alto.
_X = (0.7367, 0.7559)
_DY = (0.0222, 0.0463)
_INK = 180  # mínimo de los tres canales para contar un píxel como parte del dígito
_HALF = (7, 8)  # semiancho y semialto de la ventana de comparación (15×17 px)
MIN_SCORE = 0.9  # coincidencia mínima con una plantilla
MIN_MARGIN = 0.2  # ventaja mínima sobre la segunda mejor
# Los muros solo necesitan saber si el nivel es 2, 3 o 4 (se pagan solo con oro): con esos tres dígitos el margen
# estricto se cumple de sobra. NO se amplía a los demás para no relajar esa garantía.
GOLD_ONLY_DIGITS = (2, 3, 4)
# Para el resto de edificios se comparan todos los dígitos. 5, 6 y 8 se parecen mucho a 15×17 px: medido entre dos
# capturas, el dígito correcto puntúa 0,95-0,99 y el mejor rival hasta 0,93, así que el margen es mucho menor.
ANY_MIN_SCORE = 0.85
ANY_MIN_MARGIN = 0.05


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def window(image: np.ndarray, name_y: int) -> np.ndarray | None:
    """Ventana gris (15×17) centrada en el dígito de la insignia de la fila cuyo nombre está en `name_y`."""
    h, w = image.shape[:2]
    y0, y1 = int(name_y + _DY[0] * h), int(name_y + _DY[1] * h)
    x0, x1 = int(_X[0] * w), int(_X[1] * w)
    patch = image[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)]
    if patch.size == 0:
        return None
    gray = patch.min(axis=2).astype(np.float32)
    ys, xs = np.where(gray > _INK)
    if len(xs) < 5:
        return None
    shift = np.float32([[1, 0, _HALF[0] - xs.mean()], [0, 1, _HALF[1] - ys.mean()]])
    size = (2 * _HALF[0] + 1, 2 * _HALF[1] + 1)
    return cv2.warpAffine(gray, shift, size, flags=cv2.INTER_LINEAR, borderValue=0)


@lru_cache(maxsize=1)
def _templates() -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    for path in sorted(_DIR.glob("*.png")):
        if path.stem.isdigit():
            with Image.open(path) as im:
                out[int(path.stem)] = np.array(im.convert("L"), dtype=np.float32)
    return out


def read_level(
    image: np.ndarray,
    name_y: int,
    digits: tuple[int, ...] = GOLD_ONLY_DIGITS,
    min_score: float = MIN_SCORE,
    min_margin: float = MIN_MARGIN,
) -> int | None:
    """Nivel de la insignia de la fila cuyo nombre está en `name_y`, o None si no se reconoce con seguridad.

    Por defecto solo compara con 2, 3 y 4 (lo que necesitan los muros). Para cualquier nivel usa `read_any_level`.
    """
    probe = window(image, name_y)
    templates = {k: t for k, t in _templates().items() if k in digits}
    if probe is None or not templates:
        return None
    scores = sorted(((_ncc(probe, t), level) for level, t in templates.items() if t.shape == probe.shape), reverse=True)
    if not scores:
        return None
    runner_up = scores[1][0] if len(scores) > 1 else 0.0
    if scores[0][0] < min_score or scores[0][0] - runner_up < min_margin:
        return None
    return scores[0][1]


def read_any_level(image: np.ndarray, name_y: int) -> int | None:
    """Como `read_level`, pero con todos los dígitos que tengan plantilla y un margen realista entre parecidos.

    Es la lectura para el nivel de un edificio (p. ej. el Ayuntamiento). Devuelve None ante la duda.
    """
    return read_level(image, name_y, tuple(_templates()), ANY_MIN_SCORE, ANY_MIN_MARGIN)
