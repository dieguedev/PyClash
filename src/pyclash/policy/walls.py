"""Política de muros por lotes (issue #13): cuándo se mejoran, con qué recurso y de cuántos muros.

Función pura, como `decide_upgrade`: sin visión, dispositivo, reloj ni log. Reglas:

  R1: se decide con lo que hay en el ALMACÉN. Oro gastable = oro - colchón (`gold_floor`, el mínimo que
      protege las búsquedas de batalla); elixir gastable = todo. El umbral es un número libre (por
      defecto 0, medido sobre el gastable): con 0 basta con poder pagar un muro por encima del colchón.
  Constructores: con la reserva para muros activa, los constructores libres por encima del último se
      ocupan primero en mejoras; los muros usan el último. Con ella desactivada, todo el excedente va a
      mejoras y los muros solo corren cuando ya no queda nada que mejorar.
  Ritmo: el bot ataca entre `interval_min_s` y `interval_max_s` (al azar) y después abre la ventana de
      muros, para no cortar el ritmo de ataque por uno o dos muros. Si un almacén se llena antes, la
      ventana se abre sin esperar (`window_due`).
  R3: nunca se calcula un lote que se pase del gastable; el rojo se comprueba aparte, en la pantalla.

Un lote necesita un constructor libre pero NO lo ocupa: por eso la puerta es «hay al menos uno libre»
y la verificación se hace en el panel, no con el contador de libres.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

Resource = str  # "gold" | "elixir"
Triplet = tuple[int | None, int | None, int | None]  # (oro, elixir, elixir oscuro)

BIG_STEP = 10  # el botón «Add Wall +10»
ELIXIR_MIN_LEVEL = 5  # los muros se mejoran con elixir solo desde el nivel 5; el 2, 3 y 4 son solo de oro


def gold_only_level(level: int | None) -> bool:
    """True si el nivel al que suben los muros solo admite oro. Con nivel desconocido (None), False: no se
    sabe, y quien decide se apoya en que el juego ofrezca o no el botón de elixir."""
    return level is not None and level < ELIXIR_MIN_LEVEL
STORAGE_FULL_FRACTION = 0.95  # barra del HUD rellena a este nivel o más = almacén lleno (no se distingue del final)


class Preference(Enum):
    ELIXIR_FIRST = "elixir_first"  # atacar cuesta oro: por defecto se gasta primero el elixir
    GOLD_FIRST = "gold_first"
    ELIXIR_ONLY = "elixir_only"
    GOLD_ONLY = "gold_only"

    @property
    def order(self) -> tuple[Resource, ...]:
        return {
            Preference.ELIXIR_FIRST: ("elixir", "gold"),
            Preference.GOLD_FIRST: ("gold", "elixir"),
            Preference.ELIXIR_ONLY: ("elixir",),
            Preference.GOLD_ONLY: ("gold",),
        }[self]


@dataclass(frozen=True)
class WallSettings:
    threshold: int = 0  # gastable mínimo (número libre, sin unidad) para abrir la tarea; 0 = cualquiera por encima del colchón
    preference: Preference = Preference.ELIXIR_FIRST
    # Recursos con los que la tarea sabe pagar. Primera entrega: solo oro (la pantalla de pago con elixir
    # aún no está reconocida); la política respeta la preferencia dentro de lo habilitado.
    enabled: frozenset[Resource] = frozenset({"gold", "elixir"})
    reserve_builder_for_walls: bool = True  # activa por defecto (Maxer): el lote necesita un constructor libre, no lo ocupa
    interval_min_s: float = 600.0  # ataque entre ventanas de muros: 10-15 min al azar
    interval_max_s: float = 900.0

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise ValueError("el umbral de muros no puede ser negativo")
        if not 0 < self.interval_min_s <= self.interval_max_s:
            raise ValueError("el intervalo entre ventanas de muros no es válido")


@dataclass(frozen=True)
class WallState:
    resources: Triplet = (None, None, None)
    free_builders: int | None = None  # None: desconocido, no se hace nada
    gold_floor: int | None = None  # None: colchón desconocido, no se gasta oro
    # ¿Quedan mejoras de aldea por hacer? False: ya no hay nada (los muros pueden usar un constructor
    # libre). None: se ignora, así que las mejoras van primero.
    upgrades_pending: bool | None = None


@dataclass(frozen=True)
class WallDecision:
    resource: Resource | None  # con qué se paga el lote; None: no se hace nada
    spendable: int
    reason: str
    skipped: tuple[str, ...] = field(default=(), compare=False)  # motivo por recurso descartado


def effective_reserve(reserve_builders: int, reserve_for_walls: bool) -> int:
    """Constructores que mejoras y edificios nuevos deben dejar libres.

    Con la reserva para muros activa, mejoras y `build_new` dejan siempre al menos uno.
    """
    return max(reserve_builders, 1) if reserve_for_walls else reserve_builders


def wall_gate(
    free_builders: int | None, reserve_for_walls: bool = False, upgrades_pending: bool | None = None
) -> str | None:
    """Motivo por el que no se empieza ningún lote (E4R1), o None. Se comprueba antes de cada lote.

    Hace falta un constructor libre. Las mejoras de aldea tienen prioridad:
      - con la reserva activa, los muros solo usan el último constructor libre (con más de uno, esperan
        a que las mejoras los ocupen) salvo que ya no quede nada por mejorar;
      - con la reserva desactivada, solo corren cuando ya no queda nada por mejorar.
    """
    if free_builders is None:
        return "constructores libres desconocidos"
    if free_builders < 1:
        return "ningún constructor libre para los muros"
    if upgrades_pending is False:
        return None  # nada que mejorar: el constructor libre es para los muros
    if not reserve_for_walls:
        return "las mejoras de aldea van primero (reserva para muros desactivada)"
    if free_builders > 1:
        return f"{free_builders} constructores libres: las mejoras van primero, los muros usan el último"
    return None


def bar_is_full(fill: float | None, threshold: float = STORAGE_FULL_FRACTION) -> bool | None:
    """Almacén lleno según cuánto de la barra del HUD está rellena (0-1). None: no se pudo medir.

    Cerca del final no se distingue «hasta el borde» de «casi»: desde `threshold` (95 %) se da por lleno.
    """
    if fill is None:
        return None
    return fill >= threshold


def window_due(elapsed_s: float, delay_s: float, gold_full: bool | None, elixir_full: bool | None) -> str | None:
    """Motivo por el que toca abrir la ventana de muros ya, o None si el bot sigue atacando.

    Toca cuando pasó el intervalo al azar O cuando un almacén está lleno (el oro o el elixir que se
    desbordaría es justo el excedente que los muros aprovechan). `None` en un almacén = no se sabe: no cuenta.
    Que toque no basta para mejorar: después deciden `decide_walls` y la puerta de constructores.
    """
    if gold_full is True or elixir_full is True:
        which = "oro" if gold_full is True else "elixir"
        return f"almacén de {which} lleno"
    if elapsed_s >= delay_s:
        return f"pasaron {elapsed_s:.0f}s de ataque (intervalo {delay_s:.0f}s)"
    return None


def next_window_delay(settings: WallSettings, rng: random.Random) -> float:
    """Segundos de ataque hasta la próxima ventana de muros: al azar entre el mínimo y el máximo."""
    return rng.uniform(settings.interval_min_s, settings.interval_max_s)


def spendable(state: WallState, resource: Resource) -> int | None:
    """Lo que los muros pueden gastar de un recurso; None si no se puede saber."""
    gold, elixir, _ = state.resources
    if resource == "elixir":
        return elixir
    if resource == "gold":
        if gold is None or state.gold_floor is None:
            return None
        return max(0, gold - state.gold_floor)
    return None


def decide_walls(state: WallState, settings: WallSettings) -> WallDecision:
    blocked = wall_gate(state.free_builders, settings.reserve_builder_for_walls, state.upgrades_pending)
    if blocked:
        return WallDecision(None, 0, blocked)

    skipped: list[str] = []
    for resource in settings.preference.order:
        if resource not in settings.enabled:
            skipped.append(f"{resource}: la tarea aún no sabe pagar con este recurso")
            continue
        have = spendable(state, resource)
        if have is None:
            skipped.append(f"{resource}: " + ("colchón de oro desconocido" if resource == "gold" else "sin lectura"))
        elif have < settings.threshold or have <= 0:
            skipped.append(f"{resource}: gastable {have} por debajo del umbral {settings.threshold}")
        else:
            return WallDecision(resource, have, f"{resource}: gastable {have} supera el umbral {settings.threshold}",
                                tuple(skipped))
    return WallDecision(None, 0, "; ".join(skipped) or "sin recursos habilitados", tuple(skipped))


# ------------------------------------------------------------------------------ el lote


def batch_size(spendable_amount: int, unit_cost: int | None, available: int | None = None) -> int:
    """Muros que caben en lo gastable: `floor(gastable / coste_unitario)`, con tope en `available`.

    Sin coste unitario legible no se calcula nada (0): no se adivina.
    """
    if unit_cost is None or unit_cost <= 0 or spendable_amount <= 0:
        return 0
    n = spendable_amount // unit_cost
    return n if available is None else min(n, max(0, available))


def plan_clicks(target: int) -> tuple[int, int]:
    """(pulsaciones de «+10», pulsaciones de «+1») para un lote de `target` muros."""
    if target < 0:
        raise ValueError("el lote no puede ser negativo")
    return target // BIG_STEP, target % BIG_STEP


def next_add(current: int, target: int) -> int:
    """Cuánto sumar en la siguiente pulsación: 10, 1 o 0 (lote completo). Con «+10» apagado, quien llama usa 1."""
    missing = target - current
    if missing <= 0:
        return 0
    return BIG_STEP if missing >= BIG_STEP else 1


def verified_in_panel(
    before_quantity: int,
    before_cost: int | None,
    batch: int,
    after_quantity: int | None,
    after_cost: int | None,
    row_present: bool,
    panel_complete: bool,
) -> bool:
    """El panel confirma el lote (R4).

    - Misma fila con `quantity == N - k` y el mismo coste: verificado.
    - La fila ya no aparece: solo si `k == N` (se mejoró todo el nivel) y el escáner recorrió el panel
      entero sin filas cortadas.
    - Todo lo demás (fila igual, ilegible, ausente con `k < N`): no verificado y sin reintento.
    """
    if batch <= 0:
        return False
    if row_present:
        if after_quantity is None or after_cost is None or before_cost is None:
            return False
        return after_quantity == before_quantity - batch and after_cost == before_cost
    return panel_complete and batch == before_quantity


__all__ = [
    "BIG_STEP",
    "Preference",
    "STORAGE_FULL_FRACTION",
    "WallDecision",
    "WallSettings",
    "WallState",
    "bar_is_full",
    "batch_size",
    "decide_walls",
    "effective_reserve",
    "next_window_delay",
    "next_add",
    "plan_clicks",
    "spendable",
    "verified_in_panel",
    "wall_gate",
    "window_due",
]
