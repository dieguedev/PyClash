"""Tiempo de vídeo de OBS (#44): protocolo v5 con una conexión falsa y relojes inyectados, sin OBS."""

import base64
import hashlib
import json
import math
from collections import deque

import pytest

from input_log_test import Harness
from pyclash.runtime.clock import FakeClock
from pyclash.runtime.obs_link import (
    CLOSE_AUTH_FAILED,
    ObsConnectionClosed,
    ObsError,
    ObsSession,
    Sample,
    estimate_offset,
)

FPS = 60
FRAME = 1 / FPS
STARTED = "OBS_WEBSOCKET_OUTPUT_STARTED"
STOPPED = "OBS_WEBSOCKET_OUTPUT_STOPPED"
PAUSED = "OBS_WEBSOCKET_OUTPUT_PAUSED"
RESUMED = "OBS_WEBSOCKET_OUTPUT_RESUMED"


def hello(auth=None):
    d = {"obsWebSocketVersion": "5.6.3", "rpcVersion": 1}
    if auth:
        d["authentication"] = auth
    return json.dumps({"op": 0, "d": d})


IDENTIFIED = json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}})


def record_event(state):
    return json.dumps(
        {
            "op": 5,
            "d": {
                "eventType": "RecordStateChanged",
                "eventIntent": 64,
                "eventData": {"outputActive": state == STARTED, "outputState": state},
            },
        }
    )


def other_event(kind="CurrentProgramSceneChanged"):
    return json.dumps({"op": 5, "d": {"eventType": kind, "eventIntent": 4, "eventData": {}}})


class FakeObs:
    """Conexión falsa: sirve mensajes guiados y responde a las consultas con la duración de un vídeo simulado.

    Un elemento de `incoming` puede ser un texto, un `ObsConnectionClosed` o una función (se ejecuta al llegar
    su turno y devuelve el texto): sirve para mover el reloj o poner en marcha el vídeo en ese instante.
    """

    def __init__(self, clock, rtt=0.001):
        self.clock = clock
        self.rtt = rtt  # int/float, o lista con la ida y vuelta de cada consulta de duración
        self.incoming: deque = deque()
        self.sent: list[dict] = []
        self.closed = False
        self.video_zero: float | None = None  # reloj local en que sale el primer fotograma
        self.status_queries = 0
        self._ready: deque = deque()  # respuestas generadas por consultas, se sirven antes que `incoming`

    # lado servidor
    def start_recording(self, delay=0.286):
        """Aviso de inicio; el primer fotograma sale `delay` s después, como se midió en OBS real."""

        def fire():
            self.video_zero = self.clock.now() + delay
            return record_event(STARTED)

        return fire

    def duration_ms(self):
        # Como en OBS real, el contador de duración arranca 0,284 s después del inicio del archivo
        counter_zero = None if self.video_zero is None else self.video_zero + 0.284
        if counter_zero is None or self.clock.now() < counter_zero:
            return 0
        frames = math.floor((self.clock.now() - counter_zero) * FPS)
        return round(frames / FPS * 1000)

    # lado cliente (ObsConnection)
    def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg["op"] != 6:
            return
        d = msg["d"]
        if isinstance(self.rtt, list):
            rtt = self.rtt[self.status_queries] if self.status_queries < len(self.rtt) else 0.001
        else:
            rtt = self.rtt
        self.status_queries += 1
        self.clock.sleep(rtt * 0.9)  # asimétrico: una consulta lenta falsea el punto medio
        data = {"outputActive": self.video_zero is not None, "outputDuration": self.duration_ms()}
        self.clock.sleep(rtt * 0.1)
        self._ready.append(
            json.dumps(
                {
                    "op": 7,
                    "d": {
                        "requestType": d["requestType"],
                        "requestId": d["requestId"],
                        "requestStatus": {"result": True, "code": 100},
                        "responseData": data,
                    },
                }
            )
        )

    def recv(self):
        if self._ready:
            return self._ready.popleft()
        if not self.incoming:
            raise ObsConnectionClosed()
        item = self.incoming.popleft()
        if isinstance(item, ObsConnectionClosed):
            raise item
        return item() if callable(item) else item

    def close(self):
        self.closed = True


class Setup:
    def __init__(self, auth=None, password=None, rtt=0.001, recording_before=False):
        self.h = Harness()
        self.clock = self.h.clock
        self.obs = FakeObs(self.clock, rtt)
        self.notices: list[str] = []
        self.obs.incoming.append(hello(auth))
        self.obs.incoming.append(IDENTIFIED)
        if recording_before:
            self.obs.video_zero = self.clock.now() - 5
        self.session = ObsSession(self.obs, self.clock, self.h.log, self.notices.append, password)

    def play(self, *items):
        """Conecta y procesa los mensajes dados hasta que se acaban (entonces la conexión «se cae»)."""
        self.session.connect()
        self.obs.incoming.extend(items)
        self.session.run()

    def sent_ops(self, op):
        return [m["d"] for m in self.obs.sent if m["op"] == op]


