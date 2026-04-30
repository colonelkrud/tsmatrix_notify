import asyncio
import logging
import threading

from tsmatrix_notify.application.dispatcher import send_actions
from tsmatrix_notify.application.supervisors import ExponentialBackoff, MatrixReconnectSupervisor, TS3ReconnectSupervisor
from tsmatrix_notify.domain import events
from tsmatrix_notify.domain.handlers import MatrixAction, handle_ts_event
from tsmatrix_notify.domain.state import AppState
from tests.fakes.fake_matrix import FakeMatrix
from tests.fakes.fake_ts3 import FakeTS3Client, TS3ScriptedStep


def test_ts3_recv_crash_then_reconnect_resumes_delivery():
    ts3 = FakeTS3Client()
    matrix = FakeMatrix()
    state = AppState()
    restart = threading.Event()
    supervisor = TS3ReconnectSupervisor(ts3, restart, logging.getLogger("test"), backoff=ExponentialBackoff(min_delay=1, random_provider=lambda: 0), sleep=lambda _: None)

    def on_event(event):
        actions = handle_ts_event(event, state, "!room:example.com", 100.0)
        send_actions(matrix, actions, logging.getLogger("test"))

    ts3.register_event_handler(on_event)
    ts3.script([TS3ScriptedStep("event", events.TSEvent(events.CLIENT_ENTERED, {"clid": "1", "client_nickname": "Alice"}))])
    assert matrix.messages[-1][1].startswith("▶️")

    ts3.crash_recv_thread()
    restart.set()
    supervisor.reconnect_with_backoff()

    ts3.script([TS3ScriptedStep("event", events.TSEvent(events.CLIENT_LEFT, {"clid": "1", "client_nickname": "Alice"}))])
    assert matrix.messages[-1][1].startswith("◀️")


def test_ts3_reconnect_backoff_resets_after_success():
    ts3 = FakeTS3Client()
    ts3.set_reconnect_failures(2)
    restart = threading.Event(); restart.set()
    delays = []
    backoff = ExponentialBackoff(min_delay=2.0, factor=2.0, max_delay=20.0, jitter_ratio=0.0)
    sup = TS3ReconnectSupervisor(ts3, restart, logging.getLogger("test"), backoff=backoff, sleep=lambda d: delays.append(d))
    sup.reconnect_with_backoff()
    restart.set()
    sup.reconnect_with_backoff()
    assert delays == [2.0, 4.0]


def test_matrix_transient_errors_use_backoff_and_reset():
    backoff = ExponentialBackoff(min_delay=3.0, jitter_ratio=0.0)
    sup = MatrixReconnectSupervisor(logging.getLogger("test"), backoff=backoff)
    d1 = sup.handle_error(asyncio.TimeoutError("timeout"))
    d2 = sup.handle_error(ConnectionError("down"))
    sup.reset()
    d3 = sup.handle_error(ConnectionError("down"))
    assert (d1, d2, d3) == (3.0, 6.0, 3.0)


def test_matrix_send_failure_contained(caplog):
    matrix = FakeMatrix()
    matrix.queue_send_failure(TimeoutError("timeout"))
    actions = [MatrixAction(room_id="room", text="a", clid="1"), MatrixAction(room_id="room", text="b", clid="2")]
    send_actions(matrix, actions, logging.getLogger("test"))
    assert len(matrix.messages) == 1
