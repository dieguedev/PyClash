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
from ..vision.result import crop
from ..vision.templates import TemplateMatcher
from ..vision.text import DIGITS, DigitReader
from .layout import DEFAULT_LAYOUT, HomeLayout, RoiFraction, scaled_font_px
from .stable import stable_fields

Fraction = tuple[int, int]  # (libres, total)
Resources = tuple[int, int, int]  # (oro, elixir, elixir oscuro)
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

    def _chip(self, image: np.ndarray, roi: RoiFraction, max_total: int) -> Fraction | None:
        size = scaled_font_px(image, self.layout.chip_font_px)
        r = self.digits.read(image, size, region=roi.rect(image), alphabet=DIGITS + "/")
        return parse_fraction(r.value, max_total) if r is not None else None

    def read_builders(self, image: np.ndarray, base: Literal["home", "builder"] = "home") -> Fraction | None:
        if base == "home":
            return self._chip(image, self.layout.builder_chip, self.layout.max_home_builders)
        return self._chip(image, self.layout.builder_base_chip, self.layout.max_builder_base_builders)

    def read_lab(self, image: np.ndarray) -> Fraction | None:
        return self._chip(image, self.layout.lab_chip, self.layout.max_lab_slots)

    def read_resources(self, image: np.ndarray) -> Resources | None:
        """Oro, elixir y elixir oscuro del HUD: tres filas apiladas, cada una leída por separado."""
        hud = self.layout.hud.rect(image)
        size = scaled_font_px(image, self.layout.hud_font_px)
        band = hud.h // 3
        values = []
        for i in range(3):
            row = Rect(hud.x, hud.y + i * band, hud.w, band)
            r = self.digits.read(image, size, region=row)
            if r is None:
                return None
            values.append(int(r.value))
        return (values[0], values[1], values[2])

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
