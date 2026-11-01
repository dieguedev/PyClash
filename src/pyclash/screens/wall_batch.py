"""Barra de lote de muros (issue #13): lo que sale al pulsar «Upgrade More» sobre un muro.

Medido sobre capturas reales (juego en inglés): una barra inferior con cuatro botones, de izquierda a
derecha «Remove Wall (-1)», «Add Wall (+10)», «Add Wall (+1)» y «Upgrade» (con el coste TOTAL del lote
encima), y sobre ella el título «Wall (Level L)x N» (texto crema, no blanco) con N = muros del lote.

El coste total del botón «Upgrade» NO se lee con dígitos: sale demasiado pequeño y se confunde «10 000» con
«11 000». Se deduce como `N × coste unitario` (el de la fila del panel) y del botón solo se mira si está
en rojo (no se puede pagar).

Un botón apagado (no se puede pulsar: no quedan muros de ese nivel, o el lote está en el mínimo) se ve
con el icono atenuado y la etiqueta en ROJO en vez de blanca. Se busca la etiqueta con la fuente del
juego en los dos colores: color y plantilla a la vez. La barra se desplaza según haya o no panel
abierto, así que nada va en posiciones fijas.

Todo lo que no se reconoce positivamente se devuelve como `None`: quien llama trata un «no sé» como un
motivo para no pulsar.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Literal

import cv2
import numpy as np

from ..device.base import Rect
from PIL import Image

from ..vision.color import find_green_buttons
from ..vision.result import crop
from ..vision.text import DigitReader, TextFinder, render_text
from .layout import RoiFraction, scaled_font_px

WHITE = (255, 255, 255)
RED = (248, 130, 122)  # etiqueta de un botón apagado
CREAM = (245, 245, 175)  # título «Wall (Level L)x N»

BAR_REGION = RoiFraction(0.03, 0.78, 0.94, 0.12)
LABEL_PX = 22  # a BASE_HEIGHT
TITLE_X_PX = 41
TITLE_BAND = 0.045  # alto de la franja del título sobre la barra, como fracción del alto
_BIG_PX = 40
_LABEL_THRESHOLD = 0.68  # un botón translúcido deja ver las unidades de detrás; la estructura (2 «Add Wall» + «Upgrade») protege
_MIN_TEXT_PIXELS = 8
MODAL_TITLE_REGION = RoiFraction(0.25, 0.30, 0.5, 0.13)
MODAL_BUTTONS_REGION = RoiFraction(0.25, 0.52, 0.5, 0.36)
MODAL_TITLE_PX = 44  # a BASE_HEIGHT
_MODAL_TITLE_THRESHOLD = 0.6
_COUNT_MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class BatchButton:
    center: tuple[int, int]
    active: bool  # True: etiqueta blanca; False: etiqueta roja (apagado)
    confidence: float


Resource = Literal["gold", "elixir"]


@dataclass(frozen=True)
class UpgradeButton:
    center: tuple[int, int]
    resource: Resource | None  # por el icono (moneda u gota) sobre el botón; None: sin icono reconocido
    affordable: bool | None  # False: coste en rojo; None: no se vio texto de coste


@dataclass(frozen=True)
class WallModal:
    """El modal «Upgrade Walls» («Do you really want to upgrade the selected Walls for N Gold?»)."""

    okay: tuple[int, int]
    cancel: tuple[int, int]


@dataclass(frozen=True)
class BatchBar:
    remove: BatchButton | None
    add10: BatchButton | None
    add1: BatchButton | None
    upgrades: tuple[UpgradeButton, ...]  # de izquierda a derecha: con muros de nivel alto, uno de oro y otro de elixir
    count: int | None  # N de «Wall (Level L)x N»

    def upgrade_for(self, resource: Resource) -> UpgradeButton | None:
        """El único botón «Upgrade» que paga con `resource`; con cero o con dos candidatos, None (no se adivina)."""
        found = [u for u in self.upgrades if u.resource == resource]
        return found[0] if len(found) == 1 else None


_ICON_MIN_SCORE = {"gold": 0.62, "elixir": 0.7}
_ICON_MARGIN = {"gold": 0.15, "elixir": 0.2}


def _load_icons() -> dict[str, np.ndarray]:
    folder = resources.files("pyclash") / "assets" / "icons"
    out: dict[str, np.ndarray] = {}
    for key, file in (("gold", "coin.png"), ("elixir", "drop.png")):
        with (folder / file).open("rb") as handle, Image.open(handle) as im:
            out[key] = np.array(im.convert("RGB"))
    return out


def icon_resource(image: np.ndarray, region: Rect, icons: dict[str, np.ndarray]) -> Resource | None:
    """Recurso del icono (moneda u gota) dentro de `region`, o None.

    Se compara cada plantilla en color y solo vale la que supera su umbral Y le saca margen a la otra:
    con las dos parecidas (o con ninguna) no se adivina. Es un icono de ~13 px: por eso el color cuenta.
    """
    section, _ = crop(image, region)
    scores: dict[str, float] = {}
    for key, template in icons.items():
        if section.shape[0] < template.shape[0] or section.shape[1] < template.shape[1]:
            return None
        result = cv2.matchTemplate(section, template, cv2.TM_CCOEFF_NORMED)
        scores[key] = float(np.nan_to_num(result.max(), nan=0.0))
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    rival = max(v for k, v in scores.items() if k != best)
    if scores[best] >= _ICON_MIN_SCORE[best] and scores[best] - rival >= _ICON_MARGIN[best]:
        return best  # type: ignore[return-value]
    return None


def cost_affordable(image: np.ndarray, region: Rect) -> bool | None:
    """False si el texto de la región es rojo (no se puede pagar), True si es blanco, None si no hay texto."""
    section, _ = crop(image, region)
    if section.size == 0:
        return None
    px = section.reshape(-1, 3).astype(int)
    red = int(((px[:, 0] > 200) & (px[:, 1] < 150) & (px[:, 2] < 140) & (px[:, 0] - px[:, 1] > 70)).sum())
    white = int(((px.min(axis=1) > 228)).sum())
    if red >= _MIN_TEXT_PIXELS and red > white:
        return False
    if white >= _MIN_TEXT_PIXELS:
        return True
    return None


def _button_shaped(r: Rect, image: np.ndarray) -> bool:
    return 1.5 <= r.w / r.h <= 3.5 and 0.05 <= r.h / image.shape[0] <= 0.2 and r.w <= 0.3 * image.shape[1]


def _orange_buttons(image: np.ndarray, region: Rect) -> list[Rect]:
    """Botones naranja (el «Cancel» de los modales) dentro de la región."""
    section, eff = crop(image, region)
    if section.size == 0:
        return []
    hsv = cv2.cvtColor(section, cv2.COLOR_RGB2HSV)
    mask = ((hsv[..., 0] >= 5) & (hsv[..., 0] <= 18) & (hsv[..., 1] > 150) & (hsv[..., 2] > 170)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        r = Rect(eff.x + x, eff.y + y, w, h)
        if _button_shaped(r, image) and area >= 0.4 * w * h:
            out.append(r)
    return sorted(out, key=lambda r: r.x)


class BatchBarReader:
    def __init__(self, finder: TextFinder | None = None, digits: DigitReader | None = None) -> None:
        self.icons = _load_icons()
        self.finder = finder or TextFinder()
        self.digits = digits or DigitReader(threshold=0.7)

    # -------------------------------------------------------------------------- etiquetas

    def _find(self, image: np.ndarray, text: str, rgb: tuple[int, int, int], region: Rect) -> list[tuple[float, Rect]]:
        """Todas las coincidencias de `text` (sin solapes), barriendo la escala en pasos de medio píxel."""
        base = scaled_font_px(image, LABEL_PX)
        big = render_text(text, _BIG_PX, rgb)
        found: list[tuple[float, Rect]] = []
        for s in np.arange(base - 1.5, base + 1.51, 0.5):
            f = float(s) / _BIG_PX
            template = cv2.resize(big, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            for m in self.finder.matcher.find_all(image, template, region, _LABEL_THRESHOLD, prefilter_color=rgb):
                found.append((m.confidence, m.rect))
        found.sort(key=lambda t: t[0], reverse=True)
        kept: list[tuple[float, Rect]] = []
        for conf, rect in found:
            cx = rect.x + rect.w / 2
            if all(abs(cx - (k.x + k.w / 2)) > rect.w / 2 for _, k in kept):
                kept.append((conf, rect))
        return sorted(kept, key=lambda t: t[1].x)

    def _buttons(self, image: np.ndarray, text: str, region: Rect) -> list[BatchButton]:
        """Etiquetas de `text` en blanco (activas) y en rojo (apagadas), de izquierda a derecha."""
        merged: list[tuple[float, Rect, bool]] = []
        for rgb, active in ((WHITE, True), (RED, False)):
            merged += [(c, r, active) for c, r in self._find(image, text, rgb, region)]
        merged.sort(key=lambda t: t[0], reverse=True)
        kept: list[tuple[float, Rect, bool]] = []
        for conf, rect, active in merged:  # una etiqueta no puede ser a la vez blanca y roja
            cx = rect.x + rect.w / 2
            if all(abs(cx - (k.x + k.w / 2)) > rect.w / 2 for _, k, _ in kept):
                kept.append((conf, rect, active))
        kept.sort(key=lambda t: t[1].x)
        return [BatchButton((r.x + r.w // 2, r.y + r.h // 2), active, c) for c, r, active in kept]

    # -------------------------------------------------------------------------- lectura

    def read(self, image: np.ndarray) -> BatchBar | None:
        """La barra de lote, o None si no hay una barra reconocible."""
        region = BAR_REGION.rect(image)
        upgrade_labels = [r for _, r in self._find(image, "Upgrade", WHITE, region)]
        adds = self._buttons(image, "Add Wall", region)
        removes = self._buttons(image, "Remove", region)
        # «Upgrade» también aparece en «Upgrade More», pero esa pantalla no tiene «Add Wall».
        if len(adds) != 2 or not upgrade_labels:
            return None
        add10, add1 = adds  # de izquierda a derecha
        # «Remove» debe quedar a la izquierda de las dos «Add Wall»; si no, no es esta barra.
        remove = removes[0] if len(removes) == 1 and removes[0].center[0] < add10.center[0] else None
        upgrades = tuple(self._upgrade(image, label) for label in sorted(upgrade_labels, key=lambda r: r.x))
        return BatchBar(remove, add10, add1, upgrades, self.count(image))

    # -------------------------------------------------------------------------- modal de confirmación

    def modal(self, image: np.ndarray) -> WallModal | None:
        """El modal «Upgrade Walls» con sus botones, o None.

        El texto del cuerpo usa otra fuente (no se puede leer el importe con las plantillas), así que
        se reconoce por el título «Upgrade Walls» (fuente conocida) más la estructura: un botón naranja
        «Cancel» a la izquierda y uno verde «Okay» a la derecha. «Okay» es el botón verde de otros modales:
        sin el título y el «Cancel» no se pulsa.
        """
        region = MODAL_TITLE_REGION.rect(image)
        base = scaled_font_px(image, MODAL_TITLE_PX)
        big = render_text("Upgrade Walls", _BIG_PX, WHITE)
        best = 0.0
        for s in np.arange(base - 2.0, base + 2.01, 0.5):
            f = float(s) / _BIG_PX
            template = cv2.resize(big, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            m = self.finder.matcher.best(image, template, region, prefilter_color=WHITE)
            if m is not None:
                best = max(best, m.confidence)
        if best < _MODAL_TITLE_THRESHOLD:
            return None
        buttons = MODAL_BUTTONS_REGION.rect(image)
        greens = [m.rect for m in find_green_buttons(image, buttons, min_fill=0.5) if _button_shaped(m.rect, image)]
        oranges = _orange_buttons(image, buttons)
        if len(greens) != 1 or len(oranges) != 1 or oranges[0].x >= greens[0].x:
            return None
        g, o = greens[0], oranges[0]
        return WallModal((g.x + g.w // 2, g.y + g.h // 2), (o.x + o.w // 2, o.y + o.h // 2))

    def upgrade_more(self, image: np.ndarray) -> tuple[int, int] | None:
        """Centro del botón «Upgrade More» de la barra del muro seleccionado, o None.

        La barra es «Info / Select Row / Upgrade More / Upgrade» y, cuando el muro se puede pagar con
        oro o con elixir, trae DOS botones «Upgrade» (uno con moneda y otro con gota). «Upgrade More» es
        la etiqueta «Upgrade» más a la izquierda. Con una sola etiqueta es otra barra, y si hay botones
        «Add Wall» es la barra de lote, donde no existe «Upgrade More».
        """
        region = BAR_REGION.rect(image)
        if self._buttons(image, "Add Wall", region):
            return None
        labels = [r for _, r in self._find(image, "Upgrade", WHITE, region)]
        if len(labels) < 2:
            return None
        label = labels[0]
        # La etiqueta va en la parte baja del botón: se pulsa por encima, sobre el icono.
        return label.x + label.w // 2, label.y - int(0.04 * image.shape[0])

    def _upgrade(self, image: np.ndarray, label: Rect) -> UpgradeButton:
        """El botón «Upgrade»: su recurso (icono sobre el coste) y si el coste sale en rojo."""
        h = image.shape[0]
        center = (label.x + label.w // 2, label.y + label.h // 2)
        cost = Rect(label.x - label.w // 3, max(0, label.y - int(0.085 * h)), label.w + label.w // 2, int(0.065 * h))
        icon = Rect(label.x - 10, max(0, label.y - int(0.097 * h)), label.w + 40, int(0.069 * h))
        return UpgradeButton(center, icon_resource(image, icon, self.icons), cost_affordable(image, cost))

    def count(self, image: np.ndarray) -> int | None:
        """N de «Wall (Level L)x N»: se localiza la «x» y se leen los dígitos a su derecha."""
        h, w = image.shape[:2]
        band_y = int(0.70 * h)
        band = Rect(int(0.20 * w), band_y, int(0.60 * w), int(0.07 * h))
        size = scaled_font_px(image, TITLE_X_PX)
        best_x: tuple[float, Rect] | None = None
        for s in np.arange(size - 2.0, size + 2.01, 0.5):
            f = float(s) / _BIG_PX
            template = cv2.resize(render_text("x", _BIG_PX, CREAM), None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            m = self.finder.matcher.best(image, template, band, prefilter_color=CREAM)
            if m is not None and (best_x is None or m.confidence > best_x[0]):
                best_x = (m.confidence, m.rect)
        if best_x is None or best_x[0] < 0.8:
            return None
        xr = best_x[1]
        region = Rect(xr.x + xr.w + 2, band.y, min(int(0.06 * w), w - (xr.x + xr.w + 2)), band.h)
        best: tuple[float, str] | None = None
        for s in range(max(4, size - 3), size + 4):
            r = self.digits.read(image, s, CREAM, region)
            if r is not None and r.value.isdigit() and (best is None or r.confidence > best[0]):
                best = (r.confidence, r.value)
        if best is None or best[0] < _COUNT_MIN_CONFIDENCE:
            return None
        return int(best[1])


__all__ = ["BatchBar", "BatchBarReader", "BatchButton", "UpgradeButton", "WallModal", "cost_affordable"]
