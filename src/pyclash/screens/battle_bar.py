"""Barra de despliegue de la batalla: localiza las cartas por bordes, lee su contador «xN» y dice si una
carta está agotada. Sin plantillas por tropa (la identidad de cada tropa la da `troops.py`).

Idea de CoC_Bot (`detect_troop_positions`): los bordes verticales de las cartas dan un perfil de
gradiente con picos en los cantos, y las distancias entre picos distinguen «ancho de carta», «hueco
normal» y «hueco de cambio de tipo». Aquí el perfil es la **fracción de filas** de la banda donde hay
gradiente horizontal: el canto de una carta es continuo de arriba abajo (≈1.0) y el contorno punteado de
un hueco vacío no (≈0.6), así que los huecos vacíos no dan cartas y no hace falta umbral relativo.

Medido sobre capturas reales (1280×720 y 1765×990): ancho de carta 0.056–0.062 del ancho del fotograma,
hueco normal ≈0.006. Una carta agotada (gris, «x0») conserva sus cantos pero con gradiente débil: por eso el
umbral de gradiente es bajo y la exigencia de continuidad alta.

PROVISIONAL (sin capturas reales): la clasificación de hechizo / héroe / clan. Solo hay barras con tropas.
Se basa en las reglas de CoC_Bot: un contador «xN» marca tropa, hechizo o clan; no tenerlo, héroe; un hueco
mayor que el normal separa los grupos (tropas | hechizos | clan); una carta aislada entre dos huecos
grandes es de clan. Desplegar héroes, hechizos y clan es de la issue #31.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from ..device.base import Rect
from ..runtime.context import Context
from ..vision.text import DigitReader
from .layout import scaled_font_px

CardKind = Literal["troop", "spell", "hero", "clan"]

BAND_TOP = 0.865  # fracciones del alto: banda donde se mide el perfil de bordes
BAND_BOTTOM = 0.985
CARD_TOP = 0.855  # las cartas ocupan de aquí
CARD_BOTTOM = 0.99
GRADIENT_MIN = 12.0  # gradiente horizontal (0..255) por encima del cual cuenta como borde
COVERAGE_MIN = 0.8  # fracción de la banda con borde para que una columna sea un canto
MIN_CARD_W = 0.045  # fracciones del ancho del fotograma
MAX_CARD_W = 0.072
TYPE_GAP = 0.011  # un hueco mayor separa grupos de cartas (CoC_Bot: 0.007 normal, 0.015 de cambio)
OVERFLOW_X = 0.93  # una carta que llega más allá de aquí hace pensar que la barra continúa
COUNT_PX = 30  # tamaño de fuente del contador a BASE_HEIGHT
COUNT_COLORS = ((255, 255, 255), (170, 170, 170))
EXHAUSTED_SATURATION = 25.0  # una carta agotada es gris: saturación media del retrato ≈ 0 (una activa ≈ 150)


@dataclass(frozen=True)
class Card:
    rect: Rect
    kind: CardKind
    count: int | None  # contador «xN»; None si la carta no lo muestra o no se pudo leer
    exhausted: bool

    @property
    def center(self) -> tuple[int, int]:
        return self.rect.center


@dataclass(frozen=True)
class BarScan:
    cards: tuple[Card, ...]
    scrolled: bool  # hubo que desplazar la barra: las cartas de más allá de la primera pantalla no son pulsables


def _edge_columns(image: np.ndarray) -> list[int]:
    """Columnas que son canto de carta, de izquierda a derecha (un único valor por canto)."""
    h, w = image.shape[:2]
    band = image[int(h * BAND_TOP) : int(h * BAND_BOTTOM)]
    if band.size == 0:
        return []
    gray = cv2.GaussianBlur(cv2.cvtColor(band, cv2.COLOR_RGB2GRAY).astype(np.float32), (3, 3), 0)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)) / 8.0
    coverage = (gx > GRADIENT_MIN).mean(axis=0)
    window = max(2, round(0.005 * w))
    peaks = [
        x
        for x in range(w)
        if coverage[x] >= COVERAGE_MIN and coverage[x] == coverage[max(0, x - window) : x + window + 1].max()
    ]
    merged: list[list[int]] = []
    for x in peaks:  # un canto ancho da varias columnas seguidas
        if merged and x - merged[-1][-1] <= window:
            merged[-1].append(x)
        else:
            merged.append([x])
    return [round(sum(g) / len(g)) for g in merged]


def _pair_edges(edges: list[int], width: int) -> list[tuple[int, int]]:
    """Empareja cantos izquierdo-derecho por el ancho de una carta (primero el más cercano)."""
    lo, hi = MIN_CARD_W * width, MAX_CARD_W * width
    pairs: list[tuple[int, int]] = []
    i = 0
    while i < len(edges):
        match = next((j for j in range(i + 1, len(edges)) if lo <= edges[j] - edges[i] <= hi), None)
        if match is None:
            i += 1
            continue
        pairs.append((edges[i], edges[match]))
        i = match + 1
    return pairs


class BarReader:
    def __init__(self, digits: DigitReader | None = None) -> None:
        self.digits = digits or DigitReader(threshold=0.6)

    # ------------------------------------------------------------------------------ lectura

    def card_rects(self, image: np.ndarray) -> list[Rect]:
        h, w = image.shape[:2]
        top, bottom = int(h * CARD_TOP), int(h * CARD_BOTTOM)
        return [Rect(left, top, right - left, bottom - top) for left, right in _pair_edges(_edge_columns(image), w)]

    def read_count(self, image: np.ndarray, rect: Rect) -> int | None:
        """El «xN» de la esquina superior de la carta, leído con la fuente del juego; None si no hay."""
        size = scaled_font_px(image, COUNT_PX)
        region = Rect(rect.x, rect.y, rect.w, max(1, int(rect.h * 0.3)))
        best = None
        for color in COUNT_COLORS:  # blanco con la carta activa; gris con la carta agotada («x0»)
            for s in (size - 1, size, size + 1):
                if s < 6:
                    continue
                reading = self.digits.read(image, s, color=color, region=region, alphabet="x0123456789")
                if reading is not None and (best is None or reading.confidence > best.confidence):
                    best = reading
        if best is None or not best.value.startswith("x") or not best.value[1:].isdigit():
            return None
        return int(best.value[1:])

    @staticmethod
    def is_exhausted(image: np.ndarray, rect: Rect) -> bool:
        """Una carta sin tropas se pone toda en gris: saturación media del retrato casi nula."""
        x, y = rect.x + int(rect.w * 0.15), rect.y + int(rect.h * 0.25)
        core = image[y : rect.y + int(rect.h * 0.8), x : rect.x + int(rect.w * 0.85)]
        if core.size == 0:
            return False
        return float(cv2.cvtColor(core, cv2.COLOR_RGB2HSV)[..., 1].mean()) < EXHAUSTED_SATURATION

    def detect(self, image: np.ndarray) -> list[Card]:
        """Las cartas visibles, de izquierda a derecha, clasificadas (ver PROVISIONAL en el módulo)."""
        rects = self.card_rects(image)
        width = image.shape[1]
        counts = [self.read_count(image, r) for r in rects]
        gaps = [rects[i + 1].x - (rects[i].x + rects[i].w) for i in range(len(rects) - 1)]
        wide = [g > TYPE_GAP * width for g in gaps]
        cards: list[Card] = []
        groups = 0  # huecos grandes ya pasados
        for i, rect in enumerate(rects):
            if i > 0 and wide[i - 1]:
                groups += 1
            isolated = 0 < i < len(rects) - 1 and wide[i - 1] and wide[i]  # entre dos huecos grandes (regla de CoC_Bot)
            if counts[i] is None:
                kind: CardKind = "hero"
            elif isolated and groups > 0:
                kind = "clan"
            elif groups > 0:
                kind = "spell"
            else:
                kind = "troop"
            cards.append(Card(rect, kind, counts[i], self.is_exhausted(image, rect)))
        return cards

    # ------------------------------------------------------------------------------ barra larga

    def scan(self, ctx: Context, max_scrolls: int = 8, settle_s: float = 0.6) -> BarScan:
        """Todas las cartas de la barra. Si llega al borde de la pantalla se desplaza hasta que la última
        carta deja de moverse (es el final), y luego se devuelve la barra a su sitio."""
        image = ctx.device.capture().image
        cards = self.detect(image)
        w = image.shape[1]
        if not cards or cards[-1].rect.x + cards[-1].rect.w < OVERFLOW_X * w:
            return BarScan(tuple(cards), False)
        y_mid = cards[-1].rect.y + cards[-1].rect.h // 2
        moved_total = 0
        for _ in range(max_scrolls):
            ctx.check_stop()
            last = cards[-1]
            patch = image[last.rect.y : last.rect.y + last.rect.h, last.rect.x : last.rect.x + last.rect.w].copy()
            start_x = last.rect.x + last.rect.w // 2
            ctx.device.swipe(start_x, y_mid, int(0.16 * w), y_mid, 0.5)
            ctx.sleep(settle_s)
            image = ctx.device.capture().image
            found = self._locate(image, patch, last.rect)
            if found is None or abs(found - last.rect.x) < 0.004 * w:
                break  # no se movió (o se perdió): era el final
            moved_total += last.rect.x - found
            fresh = [c for c in self.detect(image) if c.rect.x > found + 0.5 * last.rect.w]
            if not fresh:
                break
            cards.extend(fresh)
        if moved_total > 0:  # devolver la barra a su sitio
            ctx.device.swipe(int(0.16 * w), y_mid, min(w - 1, int(0.16 * w) + moved_total), y_mid, 0.5)
            ctx.sleep(settle_s)
        return BarScan(tuple(cards), True)

    @staticmethod
    def _locate(image: np.ndarray, patch: np.ndarray, near: Rect) -> int | None:
        h = image.shape[0]
        y0, y1 = int(h * CARD_TOP) - 4, min(h, int(h * CARD_BOTTOM) + 4)
        band = image[y0:y1]
        if patch.shape[0] > band.shape[0] or patch.shape[1] > band.shape[1]:
            return None
        res = cv2.matchTemplate(band, patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        return int(loc[0]) if score >= 0.9 else None


__all__ = ["BarReader", "BarScan", "Card", "CardKind"]
