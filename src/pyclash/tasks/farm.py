"""Farmeo de aldea (issue #11): buscar partida -> leer la zona roja y el ejército -> desplegar -> esperar.

Recorrido y cadena de seguridad:

  1. Sin «pulsación sostenida» en el dispositivo no hay trazo: se bloquea con un mensaje claro.
  2. Búsqueda: «Battle» -> «Find a Match» -> («Attack!» del ejército) -> exploración. Cada paso comprueba la
     pantalla en que está; si la búsqueda no avanza en `search_timeout_s` se reinicia (atascado).
  3. «You need more Gold» (falta oro): se cierra con la X, **nunca** el botón verde (gasta gemas), se sale de
     la exploración con «End Battle» y la tarea queda bloqueada. Recuperar oro es la issue #30.
  4. En la exploración se ataca la **primera aldea que sale**: el `VillageAcceptor` decide si la aldea vale y
     aquí siempre dice que sí. Los mínimos de botín y «Next» son de la #12 (que sustituye al aceptador).
  5. Antes de tocar nada se lee la zona roja (sin masa clara: no se ataca) y se identifica el ejército elegido
     por los retratos de la barra (falta una tropa: «Troop Archer not found»). En ambos casos se sale de la
     exploración con «End Battle», que es gratis.
  6. Despliegue: por cada tipo de tropa del ejército, un solo gesto continuo de 3-4 vueltas (`deploy_path`);
     se mira la carta cada medio segundo y, en cuanto se agota (gris), se suelta y se descartan las vueltas que
     quedan. La entrada se libera siempre (`finally`) y la parada cooperativa también la libera.
  7. Terminado el despliegue la tarea solo espera: el fin de la batalla lo pone el juego. No se rinde ni se
     cancela. Después vuelve a la aldea por la pantalla de resultado.

El juego debe estar EN INGLÉS (botones y diálogos se leen con la fuente y el vocabulario en inglés).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from PIL import Image

from ..policy.armies import ARMIES, Army, troop_name
from ..policy.deploy_path import PathSettings, make_trace
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, Error, Warning
from ..screens.battle_bar import BarReader, Card
from ..screens.classifier import Screen, ScreenClassifier, TextIndicator
from ..screens.escapes import find_escape
from ..screens.red_zone import RedZone, read_red_zone
from ..screens.signatures import box
from ..screens.troops import TroopPortraits, check_army
from .base import Outcome, TaskResult
from .recovery import RecoveryConfig, RecoverScreen

ENGLISH_HINT = " (el bot exige el juego en inglés)"

BATTLE_BUTTON = TextIndicator("Battle", box(0.0, 0.89, 0.11, 0.99), 28)
FIND_A_MATCH = TextIndicator("Find a Match", box(0.09, 0.64, 0.27, 0.72), 32)
ATTACK_BUTTON = TextIndicator("Attack!", box(0.76, 0.82, 0.95, 0.89), 36)


class VillageAcceptor(Protocol):
    """Decide si la aldea que se está viendo en la exploración vale la pena atacarla."""

    def accept(self, image: np.ndarray) -> bool: ...


class AcceptFirst:
    """Ataca la primera aldea que sale, sea cual sea su botín (#11). La #12 lo sustituye por los mínimos."""

    def accept(self, image: np.ndarray) -> bool:
        return True


@dataclass(frozen=True)
class FarmConfig:
    army: str = "barbarians"
    path: PathSettings = field(default_factory=PathSettings)
    menu_timeout_s: float = 6.0
    search_timeout_s: float = 45.0  # si la búsqueda no llega a la exploración en este tiempo, se reinicia
    search_attempts: int = 2
    settle_s: float = 1.0
    select_pause_s: float = 0.4  # tras elegir la carta, antes de pulsar el terreno
    check_every_s: float = 0.5  # cada cuánto se mira si la carta se agotó durante el trazo
    battle_timeout_s: float = 330.0  # 3 min de batalla más margen; no se cancela si se pasa: se avisa
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


Phase = Literal["scouting", "gold", "waiting"]


class FarmTask:
    name = "village_farming"

    def __init__(
        self,
        config: FarmConfig | None = None,
        classifier: ScreenClassifier | None = None,
        bar: BarReader | None = None,
        portraits: TroopPortraits | None = None,
        acceptor: VillageAcceptor | None = None,
        armies: dict[str, Army] | None = None,
    ) -> None:
        self.config = config or FarmConfig()
        self.classifier = classifier or ScreenClassifier()
        self.bar = bar or BarReader()
        self._portraits = portraits
        self.acceptor = acceptor or AcceptFirst()
        self.armies = armies if armies is not None else ARMIES
        self._last_tap = -1e9

    @property
    def portraits(self) -> TroopPortraits:
        if self._portraits is None:
            self._portraits = TroopPortraits.load_packaged()
        return self._portraits

    # ------------------------------------------------------------------------------ recorrido

    def run(self, ctx: Context) -> TaskResult:
        cfg = self.config
        army = self.armies.get(cfg.army)
        if army is None:
            return TaskResult.failed(f"ejército desconocido: «{cfg.army}» (los disponibles son {', '.join(self.armies)})")
        if not ctx.device.capabilities.hold:
            return TaskResult.blocked("el dispositivo no admite pulsación sostenida: el despliegue por trazo se desactiva")

        result = self._start_search(ctx)
        if result is not None:
            return result
        return self._attack(ctx, army)

    # ------------------------------------------------------------------------------ búsqueda

    def _screen(self, ctx: Context) -> Screen:
        return self.classifier.classify(ctx.device.capture().image).screen

    def _to_village(self, ctx: Context) -> TaskResult | None:
        result = RecoverScreen(self.classifier, self.config.recovery).run(ctx)
        return None if result.outcome in (Outcome.DONE, Outcome.NOTHING_TO_DO) else result

    def _tap_indicator(self, ctx: Context, indicator: TextIndicator) -> bool:
        image = ctx.device.capture().image
        found = indicator.best(image, self.classifier.finder)
        if found is None or found.confidence < indicator.threshold:
            return False
        ctx.device.tap(*found.center)
        return True

    def _start_search(self, ctx: Context) -> TaskResult | None:
        """None cuando se llegó a la exploración; si no, el resultado con el que termina la tarea."""
        cfg = self.config
        for attempt in range(cfg.search_attempts):
            ctx.check_stop()
            if attempt:
                ctx.events.publish(Warning(f"la búsqueda no avanzó: se reinicia (intento {attempt + 1}/{cfg.search_attempts})"))
            outcome = self._open_search(ctx)
            if outcome == "ready":
                return None
            if isinstance(outcome, TaskResult):
                return outcome
        return TaskResult.failed(f"la búsqueda de partida se quedó atascada{ENGLISH_HINT}")

    def _open_search(self, ctx: Context) -> Literal["ready", "stuck"] | TaskResult:
        cfg = self.config
        screen = self._screen(ctx)
        if screen is Screen.FINDING_OPPONENT:
            return "ready"
        if screen is not Screen.MAIN_VILLAGE:
            back = self._to_village(ctx)
            if back is not None:
                return back
        if not self._tap_indicator(ctx, BATTLE_BUTTON):
            return TaskResult.needs_recovery(f"no se ve el botón «Battle»{ENGLISH_HINT}")
        if not ctx.wait_until(lambda: self._screen(ctx) is Screen.ATTACK_MENU, cfg.menu_timeout_s, 0.5):
            return "stuck"
        if not self._tap_indicator(ctx, FIND_A_MATCH):
            return "stuck"

        state: dict[str, Phase] = {"phase": "waiting"}

        def step() -> bool:
            image = ctx.device.capture().image
            screen = self.classifier.classify(image).screen
            if screen is Screen.FINDING_OPPONENT:
                state["phase"] = "scouting"
                return True
            if screen is Screen.NEED_MORE_GOLD:
                state["phase"] = "gold"
                return True
            if screen is Screen.ARMY_SELECTION and ctx.clock.now() - self._last_tap >= 2.0:
                found = ATTACK_BUTTON.best(image, self.classifier.finder)
                if found is not None and found.confidence >= ATTACK_BUTTON.threshold:
                    self._last_tap = ctx.clock.now()
                    ctx.device.tap(*found.center)
            return False

        if not ctx.wait_until(step, cfg.search_timeout_s, 0.5):
            back = self._to_village(ctx)
            return back if back is not None else "stuck"
        if state["phase"] == "gold":
            return self._out_of_gold(ctx)
        return "ready"

    def _out_of_gold(self, ctx: Context) -> TaskResult:
        """«You need more Gold»: solo la X (el botón verde gasta gemas) y se sale de la búsqueda."""
        image = ctx.device.capture().image
        tap = find_escape(image, Screen.NEED_MORE_GOLD, self.classifier.finder)
        if tap is None:
            return self._fail(ctx, image, "falta oro y no se encuentra la X para cerrar el aviso (el botón verde gasta gemas y no se pulsa)")
        ctx.events.publish(Warning("falta oro para buscar partida: se cierra el aviso con la X (no se compra con gemas)"))
        ctx.device.tap(tap.x, tap.y)
        ctx.sleep(self.config.settle_s)
        back = self._to_village(ctx)
        reason = "oro insuficiente para buscar partida"
        return TaskResult.blocked(reason if back is None else f"{reason}; además: {back.reason}")

    # ------------------------------------------------------------------------------ ataque

    def _leave_search(self, ctx: Context) -> str:
        """Sale de la exploración (End Battle, gratis). Devuelve un sufijo para el motivo si no se pudo."""
        back = self._to_village(ctx)
        return "" if back is None else f" (y no se pudo salir de la búsqueda: {back.reason})"

    def _attack(self, ctx: Context, army: Army) -> TaskResult:
        image = ctx.device.capture().image
        screen = self.classifier.classify(image).screen
        if screen is not Screen.FINDING_OPPONENT:  # la zona roja solo se lee en la exploración (ver red_zone.py)
            return self._abort(ctx, image, f"se esperaba la exploración y la pantalla es «{screen.value}»: no se despliega nada")
        if not self.acceptor.accept(image):
            suffix = self._leave_search(ctx)
            return TaskResult.nothing_to_do(f"la aldea no cumple lo exigido{suffix}")

        reading = read_red_zone(image)
        if reading.zone is None:
            return self._abort(ctx, image, f"no se pudo leer la zona roja: {reading.reason}")

        scan = self.bar.scan(ctx)
        image = ctx.device.capture().image
        check = check_army(image, list(scan.cards), army, self.portraits)
        if not check.ok:
            return self._abort(ctx, image, check.reason + ENGLISH_HINT if "not found" in check.reason else check.reason)
        for other in check.others:
            ctx.events.publish(Warning(f"en la barra también hay {troop_name(other)}, que no es del ejército elegido: no se usa"))

        height, width = image.shape[:2]
        for troop in army.troops:
            ctx.check_stop()
            card = check.cards[troop]
            done = self._deploy(ctx, card, reading.zone, (width, height), troop)
            if done is not None:
                return done
        return self._wait_end(ctx)

    def _abort(self, ctx: Context, image: np.ndarray, reason: str) -> TaskResult:
        """Falla antes de desplegar nada: se guarda la captura y se sale de la exploración."""
        suffix = self._leave_search(ctx)
        return self._fail(ctx, image, reason + suffix)

    def _fail(self, ctx: Context, image: np.ndarray, reason: str) -> TaskResult:
        path = self._save(ctx, image)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))
        ctx.events.publish(Error(reason))
        return TaskResult.failed(reason)

    def _save(self, ctx: Context, image: np.ndarray) -> Path | None:
        if self.config.capture_dir is None:
            return None
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.capture_dir / f"farm-{int(ctx.clock.now() * 1000)}.png"
        Image.fromarray(image).save(path)
        return path

    def _deploy(self, ctx: Context, card: Card, zone: RedZone, size: tuple[int, int], troop: str) -> TaskResult | None:
        """Un gesto continuo alrededor de la aldea con la carta `card`. None si se desplegó (o se agotó)."""
        cfg = self.config
        trace = make_trace(list(zone.hull), size, ctx.rng, cfg.path)
        ctx.device.tap(*card.center)
        ctx.sleep(cfg.select_pause_s)
        ctx.check_stop()

        exhausted = False
        started = ctx.clock.now()
        next_check = started + cfg.check_every_s
        ctx.device.press(*trace.points[0])
        try:
            for point, wait in zip(trace.points[1:], trace.waits[1:]):
                ctx.sleep(wait, step=wait)  # comprueba la parada en cada punto
                ctx.device.move(*point)
                if ctx.clock.now() >= next_check:
                    next_check = ctx.clock.now() + cfg.check_every_s
                    if self.bar.is_exhausted(ctx.device.capture().image, card.rect):
                        exhausted = True
                        break  # se descartan las vueltas que quedan
        finally:
            if ctx.device.held:
                ctx.device.release()
        if not exhausted:
            exhausted = self.bar.is_exhausted(ctx.device.capture().image, card.rect)
        if not exhausted:
            ctx.events.publish(Warning(f"tras las {trace.laps} vueltas todavía quedan tropas de {troop_name(troop)}"))
        ctx.sleep(cfg.select_pause_s)
        return None

    def _wait_end(self, ctx: Context) -> TaskResult:
        """El juego termina la batalla; aquí solo se espera y se vuelve a la aldea por la pantalla de resultado."""
        cfg = self.config
        ended = ctx.wait_until(lambda: self._screen(ctx) in (Screen.RESULT, Screen.MAIN_VILLAGE), cfg.battle_timeout_s, 2.0)
        if not ended:
            reason = f"la batalla no terminó en {cfg.battle_timeout_s:.0f} s (no se cancela)"
            ctx.events.publish(Warning(reason))
            return TaskResult.failed(reason)
        back = self._to_village(ctx)
        if back is not None:
            return back
        return TaskResult(Outcome.DONE, "ataque completado")


__all__ = ["AcceptFirst", "FarmConfig", "FarmTask", "VillageAcceptor"]
