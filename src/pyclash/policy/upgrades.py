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
MAX_TOWN_HALL = 9  # el MVP termina en TH9: el bot nunca sube a TH10

# Etiquetas de exclusión que el usuario puede activar (interruptores de tareas).
EXCLUDE_HEROES = "heroes"


class Mode(Enum):
    MAXER = "maxer"  # maxea todo y solo entonces ofrece el Ayuntamiento (nunca TH9 -> TH10)
    RUSHER = "rusher"  # solo su lista blanca hasta TH9 y sube el Ayuntamiento en cuanto puede (nunca TH9 -> TH10)
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
    GOLD_RESERVE = "dejaría el oro por debajo de la reserva"
    GOLD_UNKNOWN = "reserva de oro desconocida: no se gasta oro"
    COOLDOWN = "en cooldown"
    EXCLUDED = "excluida"
    HEROES_EXCLUDED = "héroes excluidos"
    TOWN_HALL_10 = "el Ayuntamiento no se sube: TH9, o número de la fila o tarjeta del TH10 sin confirmar"
    OTHER_UPGRADES_FIRST = "Maxer: quedan otras mejoras antes del Ayuntamiento"
    NOT_ON_RUSHER_LIST = "fuera de la lista del Rusher"
    WALL_BATCH = "los muros van por lotes"


# Lo que el Rusher mejora antes de TH9 (nombres canónicos de data/vocabulary.json). El Ayuntamiento va aparte: lo sube
# en cuanto puede. Defensas, minas, recolectores, muros, trampas y héroes esperan a TH9.
RUSHER_WHITELIST: frozenset[str] = frozenset(
    normalize(n)
    for n in (
        "Gold Storage", "Elixir Storage", "Dark Elixir Storage",
        "Army Camp", "Laboratory", "Clan Castle", "Hero Hall",
        "Barracks", "Dark Barracks", "Spell Factory", "Dark Spell Factory",
    )
)


@dataclass(frozen=True)
class UpgradeSettings:
    mode: Mode = Mode.MAXER
    order: Order = Order.PRICIEST
    reserve_builders: int = 1
    cap_per_cycle: int | None = None  # None: sin tope
    priorities: tuple[tuple[str, ...], ...] = ()  # niveles de nombres canónicos
    fallback: Fallback = Fallback.ANY
    excluded: frozenset[str] = frozenset()  # etiquetas desactivadas (p. ej. EXCLUDE_HEROES)
    rusher_troops: frozenset[str] = frozenset()  # tropas de la estrategia activa que el Rusher también mejora
    dry_run: bool = False  # simulacro con las reglas de `mode` (Mode.DRY_RUN es el simulacro con reglas de Maxer)

    @property
    def is_dry(self) -> bool:
        return self.dry_run or self.mode is Mode.DRY_RUN


@dataclass(frozen=True)
class UpgradeState:
    rows: Sequence[PanelRow]
    # (oro, elixir, elixir oscuro); None en un recurso: sin lectura, solo vale el rojo de la fila.
    resources: tuple[int | None, int | None, int | None] = (None, None, None)
    free_builders: int | None = None  # None: desconocido, no se hace nada
    started_this_cycle: int = 0
    on_cooldown: frozenset[str] = frozenset()  # nombres canónicos con cooldown activo
    # True: el filtro del juego se aplicó y se comprobó que el panel quedó ordenado por coste, así que el orden
    # de `rows` ES el orden de la alternativa y no se vuelve a ordenar.
    game_ordered: bool = False
    # Nivel del Ayuntamiento leído del perfil del HUD con la aldea a la vista. None: no se pudo confirmar (duda).
    th_level: int | None = None
    # ¿El panel ofrece la tarjeta del TH10? None: no se pudo saber. El Ayuntamiento solo se elige si es False.
    offers_th10: bool | None = None
    # True: el panel se recorrió entero, así que `rows` es todo lo que queda por mejorar.
    scan_complete: bool = False
    # Reserva de oro (#32): el oro que debe quedar tras gastar. None: se desconoce y no se gasta oro. 0: sin reserva.
    gold_floor: int | None = 0


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
    finished: bool = False  # TH9 maxeado: solo queda la tarjeta del TH10, que nunca se pulsa

    def why_not(self, name: str) -> list[Reason]:
        return [r.reason for r in self.rejected if r.row.name == name]


