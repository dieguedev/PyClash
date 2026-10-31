"""Tarea de ejemplo: pulsa un botón visible (contexto → visión → dispositivo → resultado)."""

from __future__ import annotations

from ..runtime.context import Context
from .base import TaskResult


class TapButtonTask:
    def __init__(self, button: str, timeout: float = 5.0) -> None:
        self.name = f"tap_{button}"
        self.button = button
        self.timeout = timeout

    def run(self, ctx: Context) -> TaskResult:
        found = {}

        def visible() -> bool:
            rect = ctx.vision.find(ctx.device.capture(), self.button)
            if rect is not None:
                found["rect"] = rect
            return rect is not None

        if not ctx.wait_until(visible, self.timeout):
            return TaskResult.blocked(f"no se ve el botón «{self.button}»")
        ctx.device.tap(*found["rect"].center)
        return TaskResult.done()
