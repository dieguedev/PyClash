"""Reserva de oro (issue #32): ningún gasto de oro puede dejar menos del oro necesario para buscar partida.

Lógica pura: sin visión, dispositivo ni reloj. Los costes se leen del botón «Find a Match» del menú de
ataque (se ven sin pagar) y viven solo en memoria; el tiempo entra por argumento.
"""

from __future__ import annotations

from dataclasses import dataclass

from .loot import gold_floor

MAX_COST_AGE_S = 3600.0  # pasada esta antigüedad el coste se vuelve a leer antes de cualquier decisión de oro


@dataclass(frozen=True)
class GoldCosts:
    next_cost: int
    search_cost: int
    read_at: float


class GoldReserve:
    """Últimos costes leídos y el umbral que se deriva de ellos."""

    def __init__(self) -> None:
        self.costs: GoldCosts | None = None

    def update(self, next_cost: int, search_cost: int, now: float) -> None:
        self.costs = GoldCosts(next_cost, search_cost, now)

    def is_fresh(self, now: float) -> bool:
        return self.costs is not None and now - self.costs.read_at <= MAX_COST_AGE_S

    def needs_read(self, now: float) -> bool:
        return not self.is_fresh(now)

    def floor(self, now: float) -> int | None:
        """Oro mínimo que debe quedar tras un gasto; None si el coste se desconoce o caducó (no se adivina)."""
        if not self.is_fresh(now):
            return None
        assert self.costs is not None
        return gold_floor(self.costs.next_cost, self.costs.search_cost)


def check_gold_spend(gold: int | None, cost: int, floor: int | None) -> str | None:
    """Motivo por el que gastar `cost` de oro no está permitido, o None si lo está."""
    if floor is None:
        return "reserva de oro desconocida: no se gasta oro"
    if gold is None:
        return "oro actual desconocido: no se gasta oro"
    if gold - cost < floor:
        return f"dejaría {gold - cost} de oro, por debajo de la reserva ({floor})"
    return None


__all__ = ["MAX_COST_AGE_S", "GoldCosts", "GoldReserve", "check_gold_spend"]
