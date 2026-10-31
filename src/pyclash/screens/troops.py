"""Identificar qué tropa es cada carta de la barra por su retrato (la zona de los ojos).

Cada tropa tiene un recorte pequeño de su cara en `assets/troops/<tropa>.png`, sacado de una carta a
1765 px de ancho de ventana (la carta medía ≈100 px). El recorte se escala por el ancho de la carta que
mide el detector de barra y se busca en ella en color: el color separa bien tropas parecidas (pelo
amarillo del Bárbaro, rosa de la Arquera). Medido sobre capturas reales: 0.95–0.98 con la tropa
correcta y ≈0.45 con otra.

El ejército lo elige el usuario y tiene que estar montado: si falta alguna tropa del ejército, o no se
distingue con claridad de las demás, no se ataca (`Troop Archer not found`).

Solo se identifica con la carta activa: una carta agotada se pone en gris y ya no casa. Se identifica
antes de empezar, cuando están llenas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Mapping

import cv2
import numpy as np
from PIL import Image

from ..device.base import Rect
from ..policy.armies import Army, troop_name
from .battle_bar import Card

REFERENCE_CARD_W = 100.0  # ancho de la carta de la que se sacaron los retratos
THRESHOLD = 0.8  # parecido mínimo con el retrato
MARGIN = 0.15  # lo que debe ganar al segundo retrato más parecido


@dataclass(frozen=True)
class Identification:
    troop: str | None
    score: float  # parecido con la mejor tropa
    runner_up: float  # parecido con la siguiente


@dataclass(frozen=True)
class ArmyCheck:
    ok: bool
    cards: dict[str, Card] = field(default_factory=dict)  # tropa -> su carta
    reason: str = ""
    others: tuple[str, ...] = ()  # tropas conocidas que están en la barra y no son del ejército


class TroopPortraits:
    def __init__(self, portraits: Mapping[str, np.ndarray]) -> None:
        self.portraits = dict(portraits)

    @classmethod
    def load_packaged(cls) -> "TroopPortraits":
        folder = resources.files("pyclash") / "assets" / "troops"
        found: dict[str, np.ndarray] = {}
        for item in folder.iterdir():
            if item.name.lower().endswith(".png"):
                with item.open("rb") as handle, Image.open(handle) as im:
                    found[item.name[:-4]] = np.array(im.convert("RGB"))
        return cls(found)

    def scores(self, image: np.ndarray, rect: Rect) -> dict[str, float]:
        card = image[rect.y : rect.y + rect.h, rect.x : rect.x + rect.w]
        if card.size == 0:
            return {name: 0.0 for name in self.portraits}
        factor = rect.w / REFERENCE_CARD_W
        out: dict[str, float] = {}
        for name, portrait in self.portraits.items():
            scaled = cv2.resize(portrait, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            scaled = scaled[: card.shape[0], : card.shape[1]]
            result = cv2.matchTemplate(card, scaled, cv2.TM_CCOEFF_NORMED)
            value = float(np.nan_to_num(result.max(), nan=0.0))
            out[name] = max(0.0, value)
        return out

    def identify(self, image: np.ndarray, rect: Rect, threshold: float = THRESHOLD, margin: float = MARGIN) -> Identification:
        ranked = sorted(self.scores(image, rect).items(), key=lambda kv: kv[1], reverse=True)
        if not ranked:
            return Identification(None, 0.0, 0.0)
        best_name, best = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        if best < threshold or best - runner < margin:
            return Identification(None, best, runner)
        return Identification(best_name, best, runner)


def check_army(image: np.ndarray, cards: list[Card], army: Army, portraits: TroopPortraits) -> ArmyCheck:
    """Asigna a cada tropa del ejército una carta de tropa de la barra. Si falta alguna: error claro."""
    candidates = [c for c in cards if c.kind == "troop"]
    identified = [(c, portraits.identify(image, c.rect)) for c in candidates]
    assigned: dict[str, Card] = {}
    for troop in army.troops:
        match = next((c for c, ident in identified if ident.troop == troop and c not in assigned.values()), None)
        if match is None:
            return ArmyCheck(False, reason=f"Troop {troop_name(troop)} not found")
        if match.count == 0:
            return ArmyCheck(False, reason=f"Troop {troop_name(troop)} not found (la carta marca x0)")
        assigned[troop] = match
    others = tuple(sorted({i.troop for c, i in identified if i.troop and i.troop not in army.troops}))
    return ArmyCheck(True, assigned, others=others)


__all__ = ["ArmyCheck", "Identification", "TroopPortraits", "check_army"]
