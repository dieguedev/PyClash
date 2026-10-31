"""Registro de enfriamientos, basado en el reloj inyectado."""

from __future__ import annotations

from .clock import Clock


class CooldownRegistry:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}

    def start(self, key: str, seconds: float) -> None:
        self._until[key] = self._clock.now() + seconds

    def remaining(self, key: str) -> float:
        return max(0.0, self._until.get(key, 0.0) - self._clock.now())

    def ready(self, key: str) -> bool:
        return self.remaining(key) == 0.0
