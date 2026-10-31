"""Ejércitos que el bot sabe atacar: una lista ordenada de tipos de tropa que se despliegan uno tras otro.

El usuario elige uno en la app y tiene que tenerlo montado: el bot no lo monta. Añadir un ejército es
añadir una entrada (y, si hace falta, el retrato de su tropa en `assets/troops/`).

Por ahora solo Bárbaros. Después: Bárbaros y Arqueras, Gigantes y Arqueras…
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Army:
    key: str
    name: str
    troops: tuple[str, ...]  # identificadores de tropa (= nombre del retrato), en orden de despliegue


# Nombre de cada tropa como lo escribe el juego en inglés: sale en los mensajes («Troop Archer not found»).
TROOP_NAMES: dict[str, str] = {"barbarian": "Barbarian", "archer": "Archer"}

ARMIES: dict[str, Army] = {
    "barbarians": Army("barbarians", "Bárbaros", ("barbarian",)),
}


def troop_name(troop: str) -> str:
    return TROOP_NAMES.get(troop, troop.replace("_", " ").title())
