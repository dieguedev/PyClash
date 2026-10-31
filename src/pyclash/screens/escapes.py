"""Vía de escape de cada pantalla: el único toque que la recuperación puede dar en ella.

Cada vía localiza su objetivo en el fotograma y verifica que existe antes de devolver el punto;
si no lo encuentra no hay toque. Una pantalla desconocida solo tiene dos vías: un botón de cerrar
localizado o un cuadrado de terreno vacío. Ningún «OK» se pulsa sin que la pantalla haya sido
identificada: «Okay» solo se toca dentro del aviso conocido «Surrender?», y solo si se permite rendirse.

PROVISIONAL (sin verificar en el juego, ver docs/verificacion-manual-recuperacion.md): que tocar
terreno cierre el menú de ataque, y que «End Battle» en la exploración no cueste nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..vision.buttons import find_close_buttons, find_empty_terrain
from ..vision.color import find_green_buttons
from ..vision.text import TextFinder
from .classifier import Screen, TextIndicator
from .layout import RoiFraction
from .signatures import box


@dataclass(frozen=True)
class Tap:
    x: int
    y: int
    why: str  # qué se pulsa, para el registro y las pruebas


class EscapeRoute(Protocol):
    def locate(self, image: np.ndarray, finder: TextFinder) -> Tap | None: ...


@dataclass(frozen=True)
class CloseButton:
    region: RoiFraction = box(0.0, 0.0, 1.0, 1.0)

    def locate(self, image: np.ndarray, finder: TextFinder) -> Tap | None:
        found = find_close_buttons(image, self.region.rect(image))
        return Tap(*found[0].center, "botón de cerrar") if found else None


@dataclass(frozen=True)
class EmptyTerrain:
    region: RoiFraction = box(0.15, 0.15, 0.85, 0.80)
    min_value: int = 120  # bajarlo solo en pantallas identificadas cuyo menú oscurece el terreno

    def locate(self, image: np.ndarray, finder: TextFinder) -> Tap | None:
        found = find_empty_terrain(image, self.region.rect(image), min_value=self.min_value)
        return Tap(*found.center, "terreno vacío") if found else None


@dataclass(frozen=True)
class TextButton:
    """Un botón identificado por su texto, que debe verse en su región."""

    text: TextIndicator

    def locate(self, image: np.ndarray, finder: TextFinder) -> Tap | None:
        if self.text.score(image, finder) < self.text.threshold:
            return None
        found = self.text.best(image, finder)
        return Tap(*found.center, f"botón «{self.text.text}»") if found else None


@dataclass(frozen=True)
class GreenButton:
    region: RoiFraction
    name: str

    def locate(self, image: np.ndarray, finder: TextFinder) -> Tap | None:
        found = find_green_buttons(image, self.region.rect(image))
        return Tap(*found[0].center, f"botón «{self.name}»") if found else None


_FIND_BATTLE_END = TextIndicator("End Battle", box(0.0, 0.76, 0.12, 0.82), 25)
_SURRENDER = TextIndicator("Surrender", box(0.0, 0.76, 0.12, 0.82), 24)
_OKAY = TextIndicator("Okay", box(0.54, 0.58, 0.64, 0.66), 42)
_CANCEL = TextIndicator("Cancel", box(0.34, 0.58, 0.46, 0.66), 42)

# Pantalla conocida -> sus vías, probadas en orden. Sin entrada (o vacía): no hay salida segura.
ESCAPES: dict[Screen, tuple[EscapeRoute, ...]] = {
    Screen.UPGRADE_PANEL: (CloseButton(box(0.94, 0.0, 1.0, 0.10)),),
    Screen.ARMY_SELECTION: (CloseButton(box(0.88, 0.08, 0.97, 0.19)),),
    Screen.PLACING_BUILDING: (CloseButton(box(0.30, 0.20, 0.85, 0.75)),),
    Screen.ATTACK_MENU: (EmptyTerrain(box(0.55, 0.15, 0.80, 0.45), min_value=50),),  # PROVISIONAL: el menú oscurece el mundo
    Screen.FINDING_OPPONENT: (TextButton(_FIND_BATTLE_END),),  # PROVISIONAL
    Screen.RESULT: (GreenButton(box(0.40, 0.76, 0.60, 0.94), "Return Home"),),
}

# Pantalla desconocida: solo un botón de cerrar localizado o terreno vacío. Nunca un «OK».
UNKNOWN_ESCAPES: tuple[EscapeRoute, ...] = (CloseButton(), EmptyTerrain())


def routes_for(screen: Screen, allow_surrender: bool = False, terrain_fallback: bool = True) -> tuple[EscapeRoute, ...]:
    """Vías de `screen`. Rendirse (y confirmar «Okay») solo si `allow_surrender`."""
    if screen is Screen.UNKNOWN:
        return UNKNOWN_ESCAPES if terrain_fallback else UNKNOWN_ESCAPES[:1]
    if screen is Screen.BATTLE:
        return (TextButton(_SURRENDER),) if allow_surrender else ()
    if screen is Screen.CONFIRM_SURRENDER:
        return (TextButton(_OKAY if allow_surrender else _CANCEL),)
    return ESCAPES.get(screen, ())


def find_escape(
    image: np.ndarray,
    screen: Screen,
    finder: TextFinder,
    allow_surrender: bool = False,
    terrain_fallback: bool = True,
) -> Tap | None:
    for route in routes_for(screen, allow_surrender, terrain_fallback):
        tap = route.locate(image, finder)
        if tap is not None:
            return tap
    return None