def video_events(h):
    return [e for e in h.events if "video" in e]


# --- R1, R2: conexión y autenticación -------------------------------------------


def test_identifies_without_password_and_subscribes_only_to_recording_events():
    s = Setup()
    s.session.connect()
    identify = s.sent_ops(1)[0]
    assert "authentication" not in identify
    assert identify["eventSubscriptions"] == 64  # solo la categoría de salidas, que lleva la grabación


def test_identifies_with_standard_challenge_when_password_is_given():
    s = Setup(auth={"challenge": "reto", "salt": "sal"}, password="secreto")
    s.session.connect()
    secret = base64.b64encode(hashlib.sha256(b"secretosal").digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + "reto").encode()).digest()).decode()
    assert s.sent_ops(1)[0]["authentication"] == expected


def test_password_required_and_not_given_says_where_to_set_it():
    s = Setup(auth={"challenge": "reto", "salt": "sal"})
    with pytest.raises(ObsError, match="contraseña") as exc:
        s.session.connect()
    assert "--obs-password" in str(exc.value) and "PYCLASH_OBS_PASSWORD" in str(exc.value)


def test_wrong_password_says_obs_rejected_it():
    s = Setup(auth={"challenge": "reto", "salt": "sal"}, password="mal")
    s.obs.incoming.clear()
    s.obs.incoming.extend([hello({"challenge": "reto", "salt": "sal"}), ObsConnectionClosed(CLOSE_AUTH_FAILED)])
    with pytest.raises(ObsError, match="rechazado"):
        s.session.connect()


def test_connection_closed_during_startup_is_a_clear_error():
    s = Setup()
    s.obs.incoming.clear()
    s.obs.incoming.append(hello())
    with pytest.raises(ObsError):
        s.session.connect()


# --- R8: OBS ya grabando ---------------------------------------------------------


def test_events_arriving_before_the_state_answer_are_ignored():
    s = Setup()
    s.h.log.start(9)
    s.obs.incoming.extend([record_event(STARTED)])  # llega antes de la respuesta a la consulta
    s.session.connect()
    assert "obs_start" not in s.h.kinds() and video_events(s.h) == []


def test_obs_already_recording_warns_and_gives_no_video_time():
    s = Setup(recording_before=True)
    s.h.log.start(9)
    s.play()
    assert len(s.notices) >= 1 and "reinicia la grabación" in s.notices[0]
    s.h.log.mark()
    assert video_events(s.h) == []


def test_state_is_queried_once_when_obs_is_idle_and_no_warning():
    s = Setup()
    s.session.connect()
    assert s.notices == [] and s.obs.status_queries == 1


# --- R4, R5, R9: inicio, ráfaga y precisión --------------------------------------


def test_start_and_stop_are_noted_as_events():
    s = Setup()
    s.h.log.start(9)
    s.play(s.obs.start_recording(), record_event(STOPPED))
    kinds = s.h.kinds()
    assert kinds.index("obs_start") < kinds.index("obs_stop")


def click_error(s, wait=7.3):
    """Graba un clic `wait` s tras el aviso de inicio y devuelve |tiempo de vídeo anotado - tiempo real|."""
    s.h.log.start(9)
    clicked = {}

    def click():
        s.clock.sleep(wait)
        clicked["local"] = s.clock.now()
        s.h.log.button("left", True, 500, 275)
        return other_event()

    s.play(s.obs.start_recording(), click)
    press = [e for e in s.h.events if e["event"] == "press"][0]
    return abs(press["video"] - (clicked["local"] - s.obs.video_zero))


def test_click_during_recording_carries_video_time_within_one_frame():
    # El aviso llega 286 ms antes del primer fotograma: usarlo como cero daría un error de ~0,29 s
    assert click_error(Setup()) <= FRAME + 0.002


def test_slow_queries_in_the_burst_do_not_distort_the_video_time():
    # las lentas caen cuando ya hay duración válida (el contador arranca ~0,57 s tras el aviso)
    rtts = [0.001] * 30 + [0.05, 0.001, 0.08, 0.001, 0.001, 0.04, 0.001, 0.001, 0.001, 0.001]
    assert click_error(Setup(rtt=rtts)) <= FRAME + 0.002


