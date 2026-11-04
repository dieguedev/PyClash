"""Comprueba la precisión del tiempo de vídeo de OBS (issue #44, R9) con un destello visible.

Abre una ventana negra a pantalla completa que se pone blanca mientras se mantiene pulsado el botón izquierdo.
El registro de entradas la vigila como si fuera la ventana del juego, con OBS conectado; la herramienta inicia y
para la grabación, y después busca en el vídeo el primer fotograma blanco de cada pulsación y lo compara con su
tiempo de vídeo registrado.

OBS debe capturar la pantalla entera (captura de pantalla, no de ventana). Contraseña, si la hay, en la variable
de entorno PYCLASH_OBS_PASSWORD. Haz unos 10 clics separados y pulsa Esc para terminar.

El resultado incluye la latencia de captura de OBS y el repintado de Tk (de 0 a 2 fotogramas), así que es una cota
superior del error del tiempo de vídeo: si sale pequeño y estable, el tiempo de vídeo es válido.

    python tools/check_video_time.py

Con captura de ventana del juego no hay forma de poner un destello visible. Allí se mide la reacción del propio
juego al clic (el recorte alrededor del botón está quieto y cambia 0-1 fotogramas después de la pulsación).
"""

from __future__ import annotations

import ctypes
import json
import os
import statistics
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyclash.device.base import Rect  # noqa: E402
from pyclash.interface.obs_socket import DEFAULT_HOST, DEFAULT_PORT, listen_in_thread, open_session  # noqa: E402
from pyclash.runtime.clock import SystemClock  # noqa: E402
from pyclash.runtime.input_log import FileSink, InputLog, WindowState, session_filename  # noqa: E402
from pyclash.runtime.obs_link import PASSWORD_ENV, auth_string  # noqa: E402

OUT_DIR = Path("captures_raw")
SETTLE = 1.5  # s entre iniciar la grabación y el registro: da tiempo a la ráfaga de OBS
WAIT_COLOR = "#303030"  # pantalla de espera: oscura, no cuenta como destello


def obs_request(kind: str, password: str | None) -> dict:
    import websocket

    ws = websocket.create_connection(f"ws://{DEFAULT_HOST}:{DEFAULT_PORT}", timeout=5)
    hello = json.loads(ws.recv())
    identify = {"rpcVersion": 1, "eventSubscriptions": 0}
    auth = hello["d"].get("authentication")
    if auth:
        identify["authentication"] = auth_string(password or "", auth["salt"], auth["challenge"])
    ws.send(json.dumps({"op": 1, "d": identify}))
    ws.recv()
    ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": kind}}))
    data = json.loads(ws.recv())["d"]
    ws.close()
    if not data["requestStatus"]["result"]:
        raise SystemExit(f"OBS rechazó {kind}: {data['requestStatus']}")
    return data.get("responseData", {})


def run_session(password: str | None) -> tuple[Path, str]:
    """Devuelve (ruta del registro, ruta del vídeo)."""
    from pynput import mouse

    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # coordenadas físicas, como las de pynput
    root = tk.Tk()
    root.configure(bg=WAIT_COLOR, cursor="crosshair")
    root.attributes("-fullscreen", True, "-topmost", True)
    label = tk.Label(root, text="Espera... (los clics ahora no cuentan)", bg=WAIT_COLOR, fg="#bbbbbb", font=("Segoe UI", 20))
    label.pack(pady=20)
    root.update()
    state = {"rect": Rect(root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()), "focus": True}

    log_path: list[Path] = []

    def open_sink(when, th):
        log_path.append(OUT_DIR / session_filename(when, th))
        return FileSink(log_path[0])

    log = InputLog(SystemClock(), datetime.now, lambda: WindowState(state["rect"], state["focus"]), open_sink)
    session = open_session(log, lambda m: print("AVISO OBS:", m), password=password)
    listen_in_thread(session)

    listener = mouse.Listener(
        on_move=log.move,
        on_click=lambda x, y, b, pressed: log.button(b.name, pressed, int(x), int(y)),
        on_scroll=lambda x, y, dx, dy: log.scroll(int(x), int(y), dx, dy),
    )
    listener.start()
    video: list[str] = []
    result = {}

    ready = {"on": False}  # hasta que el registro esté activo, los clics no destellan

    def flash(color):
        root.configure(bg=color)
        for child in root.winfo_children():
            child.configure(bg=color)

    def start_log():
        log.start(9)
        ready["on"] = True
        label.configure(text="Ya puedes hacer ~10 clics separados. Esc para terminar.", fg="#444444")
        flash("black")

    root.bind("<ButtonPress-1>", lambda e: ready["on"] and flash("white"))
    root.bind("<ButtonRelease-1>", lambda e: ready["on"] and flash("black"))
    root.bind("<FocusIn>", lambda e: state.update(focus=True))
    root.bind("<FocusOut>", lambda e: state.update(focus=False))

    def finish(_event=None):
        log.stop()
        listener.stop()
        video.append(obs_request("StopRecord", password)["outputPath"])
        session.close()
        root.destroy()

    root.bind("<Escape>", finish)
    obs_request("StartRecord", password)
    root.after(int(SETTLE * 1000), start_log)
    root.focus_force()
    root.mainloop()
    return log_path[0], video[0]


def analyze(log_path: Path, video_path: str) -> None:
    import cv2
    import numpy as np

    time.sleep(1.5)  # OBS termina de cerrar el fichero
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    white = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        white.append(float(np.mean(cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY))) > 128)
    onset_frames = [i for i in range(1, len(white)) if white[i] and not white[i - 1]]
    onsets = [i / fps for i in onset_frames]
    presses = [e["video"] for e in map(json.loads, log_path.read_text(encoding="utf-8").splitlines())
               if e["event"] == "press" and "video" in e]
    print(f"\nVídeo: {video_path}\n{fps:.0f} fps, {len(white)} fotogramas; {len(presses)} pulsaciones, {len(onsets)} destellos")
    errors = []
    matched = set()
    for p in presses:
        after = [(f, o) for f, o in zip(onset_frames, onsets) if o >= p - 0.05]
        if after and after[0][1] - p < 0.5:
            frame, o = after[0]
            matched.add(frame)
            errors.append(o - p)
            print(f"  pulsación en {p:8.3f} s (fotograma {p * fps:6.1f}) -> destello en fotograma {frame:4d} = {o:8.3f} s  ({(o - p) * 1000:+6.1f} ms)")
        else:
            print(f"  pulsación en {p:8.3f} s -> sin destello cercano")
    extra = [f for f in onset_frames if f not in matched]
    if extra:
        print(f"  AVISO: destellos sin pulsación registrada, en los fotogramas {extra} (clics antes de que el registro empezara?)")
    if not errors:
        print("Sin datos: ¿captura OBS la pantalla entera?")
        return
    ms = [e * 1000 for e in errors]
    print(f"\nRetraso pulsación->destello: mediana {statistics.median(ms):.1f} ms, "
          f"mín {min(ms):.1f}, máx {max(ms):.1f}, dispersión {max(ms) - min(ms):.1f} ms (1 fotograma = {1000 / fps:.1f} ms)")


def main() -> None:
    password = os.environ.get(PASSWORD_ENV)
    log_path, video_path = run_session(password)
    analyze(log_path, video_path)


if __name__ == "__main__":
    main()
