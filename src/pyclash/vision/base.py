"""Interfaz de visión (los lectores reales llegan en issues posteriores)."""

from __future__ import annotations

from typing import Protocol

from ..device.base import Frame, Rect


class Vision(Protocol):
    def find(self, frame: Frame, name: str) -> Rect | None: ...


class MetadataVision:
    """Visión para pruebas: lee los elementos de `frame.metadata["elements"]` (nombre -> Rect)."""

    def find(self, frame: Frame, name: str) -> Rect | None:
        return frame.metadata.get("elements", {}).get(name)
