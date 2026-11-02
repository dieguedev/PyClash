"""Farmeo de aldea (issue #11): buscar partida -> leer la zona roja y el ejército -> desplegar -> esperar.

Recorrido y cadena de seguridad:

  1. Sin «pulsación sostenida» en el dispositivo no hay trazo: se bloquea con un mensaje claro.
  2. Búsqueda: «Battle» -> «Find a Match» -> («Attack!» del ejército) -> exploración. Cada paso comprueba la
     pantalla en que está; si la búsqueda no avanza en `search_timeout_s` se reinicia (atascado).
  3. «You need more Gold» (falta oro): se cierra con la X, **nunca** el botón verde (gasta gemas), se sale de
     la exploración con «End Battle» y la tarea queda bloqueada. Recuperar oro es la issue #30.
  4. En la exploración (#12): sin mínimos de botín se ataca la **primera aldea que sale**. Con mínimos, la
     política pura (`policy.loot.decide`) juzga cada aldea y aquí se pulsa «Next» hasta que una cumpla todos a la
     vez, con un presupuesto que deja siempre oro para otra búsqueda. Lo dudoso se relee una vez y, si sigue
     dudoso, se ataca (atacar no cuesta copas). Cada «Next» se verifica (el botín cambió, o el nombre del rival
     si coincide); si no entra dos veces seguidas se reinicia la búsqueda.
  5. Antes de tocar nada se lee la zona roja (sin masa clara: no se ataca) y se identifica el ejército elegido
     por los retratos de la barra (falta una tropa: «Troop Archer not found»). En ambos casos se sale de la
     exploración con «End Battle», que es gratis.
  6. Despliegue: por cada tipo de tropa del ejército, un solo gesto continuo de 3-4 vueltas (`deploy_path`);
     se mira la carta cada medio segundo y, en cuanto se agota (gris), se suelta y se descartan las vueltas que
     quedan. La entrada se libera siempre (`finally`) y la parada cooperativa también la libera.
  7. Terminado el despliegue la tarea solo espera: el fin de la batalla lo pone el juego. No se rinde ni se
     cancela. En la pantalla de resultado se lee el botín de «You got:» (relectura; si sigue dudoso no se suma,
     se avisa y se guarda una captura) y después vuelve a la aldea.

El juego debe estar EN INGLÉS (botones y diálogos se leen con la fuente y el vocabulario en inglés).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from ..policy.armies import ARMIES, Army, troop_name
from ..policy.deploy_path import PathSettings, make_trace
from ..policy.loot import (
    MAX_SKIPS,
    RELAXED_ATTACKS,
    EnemyLoot,
    LootLimits,
    LootMinimum,
    RelaxedTracker,
    SessionStats,
    Verdict,
    decide,
    skip_budget,
)
from ..runtime.context import Context
from ..runtime.events import CaptureSaved, Error, LootObtained, Warning
from ..screens.battle_bar import BarReader, Card
from ..screens.classifier import Screen, ScreenClassifier, TextIndicator
from ..screens.escapes import find_escape
from ..screens.loot import (
    read_enemy_loot,
    read_enemy_name,
    read_hud_gold,
    read_next_cost,
    read_result_loot,
    read_search_cost,
)
from ..screens.red_zone import RedZone, read_red_zone_frames
from ..screens.signatures import box
from ..screens.troops import TroopPortraits, check_army
from ..vision.ocr import OcrEngine
from ..vision.text import DigitReader
from .base import Outcome, SessionView, ShouldRun, TaskResult
from .recovery import RecoveryConfig, RecoverScreen

ENGLISH_HINT = " (el bot exige el juego en inglés)"

BATTLE_BUTTON = TextIndicator("Battle", box(0.0, 0.89, 0.11, 0.99), 28)
FIND_A_MATCH = TextIndicator("Find a Match", box(0.09, 0.64, 0.27, 0.72), 32)
ATTACK_BUTTON = TextIndicator("Attack!", box(0.76, 0.82, 0.95, 0.89), 36)
NEXT_BUTTON = TextIndicator("Next", box(0.89, 0.70, 0.99, 0.78), 38, threshold=0.6)


@dataclass(frozen=True)
class FarmConfig:
    army: str = "barbarians"
    minimum: LootMinimum = field(default_factory=LootMinimum)  # 0 = ignorado: sin «Next»
    max_skips: int = MAX_SKIPS
    limits: LootLimits = field(default_factory=LootLimits)
    reread_s: float = 0.6  # espera antes de releer un botín dudoso
    next_timeout_s: float = 8.0  # cuánto se espera a que «Next» cambie la aldea
    result_settle_s: float = 1.0  # la pantalla de resultado anima las cifras: se leen dos veces seguidas
    path: PathSettings = field(default_factory=PathSettings)
    menu_timeout_s: float = 6.0
    search_timeout_s: float = 45.0  # si la búsqueda no llega a la exploración en este tiempo, se reinicia
    search_attempts: int = 2
    settle_s: float = 1.0
    select_pause_s: float = 0.4  # tras elegir la carta, antes de pulsar el terreno
    # Con el terreno pulsado y SIN mover, al azar entre estos dos valores: sin esta pausa el juego toma el
    # trazo por un arrastre del mapa en vez de por un despliegue.
    # La zona roja parpadea: se leen varios fotogramas y se combinan (ver `read_red_zone_frames`).
    max_rounds: int = 8  # trazos nuevos como máximo por carta (cada uno, 3-4 vueltas) hasta agotar las tropas
    red_zone_frames: int = 6
    red_zone_interval_s: float = 0.35
    press_hold_min_s: float = 0.3
    press_hold_max_s: float = 0.5
    check_every_s: float = 0.5  # cada cuánto se mira si la carta se agotó durante el trazo
    battle_timeout_s: float = 220.0  # 3 min de batalla más margen (debe quedar por debajo del cronómetro de inactividad)
    capture_dir: Path | None = None
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)


Phase = Literal["scouting", "gold", "waiting"]


class FarmTask:
    name = "village_farming"
    priority = 40
    retry_without_cooldown = True  # si falla se reintenta sin descansar (los fallos seguidos siguen contando)

    def __init__(
        self,
        config: FarmConfig | None = None,
        classifier: ScreenClassifier | None = None,
        bar: BarReader | None = None,
        portraits: TroopPortraits | None = None,
        armies: dict[str, Army] | None = None,
        digits: DigitReader | None = None,
        ocr: OcrEngine | None = None,
        stats: SessionStats | None = None,
    ) -> None:
        self.config = config or FarmConfig()
        self.classifier = classifier or ScreenClassifier()
        self.bar = bar or BarReader()
        self._portraits = portraits
        self.armies = armies if armies is not None else ARMIES
        self.digits = digits
        self.ocr = ocr  # solo desempata un «Next» cuyo botín coincide; sin él no hay lectura de nombres
        self.stats = stats if stats is not None else SessionStats(self.config.limits)
        # Costes leídos de los botones del juego (los usa la reserva de oro de la #32). Se leen cada vez.
        self.search_cost: int | None = None
        self.next_cost: int | None = None
        self._relaxed = RelaxedTracker()
        self._last_tap = -1e9

    def should_run(self, ctx: Context, view: SessionView) -> ShouldRun:
        """Procede mientras algún almacén tenga hueco: con oro y elixir llenos no hay nada que ganar."""
        if view.village.storages_full:
            return ShouldRun.no("almacenes llenos")
        return ShouldRun.yes()

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
        result = self._scout(ctx)
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
        # El botón muestra el coste de buscar sin pagar: se anota cada vez que se pasa por el menú.
        cost = read_search_cost(ctx.device.capture().image, self.digits)
        if cost is not None:
            self.search_cost = cost
            ctx.gold_reserve.update(cost, cost, ctx.clock.now())  # los dos costes se leen del mismo botón
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

    # ------------------------------------------------------------------------------ exploración (#12)

    def _scout(self, ctx: Context) -> TaskResult | None:
        """Pulsa «Next» hasta que la aldea cumpla los mínimos. None = atacar la aldea que se ve."""
        cfg = self.config
        if not cfg.minimum.any:
            return None  # sin mínimos: la primera aldea, como en la #11

        image = ctx.device.capture().image
        next_cost = read_next_cost(image, self.digits)
        if next_cost is not None:
            self.next_cost = next_cost
        gold = read_hud_gold(image, self.digits)
        plan = self._relaxed.plan(gold, self.next_cost, cfg.max_skips)
        budget = plan.budget
        if plan.relaxed:
            ctx.events.publish(
                Warning(
                    f"oro {gold if gold is not None else '¿?'} y «Next» {self.next_cost if self.next_cost is not None else '¿?'}: "
                    f"solo {budget} saltos de {cfg.max_skips} (siempre queda una búsqueda de reserva); "
                    f"al agotarlos se ataca lo que salga, sin exigir mínimo [{plan.streak}/{RELAXED_ATTACKS}]"
                )
            )
            if plan.streak >= RELAXED_ATTACKS:
                ctx.events.publish(Warning(f"{plan.streak} búsquedas seguidas con el oro bajo: revisa que las mejoras no lo estén gastando"))

        skips = stuck = restarts = 0
        while True:
            ctx.check_stop()
            image, loot, verdict = self._judge(ctx)
            if verdict is Verdict.ACCEPT:
                ctx.events.publish(Warning(f"aldea aceptada tras {skips} saltos"))
                return None
            if verdict is Verdict.REREAD:  # dudoso tras releer: se ataca, atacar no cuesta copas
                ctx.events.publish(Warning(f"botín dudoso tras releer ({loot}): se ataca esta aldea"))
                self._save_capture(ctx, image, "loot")
                return None
            if skips >= budget:
                why = "sin oro para otra búsqueda después" if plan.relaxed else f"tope de {cfg.max_skips} saltos"
                ctx.events.publish(Warning(f"no se salta más ({why}): se ataca esta aldea aunque no cumpla {cfg.minimum.describe()}"))
                return None
            if not self._press_next(ctx):
                ctx.events.publish(Warning("no se ve el botón «Next»: se ataca esta aldea"))
                self._save_capture(ctx, image, "next")
                return None
            if self._village_changed(ctx, loot, image):
                skips += 1
                stuck = 0
                ctx.progress()
                continue
            stuck += 1
            ctx.events.publish(Warning(f"«Next» sin efecto verificable ({stuck}/2)"))
            if stuck < 2:
                continue
            restarts += 1
            if restarts > cfg.search_attempts:
                return TaskResult.failed("«Next» no entra tras reiniciar la búsqueda" + ENGLISH_HINT)
            ctx.events.publish(Warning(f"«Next» no entra: se reinicia la búsqueda (intento {restarts}/{cfg.search_attempts})"))
            self._leave_search(ctx)
            result = self._start_search(ctx)
            if result is not None:
                return result
            skips = stuck = 0
            budget = skip_budget(read_hud_gold(ctx.device.capture().image, self.digits), self.next_cost, cfg.max_skips)

    def _judge(self, ctx: Context) -> tuple[np.ndarray, EnemyLoot, Verdict]:
        """Lee el botín y decide. Si hay duda se relee UNA vez; lo que siga dudoso lo resuelve quien llama."""
        cfg = self.config
        image = ctx.device.capture().image
        loot = read_enemy_loot(image, self.digits, cfg.limits)
        verdict = decide(loot, cfg.minimum)
        if verdict is Verdict.REREAD:
            ctx.sleep(cfg.reread_s)
            image = ctx.device.capture().image
            loot = read_enemy_loot(image, self.digits, cfg.limits)
            verdict = decide(loot, cfg.minimum)
        return image, loot, verdict

    def _press_next(self, ctx: Context) -> bool:
        return self._tap_indicator(ctx, NEXT_BUTTON)

    def _village_changed(self, ctx: Context, before: EnemyLoot, before_image: np.ndarray) -> bool:
        """¿Entró el «Next»? El botín disponible cambió; si coincide, desempata el nombre del rival.

        Solo se comparan campos leídos en las dos aldeas. El nombre se lee de las capturas que ya se
        tienen en memoria y no se registra ni se guarda.
        """
        cfg = self.config

        def differs(now: EnemyLoot) -> bool:
            pairs = ((before.gold, now.gold), (before.elixir, now.elixir), (before.dark, now.dark))
            return any(a is not None and b is not None and a != b for a, b in pairs)

        def changed() -> bool:
            image = ctx.device.capture().image
            if self.classifier.classify(image).screen is not Screen.FINDING_OPPONENT:
                return False
            return differs(read_enemy_loot(image, self.digits, cfg.limits))

        if ctx.wait_until(changed, cfg.next_timeout_s, 0.5):
            return True
        if self.ocr is None:
            ctx.events.publish(Warning("botín idéntico y sin OCR para desempatar por el nombre del rival"))
            return False
        now_image = ctx.device.capture().image
        old, new = read_enemy_name(self.ocr, before_image), read_enemy_name(self.ocr, now_image)
        return old is not None and new is not None and old != new

    # ------------------------------------------------------------------------------ ataque

    def _leave_search(self, ctx: Context) -> str:
        """Sale de la exploración (End Battle, gratis). Devuelve un sufijo para el motivo si no se pudo."""
        back = self._to_village(ctx)
        return "" if back is None else f" (y no se pudo salir de la búsqueda: {back.reason})"

    def _attack(self, ctx: Context, army: Army) -> TaskResult:
        cfg = self.config
        image = ctx.device.capture().image
        screen = self.classifier.classify(image).screen
        if screen is not Screen.FINDING_OPPONENT:  # la zona roja solo se lee en la exploración (ver red_zone.py)
            return self._abort(ctx, image, f"se esperaba la exploración y la pantalla es «{screen.value}»: no se despliega nada")
        frames = [image]
        for _ in range(max(0, cfg.red_zone_frames - 1)):
            ctx.sleep(cfg.red_zone_interval_s)
            frames.append(ctx.device.capture().image)
        reading = read_red_zone_frames(frames)
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

    def _save(self, ctx: Context, image: np.ndarray, prefix: str = "farm") -> Path | None:
        if self.config.capture_dir is None:
            return None
        self.config.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.capture_dir / f"{prefix}-{int(ctx.clock.now() * 1000)}.png"
        Image.fromarray(image).save(path)
        return path

    def _save_capture(self, ctx: Context, image: np.ndarray, prefix: str) -> None:
        """Captura de depuración de una lectura dudosa (sin carpeta configurada no se guarda nada)."""
        path = self._save(ctx, image, prefix)
        if path is not None:
            ctx.events.publish(CaptureSaved(str(path)))

    def _deploy(self, ctx: Context, card: Card, zone: RedZone, size: tuple[int, int], troop: str) -> TaskResult | None:
        """Gestos continuos alrededor de la aldea con la carta `card`, hasta agotarla. None si se desplegó.

        Cada ronda es un trazo nuevo al azar (3-4 vueltas): si al acabar quedan tropas, se hace otra. Salvaguardas:
        un máximo de rondas y, sobre todo, que el contador «xN» de la carta baje en cada ronda; si no baja, el
        gesto no está desplegando nada (p. ej. arrastra el mapa) y seguir solo lo empeoraría.
        """
        cfg = self.config
        ctx.device.tap(*card.center)
        ctx.sleep(cfg.select_pause_s)
        ctx.check_stop()

        count = self.bar.read_count(ctx.device.capture().image, card.rect)
        for round_number in range(1, cfg.max_rounds + 1):
            trace = make_trace(list(zone.hull), size, ctx.rng, cfg.path)
            if self._run_trace(ctx, card, trace):
                break
            image = ctx.device.capture().image
            left = self.bar.read_count(image, card.rect)
            if count is not None and left is not None and left >= count:
                ctx.events.publish(Warning(
                    f"tras la ronda {round_number} siguen {left} de {troop_name(troop)} (había {count}): el gesto no despliega"
                ))
                self._save_capture(ctx, image, "farm-no-deploy")
                break
            count = left
            if round_number == cfg.max_rounds:
                ctx.events.publish(Warning(f"tras {cfg.max_rounds} rondas todavía quedan tropas de {troop_name(troop)}"))
        ctx.sleep(cfg.select_pause_s)
        return None

    def _run_trace(self, ctx: Context, card: Card, trace) -> bool:
        """Un trazo: pulsar, esperar quieto, deslizar y soltar. True si la carta se agotó."""
        cfg = self.config
        exhausted = False
        next_check = ctx.clock.now() + cfg.check_every_s
        ctx.device.press(*trace.points[0])
        try:
            ctx.sleep(ctx.rng.uniform(cfg.press_hold_min_s, cfg.press_hold_max_s))  # pulsación quieta antes de deslizar
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
        return exhausted or self.bar.is_exhausted(ctx.device.capture().image, card.rect)

    def _wait_end(self, ctx: Context) -> TaskResult:
        """El juego termina la batalla; aquí solo se espera y se vuelve a la aldea por la pantalla de resultado."""
        cfg = self.config
        ctx.progress()  # empieza la espera del combate: el cronómetro de inactividad vuelve a contar desde aquí
        ended = ctx.wait_until(lambda: self._screen(ctx) in (Screen.RESULT, Screen.MAIN_VILLAGE), cfg.battle_timeout_s, 2.0)
        if not ended:
            reason = f"la batalla no terminó en {cfg.battle_timeout_s:.0f} s (no se cancela)"
            ctx.events.publish(Warning(reason))
            return TaskResult.failed(reason)
        if self._screen(ctx) is Screen.RESULT:
            self._count_result(ctx)
        back = self._to_village(ctx)
        if back is not None:
            return back
        return TaskResult(Outcome.DONE, "ataque completado")

    def _count_result(self, ctx: Context) -> None:
        """Suma el botín de «You got:». Las cifras se animan: vale una lectura que se repite en dos
        capturas seguidas. Si no hay acuerdo tras releer, ese ataque NO se suma: se avisa y se guarda captura."""
        cfg = self.config
        previous = None
        image = None
        for _ in range(3):
            ctx.sleep(cfg.result_settle_s)
            image = ctx.device.capture().image
            loot = read_result_loot(image, self.digits, cfg.limits)
            if loot is not None and loot == previous:
                gain = (loot.gold, loot.elixir, loot.dark)
                if self.stats.record(gain):
                    ctx.events.publish(LootObtained({"gold": loot.gold, "elixir": loot.elixir, "dark": loot.dark}))
                    return
                break
            previous = loot
        self.stats.record_unread()
        ctx.events.publish(Warning("no se pudo leer el botín de «You got:»: este ataque no se suma a los totales"))
        if image is not None:
            self._save_capture(ctx, image, "result")


__all__ = ["FarmConfig", "FarmTask"]
