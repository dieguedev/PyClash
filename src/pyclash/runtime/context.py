"""Contexto de ejecución inyectado: sin estado global ni tiempo real."""

from __future__ import annotations

import random
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping

from ..device.base import Device
from ..vision.base import Vision
from .clock import Clock
from .cooldowns import CooldownRegistry
from .events import EventBus
from .settings import SessionSettings
from .stop import PauseToken, StopRequested, StopToken, TaskTimeout


@dataclass(frozen=True)
class AccountConfig:
    name: str = "default"


@dataclass
class Context:
    device: Device
    vision: Vision
    clock: Clock
    rng: random.Random
    vocabulary: Mapping[str, str] = field(default_factory=dict)
    account: AccountConfig = field(default_factory=AccountConfig)
    events: EventBus = field(default_factory=EventBus)
    stop: StopToken = field(default_factory=StopToken)
    pause: PauseToken = field(default_factory=PauseToken)
    settings: SessionSettings = field(default_factory=SessionSettings)
    # Quien lanza las tareas (el orquestador) lo consulta en cada límite de tarea; None: ajustes fijos.
    settings_source: Callable[[], SessionSettings] | None = None
    # Los muros ya no tienen nada que mejorar: la reserva de constructor para ellos deja de aplicar.
    walls_complete: bool = False
    cooldowns: CooldownRegistry = field(init=False)
    _inactivity_limit: float | None = field(default=None, init=False, repr=False)
    _progress_at: float = field(default=0.0, init=False, repr=False)
    _stop_ignored: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.cooldowns = CooldownRegistry(self.clock)

    def refresh_settings(self) -> SessionSettings:
        """Toma los ajustes en vivo actuales (solo en un límite de tarea, nunca a mitad de una)."""
        if self.settings_source is not None:
            self.settings = self.settings_source()
        return self.settings

    # ------------------------------------------------------------------ cronómetro de inactividad

    def arm_watchdog(self, seconds: float | None) -> None:
        """Desde ahora, `check_stop` lanza TaskTimeout si pasan `seconds` sin llamar a `progress()`."""
        self._inactivity_limit = seconds
        self._progress_at = self.clock.now()

    def disarm_watchdog(self) -> None:
        self._inactivity_limit = None

    def progress(self) -> None:
        """Marca un avance real de la tarea (un «Next» que cambia la aldea, un paso confirmado...)."""
        self._progress_at = self.clock.now()

    @contextmanager
    def ignoring_stop(self) -> Iterator[None]:
        """Dentro del bloque la parada no corta nada: para dejar el juego en estado seguro tras parar."""
        self._stop_ignored += 1
        try:
            yield
        finally:
            self._stop_ignored -= 1

    def check_stop(self) -> None:
        """Si se pidió parar, suelta cualquier entrada sostenida y lanza StopRequested.

        Si hay un cronómetro de inactividad armado y venció, hace lo mismo con TaskTimeout."""
        if self.stop.requested and not self._stop_ignored:
            if self.device.held:
                self.device.release()
            raise StopRequested()
        limit = self._inactivity_limit
        if limit is not None and self.clock.now() - self._progress_at > limit:
            if self.device.held:
                self.device.release()
            raise TaskTimeout(limit)

    def sleep(self, seconds: float, step: float = 0.5) -> None:
        """Espera comprobando la parada en cada paso."""
        end = self.clock.now() + seconds
        while True:
            self.check_stop()
            remaining = end - self.clock.now()
            if remaining <= 0:
                return
            self.clock.sleep(min(step, remaining))

    def wait_until(self, predicate: Callable[[], bool], timeout: float, interval: float = 0.5) -> bool:
        """Sondea `predicate` hasta `timeout`; True si se cumplió."""
        end = self.clock.now() + timeout
        while True:
            self.check_stop()
            if predicate():
                return True
            remaining = end - self.clock.now()
            if remaining <= 0:
                return False
            self.clock.sleep(min(interval, remaining))
