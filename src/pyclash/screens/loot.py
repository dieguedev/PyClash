"""Lectores de botín y de costes con la fuente del juego (issue #12).

- Botín disponible del rival («Available Loot»), en la exploración.
- Coste de «Next» (exploración) y de «Find a Match» (menú de ataque, se ve sin pagar).
- Oro del HUD en la exploración.
- Botín ganado («You got:»), en la pantalla de resultado.
- Nombre del rival, solo para desempatar un «Next» cuyo botín coincide; nunca se registra.

Medido con capturas reales a 720 de alto (ver tests/fixtures): tamaños de fuente en `*_PX`, a
BASE_HEIGHT. PROVISIONAL: la fila de elixir oscuro (no hay ninguna captura con ella) y la fila de
oscuro de «You got:»; el resto está calibrado contra capturas reales.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..device.base import Rect
from ..policy.loot import EnemyLoot, LootLimits
from ..vision.ocr import OcrEngine, read_words
from ..vision.result import crop
from ..vision.text import DigitReader
from .layout import RoiFraction, scaled_font_px
from .signatures import box

# Tamaños a BASE_HEIGHT (1440): a 720 salen 13, 16, 17, 13 y 21,5.
ENEMY_LOOT_PX = 26
NEXT_COST_PX = 32
SEARCH_COST_PX = 34
HUD_GOLD_PX = 26
RESULT_PX = 43

LOOT_THRESHOLD = 0.65  # el «1» de «1 000» (1000) puntúa 0.70: con 0.75 se lee «000» y vale 0
MAX_COST = 10_000  # un coste de «Next» o de búsqueda mayor es una lectura corrupta

# Dígitos del botín del rival: las tres filas apiladas bajo «Available Loot:».
ENEMY_ROWS = (
    box(0.040, 0.098, 0.115, 0.135),  # oro
    box(0.040, 0.143, 0.115, 0.178),  # elixir
    box(0.040, 0.187, 0.115, 0.224),  # elixir oscuro (PROVISIONAL)
)
DARK_ICON = box(0.018, 0.187, 0.046, 0.224)

NEXT_COST = box(0.925, 0.76, 0.975, 0.81)
# El contenido del botón («350 🪙») va centrado: con más o menos cifras los dígitos se desplazan (0,12-0,15 con
# 3 cifras, 0,17-0,195 con 2). La región cubre cualquier longitud; la moneda no se lee como dígito (texto blanco).
SEARCH_COST = box(0.07, 0.70, 0.23, 0.75)
HUD_GOLD = box(0.80, 0.015, 0.97, 0.07)

# «You got:» (alineado a la derecha, junto al icono): oro, elixir y oscuro (PROVISIONAL la tercera).
RESULT_ROWS = (box(0.38, 0.46, 0.54, 0.53), box(0.38, 0.54, 0.54, 0.60), box(0.38, 0.60, 0.54, 0.655))

ENEMY_NAME = box(0.03, 0.008, 0.15, 0.06)  # la zona que se tapa con negro en las capturas del repo


def _reader(digits: DigitReader | None) -> DigitReader:
    return digits or DigitReader(threshold=LOOT_THRESHOLD)


def read_number(
    image: np.ndarray, region: Rect, base_px: int, digits: DigitReader
) -> tuple[int, float] | None:
    """Número de la región, leído al tamaño de la fuente de la ventana. Solo si ahí no sale nada se
    prueban los tamaños vecinos (el redondeo cambia con la ventana). Probar siempre varios tamaños
    y quedarse con la lectura más larga se midió y es peor: a otro tamaño aparecen glifos falsos
    («2000» daba «201111»)."""
    size = scaled_font_px(image, base_px)
    for s in (size, max(1, size - 1), size + 1):
        r = digits.read(image, s, region=region)
        if r is not None and r.value:
            return int(r.value), r.confidence
    return None


def _count(patch: np.ndarray, mask: np.ndarray) -> float:
    return float(mask.mean()) if mask.size else 0.0


def dark_icon_present(image: np.ndarray) -> bool:
    """¿Hay icono de elixir oscuro en la tercera fila? PROVISIONAL (sin captura con esa fila).

    El oscuro es una gota violeta muy oscura; el elixir (rosa vivo) también tiene sombra oscura,
    así que además se exige que casi no haya rosa.
    """
    patch, _ = crop(image, DARK_ICON.rect(image))
    if patch.size == 0:
        return False
    p = patch.astype(int)
    r, g, b = p[..., 0], p[..., 1], p[..., 2]
    mx = p.max(axis=2)
    dark = (b > g + 12) & (r > g + 6) & (mx < 125)
    pink = (r > 130) & (b > 130) & (g < r - 40) & (g < b - 40)
    return _count(patch, dark) >= 0.15 and _count(patch, pink) < 0.15


def read_enemy_loot(
    image: np.ndarray,
    digits: DigitReader | None = None,
    limits: LootLimits | None = None,
) -> EnemyLoot:
    """Oro, elixir y oscuro del rival. Cada fila se lee por separado; lo ilegible o inverosímil
    (más de lo que un ataque puede dar) queda en `None`.

    La fila de oscuro existe si se ve su icono o se leen sus dígitos. Sin ninguna de las dos la aldea
    no tiene oscuro: `dark_row` False (vale 0). Con icono y sin dígitos legibles: `dark_row` True y
    `dark` None. Una aldea sin oscuro es normal, no una lectura fallida.
    """
    reader = _reader(digits)
    lim = limits or LootLimits()
    got = [read_number(image, row.rect(image), ENEMY_LOOT_PX, reader) for row in ENEMY_ROWS]
    caps = (lim.max_gain_main, lim.max_gain_main, lim.max_gain_dark)
    gold, elixir, dark = (g[0] if g is not None and g[0] <= cap else None for g, cap in zip(got, caps))
    row = got[2] is not None or dark_icon_present(image)
    return EnemyLoot(gold, elixir, dark, row)


def read_next_cost(image: np.ndarray, digits: DigitReader | None = None) -> int | None:
    """Coste en oro de «Next», leído del propio botón."""
    got = read_number(image, NEXT_COST.rect(image), NEXT_COST_PX, _reader(digits))
    return got[0] if got is not None and 0 < got[0] <= MAX_COST else None


def read_search_cost(image: np.ndarray, digits: DigitReader | None = None) -> int | None:
    """Coste de «Find a Match», leído del botón del menú de ataque (sin pagar nada)."""
    got = read_number(image, SEARCH_COST.rect(image), SEARCH_COST_PX, _reader(digits))
    return got[0] if got is not None and 0 < got[0] <= MAX_COST else None


def read_hud_gold(image: np.ndarray, digits: DigitReader | None = None) -> int | None:
    """Oro propio del HUD de la exploración (solo la primera fila: ahí no hay tres)."""
    got = read_number(image, HUD_GOLD.rect(image), HUD_GOLD_PX, _reader(digits))
    return got[0] if got is not None else None


@dataclass(frozen=True)
class ResultLoot:
    gold: int
    elixir: int
    dark: int  # 0 si no hay fila de oscuro


def read_result_loot(
    image: np.ndarray,
    digits: DigitReader | None = None,
    limits: LootLimits | None = None,
) -> ResultLoot | None:
    """Botín ganado de «You got:». None si no se leen oro y elixir o si algún valor es inverosímil."""
    reader = _reader(digits)
    got = [read_number(image, row.rect(image), RESULT_PX, reader) for row in RESULT_ROWS]
    if got[0] is None or got[1] is None:
        return None
    loot = ResultLoot(got[0][0], got[1][0], got[2][0] if got[2] is not None else 0)
    if not (limits or LootLimits()).plausible(loot.gold, loot.elixir, loot.dark):
        return None
    return loot


def read_enemy_name(engine: OcrEngine, image: np.ndarray) -> str | None:
    """Nombre del rival, solo para compararlo con el anterior en memoria. No se registra jamás."""
    words = read_words(engine, image, ENEMY_NAME.rect(image))
    text = " ".join(w.value for w in sorted(words, key=lambda w: w.provenance.region.x))
    return text or None


__all__ = [
    "EnemyLoot",
    "ResultLoot",
    "dark_icon_present",
    "read_enemy_loot",
    "read_enemy_name",
    "read_hud_gold",
    "read_next_cost",
    "read_number",
    "read_result_loot",
    "read_search_cost",
]
