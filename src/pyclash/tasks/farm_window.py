"""Farmeo por ventanas (issue #14): ataques seguidos durante 15-30 minutos al azar y luego se vuelve a evaluar.

Un ataque NUNCA se interrumpe: la ventana, los almacenes llenos, la pausa y los ajustes en vivo solo se
miran entre un ataque y el siguiente. Si un ataque falla se devuelve el fallo tal cual (el orquestador lo
cuenta y reintenta sin cooldown).
"""

from __future__ import annotations

from typing import Callable

from ..policy.session import SessionPolicy, farm_window
from ..runtime.context import Context
from ..screens.village import VillageReader
from .base import Outcome, SessionView, ShouldRun, TaskResult
from .farm import FarmTask


class FarmWindowTask:
    priority = 40
    retry_without_cooldown = True

    def __init__(
        self,
        farm: FarmTask | None = None,
        policy: SessionPolicy | None = None,
        village: VillageReader | None = None,
        on_attack: Callable[[], None] | None = None,
    ) -> None:
        self.farm = farm or FarmTask()
        self.name = self.farm.name  # el interruptor de tarea es el del farmeo
        self.policy = policy or SessionPolicy()
        self.village = village or VillageReader()
        self.on_attack = on_attack  # se llama tras cada ataque terminado (p. ej. guardar el botín)

    def should_run(self, ctx: Context, view: SessionView) -> ShouldRun:
        return self.farm.should_run(ctx, view)

    def run(self, ctx: Context) -> TaskResult:
        window = farm_window(self.policy, ctx.rng)
        started = ctx.clock.now()
        attacks = 0
        while True:
            ctx.progress()
            result = self.farm.run(ctx)
            if result.outcome is not Outcome.DONE:
                return result
            attacks += 1
            if self.on_attack is not None:
                self.on_attack()
            end = self._window_over(ctx, started, window)
            if end:
                return TaskResult(Outcome.DONE, f"{attacks} ataque(s): {end}")

    def _window_over(self, ctx: Context, started: float, window: float) -> str | None:
        """Motivo por el que no se ataca otra vez, o None. Solo se llama entre ataques."""
        ctx.check_stop()
        if ctx.pause.requested:
            return "pausa pedida"
        if not ctx.refresh_settings().enabled(self.name):
            return "farmeo desactivado"
        elapsed = ctx.clock.now() - started
        if elapsed >= window:
            return f"ventana de {window:.0f}s cumplida"
        state = self.village.read_state_stable(ctx)
        if state.storages_full:
            return "almacenes llenos"
        return None
