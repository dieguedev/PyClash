"""Registro de entradas con ventana de aviso (issue #43): `python -m pyclash.interface.input_recorder`.

Requiere el extra `record` (pynput). Pegamento fino: ratón global y teclas de control (pynput), ventana del
juego (`GooglePlayGamesDevice`) y un aviso en Tk. La lógica está en `runtime/input_log.py`.

Teclas de control (globales): F9 empieza/para, F10 pone una marca. Nada más del teclado se escucha.

Con `--obs` se conecta al WebSocket de OBS y cada evento lleva su tiempo de vídeo (issue #44); la contraseña va
en `--obs-password` o en la variable de entorno `PYCLASH_OBS_PASSWORD`. Sin `--obs` funciona como antes.
"""

from __future__ import annotations

import argparse
import os
import queue
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from ..device.gpg import GooglePlayGamesDevice
from ..runtime.clock import SystemClock
from ..runtime.input_log import FileSink, InputLog, InputLogError, WindowState, session_filename
from ..runtime.obs_link import PASSWORD_ENV, ObsError
from .obs_socket import DEFAULT_HOST, DEFAULT_PORT, listen_in_thread, open_session


@dataclass(frozen=True)
class ObsOptions:
    host: str
    port: int
    password: str | None

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
    def __init__(self, obs: ObsOptions | None = None) -> None:
        from pynput import keyboard, mouse  # import tardío: el extra es opcional

        self._log = build_log(GooglePlayGamesDevice())
        self._obs_options = obs
        self._obs_session = None
        self._commands: queue.Queue[str] = queue.Queue()
        self._notices: queue.Queue[str] = queue.Queue()  # avisos de OBS, llegan desde otros hilos
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
        self._obs_label = tk.Label(self._root, text="OBS: no usado", fg="gray", wraplength=260, justify="left")
        self._obs_label.pack(padx=8, pady=(0, 8))
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
        while True:
            try:
                self._obs_label.config(text=f"OBS: {self._notices.get_nowait()}", fg="#c62828")
            except queue.Empty:
                break
        self._root.after(50, self._poll)

    def _connect_obs(self) -> None:
        """Conecta con OBS al arrancar la aplicación (no al pulsar F9): así el orden de arranque da igual."""
        opts = self._obs_options
        try:
            self._obs_session = open_session(self._log, self._notices.put, opts.host, opts.port, opts.password)
        except ObsError as exc:
            self._notices.put(str(exc))  # sin OBS el registro sigue funcionando, solo sin tiempo de vídeo
            return
        self._obs_label.config(text="OBS: conectado", fg="#2e7d32")
        listen_in_thread(self._obs_session)

    def _close(self) -> None:
        if self._obs_session is not None:
            self._obs_session.close()
        self._log.stop()
        self._hotkeys.stop()
        self._mouse.stop()
        self._root.destroy()

    def run(self) -> None:
        self._hotkeys.start()
        self._mouse.start()
        if self._obs_options is not None:
            self._connect_obs()
        self._poll()
        self._root.mainloop()


def parse_args(argv: list[str] | None = None) -> ObsOptions | None:
    parser = argparse.ArgumentParser(description="Registro de entradas del jugador")
    parser.add_argument("--obs", action="store_true", help="conectar con OBS para anotar el tiempo de vídeo")
    parser.add_argument("--obs-host", default=DEFAULT_HOST)
    parser.add_argument("--obs-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--obs-password", default=None, help=f"o la variable de entorno {PASSWORD_ENV}")
    args = parser.parse_args(argv)
    if not args.obs:
        return None
    return ObsOptions(args.obs_host, args.obs_port, args.obs_password or os.environ.get(PASSWORD_ENV))


def main() -> None:
    RecorderApp(parse_args()).run()


if __name__ == "__main__":
    main()