def test_burst_takes_less_than_a_second():
    s = Setup()
    s.h.log.start(9)
    s.play(s.obs.start_recording())
    # el aviso llegó en t=1000 y la ráfaga (más de 10 consultas, por las previas al primer fotograma) acabó antes de 1 s
    assert s.obs.status_queries > 10 and s.clock.now() - 1000 < 1.0


def test_burst_gives_up_with_a_notice_if_the_video_never_starts():
    s = Setup()
    s.h.log.start(9)
    s.play(lambda: record_event(STARTED))  # OBS avisa pero nunca entrega fotogramas
    assert any("tiempo de vídeo" in n for n in s.notices)
    s.h.log.mark()
    assert "video" not in s.h.events[-1]


def test_duration_is_not_queried_again_after_the_initial_burst():
    s = Setup()
    s.h.log.start(9)
    after_burst = {}

    def mark_count():
        after_burst["n"] = s.obs.status_queries
        s.clock.sleep(3600)  # una hora de grabación
        return other_event()

    s.play(s.obs.start_recording(), mark_count, lambda: (s.clock.sleep(3600), other_event())[1])
    assert s.obs.status_queries == after_burst["n"]


def test_estimate_offset_uses_only_the_lowest_round_trips():
    samples = [Sample(0.0003, 10.0), Sample(0.0004, 10.01), Sample(0.0005, 9.99), Sample(0.9, 99.0), Sample(1.2, -5.0)]
    assert estimate_offset(samples) == pytest.approx(10.0, abs=0.01)


def test_events_without_obs_never_carry_video_time():
    h = Harness()
    h.log.start(9)
    h.log.mark()
    assert all("video" not in e for e in h.events)


# --- R6: orden de arranque -------------------------------------------------------


def test_events_before_obs_starts_have_no_video_time_and_later_ones_do():
    s = Setup()
    s.h.log.start(9)
    s.h.log.mark()  # antes de que OBS empiece
    s.play(s.obs.start_recording(), lambda: (s.h.log.mark(), other_event())[1])
    marks = [e for e in s.h.events if e["event"] == "mark"]
    assert "video" not in marks[0] and "video" in marks[1]


def test_log_started_after_obs_began_recording_gets_video_time_from_then():
    s = Setup()

    def start_log_and_mark():
        s.h.log.start(9)  # OBS ya llevaba un rato grabando
        s.h.log.mark()
        return other_event()

    s.play(s.obs.start_recording(), start_log_and_mark)
    assert "video" in s.h.events[-1]


# --- R7: parar, pausar, reanudar -------------------------------------------------


@pytest.mark.parametrize("state", [STOPPED, PAUSED, RESUMED])
def test_stop_pause_and_resume_remove_video_time(state):
    s = Setup()
    s.h.log.start(9)
    s.play(s.obs.start_recording(), record_event(state), lambda: (s.h.log.mark(), other_event())[1])
    assert "video" not in [e for e in s.h.events if e["event"] == "mark"][-1]


def test_resume_does_not_bring_video_time_back():
    s = Setup()
    s.h.log.start(9)
    s.play(s.obs.start_recording(), record_event(PAUSED), record_event(RESUMED), lambda: (s.h.log.mark(), other_event())[1])
    assert "video" not in s.h.events[-1]


def test_new_recording_after_stop_computes_a_new_offset():
    s = Setup()
    s.h.log.start(9)

    def later():
        s.clock.sleep(500)
        return other_event()

    s.play(s.obs.start_recording(), record_event(STOPPED), later, s.obs.start_recording(), lambda: (s.h.log.mark(), other_event())[1])
    mark = s.h.events[-1]
    assert mark["event"] == "mark" and mark["video"] < 5  # cuenta desde el vídeo nuevo, no desde el anterior


# --- eventos ajenos y caídas -----------------------------------------------------


def test_events_that_are_not_recording_state_are_ignored():
    s = Setup()
    s.h.log.start(9)
    before = len(s.h.lines)
    s.play(other_event(), other_event("StreamStateChanged"), record_event("OBS_WEBSOCKET_OUTPUT_STARTING"))
    assert s.h.kinds()[before:] == []
    assert video_events(s.h) == []


def test_obs_dropping_mid_session_clears_video_time_and_warns():
    s = Setup()
    s.h.log.start(9)
    s.play(s.obs.start_recording())  # luego la conexión se cierra
    assert any("perdido la conexión" in n for n in s.notices)
    s.h.log.mark()
    assert "video" not in s.h.events[-1]


def test_closing_on_purpose_does_not_warn():
    s = Setup()
    s.session.connect()
    s.session.close()
    s.obs.incoming.clear()
    s.session.run()
    assert s.obs.closed and s.notices == []
