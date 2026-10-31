"""Política de botín (issue #12): mínimos por recurso, presupuesto de saltos y estadísticas de sesión.

Todo son funciones y datos puros (sin reloj, sin dispositivo, sin log): quien llama decide qué
hace con el veredicto. El botín de cada ataque se lee de «You got:» en la pantalla de resultado;
aquí solo se comprueba que sea verosímil y se acumula.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

Triplet = tuple[int, int, int]  # (oro, elixir, elixir oscuro)

MAX_SKIPS = 75  # tope de «Next» por búsqueda
RELAXED_ATTACKS = 3  # ataques seguidos sin exigir mínimo cuando el oro no da


@dataclass(frozen=True)
class LootLimits:
    """Lo máximo que un único ataque puede dar. Configurable: no depende del nivel de Ayuntamiento."""

    max_gain_main: int = 3_000_000  # oro y elixir
    max_gain_dark: int = 50_000

    def plausible(self, gold: int, elixir: int, dark: int) -> bool:
        return (
            0 <= gold <= self.max_gain_main
            and 0 <= elixir <= self.max_gain_main
            and 0 <= dark <= self.max_gain_dark
        )


@dataclass(frozen=True)
class EnemyLoot:
    """Botín disponible de la aldea que se ve en la exploración.

    Un campo `None` es «no se pudo leer», que NO es lo mismo que cero. Para el oscuro,
    `dark_row` distingue «la aldea no tiene fila de oscuro» (False: vale 0) de «la fila está y no
    se leyó» (True con `dark` None) y de «no se sabe si la fila está» (None).
    """

    gold: int | None
    elixir: int | None
    dark: int | None = None
    dark_row: bool | None = False


@dataclass(frozen=True)
class LootMinimum:
    """Mínimo por recurso; 0 = ignorado. Con los tres a 0 no se pulsa «Next»."""

    gold: int = 0
    elixir: int = 0
    dark: int = 0

    def __post_init__(self) -> None:
        if min(self.gold, self.elixir, self.dark) < 0:
            raise ValueError("los mínimos de botín no pueden ser negativos")

    @property
    def any(self) -> bool:
        return bool(self.gold or self.elixir or self.dark)

    def describe(self) -> str:
        return f"{self.gold}/{self.elixir}/{self.dark}"


class Verdict(str, Enum):
    ACCEPT = "accept"  # cumple todos los mínimos
    SKIP = "skip"  # alguno falla con certeza: «Next»
    REREAD = "reread"  # algo no se leyó con seguridad: releer; si sigue igual, atacar


def decide(loot: EnemyLoot | None, minimum: LootMinimum) -> Verdict:
    """Veredicto sobre una aldea. Hay que cumplir TODOS los mínimos a la vez.

    Un fallo seguro gana a una duda: si el oro ya no llega, da igual que el elixir no se leyera.
    Un recurso sin mínimo no se mira (y por tanto tampoco pide relectura).
    """
    if not minimum.any:
        return Verdict.ACCEPT
    fails = False
    unsure = False

    for have, need in ((loot.gold if loot else None, minimum.gold), (loot.elixir if loot else None, minimum.elixir)):
        if not need:
            continue
        if have is None:
            unsure = True
        elif have < need:
            fails = True

    if minimum.dark:
        if loot is None or loot.dark_row is None:
            unsure = True
        elif loot.dark_row is False:
            fails = True  # la aldea no tiene oscuro: vale 0
        elif loot.dark is None:
            unsure = True  # la fila está y no se leyó
        elif loot.dark < minimum.dark:
            fails = True

    if fails:
        return Verdict.SKIP
    return Verdict.REREAD if unsure else Verdict.ACCEPT


# ------------------------------------------------------------------------------ oro y saltos


def skip_budget(gold: int | None, next_cost: int | None, cap: int = MAX_SKIPS) -> int:
    """Cuántos «Next» se pueden pulsar sin quedarse sin oro para empezar otra búsqueda.

    `min(cap, oro // coste - 1)`: siempre queda una búsqueda de reserva. Sin oro o sin coste
    legibles no se salta nada (no se adivina).
    """
    if gold is None or next_cost is None or next_cost <= 0:
        return 0
    return max(0, min(cap, gold // next_cost - 1))


def gold_floor(next_cost: int, search_cost: int, skips: int = MAX_SKIPS) -> int:
    """Oro por debajo del cual una mejora impediría hacer `skips` saltos y una búsqueda (issue #32).

    Los dos costes valen hoy lo mismo; se mantienen separados en la fórmula.
    """
    return skips * next_cost + search_cost


@dataclass
class SkipPlan:
    """Plan de una búsqueda: cuántos saltos hay y si el oro no da para el tope.

    `relaxed` = el presupuesto es menor que el tope: al agotarlo se ataca la aldea que salga,
    cumpla o no los mínimos (y se evita que la búsqueda siguiente quede sin oro). `streak` cuenta
    las búsquedas seguidas así, para avisar de que el oro está bajo el umbral.
    """

    budget: int
    relaxed: bool
    streak: int = 0


@dataclass
class RelaxedTracker:
    streak: int = 0

    def plan(self, gold: int | None, next_cost: int | None, cap: int = MAX_SKIPS) -> SkipPlan:
        budget = skip_budget(gold, next_cost, cap)
        relaxed = budget < cap
        self.streak = self.streak + 1 if relaxed else 0
        return SkipPlan(budget, relaxed, self.streak)


# ------------------------------------------------------------------------------ sesión


@dataclass
class SessionStats:
    """Totales de la sesión. Solo suma ganancias verosímiles; la tasa usa el tiempo real."""

    limits: LootLimits = field(default_factory=LootLimits)
    started_at: float = 0.0
    total: Triplet = (0, 0, 0)
    attacks_counted: int = 0
    attacks_unread: int = 0

    def record(self, gain: Triplet) -> bool:
        """Suma un ataque. False (sin tocar nada) si la ganancia no es verosímil."""
        if not self.limits.plausible(*gain):
            return False
        self.total = (self.total[0] + gain[0], self.total[1] + gain[1], self.total[2] + gain[2])
        self.attacks_counted += 1
        return True

    def record_unread(self) -> None:
        self.attacks_unread += 1

    def rate_per_hour(self, now: float) -> Triplet:
        hours = (now - self.started_at) / 3600.0
        if hours <= 0:
            return (0, 0, 0)
        return (round(self.total[0] / hours), round(self.total[1] / hours), round(self.total[2] / hours))

    def to_record(self, now: float, wall_start: str, wall_end: str) -> dict:
        """Línea del archivo de sesiones: solo cifras, sin nombres de jugadores ni de ventana."""
        rate = self.rate_per_hour(now)
        return {
            "inicio": wall_start,
            "fin": wall_end,
            "duracion_s": round(max(0.0, now - self.started_at), 1),
            "ataques_contados": self.attacks_counted,
            "ataques_sin_lectura": self.attacks_unread,
            "oro": self.total[0],
            "elixir": self.total[1],
            "oscuro": self.total[2],
            "oro_por_hora": rate[0],
            "elixir_por_hora": rate[1],
            "oscuro_por_hora": rate[2],
        }


__all__ = [
    "EnemyLoot",
    "LootLimits",
    "LootMinimum",
    "MAX_SKIPS",
    "RELAXED_ATTACKS",
    "RelaxedTracker",
    "SessionStats",
    "SkipPlan",
    "Triplet",
    "Verdict",
    "decide",
    "gold_floor",
    "skip_budget",
]
