"""Cambio temporal del modo de pantalla principal a 16:9, con restauración pendiente.

Google Play Games fija la proporción del juego a la de la pantalla en la que se lanza. En un monitor
21:9 el juego sale 21:9 y no se puede leer. Solución: pasar la pantalla a un modo 16:9, relanzar el
juego y volver al modo original; el juego en marcha conserva el 16:9.

El modo original se guarda en disco antes de cambiar, de modo que `restore_if_pending` lo recupera al
arrancar aunque la app se cerrara a la fuerza mientras la pantalla seguía cambiada.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from ..paths import user_data_dir
from . import geometry

RESTORE_FILE = "display_restore.devmode"

_ENUM_CURRENT_SETTINGS = -1
_DM_PELSWIDTH, _DM_PELSHEIGHT = 0x80000, 0x100000
_CDS_UPDATEREGISTRY, _CDS_TEST = 0x01, 0x02
_DISP_CHANGE_SUCCESSFUL = 0


class DEVMODE(ctypes.Structure):
    """DEVMODEW de Windows (variante de pantalla)."""

    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


def best_16_9_mode(modes: set[tuple[int, int]]) -> tuple[int, int] | None:
    """El mayor modo 16:9 de `modes`, o None."""
    candidates = [m for m in modes if geometry.is_16_9(*m, tolerance=0.02)]
    return max(candidates, key=lambda m: m[0] * m[1]) if candidates else None


class DisplayService:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir
        self._u = ctypes.windll.user32
        self._u.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODE)]
        self._u.ChangeDisplaySettingsExW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(DEVMODE), wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
        ]

    def _marker(self) -> Path:
        return (self._data_dir or user_data_dir()) / RESTORE_FILE

    def _current(self) -> DEVMODE | None:
        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        return dm if self._u.EnumDisplaySettingsW(None, _ENUM_CURRENT_SETTINGS, ctypes.byref(dm)) else None

    def current_size(self) -> tuple[int, int] | None:
        dm = self._current()
        return (int(dm.dmPelsWidth), int(dm.dmPelsHeight)) if dm else None

    def _all_modes(self) -> set[tuple[int, int]]:
        modes: set[tuple[int, int]] = set()
        i = 0
        while True:
            dm = DEVMODE()
            dm.dmSize = ctypes.sizeof(DEVMODE)
            if not self._u.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
                return modes
            modes.add((int(dm.dmPelsWidth), int(dm.dmPelsHeight)))
            i += 1

    def is_16_9(self) -> bool:
        size = self.current_size()
        return bool(size and geometry.is_16_9(*size, tolerance=0.02))

    def has_pending_restore(self) -> bool:
        return self._marker().is_file()

    def switch_to_16_9(self) -> tuple[bool, tuple[int, int] | None, str]:
        """Pasa la pantalla al mayor modo 16:9 guardando el actual. Devuelve (ok, tamaño, motivo)."""
        if self.is_16_9():
            return True, self.current_size(), "already_16_9"
        target = best_16_9_mode(self._all_modes())
        if target is None:
            return False, None, "no_16_9_mode"
        current = self._current()
        if current is None:
            return False, None, "read_failed"
        # Si no se puede guardar el modo original no se cambia nada: sin marcador no habría vuelta atrás.
        try:
            self._marker().write_bytes(bytes(current))
        except OSError:
            return False, None, "marker_failed"
        dm = DEVMODE()
        ctypes.memmove(ctypes.byref(dm), ctypes.byref(current), ctypes.sizeof(DEVMODE))
        dm.dmPelsWidth, dm.dmPelsHeight = target
        dm.dmFields = _DM_PELSWIDTH | _DM_PELSHEIGHT
        if self._u.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None, _CDS_TEST, None) != _DISP_CHANGE_SUCCESSFUL:
            self._marker().unlink(missing_ok=True)
            return False, None, "not_supported"
        if self._u.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None, _CDS_UPDATEREGISTRY, None) != _DISP_CHANGE_SUCCESSFUL:
            self._marker().unlink(missing_ok=True)
            return False, None, "change_failed"
        return True, target, "ok"

    def restore(self) -> tuple[bool, tuple[int, int] | None, str]:
        """Restaura el modo guardado por `switch_to_16_9`."""
        path = self._marker()
        if not path.is_file():
            return False, None, "nothing_to_restore"
        try:
            data = path.read_bytes()
        except OSError:
            return False, None, "bad_marker"
        if len(data) < ctypes.sizeof(DEVMODE):
            return False, None, "bad_marker"
        saved = DEVMODE()
        ctypes.memmove(ctypes.byref(saved), data, ctypes.sizeof(DEVMODE))
        size = (int(saved.dmPelsWidth), int(saved.dmPelsHeight))
        if self._u.ChangeDisplaySettingsExW(None, ctypes.byref(saved), None, _CDS_UPDATEREGISTRY, None) != _DISP_CHANGE_SUCCESSFUL:
            return False, size, "change_failed"
        path.unlink(missing_ok=True)
        return True, size, "ok"

    def restore_if_pending(self) -> bool:
        """Al arrancar: si quedó un cambio sin deshacer, devuelve la pantalla a su modo. True si restauró algo."""
        return self.has_pending_restore() and self.restore()[0]
