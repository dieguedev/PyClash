"""ReadSearchCost: lee el coste de «Find a Match» sin pagar y actualiza la reserva de oro (issue #32).

Abre «Battle», lee el coste del botón, cierra con la X y vuelve a la aldea. Nunca pulsa «Find a Match».
Si no puede leerlo no inventa un valor: la reserva queda como estaba y quien llama bloquea el oro.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..runtime.context import Context
from ..screens.classifier import Screen, ScreenClassifier
from ..screens.loot import read_search_cost
from ..vision.text import DigitReader
from .base import Outcome, TaskResult
from .farm import BATTLE_BUTTON, ENGLISH_HINT
from .recovery import RecoveryConfig, RecoverScreen


@dataclass(frozen=True)
class ReadSearchCostConfig:
    menu_timeout_s: float = 6.0
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


class ReadSearchCost:
    name = "read_search_cost"
    priority = 5

    def __init__(
        self,
        config: ReadSearchCostConfig | None = None,
        classifier: ScreenClassifier | None = None,
        digits: DigitReader | None = None,
    ) -> None:
        self.config = config or ReadSearchCostConfig()
        self.classifier = classifier or ScreenClassifier()
        self.digits = digits
        self.cost: int | None = None  # última lectura de esta tarea (para la pantalla de pruebas)

    def _screen(self, ctx: Context) -> Screen:
        return self.classifier.classify(ctx.device.capture().image).screen

    def _to_village(self, ctx: Context) -> TaskResult | None:
        result = RecoverScreen(self.classifier, self.config.recovery).run(ctx)
        return None if result.outcome in (Outcome.DONE, Outcome.NOTHING_TO_DO) else result

    def run(self, ctx: Context) -> TaskResult:
        self.cost = None
        back = self._to_village(ctx)
        if back is not None:
            return back
        image = ctx.device.capture().image
        found = BATTLE_BUTTON.best(image, self.classifier.finder)
        if found is None or found.confidence < BATTLE_BUTTON.threshold:
            return TaskResult.needs_recovery(f"no se ve el botón «Battle»{ENGLISH_HINT}")
        ctx.device.tap(*found.center)
        if not ctx.wait_until(lambda: self._screen(ctx) is Screen.ATTACK_MENU, self.config.menu_timeout_s, 0.5):
            self._to_village(ctx)
            return TaskResult.failed("el menú de ataque no se abrió")
        cost = read_search_cost(ctx.device.capture().image, self.digits)
        back = self._to_village(ctx)  # cierra con la X, con o sin lectura
        if cost is None:
            return TaskResult.failed("no se pudo leer el coste de «Find a Match»")
        self.cost = cost
        ctx.gold_reserve.update(cost, cost, ctx.clock.now())
        if back is not None:
            return back
        return TaskResult.done()


__all__ = ["ReadSearchCost", "ReadSearchCostConfig"]
