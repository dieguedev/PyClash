"""Lector de los diálogos que salen al pulsar una fila del panel de mejoras.

Medido sobre capturas reales (ventana de Google Play Games, juego en inglés):
  - «Upgrade <nombre> to Level N?» (título blanco, nivel en amarillo) con un botón verde «Confirm»
    que lleva el coste y el icono del recurso. A veces hay otro «Confirm» gris (objeto mágico, p. ej.
    «Hammer of Building»): nunca se pulsa, solo vale un verde con recurso conocido.
  - «Rebuild Clan Castle»: otro título y otro botón verde con elixir.
  - «Missing N Gems!» con un botón verde «Enter Shop» que NO se pulsa.
  - Menú del muro (abajo): «Info / Select Row / Upgrade More / Upgrade» con el coste sobre «Upgrade».

Todo lo que no se reconoce positivamente se devuelve como «no»: el flujo trata un «no sé» como un
motivo para cerrar, nunca para pulsar. Los héroes siguen el mismo diálogo con elixir oscuro, según
el mantenedor, pero no hay captura de la ventana: ver docs/verificacion-manual-flujo-mejora.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import cv2
import numpy as np
from rapidfuzz import fuzz, process

from ..device.base import Rect
from ..vision.color import find_green_buttons
from ..vision.result import crop
from ..vision.text import DIGITS, TEXT_COLORS, DigitReader, TextFinder, render_text
from ..vision.vocabulary import Vocabulary
from .layout import RoiFraction, scaled_font_px
from .signatures import box
from .upgrade_panel import Resource, classify_icon, fine_match

TitleKind = Literal["upgrade", "rebuild"]
GEMS = "gems"

# Edificios que se reconstruyen en vez de mejorarse (una vez, sin constructor ni tiempo).
REBUILD_NAMES = frozenset({"Clan Castle"})

TITLE_REGION = box(0.10, 0.01, 0.82, 0.14)
TITLE_PX = 47  # a BASE_HEIGHT
MISSING_REGION = box(0.28, 0.25, 0.73, 0.40)
MISSING_PX = 42
BUTTON_REGION = box(0.08, 0.35, 0.93, 0.98)  # todo el tercio inferior: el Rebuild lo dibuja más arriba
DISCOUNT_GREEN = (104, 224, 23)  # precio con descuento (medido en «Upgrade Barracks» con Hammer of Building)
COST_SIZES = range(12, 35)  # a 720 px de alto; se escala con el fotograma
WALL_MENU_REGION = box(0.18, 0.78, 0.60, 0.98)
WALL_LABEL_PX = 20


@dataclass(frozen=True)
class DialogTitle:
    kind: TitleKind
    name: str
    confidence: float


@dataclass(frozen=True)
class ConfirmButton:
    rect: Rect
    resource: Resource | None  # None: sin icono reconocido (también las gemas verdes) -> no es pulsable
    cost: int | None
    confidence: float  # de la lectura del coste

    @property
    def center(self) -> tuple[int, int]:
        return self.rect.x + self.rect.w // 2, self.rect.y + self.rect.h // 2

    @property
    def spends_known_resource(self) -> bool:
        return self.resource in ("gold", "elixir", "dark_elixir") and self.cost is not None


@dataclass(frozen=True)
class WallUpgradeButton:
    center: tuple[int, int]
    confidence: float


class DialogReader:
    def __init__(
        self,
        vocabulary: Vocabulary | None = None,
        finder: TextFinder | None = None,
        digits: DigitReader | None = None,
        name_threshold: float = 0.7,
        margin: float = 0.05,
        rivals: int = 5,
        cost_threshold: float = 0.7,
    ) -> None:
        self.vocabulary = vocabulary or Vocabulary.load_packaged()
        self.finder = finder or TextFinder()
        self.digits = digits or DigitReader(threshold=0.5)
        self.name_threshold = name_threshold
        self.margin = margin
        self.rivals = rivals
        self.cost_threshold = cost_threshold

    # -------------------------------------------------------------------------- título

    def _score(self, image: np.ndarray, text: str) -> float:
        region = TITLE_REGION.rect(image)
        found = fine_match(self.finder, image, text, TEXT_COLORS["white"], region, scaled_font_px(image, TITLE_PX))
        return found[0] if found else 0.0

    def _rivals(self, name: str) -> list[str]:
        others = [n for n in self.vocabulary.names() if n != name]
        return [n for n, _, _ in process.extract(name, others, scorer=fuzz.ratio, limit=self.rivals)]

    def title(self, image: np.ndarray, expected: str) -> DialogTitle | None:
        """El título solo cuenta si nombra `expected` y a la vez no se parece más a un nombre parecido.

        Se comprueba «Upgrade X to Level» y, para los edificios que se reconstruyen, «Rebuild X».
        """
        forms: list[tuple[TitleKind, str]] = [("upgrade", "Upgrade {} to Level")]
        if expected in REBUILD_NAMES:
            forms.append(("rebuild", "Rebuild {}"))
        best: DialogTitle | None = None
        for kind, pattern in forms:
            score = self._score(image, pattern.format(expected))
            if score < self.name_threshold:
                continue
            rival = max((self._score(image, pattern.format(r)) for r in self._rivals(expected)), default=0.0)
            if score - rival < self.margin:
                continue
            if best is None or score > best.confidence:
                best = DialogTitle(kind, expected, score)
        return best

    def missing(self, image: np.ndarray) -> Literal["gems", "other"] | None:
        """«Missing N Gems!» (y otras faltas de recursos): None si no es ese diálogo."""
        size = scaled_font_px(image, MISSING_PX)
        region = MISSING_REGION.rect(image)
        found = fine_match(self.finder, image, "Missing", TEXT_COLORS["white"], region, size)
        if found is None or found[0] < 0.8:
            return None
        gems = fine_match(self.finder, image, "Gems!", TEXT_COLORS["white"], region, size)
        return GEMS if gems is not None and gems[0] >= 0.8 else "other"

    # -------------------------------------------------------------------------- botones

    def confirm_buttons(self, image: np.ndarray) -> list[ConfirmButton]:
        """Botones verdes del tercio inferior con su recurso y coste, de izquierda a derecha."""
        out: list[ConfirmButton] = []
        for m in find_green_buttons(image, BUTTON_REGION.rect(image), min_fill=0.5):
            r = m.rect
            if not 1.5 <= r.w / r.h <= 3.5 or not 0.05 <= r.h / image.shape[0] <= 0.2 or r.w > 0.3 * image.shape[1]:
                continue
            resource, icon_x = self._icon(image, r)
            cost, conf = self._cost(image, r, icon_x) if resource else (None, 0.0)
            out.append(ConfirmButton(r, resource, cost, conf))
        return sorted(out, key=lambda b: b.rect.x)

    @staticmethod
    def _icon(image: np.ndarray, button: Rect) -> tuple[Resource | None, int]:
        """Recurso por el icono (la mancha saturada más grande que no es verde) y su borde izquierdo."""
        section, _ = crop(image, button)
        hsv = cv2.cvtColor(section, cv2.COLOR_RGB2HSV)
        mask = (((hsv[..., 0] < 30) | (hsv[..., 0] > 95)) & (hsv[..., 1] > 90) & (hsv[..., 2] > 40)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        if count < 2:
            return None, 0
        i = max(range(1, count), key=lambda k: stats[k, 4])
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 0.01 * button.w * button.h or x < button.w * 0.4:
            return None, 0
        pixels = hsv[y:y + h, x:x + w][labels[y:y + h, x:x + w] == i]
        return classify_icon(float(np.median(pixels[:, 0])), float(pixels[:, 2].mean())), x

    def _cost(self, image: np.ndarray, button: Rect, icon_x: int) -> tuple[int | None, float]:
        """Dígitos a la izquierda del icono; se barren tamaños y gana la mejor confianza.

        Con descuento (p. ej. «Hammer of Building») el precio ACTUAL sale en verde y el original, tachado, debajo
        en blanco: se prueba primero el verde, para no leer nunca el precio tachado como si fuera el actual.
        """
        k = image.shape[0] / 720
        for color in (DISCOUNT_GREEN, "white"):
            best = None
            # Dos bandas: el botón de mejora pone «Confirm» arriba y el coste abajo; el de Rebuild, un coste mayor.
            for top, height in ((0.32, 0.56), (0.10, 0.80)):
                region = Rect(button.x + 6, button.y + int(top * button.h), max(1, icon_x - 7), int(height * button.h))
                for s in range(int(COST_SIZES.start * k), int(COST_SIZES.stop * k) + 1):
                    r = self.digits.read(image, s, color, region, DIGITS)
                    if r is not None and (best is None or r.confidence > best.confidence):
                        best = r
            if best is not None and best.confidence >= self.cost_threshold:
                return int(best.value), best.confidence
        return None, 0.0

    # -------------------------------------------------------------------------- menú del muro

    def wall_upgrade(self, image: np.ndarray) -> WallUpgradeButton | None:
        """El botón «Upgrade» (1 muro) del menú inferior: el de más a la derecha con un coste encima.

        «Upgrade More» también contiene la palabra, pero lleva un «+» en vez de un coste.
        """
        size = scaled_font_px(image, WALL_LABEL_PX)
        region = WALL_MENU_REGION.rect(image)
        labels = []
        for s in {max(1, size - 1), size, size + 1}:
            labels += self.finder.matcher.find_all(
                image, render_text("Upgrade", s, TEXT_COLORS["white"]), region, 0.7,
                prefilter_color=TEXT_COLORS["white"], label="Upgrade",
            )
        for m in sorted(labels, key=lambda m: m.rect.x, reverse=True):
            if self._cost_above(image, m.rect):
                return WallUpgradeButton(m.center, m.confidence)
        return None

    @staticmethod
    def _cost_above(image: np.ndarray, label: Rect) -> bool:
        h = image.shape[0]
        above = Rect(label.x - label.w // 3, max(0, label.y - int(0.11 * h)), label.w + label.w // 2, int(0.08 * h))
        section, _ = crop(image, above)
        if section.size == 0:
            return False
        hsv = cv2.cvtColor(section, cv2.COLOR_RGB2HSV)
        known = (hsv[..., 1] > 130) & (hsv[..., 2] > 110) & (
            ((hsv[..., 0] >= 15) & (hsv[..., 0] <= 31)) | ((hsv[..., 0] >= 125) & (hsv[..., 0] <= 170))
        )
        return float(known.mean()) >= 0.01


def usable_confirm(buttons: Sequence[ConfirmButton]) -> ConfirmButton | None:
    """El único botón con recurso y coste conocidos; con dos candidatos no se adivina (None)."""
    usable = [b for b in buttons if b.spends_known_resource]
    return usable[0] if len(usable) == 1 else None
