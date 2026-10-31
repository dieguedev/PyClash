"""Política de mejoras: qué fila del panel iniciar ahora, y por qué se descartó el resto.

Función pura: sin visión, dispositivo, reloj ni log. Todo lo que cambia (filas, recursos,
constructores, cooldowns, azar) entra por argumentos, así que una tabla de casos la prueba entera.

Maxer, Rusher y Dry run son un único motor con reglas distintas, no tres bots. Las prioridades son
niveles de nombres exactos del juego: gana el primer nivel con alguna fila elegible y dentro del
nivel decide el azar inyectado. Sin coincidencias se aplica la alternativa configurada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from ..screens.upgrade_panel import PanelRow
from ..vision.vocabulary import Vocabulary, normalize

TOWN_HALL = "Town Hall"
WALL = "Wall"

# Etiquetas de exclusión que el usuario puede activar (interruptores de tareas).
EXCLUDE_HEROES = "heroes"


class Mode(Enum):
    MAXER = "maxer"  # nunca sube el Ayuntamiento
    RUSHER = "rusher"  # sube el Ayuntamiento en cuanto puede
    DRY_RUN = "dry_run"  # decide con las reglas de Maxer, pero quien llama no pulsa nada


class Order(Enum):
    PRICIEST = "priciest"  # un constructor cuenta lo mismo: que cada uno absorba el trabajo más caro
    CHEAPEST = "cheapest"


class Fallback(Enum):
    ANY = "any"  # una mejora cualquiera si ninguna prioridad coincide
    NONE = "none"  # no hacer nada


class Reason(Enum):
    UNNAMED = "nombre no reconocido"
    NEW_BUILDING = "edificio nuevo (tarea aparte)"
    GEMS = "cuesta gemas"
    NOT_AFFORDABLE = "coste en rojo"
    UNKNOWN_RESOURCE = "recurso o coste indeterminado"
    INSUFFICIENT = "recursos insuficientes"
    COOLDOWN = "en cooldown"
    EXCLUDED = "excluida"
    HEROES_EXCLUDED = "héroes excluidos"
    MAXER_TOWN_HALL = "Maxer no sube el Ayuntamiento"
    WALL_BATCH = "los muros van por lotes"


@dataclass(frozen=True)
class UpgradeSettings:
    mode: Mode = Mode.MAXER
    order: Order = Order.PRICIEST
    reserve_builders: int = 1
    cap_per_cycle: int | None = None  # None: sin tope
    priorities: tuple[tuple[str, ...], ...] = ()  # niveles de nombres canónicos
    fallback: Fallback = Fallback.ANY
    excluded: frozenset[str] = frozenset()  # etiquetas desactivadas (p. ej. EXCLUDE_HEROES)


@dataclass(frozen=True)
class UpgradeState:
    rows: Sequence[PanelRow]
    # (oro, elixir, elixir oscuro); None en un recurso: sin lectura, solo vale el rojo de la fila.
    resources: tuple[int | None, int | None, int | None] = (None, None, None)
    free_builders: int | None = None  # None: desconocido, no se hace nada
    started_this_cycle: int = 0
    on_cooldown: frozenset[str] = frozenset()  # nombres canónicos con cooldown activo


@dataclass(frozen=True)
class Rejection:
    row: PanelRow
    reason: Reason


@dataclass(frozen=True)
class Decision:
    chosen: PanelRow | None
    reason: str  # resumen legible de por qué se eligió o por qué no
    rejected: tuple[Rejection, ...] = ()
    tier: int | None = None  # nivel de prioridad (0 = primero); None si fue Ayuntamiento o alternativa
    dry_run: bool = False  # quien llama describe la decisión en vez de ejecutarla

    def why_not(self, name: str) -> list[Reason]:
        return [r.reason for r in self.rejected if r.row.name == name]


def _amount(resources: tuple[int | None, int | None, int | None], resource: str | None) -> int | None:
    index = {"gold": 0, "elixir": 1, "dark_elixir": 2}.get(resource or "")
    return None if index is None else resources[index]


def _screen(row: PanelRow, settings: UpgradeSettings, state: UpgradeState) -> Reason | None:
    """Motivo por el que una fila no es elegible, o None. El orden importa: el primero que salta se informa."""
    if row.name is None:
        return Reason.UNNAMED
    if row.kind == "new_building":
        return Reason.NEW_BUILDING
    if row.kind == "hero" and EXCLUDE_HEROES in settings.excluded:
        return Reason.HEROES_EXCLUDED
    if row.kind == "wall" or row.name == WALL:
        if "walls" in settings.excluded:
            return Reason.EXCLUDED
    if row.name in state.on_cooldown:
        return Reason.COOLDOWN
    if row.resource == "gems":
        return Reason.GEMS
    if row.affordable is False:
        return Reason.NOT_AFFORDABLE
    if not row.free:
        if row.cost is None or row.resource is None:
            return Reason.UNKNOWN_RESOURCE
        have = _amount(state.resources, row.resource)
        if have is not None and row.cost > have:
            return Reason.INSUFFICIENT
    if row.name == TOWN_HALL and settings.mode is not Mode.RUSHER:
        return Reason.MAXER_TOWN_HALL
    return None


def _ranked(rows: Sequence[PanelRow], order: Order) -> list[PanelRow]:
    """Elixir oscuro primero (lo escaso y donde gastan los héroes), luego por coste según `order`."""
    sign = 1 if order is Order.CHEAPEST else -1
    return sorted(rows, key=lambda r: (r.resource != "dark_elixir", sign * (r.cost or 0)))


def builder_gate(state: UpgradeState, settings: UpgradeSettings) -> str | None:
    """Motivo por el que no se puede empezar nada aunque haya filas (constructores, reserva, tope), o None."""
    if state.free_builders is None:
        return "constructores libres desconocidos"
    if state.free_builders <= settings.reserve_builders:
        return f"{state.free_builders} constructores libres, reserva {settings.reserve_builders}"
    if settings.cap_per_cycle is not None and state.started_this_cycle >= settings.cap_per_cycle:
        return f"tope por ciclo alcanzado ({settings.cap_per_cycle})"
    return None


def decide_upgrade(state: UpgradeState, settings: UpgradeSettings, rng: random.Random) -> Decision:
    dry = settings.mode is Mode.DRY_RUN

    def none(reason: str, rejected: Iterable[Rejection] = ()) -> Decision:
        return Decision(None, reason, tuple(rejected), dry_run=dry)

    blocked = builder_gate(state, settings)
    if blocked:
        return none(blocked)

    rejected: list[Rejection] = []
    eligible: list[PanelRow] = []
    for row in state.rows:
        reason = _screen(row, settings, state)
        if reason is None:
            eligible.append(row)
        else:
            rejected.append(Rejection(row, reason))
    if not eligible:
        return none("ninguna fila elegible", rejected)

    def pick(row: PanelRow, why: str, tier: int | None = None) -> Decision:
        return Decision(row, why, tuple(rejected), tier, dry)

    if settings.mode is Mode.RUSHER:
        halls = [r for r in eligible if r.name == TOWN_HALL]
        if halls:
            return pick(min(halls, key=lambda r: r.cost or 0), "Rusher: Ayuntamiento asequible")

    # Los muros solo se eligen si el usuario los puso en una prioridad: «cualquiera» los deja a los lotes.
    for tier, names in enumerate(settings.priorities):
        wanted = {normalize(n) for n in names}
        level = [r for r in eligible if normalize(r.name or "") in wanted]
        if level:
            level.sort(key=lambda r: (r.y, r.name or ""))  # base determinista para que el azar semillado sea reproducible
            row = rng.choice(level)
            return pick(row, f"prioridad {tier + 1}: {row.name}", tier)

    if settings.fallback is Fallback.NONE:
        return none("ninguna prioridad coincide y la alternativa es no hacer nada", rejected)
    anything = [r for r in eligible if r.kind != "wall" and r.name != WALL]
    for r in eligible:
        if r not in anything:
            rejected.append(Rejection(r, Reason.WALL_BATCH))
    if not anything:
        return none("solo quedan muros", rejected)
    row = _ranked(anything, settings.order)[0]
    return pick(row, f"alternativa ({settings.order.value}): {row.name}")


# ---------------------------------------------------------------- validación de las listas

@dataclass(frozen=True)
class PriorityProblem:
    tier: int
    name: str
    suggestions: tuple[str, ...]


def validate_priorities(
    priorities: Sequence[Sequence[str]], vocabulary: Vocabulary, categories: Iterable[str] | None = None
) -> list[PriorityProblem]:
    """Nombres de las listas que no existen en el vocabulario, con sugerencias; vacío si todo es válido."""
    problems: list[PriorityProblem] = []
    for tier, names in enumerate(priorities):
        for result in vocabulary.validate(names, categories):
            if not result.valid:
                problems.append(PriorityProblem(tier, result.name, result.suggestions))
    return problems


# ---------------------------------------------------------------- idle

class Idle(Enum):
    WORK = "work"
    WAIT = "wait"
    FINISH = "finish"


@dataclass(frozen=True)
class IdleDecision:
    action: Idle
    reason: str
    next_check_s: float | None = None  # segundos hasta el siguiente chequeo; None si no aplica


@dataclass(frozen=True)
class IdleConfig:
    check_interval_s: float = 300.0  # cuando no se sabe cuándo acaba la siguiente obra
    min_wait_s: float = 30.0
    max_wait_s: float = 1800.0


def decide_idle(
    free_builders: int | None,
    reserve: int,
    storages_full: bool,
    next_builder_free_s: float | None = None,
    nothing_left: bool = False,
    config: IdleConfig = IdleConfig(),
) -> IdleDecision:
    """Trabajar, esperar (con motivo y próximo chequeo) o terminar.

    Con constructores por encima de la reserva se trabaja. Si no los hay se espera: hasta que acabe
    la obra más próxima si se sabe, o el intervalo por defecto; nunca se ataca solo por aburrimiento
    (un almacén lleno no justifica un ataque que no puede gastarse). `nothing_left`: «hasta maxear».
    """
    if nothing_left:
        return IdleDecision(Idle.FINISH, "no queda nada por mejorar")
    if free_builders is not None and free_builders > reserve:
        return IdleDecision(Idle.WORK, f"{free_builders} constructores libres (reserva {reserve})")
    wait = config.check_interval_s if next_builder_free_s is None else next_builder_free_s
    wait = min(config.max_wait_s, max(config.min_wait_s, wait))
    state = "desconocidos" if free_builders is None else f"{free_builders} libres (reserva {reserve})"
    why = f"constructores {state}"
    if storages_full:
        why = f"almacenes llenos y {why}"
    return IdleDecision(Idle.WAIT, why, wait)
