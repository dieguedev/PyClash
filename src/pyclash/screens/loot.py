"""Botín disponible del rival («Available Loot»), leído de la pantalla de exploración."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..device.base import Rect
from ..policy.loot import LootLimits, Triplet
from ..vision.result import crop
from ..vision.text import DigitReader
from .layout import scaled_font_px

ENEMY_LOOT_FONT_PX = 30  # PROVISIONAL, a 1440 de alto (ver layout.py)


@dataclass(frozen=True)
class EnemyLoot:
    amounts: Triplet
    confidence: float  # la del peor dígito leído


def read_enemy_loot(
    image: np.ndarray,
    roi: Rect,
    digits: DigitReader | None = None,
    limits: LootLimits | None = None,
) -> EnemyLoot | None:
    """Oro, elixir y oscuro del rival. `roi` cubre las tres filas, a la derecha de sus iconos.

    Cada tercio se lee por separado. Una fila de oscuro vacía vale 0 (las aldeas bajas no tienen).
    Devuelve None si no se leen oro o elixir, o si algún valor supera lo que un ataque puede dar:
    el botín disponible nunca excede lo que un ataque puede llevarse.
    """
    reader = digits or DigitReader()
    section, eff = crop(image, roi)
    if section.size == 0:
        return None
    size = scaled_font_px(image, ENEMY_LOOT_FONT_PX)
    band = eff.h // 3
    values: list[int | None] = []
    confidences: list[float] = []
    for i in range(3):
        row = Rect(eff.x, eff.y + i * band, eff.w, band)
        r = reader.read(image, size, region=row)
        values.append(int(r.value) if r is not None else None)
        if r is not None:
            confidences.append(r.confidence)
    gold, elixir, dark = values
    if gold is None or elixir is None:
        return None
    amounts = (gold, elixir, dark or 0)
    lim = limits or LootLimits()
    if amounts[0] > lim.max_gain_main or amounts[1] > lim.max_gain_main or amounts[2] > lim.max_gain_dark:
        return None
    return EnemyLoot(amounts, min(confidences))
