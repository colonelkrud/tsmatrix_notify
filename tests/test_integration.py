from tests.fakes.fake_clock import FakeClock
from tests.fakes.fake_matrix import FakeMatrix
from tests.fakes.fake_ts3 import FakeTS3Client
from tsmatrix_notify.domain import events
from tsmatrix_notify.domain.handlers import handle_ts_event, reconcile_presence
from tsmatrix_notify.domain.state import AppState


def test_fake_integration_sequence():
    clock = FakeClock(now=100.0)
    state = AppState()
    matrix = FakeMatrix()
    ts3 = FakeTS3Client()

    def on_event(event):
        for action in handle_ts_event(event, state, "room", clock.time()):
            matrix.send_text(action.room_id, action.text, action.clid)

    ts3.register_event_handler(on_event)

    ts3.add_client("1", "Alice")
    ts3.emit(events.TSEvent(events.CLIENT_ENTERED, {"clid": "1", "client_nickname": "Alice"}))

    assert matrix.messages[-1][1] == "▶️ Alice joined TS3."

    clock.advance(10)
    ts3.remove_client("1")
    actions = reconcile_presence(ts3.clientlist(), state, "room", clock.time())
    for action in actions:
        matrix.send_text(action.room_id, action.text, action.clid)

    assert matrix.messages[-1][1] == "◀️ Alice left TS3."
