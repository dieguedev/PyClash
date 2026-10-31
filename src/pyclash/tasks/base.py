"""Interfaz de tarea y resultado tipado."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..runtime.context import Context
from ..runtime.events import TaskFinished, TaskStarted
from ..runtime.stop import StopRequested


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

    @staticmethod
    def done() -> "TaskResult":
        return TaskResult(Outcome.DONE)

    @staticmethod
    def nothing_to_do(reason: str = "") -> "TaskResult":
        return TaskResult(Outcome.NOTHING_TO_DO, reason)

    @staticmethod
    def blocked(reason: str) -> "TaskResult":
        return TaskResult(Outcome.BLOCKED, reason)

    @staticmethod
    def failed(reason: str) -> "TaskResult":
        return TaskResult(Outcome.FAILED, reason)

    @staticmethod
    def needs_recovery(reason: str) -> "TaskResult":
        return TaskResult(Outcome.NEEDS_RECOVERY, reason)


class Task(Protocol):
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
    ctx.events.publish(TaskFinished(task.name, result.outcome.value, result.reason))
    return result
