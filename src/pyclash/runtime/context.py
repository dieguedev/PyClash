"""Contexto de ejecución inyectado: sin estado global ni tiempo real."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Mapping

from ..device.base import Device
from ..vision.base import Vision
from .clock import Clock
from .cooldowns import CooldownRegistry
from .events import EventBus
from .stop import StopRequested, StopToken


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
    cooldowns: CooldownRegistry = field(init=False)

    def __post_init__(self) -> None:
        self.cooldowns = CooldownRegistry(self.clock)

    def check_stop(self) -> None:
        """Si se pidió parar, suelta cualquier entrada sostenida y lanza StopRequested."""
        if self.stop.requested:
            if self.device.held:
                self.device.release()
            raise StopRequested()

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
