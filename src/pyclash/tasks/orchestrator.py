"""Orquestador de sesión (issue #14): encadena las tareas por prioridad, aplica cooldowns y el cronómetro
de inactividad, decide el idle y se detiene o pausa sin dejar el juego a medias.

Cada ciclo: límite de tarea (parada, pausa, ajustes en vivo) -> volver a la aldea -> leer el estado una vez
-> la primera tarea por prioridad a la que le procede se ejecuta -> se vuelve a empezar desde arriba. Si a
ninguna le procede hay idle (con motivo) y se revisa pasados 5-8 minutos al azar.

Sesión «sin límite de tiempo»: solo termina por la parada del usuario, por fallos repetidos de una tarea o por
un problema que reintentar no arregla (juego cerrado, inicio de sesión, ventana perdida).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from ..policy.session import SessionPolicy, failure_cooldown, idle_wait
from ..runtime.context import Context
from ..runtime.events import Error, Idle, SessionEnded, StateChanged, Warning
from ..runtime.session_log import LootSession
from ..runtime.stop import StopRequested
from ..screens.classifier import Screen, ScreenClassifier
from ..screens.village import VillageReader
from .base import Outcome, SessionView, ShouldRun, Task, TaskResult, run_task
from .recovery import RecoverScreen, RecoveryConfig

DEFAULT_PRIORITY = 1000
WALLS_TASK = "upgrade_walls"
WALLS_COMPLETE_KEY = "session:walls_complete"
RECOVERY_NAME = "recover_screen"
BATTLE_END_WAIT_S = 240.0  # al parar en pleno combate se espera a que termine (3 min más margen)


@dataclass(frozen=True)
class SessionResult:
    reason: str
    error: bool = False
    tasks_run: int = 0


class Orchestrator:
    def __init__(
        self,
        ctx: Context,
        tasks: list[Task],
        policy: SessionPolicy | None = None,
        village: VillageReader | None = None,
        classifier: ScreenClassifier | None = None,
        recovery: RecoveryConfig | None = None,
        loot: LootSession | None = None,
    ) -> None:
        self.ctx = ctx
        self.tasks = sorted(tasks, key=lambda t: getattr(t, "priority", DEFAULT_PRIORITY))
        self.policy = policy or SessionPolicy()
        self.village = village or VillageReader()
        self.classifier = classifier or ScreenClassifier()
        self.recovery = recovery or RecoveryConfig()
        self.loot = loot
        self.failures: dict[str, int] = {}
        self._tasks_run = 0

    # ------------------------------------------------------------------ mandos (los usa el controlador)

    def stop(self) -> None:
        self.ctx.stop.request()

    def pause(self) -> None:
        self.ctx.pause.request()

    def resume(self) -> None:
        self.ctx.pause.clear()

    # ------------------------------------------------------------------ sesión

    def run(self) -> SessionResult:
        ctx = self.ctx
        if self.loot is not None:
            self.loot.__enter__()
        try:
            ctx.events.publish(StateChanged("running"))
            try:
                result = self._loop()
            except StopRequested:
                result = SessionResult("parada solicitada", False, self._tasks_run)
            ctx.disarm_watchdog()
            self._safe_exit()
        finally:
            if self.loot is not None:
                self.loot.__exit__(None, None, None)
        ctx.events.publish(StateChanged("stopped"))
        ctx.events.publish(SessionEnded(result.reason, result.error))
        return result

    def _loop(self) -> SessionResult:
        ctx = self.ctx
        while True:
            self._boundary()
            stop = self._ensure_village()
            if stop is not None:
                return stop
            state = self.village.read_state_stable(ctx)
            chosen, skipped = self._choose(SessionView(state))
            if chosen is None:
                self._idle(skipped)
                continue
            stop = self._execute(chosen)
            if stop is not None:
                return stop

    # ------------------------------------------------------------------ límite de tarea

    def _boundary(self) -> None:
        """Parada, pausa y ajustes en vivo: solo aquí, nunca a mitad de una tarea."""
        ctx = self.ctx
        ctx.check_stop()
        if ctx.pause.requested:
            ctx.events.publish(StateChanged("paused"))
            while ctx.pause.requested:
                ctx.sleep(0.5)
            ctx.events.publish(StateChanged("running"))
        ctx.refresh_settings()
        ctx.walls_complete = not ctx.cooldowns.ready(WALLS_COMPLETE_KEY)

    def _ensure_village(self) -> SessionResult | None:
        """Antes de leer el estado, la aldea. Una pantalla de la que no se sale cuenta como fallo."""
        result = RecoverScreen(self.classifier, self.recovery).run(self.ctx)
        if result.outcome in (Outcome.DONE, Outcome.NOTHING_TO_DO):
            self.failures[RECOVERY_NAME] = 0
            return None
        if result.outcome is Outcome.BLOCKED:  # ventana perdida, juego cerrado, inicio de sesión...
            return self._fatal(result.reason)
        return self._count_failure(RECOVERY_NAME, result, cooldown=False)

    # ------------------------------------------------------------------ elección

    def _choose(self, view: SessionView) -> tuple[Task | None, list[str]]:
        ctx = self.ctx
        skipped: list[str] = []
        for task in self.tasks:
            if not ctx.settings.enabled(task.name):
                skipped.append(f"{task.name}: desactivada")
                continue
            if not ctx.cooldowns.ready(self._cd(task.name)):
                skipped.append(f"{task.name}: en cooldown {ctx.cooldowns.remaining(self._cd(task.name)):.0f}s")
                continue
            ask = getattr(task, "should_run", None)
            decision = ask(ctx, view) if ask is not None else ShouldRun.yes()
            if decision.proceed:
                return task, skipped
            skipped.append(f"{task.name}: {decision.reason}")
        return None, skipped

    def _idle(self, skipped: list[str]) -> None:
        ctx = self.ctx
        seconds = idle_wait(self.policy, ctx.rng)
        reason = "; ".join(skipped) or "no hay tareas"
        ctx.events.publish(Idle(seconds, reason))
        ctx.events.publish(StateChanged("idle"))
        ctx.wait_until(lambda: ctx.pause.requested, seconds, 0.5)  # la pausa corta la espera
        ctx.events.publish(StateChanged("running"))

    # ------------------------------------------------------------------ ejecución

    def _execute(self, task: Task) -> SessionResult | None:
        ctx = self.ctx
        ctx.arm_watchdog(self.policy.inactivity_s)
        try:
            result = run_task(task, ctx)
        finally:
            ctx.disarm_watchdog()
        self._tasks_run += 1
        if self.loot is not None:
            self.loot.checkpoint()
        if result.outcome is Outcome.STOPPED:
            return SessionResult("parada solicitada", False, self._tasks_run)
        if result.timed_out:
            self._recover_after_timeout()

        name = task.name
        policy, cooldowns = self.policy, ctx.cooldowns
        if result.outcome is Outcome.DONE:
            self.failures[name] = 0
        elif result.outcome is Outcome.NOTHING_TO_DO:
            if result.exhausted:
                cooldowns.start(self._cd(name), policy.exhausted_cooldown_s)
                if name == WALLS_TASK:
                    cooldowns.start(WALLS_COMPLETE_KEY, policy.exhausted_cooldown_s)
            else:
                cooldowns.start(self._cd(name), policy.nothing_cooldown_s)
        elif result.outcome is Outcome.BLOCKED:
            ctx.events.publish(Warning(f"{name} bloqueada: {result.reason}"))
            cooldowns.start(self._cd(name), policy.blocked_cooldown_s)
        else:  # FAILED, NEEDS_RECOVERY
            retry = bool(getattr(task, "retry_without_cooldown", False))
            return self._count_failure(name, result, cooldown=not retry)
        return None

    def _count_failure(self, name: str, result: TaskResult, cooldown: bool) -> SessionResult | None:
        ctx, policy = self.ctx, self.policy
        n = self.failures.get(name, 0) + 1
        self.failures[name] = n
        ctx.events.publish(Warning(f"{name} falló ({n}/{policy.max_consecutive_failures}): {result.reason}"))
        if n >= policy.max_consecutive_failures:
            return self._fatal(f"«{name}» falló {n} veces seguidas: {result.reason}")
        if cooldown:
            ctx.cooldowns.start(self._cd(name), failure_cooldown(policy, n))
        return None

    def _fatal(self, reason: str) -> SessionResult:
        self.ctx.events.publish(Error(reason))
        return SessionResult(reason, True, self._tasks_run)

    @staticmethod
    def _cd(name: str) -> str:
        return f"session:{name}"

    # ------------------------------------------------------------------ dejar el juego en estado seguro

    def _recover_after_timeout(self) -> None:
        """Tras 4 minutos sin avanzar lo más probable es el resumen del combate (o la aldea enemiga):
        se sale con «Return Home», «End Battle» o «Surrender»."""
        cfg = replace(self.recovery, allow_surrender=True)
        RecoverScreen(self.classifier, cfg).run(self.ctx)

    def _safe_exit(self) -> None:
        """Al terminar la sesión: soltar la entrada, esperar a que acabe un combate en curso (sin rendirse)
        y cerrar lo que haya abierto."""
        ctx = self.ctx
        with ctx.ignoring_stop():
            try:
                if ctx.device.held:
                    ctx.device.release()
                if self._screen() is Screen.BATTLE:
                    ctx.events.publish(Warning("hay un combate en curso: se espera a que termine"))
                    ctx.wait_until(
                        lambda: self._screen() in (Screen.RESULT, Screen.MAIN_VILLAGE), BATTLE_END_WAIT_S, 2.0
                    )
                RecoverScreen(self.classifier, replace(self.recovery, allow_surrender=False)).run(ctx)
            except Exception as exc:  # el estado seguro es un esfuerzo: nunca debe tapar la causa de la parada
                ctx.events.publish(Warning(f"no se pudo dejar el juego en la aldea al terminar: {exc}"))

    def _screen(self) -> Screen:
        return self.classifier.classify(self.ctx.device.capture().image).screen


def default_tasks(
    policy: SessionPolicy | None = None,
    on_attack: Callable[[], None] | None = None,
) -> list[Task]:
    """Las tareas de la aldea principal con el cableado de la sesión (los muros usan el último constructor)."""
    from ..policy.walls import WallSettings
    from .build_new import BuildNewTask
    from .farm_window import FarmWindowTask
    from .upgrade import UpgradeTask
    from .walls import WallBatchTask, WallConfig, WallInputs

    # Los muros solo gastan oro con la reserva (#32) conocida y vigente; sin ella gastan solo elixir.
    walls = WallBatchTask(
        WallConfig(settings=WallSettings(reserve_builder_for_walls=True)),
        inputs=lambda ctx: WallInputs(gold_floor=ctx.gold_reserve.floor(ctx.clock.now())),
    )
    return [BuildNewTask(), UpgradeTask(), walls, FarmWindowTask(policy=policy, on_attack=on_attack)]


__all__ = ["Orchestrator", "SessionResult", "default_tasks"]
