"""Interfaz de tarea y resultado tipado."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..runtime.context import Context
from ..screens.village import VillageState
from ..runtime.events import TaskFinished, TaskStarted
from ..runtime.stop import StopRequested, TaskTimeout


class Outcome(str, Enum):
    DONE = "done"
    NOTHING_TO_DO = "nothing_to_do"
    BLOCKED = "blocked"
    FAILED = "failed"
    NEEDS_RECOVERY = "needs_recovery"
    STOPPED = "stopped"


@dataclass(frozen=True)
class TaskResult:
    outcome: Outcome
    reason: str = ""
    exhausted: bool = False  # NOTHING_TO_DO para siempre (p. ej. no queda ningún muro por mejorar)
    timed_out: bool = False  # FAILED porque el cronómetro de inactividad venció

    @staticmethod
    def done() -> "TaskResult":
        return TaskResult(Outcome.DONE)

    @staticmethod
    def nothing_to_do(reason: str = "", exhausted: bool = False) -> "TaskResult":
        return TaskResult(Outcome.NOTHING_TO_DO, reason, exhausted)

    @staticmethod
    def blocked(reason: str) -> "TaskResult":
        return TaskResult(Outcome.BLOCKED, reason)

    @staticmethod
    def failed(reason: str) -> "TaskResult":
        return TaskResult(Outcome.FAILED, reason)

    @staticmethod
    def needs_recovery(reason: str) -> "TaskResult":
        return TaskResult(Outcome.NEEDS_RECOVERY, reason)


@dataclass(frozen=True)
class ShouldRun:
    """Respuesta barata a «¿procede esta tarea ahora?», solo con el estado ya leído."""

    proceed: bool
    reason: str = ""

    @staticmethod
    def yes(reason: str = "") -> "ShouldRun":
        return ShouldRun(True, reason)

    @staticmethod
    def no(reason: str) -> "ShouldRun":
        return ShouldRun(False, reason)


@dataclass(frozen=True)
class SessionView:
    """Lo que el orquestador ya leyó de la aldea en este ciclo y reparte a todas las tareas."""

    village: VillageState


class Task(Protocol):
    """Una tarea. `priority` (menor = antes) y `should_run` son opcionales para el orquestador:
    sin ellos la tarea va la última y siempre procede. `retry_without_cooldown`: si falla, se
    reintenta sin descansar (los fallos seguidos siguen contando)."""

    name: str

    def run(self, ctx: Context) -> TaskResult: ...


def run_task(task: Task, ctx: Context) -> TaskResult:
    """Ejecuta una tarea publicando sus eventos y convirtiendo la parada en resultado."""
    ctx.events.publish(TaskStarted(task.name))
    try:
        ctx.check_stop()
        result = task.run(ctx)
    except StopRequested:
        result = TaskResult(Outcome.STOPPED, "parada solicitada")
    except TaskTimeout as exc:
        result = TaskResult(Outcome.FAILED, f"{exc}: la tarea dejó de avanzar", timed_out=True)
    ctx.events.publish(TaskFinished(task.name, result.outcome.value, result.reason))
    return result
