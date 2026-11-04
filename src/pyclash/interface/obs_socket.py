"""Socket real hacia el servidor WebSocket de OBS (issue #44). Requiere el extra `record` (websocket-client).

Pegamento fino: adapta `websocket-client` a `ObsConnection`. El protocolo está en `runtime/obs_link.py`.
"""

from __future__ import annotations

import struct
import threading
from typing import Callable

from ..runtime.clock import SystemClock
from ..runtime.obs_link import (
    ObsConnectionClosed,
    ObsError,
    ObsSession,
    VideoTimeSink,
    connect_error,
)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 4455
CONNECT_TIMEOUT = 3.0


class WebSocketConnection:
    def __init__(self, ws) -> None:
        self._ws = ws

    def send(self, text: str) -> None:
        try:
            self._ws.send(text)
        except Exception as exc:
            raise ObsConnectionClosed() from exc

    def recv(self) -> str:
        import websocket

        try:
            opcode, data = self._ws.recv_data()
        except (websocket.WebSocketException, OSError) as exc:
            raise ObsConnectionClosed() from exc
        if opcode == websocket.ABNF.OPCODE_CLOSE:
            code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
            raise ObsConnectionClosed(code)
        return data.decode("utf-8") if isinstance(data, bytes) else data

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def open_session(
    sink: VideoTimeSink,
    notify: Callable[[str], None],
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    password: str | None = None,
) -> ObsSession:
    """Conecta y deja la sesión lista; lanza `ObsError` con el motivo si no puede. Después, `run()` escucha."""
    import websocket

    try:
        ws = websocket.create_connection(f"ws://{host}:{port}", timeout=CONNECT_TIMEOUT)
    except (websocket.WebSocketException, OSError) as exc:
        raise connect_error(host, port) from exc
    session = ObsSession(WebSocketConnection(ws), SystemClock(), sink, notify, password)
    try:
        session.connect()  # con el tiempo límite: un servidor mudo no cuelga el arranque
    except ObsError:
        session.close()
        raise
    ws.settimeout(None)  # la escucha espera eventos sin límite
    return session


def listen_in_thread(session: ObsSession) -> threading.Thread:
    thread = threading.Thread(target=session.run, name="obs-listener", daemon=True)
    thread.start()
    return thread
