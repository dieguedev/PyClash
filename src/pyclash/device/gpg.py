"""Backend de ventana de Google Play Games (Windows): detección, captura, clic y scroll por mensajes de ventana."""

from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..paths import user_data_dir
from ..runtime.clock import Clock, SystemClock
from . import geometry
from .base import (
    BlackCapture,
    Capabilities,
    CaptureFailed,
    DeviceError,
    Frame,
    Rect,
    WindowMinimized,
    WindowNotFound,
)

SURFACE_CLASS_PREFIX = "CROSVM"

_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_WM_MOUSEWHEEL = 0x020A
_MK_LBUTTON = 0x0001
_WHEEL_DELTA = 120
_PW_RENDERFULLCONTENT = 2
_SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE = 0x1, 0x4, 0x10
_SW_RESTORE = 9
_GA_ROOT = 2
_MONITOR_DEFAULTTONEAREST = 2
_SM_XVIRTUALSCREEN = 76
_OFFSCREEN_GAP = 200  # px a la izquierda del monitor más a la izquierda, para que no asome ningún borde
_RESIZE_ATTEMPTS = 3  # la superficie puede quedar unos px fuera (barra de título, DPI): se vuelve a medir
_RESIZE_SETTLE_SECONDS = 0.6

HIDDEN_MARKER = "hidden_window.json"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)


