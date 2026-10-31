"""Registro de botín: ganancias entre lecturas del HUD con comprobación de cordura.

Función de estado pura (sin reloj, sin dispositivo, sin log): devuelve qué pasó y quien llama
decide si lo registra o lo avisa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Triplet = tuple[int, int, int]  # (oro, elixir, elixir oscuro)
Status = Literal["baseline", "ok", "discarded", "rebased"]


@dataclass(frozen=True)
class LootLimits:
    """Lo máximo que un único ataque puede dar. Configurable: no depende del nivel de Ayuntamiento."""

    max_gain_main: int = 3_000_000  # oro y elixir
    max_gain_dark: int = 50_000


@dataclass(frozen=True)
class LootUpdate:
    status: Status
    gain: Triplet  # ganancia contada con esta lectura
    discarded: tuple[str, ...] = ()  # recursos cuya lectura se descartó por inverosímil
    warnings: tuple[str, ...] = ()


@dataclass
class LootTracker:
    limits: LootLimits = field(default_factory=LootLimits)
    total: Triplet = (0, 0, 0)
    baseline: Triplet | None = None
    # Lecturas inverosímiles vistas por recurso: si la misma se repite, es real y se adopta.
    _suspect: list[int | None] = field(default_factory=lambda: [None, None, None])

    NAMES = ("oro", "elixir", "elixir oscuro")

    def update(self, reading: Triplet) -> LootUpdate:
        """Procesa una lectura estable del HUD.

        Por recurso: una subida plausible suma; una bajada (gasto en mejoras o muros) no suma y
        adopta la nueva base; una subida inverosímil se descarta SIN tocar la base, para que un
        fallo de OCR no envenene las siguientes comparaciones. Si esa misma cifra se repite en la
        lectura siguiente se toma por real (p. ej. un regalo) y pasa a ser la base, sin contarla.
        Un almacén ya lleno da delta 0, es decir, ganancia 0.
        """
        if self.baseline is None:
            self.baseline = reading
            return LootUpdate("baseline", (0, 0, 0))
        gains = [0, 0, 0]
        new_base = list(self.baseline)
        discarded: list[str] = []
        warnings: list[str] = []
        rebased = False
        for i, cur in enumerate(reading):
            delta = cur - self.baseline[i]
            cap = self.limits.max_gain_dark if i == 2 else self.limits.max_gain_main
            if delta <= cap:
                new_base[i] = cur
                gains[i] = max(0, delta)
                self._suspect[i] = None
            elif self._suspect[i] == cur:
                new_base[i] = cur
                self._suspect[i] = None
                rebased = True
                warnings.append(f"{self.NAMES[i]}: {cur} se repite; se toma como nueva base sin contar ganancia")
            else:
                self._suspect[i] = cur
                discarded.append(self.NAMES[i])
                warnings.append(
                    f"{self.NAMES[i]}: +{delta} excede el máximo por ataque ({cap}); lectura descartada"
                )
        self.baseline = (new_base[0], new_base[1], new_base[2])
        gain = (gains[0], gains[1], gains[2])
        self.total = (self.total[0] + gain[0], self.total[1] + gain[1], self.total[2] + gain[2])
        status: Status = "discarded" if discarded else ("rebased" if rebased else "ok")
        return LootUpdate(status, gain, tuple(discarded), tuple(warnings))

    def reset(self) -> None:
        self.total = (0, 0, 0)
        self.baseline = None
        self._suspect = [None, None, None]
