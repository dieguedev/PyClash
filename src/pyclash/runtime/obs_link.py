"""Conexión del registro de entradas con OBS (issue #44): tiempo de vídeo en cada evento.

Lógica pura del protocolo obs-websocket v5: recibe una conexión (mensajes de texto) y un reloj inyectados, así
que se prueba sin OBS ni tiempo real. El socket real vive en `interface/obs_socket.py`.

Flujo: Hello -> Identify (solo eventos de grabación, con desafío si hay contraseña) -> consulta única del estado
de grabación -> escucha. Al llegar «grabación empezada» hace una ráfaga corta de consultas de la duración
(menos de un segundo; las anteriores al primer fotograma, con duración 0, no cuentan) y, con las de menor ida y
vuelta, calcula la distancia entre el reloj local y el vídeo (`estimate_offset`). Parar,
pausar o reanudar borran esa distancia; no hay consultas periódicas.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Protocol

from .clock import Clock

PASSWORD_ENV = "PYCLASH_OBS_PASSWORD"
EVENT_SUBSCRIPTIONS = 1 << 6  # «Outputs»: la categoría más estrecha que incluye RecordStateChanged
RPC_VERSION = 1
BURST_SIZE = 10
# La duración de grabación que da OBS va ~284 ms por detrás del archivo: el vídeo es 0,284 s más largo que
# `outputDuration` en el mismo instante. Medido con destellos visibles consultando la duración justo antes de
# cada uno: OBS 32.0.4, obs-websocket 5.6.3, 60 fps, 5 destellos con la misma diferencia, con captura DXGI y WGC.
# Si cambia el equipo, la versión de OBS o el codificador, repetir con `tools/check_video_time.py`.
VIDEO_LAG = 0.284
BURST_INTERVAL = 0.02  # s entre consultas (~1 fotograma): da tiempo a que salga el primero y varía la fase
BURST_TIMEOUT = 1.5  # s: se abandona si no logra muestras válidas a tiempo (en OBS real dura ~0,5 s)
BEST_SAMPLES = 3  # de la ráfaga solo pesan las de menor ida y vuelta

# Códigos de operación del protocolo v5
OP_HELLO, OP_IDENTIFY, OP_IDENTIFIED, OP_EVENT, OP_REQUEST, OP_RESPONSE = 0, 1, 2, 5, 6, 7
CLOSE_AUTH_FAILED = 4009


class ObsError(Exception):
    """No se pudo conectar con OBS; el mensaje dice por qué y qué hacer."""


class ObsConnectionClosed(Exception):
    """La conexión se cerró; `code` es el código de cierre del WebSocket, si lo hubo."""

    def __init__(self, code: int | None = None) -> None:
        super().__init__(code)
        self.code = code


class ObsConnection(Protocol):
    def send(self, text: str) -> None: ...
    def recv(self) -> str:
        """Siguiente mensaje de texto; lanza `ObsConnectionClosed` si la conexión se cierra."""
        ...
    def close(self) -> None: ...


class VideoTimeSink(Protocol):
    """Lo que el enlace con OBS necesita del registro de entradas."""

    def obs_event(self, kind: str) -> None: ...
    def set_video_offset(self, offset: float | None) -> None: ...


@dataclass(frozen=True)
class Sample:
    """Una consulta de la ráfaga: ida y vuelta local y distancia con el vídeo que implica."""

    rtt: float
    offset: float


def estimate_offset(samples: list[Sample]) -> float:
    """Distancia reloj local - vídeo: media de las muestras de menor ida y vuelta."""
    best = sorted(samples, key=lambda s: s.rtt)[:BEST_SAMPLES]
    return sum(s.offset for s in best) / len(best)


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def connect_error(host: str, port: int) -> ObsError:
    return ObsError(
        f"No se puede conectar con OBS en {host}:{port}. Comprueba que OBS está abierto y que el servidor "
        "WebSocket está activado (Herramientas > Ajustes del servidor WebSocket)."
    )


class ObsSession:
    def __init__(
        self,
        conn: ObsConnection,
        clock: Clock,
        sink: VideoTimeSink,
        notify: Callable[[str], None],
        password: str | None = None,
    ) -> None:
        self._conn = conn
        self._clock = clock
        self._sink = sink
        self._notify = notify
        self._password = password
        self._next_id = 0
        self._interrupted = False  # llegó un aviso que invalida la ráfaga en curso
        self._in_burst = False
        self._closing = False

    # --- ciclo de vida --------------------------------------------------------

    def connect(self) -> None:
        """Handshake y consulta única del estado. Lanza `ObsError` con el motivo si falla."""
        try:
            self._identify()
            self._check_already_recording()
        except ObsConnectionClosed as exc:
            raise ObsError("OBS ha cerrado la conexión durante el arranque.") from exc

    def run(self) -> None:
        """Escucha eventos hasta que la conexión se cierra."""
        try:
            while True:
                self._handle(self._read())
        except ObsConnectionClosed:
            if not self._closing:
                self._sink.set_video_offset(None)
                self._notify("Se ha perdido la conexión con OBS: los eventos salen sin tiempo de vídeo.")

    def close(self) -> None:
        self._closing = True
        self._conn.close()

    # --- handshake ------------------------------------------------------------

    def _identify(self) -> None:
        hello = self._read()
        if hello.get("op") != OP_HELLO:
            raise ObsError("OBS no ha respondido como un servidor obs-websocket v5.")
        identify = {"rpcVersion": RPC_VERSION, "eventSubscriptions": EVENT_SUBSCRIPTIONS}
        auth = hello["d"].get("authentication")
        if auth:
            if not self._password:
                raise ObsError(
                    f"OBS pide contraseña: indícala con --obs-password o con la variable de entorno {PASSWORD_ENV}."
                )
            identify["authentication"] = auth_string(self._password, auth["salt"], auth["challenge"])
        self._send(OP_IDENTIFY, identify)
        try:
            reply = self._read()
        except ObsConnectionClosed as exc:
            if exc.code == CLOSE_AUTH_FAILED:
                raise ObsError("OBS ha rechazado la contraseña.") from exc
            raise
        if reply.get("op") != OP_IDENTIFIED:
            raise ObsError("OBS no ha aceptado la identificación.")

    def _check_already_recording(self) -> None:
        status = self._request("GetRecordStatus", ignore_events=True)
        if status.get("outputActive"):
            self._notify(
                "OBS ya estaba grabando: reinicia la grabación si quieres tiempo de vídeo; hasta entonces "
                "los eventos salen sin él."
            )

    # --- eventos --------------------------------------------------------------

    def _handle(self, msg: dict) -> None:
        if msg.get("op") != OP_EVENT:
            return
        d = msg["d"]
        if d.get("eventType") != "RecordStateChanged":
            return  # la categoría de salidas trae también emisión, cámara virtual, etc.
        state = d["eventData"]["outputState"]
        if state == "OBS_WEBSOCKET_OUTPUT_STARTED":
            if self._in_burst:
                self._interrupted = True  # otra grabación en menos de un segundo: no se puede fiar la ráfaga
                return
            self._sink.obs_event("start")
            self._sink.set_video_offset(self._burst())
        elif state == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            self._interrupted = True
            self._sink.set_video_offset(None)
            self._sink.obs_event("stop")
        elif state in ("OBS_WEBSOCKET_OUTPUT_PAUSED", "OBS_WEBSOCKET_OUTPUT_RESUMED"):
            self._interrupted = True
            self._sink.set_video_offset(None)

    def _burst(self) -> float | None:
        """Ráfaga de consultas de la duración; None si la grabación se interrumpe a mitad."""
        self._interrupted = False
        self._in_burst = True
        try:
            started = self._clock.now()
            samples: list[Sample] = []
            while len(samples) < BURST_SIZE:
                if self._clock.now() - started > BURST_TIMEOUT:
                    self._notify("No se pudo calcular el tiempo de vídeo de esta grabación.")
                    return None
                sent = self._clock.now()
                data = self._request("GetRecordStatus")
                received = self._clock.now()
                if self._interrupted:
                    return None
                # El aviso llega antes del primer fotograma y entonces la duración es 0: esa muestra no mide
                # nada del vídeo, así que no cuenta; se sigue consultando hasta tener las suficientes.
                if data["outputDuration"] > 0:
                    video = data["outputDuration"] / 1000 + VIDEO_LAG  # tiempo en el archivo, no el contador
                    samples.append(Sample(rtt=received - sent, offset=(sent + received) / 2 - video))
                self._clock.sleep(BURST_INTERVAL)
            return estimate_offset(samples)
        finally:
            self._in_burst = False

    # --- mensajes -------------------------------------------------------------

    def _request(self, kind: str, ignore_events: bool = False) -> dict:
        self._next_id += 1
        request_id = str(self._next_id)
        self._send(OP_REQUEST, {"requestType": kind, "requestId": request_id})
        while True:
            msg = self._read()
            if msg.get("op") == OP_RESPONSE and msg["d"].get("requestId") == request_id:
                if not msg["d"]["requestStatus"]["result"]:
                    raise ObsError(f"OBS ha rechazado la consulta {kind}.")
                return msg["d"].get("responseData", {})
            if not ignore_events:
                self._handle(msg)

    def _send(self, op: int, data: dict) -> None:
        self._conn.send(json.dumps({"op": op, "d": data}))

    def _read(self) -> dict:
        return json.loads(self._conn.recv())
