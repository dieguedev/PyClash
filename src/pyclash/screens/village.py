"""Estado del pueblo: constructores, laboratorio, almacenes llenos y recursos del HUD.

Todo campo es opcional: un indicador ausente o ilegible da `None`, nunca una suposición, para
que quien decida sea conservador. Los valores salen de los indicadores del juego, no de tablas
por nivel de Ayuntamiento.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping

import numpy as np

from ..device.base import Frame, Rect
from ..runtime.context import Context
from ..vision.color import RGB, color_fraction
from ..vision.result import Reading, crop
from ..vision.templates import TemplateMatcher
from ..vision.text import DIGITS, DigitReader
from .layout import DEFAULT_LAYOUT, HomeLayout, RoiFraction, scaled_font_px
from .stable import stable_fields

Fraction = tuple[int, int]  # (libres, total)
Resources = tuple[int, int, int | None]  # (oro, elixir, elixir oscuro); el oscuro aún no se lee (None)
RESOURCES = ("gold", "elixir")  # el elixir oscuro no tiene icono de almacén lleno


@dataclass(frozen=True)
class StorageIcon:
    """Icono de «almacén lleno» de la barra del héroe: plantilla y el color que debe tener."""

    template: np.ndarray
    color: RGB
    threshold: float = 0.85
    min_color_fraction: float = 0.5


@dataclass(frozen=True)
class VillageState:
    builders: Fraction | None
    lab: Fraction | None
    gold_full: bool | None  # None: no se pudo comprobar (sin icono configurado o sin acuerdo)
    elixir_full: bool | None
    resources: Resources | None
    unreliable: frozenset[str] = field(default_factory=frozenset)  # campos sin acuerdo entre capturas

    @property
    def reliable(self) -> bool:
        return not self.unreliable

    @property
    def free_builders(self) -> int | None:
        return self.builders[0] if self.builders else None

    @property
    def lab_idle(self) -> bool:
        """Solo es True si el indicador se leyó y muestra una ranura libre."""
        return bool(self.lab) and self.lab[0] > 0

    @property
    def storages_full(self) -> bool:
        return self.gold_full is True and self.elixir_full is True


def parse_fraction(text: str, max_total: int) -> Fraction | None:
    """«2/5» -> (2, 5). Sin barra se acepta solo «25» (dos dígitos). None si es incoherente."""
    if "/" in text:
        left, _, right = text.partition("/")
        parts = (left, right)
    elif len(text) == 2:
        parts = (text[0], text[1])
    else:
        return None
    if not all(p.isdigit() and 0 < len(p) <= 2 for p in parts):
        return None
    free, total = int(parts[0]), int(parts[1])
    return (free, total) if 0 <= free <= total <= max_total and total >= 1 else None


class VillageReader:
    def __init__(
        self,
        layout: HomeLayout = DEFAULT_LAYOUT,
        digits: DigitReader | None = None,
        matcher: TemplateMatcher | None = None,
        storage_icons: Mapping[str, StorageIcon] | None = None,
        min_confidence: float = 0.75,
    ) -> None:
        self.layout = layout
        self.digits = digits or DigitReader(threshold=min_confidence)
        self.matcher = matcher or TemplateMatcher()
        self.storage_icons = dict(storage_icons or {})

    def _best(self, image: np.ndarray, base_px: int, region: Rect, alphabet: str) -> list[Reading[str]]:
        """Lecturas por grupos con el tamaño de fuente que mejor casa (el tamaño exacto varía ±1)."""
        size = scaled_font_px(image, base_px)
        best: list[Reading[str]] = []
        best_score = (0, 0.0)
        for s in (size - 1, size, size + 1):
            if s < 6:
                continue
            groups = self.digits.read_groups(image, s, region=region, alphabet=alphabet)
            glyphs = sum(len(g.value) for g in groups)
            # Un tamaño equivocado lee de menos (se salta glifos): gana el que lee más; a igualdad, el más seguro.
            score = (glyphs, sum(g.confidence * len(g.value) for g in groups) / glyphs if glyphs else 0.0)
            if glyphs and score > best_score:
                best, best_score = groups, score
        return best

    def _chips(self, image: np.ndarray, strip: RoiFraction) -> list[str]:
        return [g.value for g in self._best(image, self.layout.chip_font_px, strip.rect(image), DIGITS + "/")]

    def read_builders(self, image: np.ndarray, base: Literal["home", "builder"] = "home") -> Fraction | None:
        """El primer chip de la franja (el de más a la izquierda)."""
        home = base == "home"
        chips = self._chips(image, self.layout.chip_strip if home else self.layout.builder_base_strip)
        maximum = self.layout.max_home_builders if home else self.layout.max_builder_base_builders
        return parse_fraction(chips[0], maximum) if chips else None

    def read_lab(self, image: np.ndarray) -> Fraction | None:
        """El segundo chip de la franja; sin él (cuentas con un solo chip), None."""
        chips = self._chips(image, self.layout.chip_strip)
        return parse_fraction(chips[1], self.layout.max_lab_slots) if len(chips) > 1 else None

    def _amount(self, image: np.ndarray, roi: RoiFraction) -> int | None:
        groups = self._best(image, self.layout.hud_font_px, roi.rect(image), DIGITS)
        return int("".join(g.value for g in groups)) if groups else None

    def read_resources(self, image: np.ndarray) -> Resources | None:
        """Oro y elixir del HUD, cada uno de su barra. El elixir oscuro no se lee por posición (None)."""
        gold, elixir = self._amount(image, self.layout.gold_bar), self._amount(image, self.layout.elixir_bar)
        return None if gold is None or elixir is None else (gold, elixir, None)

    def storage_full(self, image: np.ndarray, resource: str) -> bool | None:
        """True/False según el icono de almacén lleno; None si no hay icono configurado."""
        icon = self.storage_icons.get(resource)
        if icon is None:
            return None
        hud = self.layout.hud.rect(image)
        for m in self.matcher.find_all(image, icon.template, hud, icon.threshold):
            patch, _ = crop(image, m.rect)
            if color_fraction(patch, icon.color) >= icon.min_color_fraction:
                return True
        return False

    def read_state(self, image: np.ndarray) -> VillageState:
        """Una sola captura, sin comprobar acuerdo (para eso, `read_state_stable`)."""
        return VillageState(
            builders=self.read_builders(image),
            lab=self.read_lab(image),
            gold_full=self.storage_full(image, "gold"),
            elixir_full=self.storage_full(image, "elixir"),
            resources=self.read_resources(image),
        )

    def read_state_stable(
        self,
        ctx: Context,
        capture: Callable[[], Frame] | None = None,
        required: int = 3,
        attempts: int = 6,
        interval: float = 0.35,
    ) -> VillageState:
        """Estado con acuerdo entre capturas consecutivas, campo a campo.

        Un campo sin acuerdo queda en `None` y figura en `unreliable`; quien decida debe
        comprobar `reliable` (o que el campo concreto no esté en `unreliable`) antes de fiarse.
        """
        readers = {
            "builders": lambda f: self.read_builders(f.image),
            "lab": lambda f: self.read_lab(f.image),
            "gold_full": lambda f: self.storage_full(f.image, "gold"),
            "elixir_full": lambda f: self.storage_full(f.image, "elixir"),
            "resources": lambda f: self.read_resources(f.image),
        }
        # Sin icono configurado el campo es siempre None: no se espera acuerdo de lo que no se puede leer.
        unreadable = {f"{r}_full" for r in RESOURCES if r not in self.storage_icons}
        stable = stable_fields(
            ctx, {k: v for k, v in readers.items() if k not in unreadable}, capture, required, attempts, interval
        )
        values = {k: s.value for k, s in stable.items()}
        return VillageState(
            builders=values["builders"],
            lab=values["lab"],
            gold_full=values.get("gold_full"),
            elixir_full=values.get("elixir_full"),
            resources=values["resources"],
            unreliable=frozenset(k for k, s in stable.items() if not s.reliable),
        )
