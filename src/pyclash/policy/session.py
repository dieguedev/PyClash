"""Política de sesión (issue #14): números y reglas puras del orquestador, sin visión ni dispositivo.

Sesión «sin límite de tiempo»: solo la detienen el usuario, un fallo de los que no se arreglan reintentando
o los fallos repetidos de una tarea. Reloj y azar se inyectan; aquí no se lee el tiempo real.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionPolicy:
    # Una tarea que lleva tanto sin avanzar se corta. Cada avance (`ctx.progress()`) reinicia la cuenta,
    # así que una búsqueda larga pero activa no salta. Cubre 3 min de ataque más ~30 s de preparación.
    inactivity_s: float = 240.0
    max_consecutive_failures: int = 3  # fallos seguidos de una misma tarea que detienen la sesión
    failure_cooldown_s: float = 300.0  # tras el primer fallo; se duplica con cada fallo seguido
    failure_cooldown_cap_s: float = 1800.0
    blocked_cooldown_s: float = 1800.0  # algo concreto lo impide: no es un fallo, pero no se insiste
    nothing_cooldown_s: float = 120.0  # no había nada que hacer: no se repite la comprobación sin parar
    exhausted_cooldown_s: float = 1800.0  # «no queda nada para siempre» (p. ej. todos los muros): se revisa de vez en cuando
    idle_min_s: float = 300.0  # revisión en idle: al azar entre ambos valores
    idle_max_s: float = 480.0
    farm_window_min_s: float = 900.0  # ventana de ataques seguidos antes de volver a evaluar desde arriba
    farm_window_max_s: float = 1800.0

    def __post_init__(self) -> None:
        if self.inactivity_s <= 0 or self.max_consecutive_failures < 1:
            raise ValueError("cronómetro o número de fallos no válidos")
        if not 0 < self.idle_min_s <= self.idle_max_s:
            raise ValueError("el intervalo de idle no es válido")
        if not 0 < self.farm_window_min_s <= self.farm_window_max_s:
            raise ValueError("la ventana de farmeo no es válida")


def failure_cooldown(policy: SessionPolicy, consecutive_failures: int) -> float:
    """Descanso tras un fallo: 5 min, 10, 20... hasta el tope."""
    n = max(1, consecutive_failures)
    return min(policy.failure_cooldown_cap_s, policy.failure_cooldown_s * 2 ** (n - 1))


def idle_wait(policy: SessionPolicy, rng: random.Random) -> float:
    """Segundos hasta la siguiente revisión en idle: nunca el mismo valor dos veces seguidas."""
    return rng.uniform(policy.idle_min_s, policy.idle_max_s)


def farm_window(policy: SessionPolicy, rng: random.Random) -> float:
    """Segundos de ataques antes de volver a mirar si hay algo mejor que hacer."""
    return rng.uniform(policy.farm_window_min_s, policy.farm_window_max_s)


__all__ = ["SessionPolicy", "failure_cooldown", "farm_window", "idle_wait"]
