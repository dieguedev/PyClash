"""Registro de entradas con ventana de aviso (issue #43): `python -m pyclash.interface.input_recorder`.

Requiere el extra `record` (pynput). Pegamento fino: ratón global y teclas de control (pynput), ventana del
juego (`GooglePlayGamesDevice`) y un aviso en Tk. La lógica está en `runtime/input_log.py`.

Teclas de control (globales): F9 empieza/para, F10 pone una marca. Nada más del teclado se escucha.
"""

from __future__ import annotations

import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from ..device.gpg import GooglePlayGamesDevice
from ..runtime.clock import SystemClock
from ..runtime.input_log import FileSink, InputLog, InputLogError, WindowState, session_filename

DEFAULT_CAPTURE_DIR = Path("captures_raw")  # ignorado por git: los registros llevan datos personales
TOGGLE_KEY = "<f9>"
MARK_KEY = "<f10>"


def build_log(device: GooglePlayGamesDevice, out_dir=DEFAULT_CAPTURE_DIR) -> InputLog:
    def window() -> WindowState | None:
        state = device.input_window_state()
        return None if state is None else WindowState(*state)

    return InputLog(
        SystemClock(),
        datetime.now,
        window,
        lambda when, th: FileSink(out_dir / session_filename(when, th)),
    )


class RecorderApp:
    def __init__(self) -> None:
        from pynput import keyboard, mouse  # import tardío: el extra es opcional

        self._log = build_log(GooglePlayGamesDevice())
        self._commands: queue.Queue[str] = queue.Queue()
        self._root = tk.Tk()
        self._root.title("Registro de entradas")
        self._root.attributes("-topmost", True)
        self._level = tk.IntVar(value=9)
        self._status = tk.Label(self._root, text="Parado", width=34, pady=10)
        self._status.pack(fill="x")
        row = tk.Frame(self._root)
        row.pack(pady=4)
        tk.Label(row, text="Ayuntamiento").pack(side="left")
        self._spin = ttk.Spinbox(row, from_=2, to=10, width=4, textvariable=self._level)
        self._spin.pack(side="left", padx=6)
        self._button = tk.Button(self._root, text=f"Empezar ({TOGGLE_KEY.strip('<>').upper()})", command=lambda: self._commands.put("toggle"))
        self._button.pack(pady=4)
        tk.Label(self._root, text=f"{MARK_KEY.strip('<>').upper()}: poner marca", fg="gray").pack(pady=(0, 6))
        self._hotkeys = keyboard.GlobalHotKeys(
            {TOGGLE_KEY: lambda: self._commands.put("toggle"), MARK_KEY: lambda: self._commands.put("mark")}
        )
        self._mouse = mouse.Listener(
            on_move=self._log.move,
            on_click=lambda x, y, button, pressed: self._log.button(button.name, pressed, int(x), int(y)),
            on_scroll=lambda x, y, dx, dy: self._log.scroll(int(x), int(y), dx, dy),
        )
        self._root.protocol("WM_DELETE_WINDOW", self._close)

    def _toggle(self) -> None:
        try:
            self._log.toggle(int(self._level.get()))
        except InputLogError as exc:
            self._status.config(text=str(exc), bg="#d0d0d0", fg="black")
            return
        self._refresh()

    def _refresh(self) -> None:
        if self._log.recording:
            self._status.config(text="● GRABANDO", bg="#c62828", fg="white")
            self._button.config(text="Parar (F9)")
            self._spin.state(["disabled"])
        else:
            self._status.config(text="Parado", bg="#d0d0d0", fg="black")
            self._button.config(text="Empezar (F9)")
            self._spin.state(["!disabled"])

    def _poll(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            if command == "toggle":
                self._toggle()
            else:
                self._log.mark()
        self._root.after(50, self._poll)

    def _close(self) -> None:
        self._log.stop()
        self._hotkeys.stop()
        self._mouse.stop()
        self._root.destroy()

    def run(self) -> None:
        self._hotkeys.start()
        self._mouse.start()
        self._poll()
        self._root.mainloop()


def main() -> None:
    RecorderApp().run()


if __name__ == "__main__":
    main()
