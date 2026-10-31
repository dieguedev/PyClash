"""Motor de OCR detrás de una interfaz. EasyOCR es el motor por defecto (extra opcional `.[ocr]`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

from ..device.base import Rect
from .preprocess import binarize_white, component_boxes
from .result import Provenance, Reading, crop


@dataclass(frozen=True)
class WordBox:
    text: str
    rect: Rect  # relativo a la imagen que se pasó al motor
    confidence: float


@dataclass(frozen=True)
class DigitGlyph:
    char: str
    rect: Rect
    confidence: float


class OcrEngine(Protocol):
    name: str

    def read_words(self, image: np.ndarray) -> list[WordBox]:
        """Palabras con caja y confianza."""
        ...

    def read_digits(self, image: np.ndarray) -> list[DigitGlyph]:
        """Dígitos carácter a carácter, de izquierda a derecha, con confianza por glifo."""
        ...


class FakeOcr:
    """OCR para pruebas: devuelve lo que se le indicó, sin mirar la imagen."""

    name = "fake"

    def __init__(self, words: Sequence[WordBox] = (), digits: Sequence[DigitGlyph] = ()) -> None:
        self._words = list(words)
        self._digits = list(digits)
        self.calls: list[str] = []

    def read_words(self, image: np.ndarray) -> list[WordBox]:
        self.calls.append("words")
        return list(self._words)

    def read_digits(self, image: np.ndarray) -> list[DigitGlyph]:
        self.calls.append("digits")
        return list(self._digits)


class EasyOcrEngine:
    """EasyOCR con importación perezosa: `import pyclash.vision.ocr` no arrastra PyTorch."""

    name = "easyocr"

    def __init__(self, languages: Sequence[str] = ("en",), gpu: bool = False) -> None:
        self._languages = list(languages)
        self._gpu = gpu
        self._reader = None

    def _get_reader(self):
        if self._reader is None:
            try:
                import easyocr
            except ImportError as e:  # pragma: no cover - depende del extra
                raise RuntimeError('EasyOCR no está instalado: python -m pip install -e ".[ocr]"') from e
            self._reader = easyocr.Reader(self._languages, gpu=self._gpu)
        return self._reader

    def read_words(self, image: np.ndarray) -> list[WordBox]:  # pragma: no cover - requiere EasyOCR
        words = []
        for corners, text, conf in self._get_reader().readtext(image):
            xs = [int(p[0]) for p in corners]
            ys = [int(p[1]) for p in corners]
            if text.strip():
                words.append(WordBox(text.strip(), Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), float(conf)))
        return words

    def read_digits(self, image: np.ndarray) -> list[DigitGlyph]:  # pragma: no cover - requiere EasyOCR
        """Aísla cada glifo (componentes del texto blanco) y reconoce cada recorte por separado,
        para tener una confianza por carácter."""
        reader = self._get_reader()
        glyphs = []
        for x, y, w, h in component_boxes(binarize_white(image), min_area=4):
            pad = 3
            crop_img = image[max(0, y - pad): y + h + pad, max(0, x - pad): x + w + pad]
            result = reader.recognize(crop_img, allowlist="0123456789", detail=1)
            if result:
                _, text, conf = result[0]
                if len(text) == 1:
                    glyphs.append(DigitGlyph(text, Rect(x, y, w, h), float(conf)))
        return glyphs


def read_words(engine: OcrEngine, image: np.ndarray, region: Rect | None = None) -> list[Reading[str]]:
    """Palabras con la región y el método de procedencia; las cajas pasan a coordenadas del fotograma."""
    section, eff = crop(image, region)
    if section.size == 0:
        return []
    prov = Provenance(eff, f"ocr:{engine.name}:words")
    return [Reading(w.text, w.confidence, Provenance(Rect(eff.x + w.rect.x, eff.y + w.rect.y, w.rect.w, w.rect.h), prov.method))
            for w in engine.read_words(section)]


def read_digit_string(engine: OcrEngine, image: np.ndarray, region: Rect | None = None) -> Reading[str] | None:
    """Número leído carácter a carácter; la confianza es la del peor glifo. None si no hay dígitos."""
    section, eff = crop(image, region)
    if section.size == 0:
        return None
    glyphs = sorted(engine.read_digits(section), key=lambda g: g.rect.x)
    if not glyphs:
        return None
    return Reading("".join(g.char for g in glyphs), min(g.confidence for g in glyphs),
                   Provenance(eff, f"ocr:{engine.name}:digits"))
