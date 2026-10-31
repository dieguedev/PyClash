"""Parada cooperativa."""

from __future__ import annotations

import threading


class StopRequested(Exception):
    """Se lanza en el siguiente punto de comprobación tras pedir la parada."""


class StopToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()