# Filas que ahora no se pueden empezar pero siguen por mejorar: cuentan como «quedan otras mejoras».
_STILL_PENDING = frozenset(
    {
        Reason.NOT_AFFORDABLE, Reason.INSUFFICIENT, Reason.UNKNOWN_RESOURCE, Reason.COOLDOWN, Reason.GEMS,
        Reason.GOLD_RESERVE, Reason.GOLD_UNKNOWN,
    }
)


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
    if settings.mode is Mode.RUSHER and state.th_level != MAX_TOWN_HALL and not _rusher_allows(row, settings):
        return Reason.NOT_ON_RUSHER_LIST
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
        if row.resource == "gold":  # solo el oro tiene reserva: elixir y elixir oscuro no se tocan
            if state.gold_floor is None:
                return Reason.GOLD_UNKNOWN
            if state.gold_floor > 0:
                if have is None:
                    return Reason.GOLD_UNKNOWN
                if have - row.cost < state.gold_floor:
                    return Reason.GOLD_RESERVE
    # Nunca TH9 -> TH10: el Ayuntamiento solo se elige si la fila está localizada y su número (el nivel al que se sube)
    # se reconoció y es 2-9, el HUD no dice TH9 y el panel descarta con seguridad la tarjeta del TH10. Cualquier duda
    # (fila sin número, nivel sin leer, lectura None) es «no se sube».
    if row.name == TOWN_HALL and not (
        row.level is not None
        and 2 <= row.level <= MAX_TOWN_HALL
        and state.th_level != MAX_TOWN_HALL
        and state.offers_th10 is False
    ):
        return Reason.TOWN_HALL_10
    return None


def _rusher_allows(row: PanelRow, settings: UpgradeSettings) -> bool:
    name = normalize(row.name or "")
    return name == normalize(TOWN_HALL) or name in RUSHER_WHITELIST or name in {normalize(t) for t in settings.rusher_troops}


def _is_finished(state: UpgradeState) -> bool:
    """TH9 maxeado: TH9 confirmado, el panel entero visto y lo único que queda es la tarjeta del TH10 (sin muros ni nada más)."""
    return (
        state.th_level == MAX_TOWN_HALL
        and state.offers_th10 is True
        and state.scan_complete
        and bool(state.rows)
        and all(r.name == TOWN_HALL for r in state.rows)
    )


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
    dry = settings.is_dry

    def none(reason: str, rejected: Iterable[Rejection] = ()) -> Decision:
        return Decision(None, reason, tuple(rejected), dry_run=dry)

    if _is_finished(state):
        return Decision(None, "TH9 maxeado: solo queda el Ayuntamiento 10 y no se sube", dry_run=dry, finished=True)

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
    if settings.mode is not Mode.RUSHER and any(r.name == TOWN_HALL for r in eligible):
        # Maxer (y el simulacro): el Ayuntamiento solo cuando no queda otra mejora, pendiente aunque ahora no se pueda pagar.
        pending = [r for r in eligible if r.name != TOWN_HALL and r.kind not in ("wall", "new_building")]
        pending += [
            j.row for j in rejected
            if j.reason in _STILL_PENDING and j.row.name != TOWN_HALL and j.row.kind not in ("wall", "new_building")
        ]
        if pending:
            for r in [r for r in eligible if r.name == TOWN_HALL]:
                eligible.remove(r)
                rejected.append(Rejection(r, Reason.OTHER_UPGRADES_FIRST))
    if not eligible:
        return none("ninguna fila elegible", rejected)

    def pick(row: PanelRow, why: str, tier: int | None = None) -> Decision:
        return Decision(row, why, tuple(rejected), tier, dry)

    if settings.mode is Mode.RUSHER:
        halls = [r for r in eligible if r.name == TOWN_HALL]
        if halls:
            return pick(min(halls, key=lambda r: r.cost or 0), "Rusher: Ayuntamiento asequible")

    if state.game_ordered:
        # Filtro del juego aplicado: manda su orden. La primera fila elegible (el Ayuntamiento ya está descartado
        # en Maxer) y nada más: ni prioridades ni alternativa. Los muros van aparte, en lotes.
        first = next((r for r in eligible if r.kind != "wall" and r.name != WALL), None)
        if first is None:
            return none("solo quedan muros", rejected)
        return pick(first, f"primera fila del filtro del juego ({settings.order.value}): {first.name}")

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