# Sin argtypes, ctypes trunca los handles de 64 bits.
_H, _P = wintypes.HWND, ctypes.POINTER
_sig(_user32.GetWindowRect, wintypes.BOOL, _H, _P(wintypes.RECT))
_sig(_user32.GetClassNameW, ctypes.c_int, _H, wintypes.LPWSTR, ctypes.c_int)
_sig(_user32.GetWindowTextLengthW, ctypes.c_int, _H)
_sig(_user32.GetWindowTextW, ctypes.c_int, _H, wintypes.LPWSTR, ctypes.c_int)
_sig(_user32.EnumWindows, wintypes.BOOL, _WNDENUMPROC, wintypes.LPARAM)
_sig(_user32.EnumChildWindows, wintypes.BOOL, _H, _WNDENUMPROC, wintypes.LPARAM)
_sig(_user32.GetForegroundWindow, _H)
_sig(_user32.IsWindow, wintypes.BOOL, _H)
_sig(_user32.IsWindowVisible, wintypes.BOOL, _H)
_sig(_user32.IsIconic, wintypes.BOOL, _H)
_sig(_user32.IsZoomed, wintypes.BOOL, _H)
_sig(_user32.ShowWindow, wintypes.BOOL, _H, ctypes.c_int)
_sig(_user32.GetAncestor, _H, _H, wintypes.UINT)
_sig(_user32.SetWindowPos, wintypes.BOOL, _H, _H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
_sig(_user32.MonitorFromWindow, wintypes.HANDLE, _H, wintypes.DWORD)
_sig(_user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HANDLE, _P(_MONITORINFO))
_sig(_user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_sig(_user32.GetWindowDC, wintypes.HDC, _H)
_sig(_user32.ReleaseDC, ctypes.c_int, _H, wintypes.HDC)
_sig(_user32.PrintWindow, wintypes.BOOL, _H, wintypes.HDC, wintypes.UINT)
_sig(_user32.SendMessageW, ctypes.c_ssize_t, _H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(_user32.ClientToScreen, wintypes.BOOL, _H, _P(wintypes.POINT))
_sig(_user32.GetClientRect, wintypes.BOOL, _H, _P(wintypes.RECT))
_sig(_gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(_gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_sig(_gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(_gdi32.GetDIBits, ctypes.c_int, wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
     ctypes.c_void_p, _P(_BITMAPINFOHEADER), wintypes.UINT)
_sig(_gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_sig(_gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _find_descendant(root: int, predicate) -> tuple[int, str]:
    """Búsqueda en profundidad del primer descendiente cuya clase cumple `predicate`."""
    found = {"hwnd": 0, "cls": ""}

    def visit(hwnd, _lparam):
        cls = _class_name(hwnd)
        if predicate(cls):
            found["hwnd"], found["cls"] = hwnd, cls
            return False
        _user32.EnumChildWindows(hwnd, _WNDENUMPROC(visit), 0)
        return not found["hwnd"]

    _user32.EnumChildWindows(root, _WNDENUMPROC(visit), 0)
    return found["hwnd"], found["cls"]


def _is_surface_class(cls: str) -> bool:
    return cls.upper().startswith(SURFACE_CLASS_PREFIX)


@dataclass(frozen=True)
class WindowInfo:
    """Ventana de primer nivel visible y, si la tiene, su superficie de juego."""

    top_hwnd: int
    title: str
    top_class: str
    surface_hwnd: int = 0
    surface_class: str = ""

    @property
    def is_game(self) -> bool:
        return self.surface_hwnd != 0

    @property
    def label(self) -> str:
        title = self.title or "(sin título)"
        return f"{title} — superficie {self.surface_class}" if self.is_game else f"{title} — sin superficie de juego"


def list_windows() -> list[WindowInfo]:
    """Ventanas visibles con título; primero las que tienen superficie de juego."""
    out: list[WindowInfo] = []

    def visit(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd):
            title = _title(hwnd)
            if title:
                top_class = _class_name(hwnd)
                s_hwnd, s_cls = _find_descendant(hwnd, _is_surface_class)
                if not s_hwnd and _is_surface_class(top_class):
                    s_hwnd, s_cls = hwnd, top_class
                out.append(WindowInfo(hwnd, title, top_class, s_hwnd or 0, s_cls))
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    out.sort(key=lambda w: (not w.is_game, w.title.lower()))
    return out


def write_hidden_marker(path: Path, x: int, y: int) -> None:
    path.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")


def read_hidden_marker(path: Path) -> tuple[int, int]:
    """Posición guardada al ocultar; (0, 0) si el marcador está dañado."""
    try:
        pos = json.loads(path.read_text(encoding="utf-8"))
        return int(pos["x"]), int(pos["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0, 0


class GooglePlayGamesDevice:
    """Implementa `Device` sobre la ventana de Google Play Games.

    `capture()` entrega solo el área de juego (sin barras negras); `tap`, `swipe` y `scroll`
    reciben coordenadas de ese fotograma recortado.
    """

    def __init__(self, clock: Clock | None = None, title_hint: str = "Clash of Clans", data_dir: Path | None = None) -> None:
        self._clock = clock or SystemClock()
        self._title_hint = title_hint.lower()
        self._data_dir = data_dir
        self._pinned: WindowInfo | None = None
        self._hwnd = 0
        self._area: Rect | None = None
        self._held = False
        self._last_pos = (0, 0)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            _user32.SetProcessDPIAware()

    # --- selección de ventana -------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(hold=True, swipe=True, scroll=True)

    @property
    def held(self) -> bool:
        return self._held

    def windows(self) -> list[WindowInfo]:
        return list_windows()

    def select(self, info: WindowInfo | None) -> None:
        """Fija la ventana elegida a mano; `None` vuelve a la detección automática."""
        self._pinned = info
        self._hwnd = 0
        self._area = None

    def _resolve(self) -> int:
        if self._hwnd and _user32.IsWindow(self._hwnd):
            return self._hwnd
        self._hwnd = 0
        for w in list_windows():
            if not w.is_game:
                continue
            if self._pinned is not None:
                if w.title.strip().lower() != self._pinned.title.strip().lower() or w.top_class != self._pinned.top_class:
                    continue
            elif self._title_hint not in w.title.lower():
                continue
            self._hwnd = w.surface_hwnd
            break
        return self._hwnd

    def _require(self) -> int:
        hwnd = self._resolve()
        if not hwnd:
            raise WindowNotFound("No se encuentra la ventana de Google Play Games con el juego abierto.")
        return hwnd

    def _top(self) -> int:
        hwnd = self._require()
        return _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd

    def input_window_state(self) -> tuple[Rect, bool] | None:
        """Área cliente de la superficie en pantalla y si su ventana tiene el foco; `None` si no hay ventana.

        Solo lee: no mueve nada. Lo usa el registro de entradas (#43) para situar los clics del jugador.
        """
        hwnd = self._resolve()
        if not hwnd:
            return None
        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not _user32.GetClientRect(hwnd, ctypes.byref(client)) or not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        if client.right <= 0 or client.bottom <= 0:
            return None
        top = _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd
        focused = _user32.GetForegroundWindow() == top
        return Rect(origin.x, origin.y, client.right, client.bottom), focused

    def is_minimized(self) -> bool:
        return bool(_user32.IsIconic(self._top()))

    def restore_window(self) -> None:
        _user32.ShowWindow(self._top(), _SW_RESTORE)

    # --- captura --------------------------------------------------------------

    def _grab(self, hwnd: int) -> np.ndarray:
        left, top, right, bottom = _rect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            raise CaptureFailed("La ventana tiene un tamaño no válido.")
        hdc = _user32.GetWindowDC(hwnd)
        mem = _gdi32.CreateCompatibleDC(hdc)
        bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
        try:
            old = _gdi32.SelectObject(mem, bmp)
            _user32.PrintWindow(hwnd, mem, _PW_RENDERFULLCONTENT)
            _gdi32.SelectObject(mem, old)  # GetDIBits exige que el bitmap no esté seleccionado
            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth, bmi.biHeight = w, -h
            bmi.biPlanes, bmi.biBitCount = 1, 32
            buf = np.empty((h, w, 4), np.uint8)
            if not _gdi32.GetDIBits(hdc, bmp, 0, h, buf.ctypes.data, ctypes.byref(bmi), 0):
                raise CaptureFailed("No se pudo leer la captura de la ventana.")
        finally:
            _gdi32.DeleteObject(bmp)
            _gdi32.DeleteDC(mem)
            _user32.ReleaseDC(hwnd, hdc)
        rgb = buf[:, :, [2, 1, 0]]  # BGRA -> RGB
        # Si la superficie es la ventana de primer nivel, la captura incluye el marco (barra de título,
        # bordes). Los mensajes de ratón usan coordenadas del área cliente, así que se recorta a ella.
        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if _user32.GetClientRect(hwnd, ctypes.byref(client)) and _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            ox, oy = origin.x - left, origin.y - top
            if 0 <= ox and 0 <= oy and ox + client.right <= w and oy + client.bottom <= h and client.right > 0 and client.bottom > 0:
                rgb = rgb[oy : oy + client.bottom, ox : ox + client.right]
        return np.ascontiguousarray(rgb)

    def capture(self) -> Frame:
        hwnd = self._require()
        if self.is_minimized():
            raise WindowMinimized("La ventana está minimizada: Google Play Games no se puede capturar así.")
        image = self._grab(hwnd)
        area = geometry.play_area(image)
        if area is None:
            raise BlackCapture("La captura es negra.")
        self._area = area
        cropped = np.ascontiguousarray(image[area.y : area.y + area.h, area.x : area.x + area.w])
        return Frame(cropped, (area.w, area.h), self._clock.now(), {"window_size": image.shape[1::-1], "crop": area})

    # --- entrada --------------------------------------------------------------

    def _window_point(self, x: int, y: int) -> tuple[int, int]:
        """Pasa coordenadas del fotograma recortado a coordenadas de la ventana."""
        if self._area is None:
            self.capture()
        area = self._area
        assert area is not None
        x = min(max(int(x), 0), area.w - 1)
        y = min(max(int(y), 0), area.h - 1)
        return x + area.x, y + area.y

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return (y << 16) | (x & 0xFFFF)

    def _send(self, msg: int, wparam: int, x: int, y: int) -> None:
        _user32.SendMessageW(self._require(), msg, wparam, self._lparam(x, y))

    def tap(self, x: int, y: int) -> None:
        wx, wy = self._window_point(x, y)
        self._send(_WM_LBUTTONDOWN, _MK_LBUTTON, wx, wy)
        self._send(_WM_LBUTTONUP, 0, wx, wy)

    def press(self, x: int, y: int) -> None:
        wx, wy = self._window_point(x, y)
        self._send(_WM_LBUTTONDOWN, _MK_LBUTTON, wx, wy)
        self._held, self._last_pos = True, (wx, wy)

    def move(self, x: int, y: int) -> None:
        if not self._held:
            raise DeviceError("move sin pulsación previa (falta press)")
        wx, wy = self._window_point(x, y)
        self._send(_WM_MOUSEMOVE, _MK_LBUTTON, wx, wy)
        self._last_pos = (wx, wy)

    def release(self) -> None:
        if self._held:
            self._send(_WM_LBUTTONUP, 0, *self._last_pos)
        self._held = False

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        steps = max(2, int(duration / 0.01))
        self.press(x1, y1)
        try:
            for i in range(1, steps + 1):
                t = i / steps
                wx, wy = self._window_point(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
                self._send(_WM_MOUSEMOVE, _MK_LBUTTON, wx, wy)
                self._last_pos = (wx, wy)
                self._clock.sleep(duration / steps)
        finally:
            self.release()

    def scroll(self, x: int, y: int, amount: int) -> None:
        """`amount` > 0 desplaza hacia arriba, < 0 hacia abajo; una muesca por unidad."""
        wx, wy = self._window_point(x, y)
        pt = wintypes.POINT(wx, wy)
        if not _user32.ClientToScreen(self._require(), ctypes.byref(pt)):
            pt = wintypes.POINT(wx, wy)
        delta = _WHEEL_DELTA if amount > 0 else -_WHEEL_DELTA
        wparam = (delta & 0xFFFF) << 16
        lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
        for _ in range(abs(amount)):
            _user32.SendMessageW(self._require(), _WM_MOUSEWHEEL, wparam, lparam)
            self._clock.sleep(0.05)

    # --- ventana: 16:9, ocultar y mostrar -------------------------------------

    def resize_to_16_9(self) -> tuple[bool, tuple[int, int] | None, str]:
        """Redimensiona la ventana para que la superficie sea 16:9. Devuelve (ok, tamaño, motivo)."""
        if not self._resolve():
            return False, None, "not_found"
        hwnd = self._hwnd
        l, t, r, b = _rect(hwnd)
        surface = (r - l, b - t)
        if geometry.is_16_9(*surface):
            return True, surface, "already_16_9"
        if self.is_minimized():
            return False, None, "minimized"
        top = self._top()
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        _user32.GetMonitorInfoW(_user32.MonitorFromWindow(top, _MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
        work = (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)
        mon = (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom)
        if _user32.IsZoomed(top):
            _user32.ShowWindow(top, _SW_RESTORE)
            self._clock.sleep(_RESIZE_SETTLE_SECONDS)
        for attempt in range(_RESIZE_ATTEMPTS):
            outer = _rect(top)
            if attempt == 0 and outer == mon:
                return False, surface, "fullscreen"  # sin marco que medir; SetWindowPos se ignora
            x, y, w, h = geometry.fit_16_9(surface, outer, work)
            _user32.SetWindowPos(top, None, x, y, w, h, _SWP_NOZORDER | _SWP_NOACTIVATE)
            self._clock.sleep(_RESIZE_SETTLE_SECONDS)
            l, t, r, b = _rect(hwnd)
            surface = (r - l, b - t)
            if abs(surface[0] / surface[1] - 16 / 9) < 0.005:
                break
        if not geometry.is_16_9(*surface):
            return False, surface, "not_16_9_after_resize"
        self._area = None
        return True, surface, "ok"

    def _marker(self) -> Path:
        return (self._data_dir or user_data_dir()) / HIDDEN_MARKER

    def hide_offscreen(self) -> tuple[bool, str]:
        """Aparca la ventana a la izquierda de todos los monitores (sigue renderizando, a diferencia de minimizar).

        La posición anterior se guarda para que `show_back` —o el siguiente arranque— la recupere aunque la app se cierre.
        """
        if not self._resolve():
            return False, "not_found"
        if self.is_minimized():
            return False, "minimized"
        top = self._top()
        l, t, r, _ = _rect(top)
        left_edge = _user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        if r <= left_edge:
            return True, "already_hidden"
        write_hidden_marker(self._marker(), l, t)
        x = left_edge - (r - l) - _OFFSCREEN_GAP
        _user32.SetWindowPos(top, None, x, t, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
        return True, "ok"

    def has_hidden_marker(self) -> bool:
        return self._marker().is_file()

    def show_back(self) -> tuple[bool, str]:
        marker = self._marker()
        if not marker.is_file():
            return False, "not_hidden"
        if not self._resolve():
            return False, "not_found"  # el marcador se conserva: se reintenta con el juego abierto
        x, y = read_hidden_marker(marker)
        _user32.SetWindowPos(self._top(), None, x, y, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
        marker.unlink(missing_ok=True)
        return True, "ok"
