"""Lógica del botón «Probar»: captura una vez y explica qué ve (o por qué no ve nada)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import geometry
from .base import BlackCapture, CaptureFailed, Device, DeviceError, Frame, WindowMinimized, WindowNotFound


class ProbeStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    MINIMIZED = "minimized"
    BLACK = "black"
    FAILED = "failed"


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    message: str
    frame: Frame | None = None

    @property
    def ok(self) -> bool:
        return self.status is ProbeStatus.OK


def probe(device: Device) -> ProbeResult:
    try:
        frame = device.capture()
    except WindowNotFound as e:
        return ProbeResult(ProbeStatus.NOT_FOUND, f"{e} Elige la ventana a mano en la lista.")
    except WindowMinimized as e:
        return ProbeResult(ProbeStatus.MINIMIZED, f"{e} ¿Restaurar la ventana?")
    except BlackCapture as e:
        return ProbeResult(ProbeStatus.BLACK, f"{e} ¿Está minimizada o tapada? Prueba a restaurarla.")
    except (CaptureFailed, DeviceError) as e:
        return ProbeResult(ProbeStatus.FAILED, str(e))
    w, h = frame.viewport
    return ProbeResult(ProbeStatus.OK, f"Captura {w}×{h} — {geometry.aspect_label(w, h)}", frame)
