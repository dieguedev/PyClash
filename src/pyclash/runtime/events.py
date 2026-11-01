"""Bus de eventos tipado."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, TypeVar


@dataclass(frozen=True)
class Event:
    pass


@dataclass(frozen=True)
class StateChanged(Event):
    state: str


@dataclass(frozen=True)
class TaskStarted(Event):
    task: str


@dataclass(frozen=True)
class TaskFinished(Event):
    task: str
    outcome: str
    reason: str = ""


@dataclass(frozen=True)
class ImprovementStarted(Event):
    name: str


@dataclass(frozen=True)
class UpgradePlanned(Event):
    """Dry run: lo que se habría empezado y por qué. No se ha tocado nada."""

    name: str
    reason: str


@dataclass(frozen=True)
class WallBatchPlanned(Event):
    """Muros por lotes: lo que se va a mejorar y por qué (en modo de prueba, lo que se habría hecho)."""

    resource: str
    count: int
    unit_cost: int
    reason: str


@dataclass(frozen=True)
class WallBatchStep(Event):
    """Un paso del lote (traza de diagnóstico): botón pulsado, contador leído, etc."""

    action: str
    count: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class LootObtained(Event):
    resources: dict[str, int]


@dataclass(frozen=True)
class Idle(Event):
    seconds: float


@dataclass(frozen=True)
class Warning(Event):
    message: str


@dataclass(frozen=True)
class Error(Event):
    message: str


@dataclass(frozen=True)
class CaptureSaved(Event):
    path: str


E = TypeVar("E", bound=Event)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[type[Event], list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Suscribe `handler` a `event_type` y sus subclases (Event recibe todo)."""
        self._subs[event_type].append(handler)  # type: ignore[arg-type]

    def publish(self, event: Event) -> None:
        for etype, handlers in list(self._subs.items()):
            if isinstance(event, etype):
                for h in list(handlers):
                    h(event)
