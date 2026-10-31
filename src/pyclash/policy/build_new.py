"""Política de edificios nuevos: qué fila «New» construir ahora, y por qué se descartó el resto.

Función pura, como `decide_upgrade`: sin visión, dispositivo ni reloj. Reglas de la issue #10:

  R2: nunca gemas, nunca el muro gratis del tutorial, nunca un precio en rojo ni indeterminado.
  R3: solo con un constructor libre por encima de la reserva (sin él ni se abre el panel).

Dentro de lo elegible decide `rank` (por defecto, el más caro asequible: lo que más aporta). Es un punto
de extensión: los filtros por tipo del rusher (almacenes, ejército, lo que desbloquea el Ayuntamiento)
serán otro `rank` o un prefiltro, sin tocar la cadena de seguridad. Los muros de pago quedan fuera:
encadenan colocaciones en línea y son otro flujo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Sequence

from ..screens.upgrade_panel import PanelRow
from .upgrades import WALL, Order, _amount

Rank = Callable[[Sequence[PanelRow]], Sequence[PanelRow]]  # la primera de la lista es la elegida


class Reason(Enum):
    UNNAMED = "nombre no reconocido"
    NOT_NEW = "no es un edificio nuevo"
    COOLDOWN = "en cooldown"
    GEMS = "cuesta gemas"
    FREE = "gratis (tutorial)"
    WALL = "los muros nuevos van aparte"
    NOT_AFFORDABLE = "coste en rojo"
    UNKNOWN_COST = "recurso o coste indeterminado"
    INSUFFICIENT = "recursos insuficientes"


def by_cost(order: Order = Order.PRICIEST) -> Rank:
    """El más caro (o barato) primero; a igual coste, el que sale antes en el panel."""
    sign = -1 if order is Order.PRICIEST else 1
    return lambda rows: sorted(rows, key=lambda r: (sign * (r.cost or 0), r.y))


@dataclass(frozen=True)
class BuildSettings:
    reserve_builders: int = 1
    order: Order = Order.PRICIEST
    max_placements: int = 3  # colocaciones por fila y pasada (Bomb ×2, Cannon ×2...)
    rank: Rank | None = None  # sustituye a `order` cuando haga falta otro criterio
    dry_run: bool = False


@dataclass(frozen=True)
class BuildState:
    rows: Sequence[PanelRow]
    resources: tuple[int | None, int | None, int | None] = (None, None, None)  # oro, elixir, oscuro
    free_builders: int | None = None  # None: desconocido, no se hace nada
    on_cooldown: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Rejection:
    row: PanelRow
    reason: Reason


@dataclass(frozen=True)
class BuildDecision:
    chosen: PanelRow | None
    reason: str
    placements: int = 0  # cuántas unidades de la fila se pueden colocar de una vez
    rejected: tuple[Rejection, ...] = field(default=(), compare=False)
    dry_run: bool = False

    def why_not(self, name: str) -> list[Reason]:
        return [r.reason for r in self.rejected if r.row.name == name]


def build_gate(free_builders: int | None, reserve: int) -> str | None:
    """Motivo por el que no se puede ni abrir el panel (R3), o None."""
    if free_builders is None:
        return "constructores libres desconocidos"
    if free_builders <= reserve:
        return f"{free_builders} constructores libres, reserva {reserve}"
    return None


def _screen(row: PanelRow, state: BuildState) -> Reason | None:
    """Motivo por el que una fila no es elegible, o None. El primero que salta es el que se informa."""
    if row.name is None:
        return Reason.UNNAMED
    if row.kind != "new_building" and not (row.new and row.kind == "wall"):
        return Reason.NOT_NEW
    if row.name in state.on_cooldown:
        return Reason.COOLDOWN
    if row.resource == "gems":
        return Reason.GEMS
    if row.free:
        return Reason.FREE
    if row.kind == "wall" or row.name == WALL:
        return Reason.WALL
    if row.affordable is False:
        return Reason.NOT_AFFORDABLE
    if row.cost is None or row.resource is None:
        return Reason.UNKNOWN_COST
    have = _amount(state.resources, row.resource)
    if have is not None and row.cost > have:
        return Reason.INSUFFICIENT
    return None


def decide_build_new(state: BuildState, settings: BuildSettings) -> BuildDecision:
    dry = settings.dry_run

    def none(reason: str, rejected: Iterable[Rejection] = ()) -> BuildDecision:
        return BuildDecision(None, reason, 0, tuple(rejected), dry)

    blocked = build_gate(state.free_builders, settings.reserve_builders)
    if blocked:
        return none(blocked)

    rejected: list[Rejection] = []
    eligible: list[PanelRow] = []
    for row in state.rows:
        reason = _screen(row, state)
        if reason is None:
            eligible.append(row)
        elif reason is not Reason.NOT_NEW:  # las mejoras no son «rechazos» de esta tarea
            rejected.append(Rejection(row, reason))
    if not eligible:
        return none("ninguna fila nueva elegible", rejected)

    rank = settings.rank or by_cost(settings.order)
    row = rank(eligible)[0]
    have = _amount(state.resources, row.resource)
    affordable_units = row.quantity if have is None or not row.cost else have // row.cost
    placements = max(1, min(row.quantity, affordable_units, settings.max_placements))
    why = f"{row.name} ×{placements} ({settings.order.value})" if settings.rank is None else f"{row.name} ×{placements}"
    return BuildDecision(row, why, placements, tuple(rejected), dry)
