"""Ajustes de la sesión que el usuario puede cambiar en vivo (se leen en cada límite de tarea)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionSettings:
    disabled_tasks: frozenset[str] = frozenset()  # nombres de tarea desactivados (interruptores)
    excluded: frozenset[str] = frozenset()  # etiquetas de exclusión de mejoras (p. ej. EXCLUDE_HEROES)

    def enabled(self, task_name: str) -> bool:
        return task_name not in self.disabled_tasks
