"""Parada cooperativa, pausa y cronómetro de inactividad."""

from __future__ import annotations

import threading


class StopRequested(Exception):
    """Se lanza en el siguiente punto de comprobación tras pedir la parada."""


class TaskTimeout(Exception):
    """Se lanza en el siguiente punto de comprobación si la tarea lleva demasiado sin avanzar."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"{seconds:.0f}s sin avanzar")
        self.seconds = seconds


class StopToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


class PauseToken:
    """Pausa pedida por el usuario: la atiende el orquestador en el siguiente límite de tarea."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    @property
    def requested(self) -> bool:
        return self._event.is_set()
