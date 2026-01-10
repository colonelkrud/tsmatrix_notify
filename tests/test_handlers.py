from tsmatrix_notify.domain import events
from tsmatrix_notify.domain.handlers import handle_ts_event, reconcile_presence
from tsmatrix_notify.domain.state import AppState


def test_handle_client_entered_and_left():
    state = AppState()
    enter = events.TSEvent(events.CLIENT_ENTERED, {"clid": "1", "client_nickname": "Bob"})
    actions = handle_ts_event(enter, state, "room", now=10.0)

    assert state.client_names["1"] == "Bob"
    assert actions[0].text == "▶️ Bob joined TS3."

    leave = events.TSEvent(events.CLIENT_LEFT, {"clid": "1"})
    actions = handle_ts_event(leave, state, "room", now=20.0)

    assert "1" not in state.client_names
    assert actions[0].text == "◀️ Bob left TS3."


def test_handle_move_and_kick():
    state = AppState(client_names={"1": "Bob"})
    move = events.TSEvent(events.CLIENT_MOVED_SELF, {"clid": "1", "ctid": "42"})
    actions = handle_ts_event(move, state, "room", now=0.0)
    assert actions[0].text == "🔀 Bob moved to channel 42"

    kick = events.TSEvent(events.CLIENT_KICK_SERVER, {"clid": "1", "reasonmsg": "rule"})
    actions = handle_ts_event(kick, state, "room", now=0.0)
    assert actions[0].text == "⚠️ Bob kicked from server. Reason: rule"


def test_handle_ban_mismatch_does_not_crash():
    state = AppState(client_names={"1": "Bob"})
    ban = events.TSEvent(events.CLIENT_BANNED, {"cldbid": "99", "reasonmsg": "nope"})
    actions = handle_ts_event(ban, state, "room", now=0.0)
    assert actions[0].text == "⛔️ <unknown> banned. Reason: nope"


def test_reconcile_presence():
    state = AppState(client_names={"1": "Bob"}, join_times={"1": 0.0})
    clientlist = [
        {"clid": "2", "client_type": "0", "client_nickname": "Alice"},
    ]

    actions = reconcile_presence(clientlist, state, "room", now=10.0)

    assert "2" in state.client_names
    assert "1" not in state.client_names
    assert actions[0].text == "▶️ Alice joined TS3."
    assert actions[1].text == "◀️ Bob left TS3."
