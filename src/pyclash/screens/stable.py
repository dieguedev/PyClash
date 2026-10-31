"""Lectura estable: un valor solo se da por bueno si coincide en capturas consecutivas.

Las animaciones y ventanas emergentes corrompen lecturas sueltas, pero la corrupción es
aleatoria: varias lecturas idénticas seguidas son fiables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, Mapping, TypeVar

from ..device.base import Frame
from ..runtime.context import Context

T = TypeVar("T")


@dataclass(frozen=True)
class Stable(Generic[T]):
    value: T | None
    reliable: bool
    captures: int  # capturas hechas hasta dar el campo por bueno (o hasta agotar los intentos)


def stable_fields(
    ctx: Context,
    readers: Mapping[str, Callable[[Frame], Any]],
    capture: Callable[[], Frame] | None = None,
    required: int = 3,
    attempts: int = 6,
    interval: float = 0.35,
) -> dict[str, Stable]:
    """Lee varios campos de las mismas capturas; cada uno se asienta por separado.

    Un campo se asienta tras `required` lecturas iguales y consecutivas. Una lectura `None`
    (ilegible) o distinta reinicia su racha, así que un valor suelto como un «7» entre
    «2» no cuenta. Se captura hasta asentar todos o agotar `attempts`; un campo sin acuerdo
    queda con `value=None` y `reliable=False`. La parada se comprueba entre capturas.
    """
    if required < 1 or attempts < required:
        raise ValueError("attempts debe ser >= required >= 1")
    grab = capture or ctx.device.capture
    streak: dict[str, tuple[Any, int]] = {name: (None, 0) for name in readers}
    settled: dict[str, Any] = {}
    done_at: dict[str, int] = {}
    used = 0
    for i in range(attempts):
        if i:
            ctx.sleep(interval)
        ctx.check_stop()
        frame = grab()
        used = i + 1
        for name, read in readers.items():
            if name in settled:
                continue
            value = read(frame)
            if value is None:
                streak[name] = (None, 0)
                continue
            prev, n = streak[name]
            n = n + 1 if value == prev else 1
            streak[name] = (value, n)
            if n >= required:
                settled[name] = value
                done_at[name] = used
        if len(settled) == len(readers):
            break
    return {
        name: Stable(settled.get(name), name in settled, done_at.get(name, used)) for name in readers
    }


def stable_read(
    ctx: Context,
    read: Callable[[Frame], T | None],
    capture: Callable[[], Frame] | None = None,
    required: int = 3,
    attempts: int = 6,
    interval: float = 0.35,
) -> Stable[T]:
    """`stable_fields` para un único campo."""
    return stable_fields(ctx, {"value": read}, capture, required, attempts, interval)["value"]
