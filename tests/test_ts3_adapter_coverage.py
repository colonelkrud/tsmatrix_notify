import logging
from types import SimpleNamespace

import pytest

from tsmatrix_notify.adapters import ts3_ts3api


class FakeConn:
    def __init__(self):
        self._notifies_bound = False
        self.calls = []
        self._keepalive_thread = None

    def login(self, user, pw):
        self.calls.append(("login", user, pw))

    def version(self):
        return {"version": "3.13", "build": "1", "platform": "linux"}

    def use(self, sid):
        self.calls.append(("use", sid))

    def register_for_server_events(self, cb):
        self.server_cb = cb

    def register_for_channel_events(self, cid, cb):
        self.channel_cb = cb

    def start_keepalive_loop(self):
        self.calls.append("start_keepalive")

    def stop_keepalive_loop(self):
        self.calls.append("stop_keepalive")

    def quit(self):
        self.calls.append("quit")

    def clientlist(self):
        return [{"clid": "1"}]

    def clientinfo(self, clid):
        return {"clid": clid}

    def hostinfo(self):
        return {"instance_uptime": "1"}

    def serverinfo(self):
        return {"virtualserver_name": "v"}


def test_connect_ts3_happy_path(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(ts3_ts3api.socket, "create_connection", lambda *_a, **_k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ts3_ts3api, "TS3Connection", lambda *_a, **_k: conn)
    out = ts3_ts3api.connect_ts3("h", 1, "u", "p", 2, logging.getLogger("test"))
    assert out is conn
    assert ("login", "u", "p") in conn.calls
    assert ("use", 2) in conn.calls


def test_connect_ts3_permissions_error(monkeypatch):
    class E(Exception):
        pass

    monkeypatch.setattr(ts3_ts3api, "TS3QueryException", E)
    monkeypatch.setattr(ts3_ts3api.socket, "create_connection", lambda *_a, **_k: SimpleNamespace(close=lambda: None))

    class BadConn(FakeConn):
        def login(self, user, pw):
            raise E("insufficient client permissions")

    monkeypatch.setattr(ts3_ts3api, "TS3Connection", lambda *_a, **_k: BadConn())
    with pytest.raises(E):
        ts3_ts3api.connect_ts3("h", 1, "u", "p", 2, logging.getLogger("test"))


def test_translate_and_notify_callback(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(ts3_ts3api, "connect_ts3", lambda *a, **k: conn)
    adapter = ts3_ts3api.TS3APIAdapter("h", 1, "u", "p", 1, logging.getLogger("test"))
    got = []
    adapter.register_event_handler(lambda ev: got.append(ev.kind))
    ev = SimpleNamespace(_data={"clid": "1"})
    # monkeypatch Event class used by isinstance
    monkeypatch.setattr(ts3_ts3api.Events, "ClientEnteredEvent", type(ev))
    conn.server_cb(event=ev)
    assert got


def test_translate_unknown_event_returns_none(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(ts3_ts3api, "connect_ts3", lambda *a, **k: conn)
    adapter = ts3_ts3api.TS3APIAdapter("h", 1, "u", "p", 1, logging.getLogger("test"))
    monkeypatch.setattr(ts3_ts3api.Events, "ClientKickFromChannelEvent", type("A", (), {}), raising=False)
    monkeypatch.setattr(ts3_ts3api.Events, "ClientKickFromServerEvent", type("B", (), {}), raising=False)
    monkeypatch.setattr(ts3_ts3api.Events, "ClientBanEvent", type("C", (), {}), raising=False)
    assert adapter._translate_event(object()) is None
