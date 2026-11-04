"""Ejércitos que el bot sabe atacar: una lista ordenada de tipos de tropa que se despliegan uno tras otro.

El jugador deja activo en el juego el ejército que quiere usar: el bot no lo monta, no pulsa plantillas ni
lee colas. Añadir un ejército es añadir una entrada (y, si hace falta, el retrato de su tropa en `assets/troops/`).

BArch (#38): bárbaros y después arqueras, solo las cartas que haya en la barra (en TH1 solo hay bárbaros).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Army:
    key: str
    name: str
    troops: tuple[str, ...]  # identificadores de tropa (= nombre del retrato), en orden de despliegue
    # True: toda tropa de la lista debe estar en la barra. False: se despliegan las que haya y solo falla si no hay ninguna.
    strict: bool = True


# Nombre de cada tropa como lo escribe el juego en inglés: sale en los mensajes («Troop Archer not found»).
TROOP_NAMES: dict[str, str] = {"barbarian": "Barbarian", "archer": "Archer"}

ARMIES: dict[str, Army] = {
    "barch": Army("barch", "BArch", ("barbarian", "archer"), strict=False),
    "barbarians": Army("barbarians", "Bárbaros", ("barbarian",)),
}


def troop_name(troop: str) -> str:
    return TROOP_NAMES.get(troop, troop.replace("_", " ").title())
