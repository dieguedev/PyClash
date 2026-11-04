"""Texto y dígitos con la fuente del juego: se renderiza la cadena y se busca por plantilla."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..device.base import Rect
from .color import RGB
from .result import Match, Provenance, Reading, crop
from .templates import TemplateMatcher

TEXT_COLORS: dict[str, RGB] = {
    "white": (255, 255, 255),
    "green": (180, 247, 86),  # nombre de una fila nueva («New»)
}
DIGITS = "0123456789"


def game_font_path() -> str:
    return str(resources.files("pyclash") / "assets" / "fonts" / "SupercellMagic.ttf")


@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # Motor de texto BÁSICO fijo: con raqm (si Pillow lo encuentra en el entorno) el espaciado cambia y las plantillas
    # salen con 1-2 píxeles de diferencia, lo que mueve las confianzas medidas (p. ej. 0,73 → 0,65). Todo se calibró sin raqm.
    return ImageFont.truetype(game_font_path(), size, layout_engine=ImageFont.Layout.BASIC)


@lru_cache(maxsize=2048)
def _render(text: str, size: int, color: RGB) -> np.ndarray:
    font = _font(size)
    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), text, font=font)
    w, h = max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(canvas).text((-bbox[0], -bbox[1]), text, font=font, fill=color)
    out = np.array(canvas)
    out.flags.writeable = False  # compartido por la caché
    return out


def render_text(text: str, size: int, color: RGB | str = "white") -> np.ndarray:
    """`text` con la fuente del juego a `size` px, `color` sobre negro, recortado a la tinta."""
    rgb = TEXT_COLORS[color] if isinstance(color, str) else color
    return _render(text, size, rgb)


@dataclass(frozen=True)
class SizeSweep:
    best_size: int
    best_confidence: float
    confidences: dict[int, float]


class TextFinder:
    def __init__(self, matcher: TemplateMatcher | None = None, threshold: float = 0.75) -> None:
        self.matcher = matcher or TemplateMatcher()
        self.threshold = threshold

    def find(
        self,
        image: np.ndarray,
        text: str,
        size: int,
        color: str | RGB = "white",
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match | None:
        """Centro y confianza de `text` renderizado a `size` y `color`; None si no está."""
        rgb = TEXT_COLORS[color] if isinstance(color, str) else color
        name = color if isinstance(color, str) else "rgb"
        return self.matcher.find(
            image,
            render_text(text, size, rgb),
            region,
            self.threshold if threshold is None else threshold,
            prefilter_color=rgb,
            label=text,
            method=f"font:{name}:s{size}",
        )

    def find_any(
        self,
        image: np.ndarray,
        text: str,
        sizes: Iterable[int],
        colors: Iterable[str | RGB] = ("white",),
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match | None:
        """La mejor coincidencia entre varios tamaños y colores (p. ej. blanco y verde «New»)."""
        best = None
        for color in colors:
            for size in sizes:
                m = self.find(image, text, size, color, region, threshold)
                if m is not None and (best is None or m.confidence > best.confidence):
                    best = m
        return best

    def calibrate_size(
        self,
        image: np.ndarray,
        text: str,
        sizes: Iterable[int] = range(8, 80),
        color: str | RGB = "white",
        region: Rect | None = None,
    ) -> SizeSweep:
        """Barrido de tamaños: el que mejor casa `text` en un fotograma que lo contiene."""
        rgb = TEXT_COLORS[color] if isinstance(color, str) else color
        confidences: dict[int, float] = {}
        for size in sizes:
            m = self.matcher.best(image, render_text(text, size, rgb), region, prefilter_color=rgb)
            confidences[size] = m.confidence if m is not None else 0.0
        if not confidences:
            raise ValueError("sizes vacío")
        best = max(confidences, key=confidences.get)  # type: ignore[arg-type]
        return SizeSweep(best, confidences[best], confidences)


class DigitReader:
    """Lee números renderizando los glifos 0–9 con la fuente del juego (sin OCR)."""

    def __init__(self, matcher: TemplateMatcher | None = None, threshold: float = 0.75) -> None:
        self.matcher = matcher or TemplateMatcher()
        self.threshold = threshold

    def _glyphs(
        self, image: np.ndarray, size: int, color: str | RGB, region: Rect | None, alphabet: str
    ) -> tuple[list[Match], Rect | None]:
        """Glifos aceptados de la región, de izquierda a derecha, y la región efectiva."""
        rgb = TEXT_COLORS[color] if isinstance(color, str) else color
        section, eff = crop(image, region)
        if section.size == 0:
            return [], eff
        candidates: list[Match] = []
        for d in alphabet:
            candidates += self.matcher.find_all(
                section, render_text(d, size, rgb), None, self.threshold,
                prefilter_color=rgb, max_overlap=0.3, label=d,
            )
        # Un glifo puede casar dentro de otro (un «1» dentro de un «0»): gana la mayor confianza.
        accepted: list[Match] = []
        for m in sorted(candidates, key=lambda c: c.confidence, reverse=True):
            if all(_x_overlap(m.rect, a.rect) <= 0.5 for a in accepted):
                accepted.append(m)
        accepted.sort(key=lambda m: m.rect.x)
        return accepted, eff

    def read(
        self,
        image: np.ndarray,
        size: int,
        color: str | RGB = "white",
        region: Rect | None = None,
        alphabet: str = DIGITS,
    ) -> Reading[str] | None:
        """Cadena de glifos de `alphabet` (por defecto 0–9) de la región; `confidence` es la del
        peor glifo. None si no hay ninguno. Añadir «/» sirve para leer fracciones como «2/5»."""
        accepted, eff = self._glyphs(image, size, color, region, alphabet)
        if not accepted:
            return None
        value = "".join(m.label or "" for m in accepted)
        return Reading(value, min(m.confidence for m in accepted), Provenance(eff, f"digits:font:s{size}"))

    def read_groups(
        self,
        image: np.ndarray,
        size: int,
        color: str | RGB = "white",
        region: Rect | None = None,
        alphabet: str = DIGITS,
        gap: float = 0.6,
    ) -> list[Reading[str]]:
        """Como `read`, pero separa los grupos de glifos que distan más de `gap` × `size` píxeles
        (varios chips en una misma franja). De izquierda a derecha."""
        accepted, eff = self._glyphs(image, size, color, region, alphabet)
        groups: list[list[Match]] = []
        for m in accepted:
            if groups and m.rect.x - (groups[-1][-1].rect.x + groups[-1][-1].rect.w) <= gap * size:
                groups[-1].append(m)
            else:
                groups.append([m])
        return [
            Reading("".join(m.label or "" for m in g), min(m.confidence for m in g), Provenance(eff, f"digits:font:s{size}"))
            for g in groups
        ]


def _x_overlap(a: Rect, b: Rect) -> float:
    """Solape horizontal respecto al más estrecho de los dos."""
    inter = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    return inter / max(1, min(a.w, b.w))


__all__ = ["DIGITS", "DigitReader", "SizeSweep", "TEXT_COLORS", "TextFinder", "game_font_path", "render_text"]
