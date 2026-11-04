"""Lector del panel «Buildings & Heroes»: filas, costes, secciones y recorrido completo.

Cada fila se ancla al **icono de recurso** del coste (moneda, gota, gema), que está a la derecha
del número: da el recurso, la línea de la fila y dónde acaban los dígitos, así el icono no se
pega como dígito ni el nombre se mezcla con la columna de coste. Las filas gratuitas no tienen
icono: se anclan al texto «FREE». Nombre, coste, tiempo y cantidad se leen con la fuente del juego
(el nombre contra el vocabulario empaquetado); el OCR solo se usa, si se da, para un nombre que no
está en el vocabulario. Todo campo ilegible es `None`, nunca una suposición.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from functools import lru_cache
from importlib import resources
from typing import Callable, Literal, Sequence

import cv2
import numpy as np
from PIL import Image

from ..device.base import Frame, Rect
from ..runtime.context import Context
from ..vision.color import INSUFFICIENT_RED, RGB, color_fraction, isolate_color
from ..vision.numeric import parse_duration
from ..vision.ocr import OcrEngine
from ..vision.result import Match, crop
from ..vision.text import DIGITS, TEXT_COLORS, DigitReader, TextFinder, render_text
from ..vision.vocabulary import Vocabulary
from .layout import scaled_font_px
from .level_badge import read_any_level, read_level
from .panel_layout import FILTER_BUTTON, VILLAGE_PANEL, PanelLayout

Resource = Literal["gold", "elixir", "dark_elixir", "gems"]
Kind = Literal["upgrade", "new_building", "hero", "wall"]
SECTION_SUGGESTED = "suggested"
SECTION_OTHER = "other"

HEADER_COLOR: RGB = (195, 172, 166)  # gris rosado de «Suggested upgrades» / «Other upgrades»
HEADERS = {SECTION_SUGGESTED: "Suggested upgrades", SECTION_OTHER: "Other upgrades"}
NAME_CATEGORIES = ("resource_buildings", "army_buildings", "defenses", "traps", "other_buildings", "heroes")
_TIME_ALPHABET = DIGITS + "hmds"
_BIG_PX = 40  # tamaño al que se renderiza el nombre antes de reducirlo a la escala medida


@dataclass(frozen=True)
class PanelRow:
    name: str | None  # nombre canónico del vocabulario; None si no se reconoce
    quantity: int  # «Wall x 26» -> 26 (1 si no hay «x N»)
    cost: int | None  # None: gratis, ilegible o fuera de rango
    resource: Resource | None  # None: gratis o icono desconocido (moneda no gastable)
    free: bool
    new: bool
    duration: int | None  # segundos; None si la fila no muestra tiempo
    affordable: bool | None  # False: coste en rojo; None: sin coste que comprobar
    kind: Kind
    section: str | None
    center: tuple[int, int]  # centro del nombre: dónde tocar la fila
    y: int  # línea del coste
    confidence: float = 0.0
    level: int | None = None  # nivel de la insignia (solo se lee en los muros); None: no se reconoció

    @property
    def spendable(self) -> bool:
        """Se puede comprar con un recurso que el bot entiende (gratis o coste y recurso leídos)."""
        return self.free or (self.cost is not None and self.resource is not None)


@dataclass(frozen=True)
class PanelReading:
    rows: tuple[PanelRow, ...]
    partial: int = 0  # filas cortadas por el borde del panel (se leerán completas al desplazar)
    headers: dict[str, int] = field(default_factory=dict)  # sección -> y de su cabecera


@dataclass(frozen=True)
class _Anchor:
    y: int  # línea del coste
    resource: Resource | None
    left: int  # borde izquierdo del icono (los dígitos acaban aquí); 0 si no hay icono
    free: bool = False


Prepared = tuple[np.ndarray, Rect]  # (recorte con solo el color buscado, su rectángulo en la captura)


def prepare_region(image: np.ndarray, region: Rect, color: RGB) -> Prepared:
    """Aísla `color` en `region` una sola vez. Buscar muchos textos en la misma región recalculaba esto por cada uno
    (3000 veces por captura del panel): es la mayor parte de lo que tarda en leerse."""
    section, eff = crop(image, region)
    return (isolate_color(section, color, 30.0) if section.size else section), eff  # 30: la tolerancia de `TemplateMatcher.best`


def _best_in(
    finder: TextFinder, image: np.ndarray, template: np.ndarray, region: Rect, color: RGB, prepared: Prepared | None
) -> Match | None:
    if prepared is None:
        return finder.matcher.best(image, template, region, prefilter_color=color)
    section, eff = prepared
    m = finder.matcher.best(section, template)
    if m is None:
        return None
    return replace(m, rect=Rect(eff.x + m.rect.x, eff.y + m.rect.y, m.rect.w, m.rect.h))


def _best_text(
    finder: TextFinder, image: np.ndarray, text: str, size: int, color: RGB, region: Rect, prepared: Prepared | None = None
) -> Match | None:
    best: Match | None = None
    for s in {max(1, size - 1), size, size + 1}:  # el redondeo del tamaño cambia con la ventana
        m = _best_in(finder, image, render_text(text, s, color), region, color, prepared)
        if m is not None and (best is None or m.confidence > best.confidence):
            best = m
    return best


def classify_icon(hue: float, value: float) -> Resource | None:
    """Recurso por el matiz (OpenCV, 0-180) del icono. Un matiz desconocido es None: una moneda de
    crafteo no es gastable y no debe confundirse con ninguna de las conocidas."""
    if 15 <= hue <= 31:
        return "gold"
    if 32 <= hue <= 90:
        return "gems"
    if 125 <= hue <= 170:
        return "elixir" if value >= 150 else "dark_elixir"
    return None


def find_cost_icons(image: np.ndarray, layout: PanelLayout = VILLAGE_PANEL) -> list[_Anchor]:
    """Iconos de recurso de la franja derecha, de arriba abajo."""
    h, w = image.shape[:2]
    x0, y0, _, y1 = layout.viewport(h, w)
    ix0 = int(w * layout.icon_x[0])
    strip = image[y0:y1, ix0:int(w * layout.icon_x[1]) or w]
    if strip.size == 0:
        return []
    hsv = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)
    mask = ((hsv[..., 1] > 130) & (hsv[..., 2] > 110)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    anchors = []
    for i in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        side = bh / h
        if area < layout.icon_min_area * h * h or not (layout.icon_side[0] <= side <= layout.icon_side[1]):
            continue
        pixels = hsv[y:y + bh, x:x + bw][labels[y:y + bh, x:x + bw] == i]
        resource = classify_icon(float(np.median(pixels[:, 0])), float(pixels[:, 2].mean()))
        anchors.append(_Anchor(y0 + y + bh // 2, resource, ix0 + x))
    # El color no basta: la gota de elixir oscuro es casi negra y no pasa el filtro de saturación. Los recortes
    # reales de los cuatro iconos mandan; el color solo aporta lo que ellos no encuentran (p. ej. otra escala).
    by_template = _template_icons(image, layout)
    anchors = by_template + [a for a in anchors if all(abs(a.y - t.y) >= 0.03 * h for t in by_template)]
    return sorted(anchors, key=lambda a: a.y)


ICON_REF_HEIGHT = 993  # alto de la captura de la que se recortaron los iconos de coste
ICON_THRESHOLD = 0.8
_COST_ICONS = ("gold", "elixir", "dark_elixir", "gems")


@lru_cache(maxsize=None)
def _icon_templates() -> dict[str, np.ndarray]:
    out = {}
    for name in _COST_ICONS:
        with (resources.files("pyclash") / "assets" / "resources" / f"{name}.png").open("rb") as handle, Image.open(handle) as im:
            out[name] = np.array(im.convert("RGB"))
    return out


def _template_icons(image: np.ndarray, layout: PanelLayout) -> list[_Anchor]:
    h, w = image.shape[:2]
    _, y0, _, y1 = layout.viewport(h, w)
    ix0 = max(0, int(w * layout.icon_x[0]) - int(0.02 * w))  # margen: el recorte lleva borde y fondo
    strip = image[y0:y1, ix0:]
    found: list[_Anchor] = []
    for name, tpl in _icon_templates().items():
        scale = h / ICON_REF_HEIGHT
        size = max(4, round(tpl.shape[0] * scale))
        scaled = cv2.resize(tpl, (size, size), interpolation=cv2.INTER_AREA)
        if strip.shape[0] < size or strip.shape[1] < size:
            continue
        scores = cv2.matchTemplate(strip, scaled, cv2.TM_CCOEFF_NORMED)
        while True:
            _, best, _, loc = cv2.minMaxLoc(scores)
            if best < ICON_THRESHOLD:
                break
            found.append(_Anchor(y0 + loc[1] + size // 2, name, ix0 + loc[0] + round(size * 3 / 28), free=False))  # el icono empieza ~3 px dentro del recorte
            ya, yb = max(0, loc[1] - size), loc[1] + size
            scores[ya:yb, :] = -1.0  # una fila no tiene dos iconos de coste
    found.sort(key=lambda a: a.y)
    out: list[_Anchor] = []
    for a in found:  # dos recortes que casan en la misma fila: gana el primero por confianza de orden de hallazgo
        if all(abs(a.y - o.y) >= 0.03 * h for o in out):
            out.append(a)
    return out


def fine_match(
    finder: TextFinder,
    image: np.ndarray,
    text: str,
    rgb: RGB,
    region: Rect,
    size: int,
    slack: float | None = None,
    prepared: Prepared | None = None,
) -> tuple[float, Rect, float] | None:
    """Mejor coincidencia de `text` barriendo la escala en pasos de 0.25 px alrededor de `size`.

    Se renderiza grande y se reduce con `cv2.resize`: Pillow redondea los tamaños fraccionarios y la
    fuente del juego (con contorno) solo casa bien (0.8-0.9) a la escala exacta. `slack`, si se da,
    exige que la coincidencia empiece a menos de esos píxeles del borde izquierdo de la región.
    Devuelve (confianza, caja, escala).
    """
    big = render_text(text, _BIG_PX, rgb)
    best: tuple[float, Rect, float] | None = None
    for s in np.arange(size - 2.0, size + 2.01, 0.25):
        f = s / _BIG_PX
        template = cv2.resize(big, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        m = _best_in(finder, image, template, region, rgb, prepared)
        if m is None or (slack is not None and m.rect.x - region.x > slack):
            continue
        if best is None or m.confidence > best[0]:
            best = (m.confidence, m.rect, float(s))
    return best


class PanelReader:
    def __init__(
        self,
        layout: PanelLayout = VILLAGE_PANEL,
        vocabulary: Vocabulary | None = None,
        finder: TextFinder | None = None,
        ocr: OcrEngine | None = None,
        name_threshold: float = 0.7,
        digit_threshold: float = 0.5,
        name_margin: float = 0.08,
        shortlist: int = 10,
        categories: Sequence[str] = NAME_CATEGORIES,
    ) -> None:
        self.layout = layout
        self.vocabulary = vocabulary or Vocabulary.load_packaged()
        self.finder = finder or TextFinder()
        self.digits = DigitReader(self.finder.matcher, threshold=digit_threshold)
        self.ocr = ocr
        self.name_threshold = name_threshold
        self.name_margin = name_margin
        self.shortlist = shortlist
        self.categories = tuple(categories)
        self._names = self.vocabulary.names(self.categories)
        self._heroes = set(self.vocabulary.names(["heroes"]))

    # --- una captura ----------------------------------------------------------------------

    def read(self, image: np.ndarray, initial_section: str | None = None) -> PanelReading:
        """Todas las filas completas de esta captura. `initial_section` es la sección en curso cuando
        su cabecera ya salió por arriba al desplazar."""
        h, w = image.shape[:2]
        lay = self.layout
        _, top, _, bottom = lay.viewport(h, w)
        anchors = self._merge(find_cost_icons(image, lay) + self._free_anchors(image), h)
        headers = self._headers(image)
        rows, partial = [], 0
        for a in anchors:
            band = int(lay.band * h)
            name_y = a.y + int(lay.name_dy * h)
            if name_y - band < top or a.y + band > bottom:
                partial += 1
                continue
            rows.append(self._row(image, a, name_y, self._section_at(a.y, headers, initial_section)))
        return PanelReading(tuple(rows), partial, headers)

    @staticmethod
    def _merge(anchors: list[_Anchor], h: int) -> list[_Anchor]:
        out: list[_Anchor] = []
        for a in sorted(anchors, key=lambda a: a.y):
            if out and a.y - out[-1].y < 0.03 * h:
                continue
            out.append(a)
        return out

    def _free_anchors(self, image: np.ndarray) -> list[_Anchor]:
        h, w = image.shape[:2]
        lay = self.layout
        _, top, _, bottom = lay.viewport(h, w)
        region = Rect(int(w * lay.free_x[0]), top, int(w * (lay.free_x[1] - lay.free_x[0])), bottom - top)
        size = scaled_font_px(image, lay.free_px)
        found: list[Match] = []
        for s in {max(1, size - 1), size, size + 1}:
            found += self.finder.matcher.find_all(
                image, render_text("FREE", s), region, 0.7, prefilter_color=TEXT_COLORS["white"], label="FREE"
            )
        return [_Anchor(m.center[1], None, 0, free=True) for m in sorted(found, key=lambda m: m.rect.y)]

    def _headers(self, image: np.ndarray) -> dict[str, int]:
        h, w = image.shape[:2]
        lay = self.layout
        x0, top, _, bottom = lay.viewport(h, w)
        region = Rect(int(w * lay.header_x[0]), top, int(w * (lay.header_x[1] - lay.header_x[0])), bottom - top)
        size = scaled_font_px(image, lay.header_px)
        found = {}
        for section, text in HEADERS.items():
            m = _best_text(self.finder, image, text, size, HEADER_COLOR, region)
            if m is not None and m.confidence >= 0.55:
                found[section] = m.center[1]
        return found

    @staticmethod
    def _section_at(y: int, headers: dict[str, int], initial: str | None) -> str | None:
        above = [(hy, s) for s, hy in headers.items() if hy < y]
        return max(above)[1] if above else initial

    # --- una fila -------------------------------------------------------------------------

    def _row(self, image: np.ndarray, a: _Anchor, name_y: int, section: str | None) -> PanelRow:
        h, w = image.shape[:2]
        lay = self.layout
        band = int(lay.band * h)

        def region(x: tuple[float, float], y: int) -> Rect:
            return Rect(int(w * x[0]), y - band, int(w * (x[1] - x[0])), 2 * band)

        name, name_rect, color, name_conf, scale = self._name(image, region(lay.name_x, name_y))
        green = name is not None and color == "green"
        quantity = self._quantity(image, name_rect, region(lay.name_x, name_y), color, scale)
        duration = self._duration(image, region(lay.time_x, name_y))
        new = green or self._new_label(image, region(lay.new_x, a.y))
        cost, affordable, cost_conf = (None, None, 1.0) if a.free else self._cost(image, a, region((lay.cost_x_min, 1.0), a.y))
        if a.free:
            affordable = True
        kind: Kind = "wall" if name == "Wall" else "hero" if name in self._heroes else "new_building" if new else "upgrade"
        cx = int(w * (lay.name_x[0] + 0.04))
        # La insignia es el nivel AL QUE SE MEJORA. Solo se lee en muros (2-4) y en el Ayuntamiento (2-9; el «10» tiene
        # dos dígitos y no se lee): sin número reconocido, la política no sube el Ayuntamiento.
        level = read_level(image, name_y) if name == "Wall" else read_any_level(image, name_y) if name == "Town Hall" else None
        return PanelRow(
            name, quantity, cost, a.resource if cost is not None else None, a.free, new, duration, affordable,
            kind, section, (cx, name_y), a.y, min(name_conf, cost_conf), level,
        )

    def _name(self, image: np.ndarray, region: Rect) -> tuple[str | None, Rect | None, str, float, float]:
        """Nombre canónico, su caja, color («white»/«green» si es nuevo), confianza y tamaño de fuente.

        Dos pasadas con la fuente del juego: una rápida a tamaños enteros para quedarse con los mejores
        candidatos del vocabulario, y un barrido fino de escala solo sobre esos (el contorno del texto del
        juego baja la confianza a ~0.6 a cualquier tamaño entero; a la escala exacta sube a 0.8-0.9). Se
        exige margen sobre el segundo: ante un empate no se elige ninguno. La plantilla debe empezar en el
        borde izquierdo de la región, así «Bomb» no casa dentro de «Giant Bomb».
        """
        size = scaled_font_px(image, self.layout.name_px)
        slack = 0.012 * image.shape[1]
        colors = self._color_order(image, region)
        for color_name in colors:
            rgb = TEXT_COLORS[color_name]
            prepared = prepare_region(image, region, rgb)  # una vez por color, no una por cada nombre del vocabulario
            coarse = []
            for name in self._names:
                m = _best_text(self.finder, image, name, size, rgb, region, prepared)
                if m is not None and m.rect.x - region.x <= slack:
                    coarse.append((m.confidence, name))
            fine: list[tuple[float, str, Rect, float]] = []
            for _, name in sorted(coarse, reverse=True)[: self.shortlist]:
                best = fine_match(self.finder, image, name, rgb, region, size, slack, prepared)
                if best is not None:
                    fine.append((best[0], name, best[1], best[2]))
            fine.sort(key=lambda t: t[0], reverse=True)
            if fine and fine[0][0] >= self.name_threshold:
                runner_up = fine[1][0] if len(fine) > 1 else 0.0
                if fine[0][0] - runner_up >= self.name_margin:
                    conf, name, rect, scale = fine[0]
                    return name, rect, color_name, conf, scale
        return self._name_by_ocr(image, region), None, colors[0], 0.5, float(size)

    def _color_order(self, image: np.ndarray, region: Rect) -> tuple[str, ...]:
        """Verde primero si el texto de la fila es verde (nueva), blanco primero si no."""
        section, _ = crop(image, region)
        green = color_fraction(section, TEXT_COLORS["green"], 25.0)
        white = color_fraction(section, TEXT_COLORS["white"], 25.0)
        return ("green", "white") if green > white else ("white", "green")

    def _name_by_ocr(self, image: np.ndarray, region: Rect) -> str | None:
        if self.ocr is None:
            return None
        section, _ = crop(image, region)
        words = sorted(self.ocr.read_words(section), key=lambda b: b.rect.x)
        resolved = self.vocabulary.resolve(" ".join(b.text for b in words), self.categories)
        return resolved.canonical

    def _quantity(self, image: np.ndarray, name_rect: Rect | None, region: Rect, color: str, scale: float) -> int:
        """«Wall x 26» -> 26. Se localiza la «x» tras el nombre (mismo color y escala) y se leen los
        dígitos desde su borde derecho: sin eso, la «x» se lee como un «1» y «x 2» sale «12»."""
        if name_rect is None:
            return 1
        rgb = TEXT_COLORS[color]
        start = name_rect.x + name_rect.w
        rest = Rect(start, region.y, region.x + region.w - start, region.h)
        x = fine_match(self.finder, image, "x", rgb, rest, int(scale), 0.03 * image.shape[1])
        if x is None or x[0] < 0.55:
            return 1
        first = x[1].x + x[1].w
        digits = Rect(first, region.y, region.x + region.w - first, region.h)
        best = None
        for sz in (int(scale) - 1, int(scale), int(scale) + 1):
            r = self.digits.read(image, max(1, sz), color, digits)
            if r is not None and (best is None or r.confidence > best.confidence):
                best = r
        return int(best.value) if best and best.value.isdigit() else 1

    def _new_label(self, image: np.ndarray, region: Rect) -> bool:
        m = _best_text(self.finder, image, "New", scaled_font_px(image, self.layout.new_px), TEXT_COLORS["green"], region)
        return m is not None and m.confidence >= 0.6

    def _duration(self, image: np.ndarray, region: Rect) -> int | None:
        value = self._read_digits(image, region, self.layout.time_px, alphabet=_TIME_ALPHABET)
        return parse_duration(value) if value else None

    def _cost(self, image: np.ndarray, a: _Anchor, region: Rect) -> tuple[int | None, bool | None, float]:
        """Coste a la izquierda del icono. Rojo = no asequible: se lee con el rojo del juego."""
        limit = (a.left - 1) if a.left else region.x + region.w
        region = Rect(region.x, region.y, max(1, limit - region.x), region.h)
        section, _ = crop(image, region)
        red = color_fraction(section, INSUFFICIENT_RED) >= 0.02
        color = INSUFFICIENT_RED if red else TEXT_COLORS["white"]
        value = self._read_digits(image, region, self.layout.cost_px, color=color)
        if not value:
            return None, (False if red else None), 0.0
        cost = int(value)
        lay = self.layout
        if not lay.min_cost <= cost <= lay.max_cost:
            return None, (False if red else None), 0.0
        return cost, not red, 1.0

    def _read_digits(
        self, image: np.ndarray, region: Rect, base_px: int, color: RGB | str = "white", alphabet: str = DIGITS
    ) -> str:
        size = scaled_font_px(image, base_px)
        best = None
        for s in {max(1, size - 1), size, size + 1}:
            r = self.digits.read(image, s, color, region, alphabet)
            if r is not None and (best is None or r.confidence > best.confidence):
                best = r
        return best.value if best else ""


# --- orden y recorrido -----------------------------------------------------------------------


Order = Literal["cost_max", "cost_min"]
_ORDER_LABEL = {"cost_max": "Cost Max", "cost_min": "Cost Min"}


def verify_order(rows: Sequence[PanelRow], order: Order) -> tuple[bool, str]:
    """¿Las filas con coste siguen el orden pedido? Es la comprobación de que el filtro se aplicó."""
    costs = [(r.name, r.cost) for r in rows if r.cost is not None]
    for (a, ca), (b, cb) in zip(costs, costs[1:]):
        if (order == "cost_max" and ca < cb) or (order == "cost_min" and ca > cb):
            return False, f"«{a}» ({ca}) antes que «{b}» ({cb}) rompe el orden {order}"
    return True, ""


@dataclass(frozen=True)
class PanelScan:
    rows: tuple[PanelRow, ...]
    steps: int
    reached_end: bool  # True: dos capturas seguidas iguales; False: se agotaron los pasos
    order_ok: bool | None = None  # None si no se pidió verificar un orden
    order_reason: str = ""


def panel_diff(a: np.ndarray, b: np.ndarray, layout: PanelLayout = VILLAGE_PANEL) -> float:
    """Diferencia media (0..1) de la zona de filas entre dos capturas."""
    h, w = a.shape[:2]
    x0, y0, x1, y1 = layout.viewport(h, w)
    ga = cv2.cvtColor(a[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY).astype(np.float32)
    return float(np.abs(ga - gb).mean() / 255.0)


class PanelScanner:
    """Recorre el panel con la rueda hasta que dos capturas seguidas son iguales (o se agotan los pasos).

    La rueda mueve el panel a saltos fijos y sin rebote: en el tope la imagen deja de cambiar y el recorrido
    termina enseguida (un arrastre rebotaba y daba pasos de más). `notches` son las muescas por paso; deben
    mover menos que el alto del panel para que las filas se solapen entre capturas (se mide con la
    comprobación «Medir la rueda en el panel»).
    """

    def __init__(self, reader: PanelReader | None = None, end_threshold: float = 0.01, notches: int = 3) -> None:
        self.reader = reader or PanelReader()
        self.end_threshold = end_threshold
        self.notches = notches

    def _wheel(self, ctx: Context, frame: Frame, forward: bool) -> None:
        """Una tanda de muescas con el cursor sobre las tarjetas. `forward`: ver lo que hay más abajo."""
        h, w = frame.image.shape[:2]
        _, top, _, bottom = self.reader.layout.viewport(h, w)
        x = int(w * (1 + self.reader.layout.left) / 2)
        ctx.device.scroll(x, (top + bottom) // 2, -self.notches if forward else self.notches)

    def confirm(self, ctx: Context, row: PanelRow, taken: int, capture: Callable[[], Frame] | None = None) -> bool:
        """¿El panel abierto muestra `row` con `taken` muros menos, o ya sin ella si se mejoraron todos?

        El panel se actualiza en directo: se mira en el sitio, sin cerrarlo. Si la fila sigue a la vista es una sola
        captura; solo cuando ya no aparece hay que recorrer el panel entero para estar seguros de que no está.
        """
        if taken <= 0:
            return False
        if taken < row.quantity:
            return self.reveal(ctx, replace(row, quantity=row.quantity - taken), capture=capture) is not None
        return self.reveal(ctx, row, capture=capture) is None

    def rewind(
        self, ctx: Context, settle: float = 0.4, max_steps: int = 30, capture: Callable[[], Frame] | None = None
    ) -> None:
        """Vuelve al principio del panel abierto: rueda hacia arriba hasta que deja de moverse."""
        grab = capture or ctx.device.capture
        frame = grab()
        for _ in range(max_steps):
            ctx.check_stop()
            self._wheel(ctx, frame, forward=False)
            ctx.sleep(settle)
            nxt = grab()
            if panel_diff(frame.image, nxt.image, self.reader.layout) < self.end_threshold:
                return
            frame = nxt

    def scan(
        self,
        ctx: Context,
        from_top: bool = True,
        max_steps: int = 30,
        settle: float = 0.4,
        expect_order: Order | None = None,
        capture: Callable[[], Frame] | None = None,
    ) -> PanelScan:
        lay = self.reader.layout
        grab = capture or ctx.device.capture
        frame = grab()
        found: dict[tuple, PanelRow] = {}
        order: list[tuple] = []
        section: str | None = None
        reached_end, steps = False, 0
        # Leer una captura (~0,6 s) solo necesita la imagen: se hace a la vez que la rueda, la espera y la captura
        # siguiente, que sí hablan con el juego. El dispositivo solo lo toca este hilo.
        with ThreadPoolExecutor(max_workers=1) as pool:
            while True:
                ctx.check_stop()
                reading_task = pool.submit(self.reader.read, frame.image, section)
                nxt = None
                if steps < max_steps:
                    self._wheel(ctx, frame, forward=from_top)
                    ctx.sleep(settle)
                    nxt = grab()
                    steps += 1
                reading = reading_task.result()
                fresh: list[tuple] = []
                for row in reading.rows:
                    self._add(found, fresh, row)
                # Desde arriba, lo nuevo queda debajo; desde abajo, encima. El orden interno de la página se conserva.
                order = order + fresh if from_top else fresh + order
                if reading.rows and from_top:
                    section = reading.rows[-1].section or section
                if nxt is None:
                    break
                if panel_diff(frame.image, nxt.image, lay) < self.end_threshold:
                    reached_end = True
                    break
                frame = nxt
        rows = tuple(found[k] for k in order)
        ok, reason = (None, "") if expect_order is None else verify_order(rows, expect_order)
        return PanelScan(rows, steps, reached_end, ok, reason)

    def reveal(
        self,
        ctx: Context,
        target: PanelRow,
        max_steps: int = 30,
        settle: float = 0.4,
        capture: Callable[[], Frame] | None = None,
    ) -> PanelRow | None:
        """Vuelve a poner `target` a la vista y devuelve la fila tal y como se ve AHORA (con su posición actual).

        Tras `scan` el panel queda desplazado (normalmente al final) y las coordenadas de las filas leídas son
        las de la captura donde se vieron: pulsarlas cae en otra fila. Se mira la pantalla actual, luego se
        sube hasta el principio leyendo cada paso y, si aún no está, se baja desde allí. None si no aparece.
        """
        lay = self.reader.layout
        grab = capture or ctx.device.capture
        frame = grab()
        hit = self._find(frame.image, target)
        if hit is not None:
            return hit
        for forward in (False, True):  # primero hasta arriba (se ve lo anterior), luego hacia abajo
            for _ in range(max_steps):
                ctx.check_stop()
                self._wheel(ctx, frame, forward)
                ctx.sleep(settle)
                nxt = grab()
                if panel_diff(frame.image, nxt.image, lay) < self.end_threshold:
                    break  # extremo del panel
                frame = nxt
                hit = self._find(frame.image, target)
                if hit is not None:
                    return hit
        return None

    def _find(self, image: np.ndarray, target: PanelRow) -> PanelRow | None:
        for row in self.reader.read(image).rows:
            if row.name == target.name and row.quantity == target.quantity and (
                row.cost is None or target.cost is None or row.cost == target.cost
            ):
                return row
        return None

    @staticmethod
    def _add(found: dict[tuple, PanelRow], fresh: list[tuple], row: PanelRow) -> None:
        """Una fila vista varias veces cuenta una: gana la lectura con más campos. `fresh` recoge las claves
        que no se habían visto antes.

        Dos filas con el mismo nombre y cantidad pero distinto coste son filas distintas (en el panel real
        hay varios «Elixir Collector»: uno nuevo y mejoras de nivel). Un coste sin leer en una de las dos
        lecturas no las distingue: se toman por la misma fila.
        """
        if row.name is None:
            return  # sin nombre no se puede deduplicar ni decidir: se reintenta en otra captura
        base = (row.name, row.quantity)
        key = None
        variants = 0
        for k, seen in found.items():
            if k[:2] != base:
                continue
            variants += 1
            if key is None and (seen.cost is None or row.cost is None or seen.cost == row.cost):
                key = k
        if key is None:
            key = (*base, variants)
        old = found.get(key)
        if old is None:
            found[key] = row
            fresh.append(key)
        elif _completeness(row) > _completeness(old):
            found[key] = replace(row, section=row.section or old.section)
        elif old.section is None and row.section is not None:
            found[key] = replace(old, section=row.section)


def _completeness(row: PanelRow) -> int:
    return sum(v is not None for v in (row.cost, row.resource, row.duration, row.affordable, row.section)) + row.free


def _button_active(image: np.ndarray, label: Rect, min_fraction: float = 0.3) -> bool:
    """¿El botón de la ventana del filtro está activo? El juego lo pinta de amarillo-naranja (medido: 0,73 de
    sus píxeles activo y 0,0 en reposo). `label` es la caja del texto del botón; el botón lo rodea."""
    cx, cy = label.center
    hw, hh = int(label.w * 0.8), int(label.h * 1.4)
    patch = image[max(0, cy - hh) : cy + hh, max(0, cx - hw) : cx + hw]
    if patch.size == 0:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    mask = (hsv[..., 0] >= 12) & (hsv[..., 0] <= 32) & (hsv[..., 1] >= 150) & (hsv[..., 2] >= 170)
    return float(mask.mean()) >= min_fraction


def apply_sort(
    ctx: Context,
    order: Order = "cost_max",
    layout: PanelLayout = VILLAGE_PANEL,
    finder: TextFinder | None = None,
    settle: float = 0.5,
) -> bool:
    """Abre el filtro del panel y pulsa «Cost Max» / «Cost Min». True si la ventana se abrió y se
    cerró tras elegir; que el orden se aplicó se comprueba después con `verify_order`.

    PROVISIONAL: la posición del botón y que la ventana se cierre al elegir no están verificadas en el juego.
    """
    finder = finder or TextFinder()
    frame = ctx.device.capture()
    ctx.device.tap(*FILTER_BUTTON.rect(frame.image).center)
    ctx.sleep(settle)
    frame = ctx.device.capture()
    h, w = frame.image.shape[:2]
    popup = Rect(int(w * 0.40), int(h * 0.09), int(w * 0.39), int(h * 0.55))
    white = TEXT_COLORS["white"]
    title = fine_match(finder, frame.image, "Sorting", white, popup, scaled_font_px(frame.image, 32))
    if title is None or title[0] < 0.7:
        return False  # la ventana del filtro no se abrió: no se pulsa nada más
    size = scaled_font_px(frame.image, 26)
    # «Cost Max» y «Cost Min» se diferencian en una letra y puntúan casi igual en el sitio del otro (medido en
    # el juego real: 0,58 frente a 0,53). No se decide por la diferencia de puntuación sino por la posición: se
    # buscan las dos etiquetas, deben quedar en la misma fila y distintas, con «Max» a la izquierda de «Min».
    found = {key: fine_match(finder, frame.image, label, white, popup, size) for key, label in _ORDER_LABEL.items()}
    left, right = found["cost_max"], found["cost_min"]
    if left is None or right is None:
        return False
    # La que se va a pulsar debe casar razonablemente (medido: 0,55-0,58) y la otra solo fija la posición; la
    # comprobación geométrica de abajo es lo que la valida.
    wanted, other = (left, right) if order == "cost_max" else (right, left)
    if wanted[0] < 0.5 or other[0] < 0.45:
        return False
    row_tolerance = max(left[1].h, right[1].h)
    if abs(left[1].center[1] - right[1].center[1]) > row_tolerance or left[1].x + left[1].w > right[1].x:
        return False  # no son dos botones distintos en fila: no se pulsa nada
    chosen = left if order == "cost_max" else right

    def popup_open(image: np.ndarray) -> bool:
        title_now = fine_match(finder, image, "Sorting", white, popup, scaled_font_px(image, 32))
        return title_now is not None and title_now[0] >= 0.7

    def close_popup() -> bool:
        """Cierra la ventana volviendo a pulsar el botón del filtro (el que la abrió). True si se cerró."""
        ctx.device.tap(*FILTER_BUTTON.rect(frame.image).center)
        ctx.sleep(settle)
        return not popup_open(ctx.device.capture().image)

    if _button_active(frame.image, chosen[1]):
        # El filtro es un interruptor que el juego recuerda: pulsarlo ya activo lo APAGARÍA. Se deja como está.
        return close_popup()
    ctx.device.tap(*chosen[1].center)
    ctx.sleep(settle)
    after = ctx.device.capture()
    if not popup_open(after.image):
        return True  # la ventana se cerró sola al elegir
    # Medido en el juego: elegir NO cierra la ventana; el botón pasa a naranja. Se comprueba eso y se cierra.
    if not _button_active(after.image, chosen[1]):
        return False
    return close_popup()
