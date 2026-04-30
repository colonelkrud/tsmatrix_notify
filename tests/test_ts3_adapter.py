import logging
from tsmatrix_notify.adapters import ts3_ts3api


class DummyConn:
    def __init__(self):
        self._notifies_bound = False
        self.called = []

    def version(self):
        return {"version": "1.2.3"}

    def clientlist(self):
        return [{"clid": "1"}]

    def clientinfo(self, clid):
        return {"clid": clid}

    def hostinfo(self):
        return {"host": "h"}

    def serverinfo(self):
        return {"server": "s"}

    def start_keepalive_loop(self):
        self.called.append("start")

    def stop_keepalive_loop(self):
        self.called.append("stop")

    def quit(self):
        self.called.append("quit")

    def register_for_server_events(self, cb):
        self.server_cb = cb

    def register_for_channel_events(self, _cid, cb):
        self.channel_cb = cb


def test_ts3safe_call_and_reconnect_path():
    conn1 = DummyConn()
    conn2 = DummyConn()
    seq = [conn1, conn2]

    def factory():
        return seq.pop(0)

    safe = ts3_ts3api.TS3Safe(factory, post_connect=None, log=logging.getLogger("test"))

    def fn_fail_once(conn):
        if conn is conn1:
            raise OSError("boom")
        return "ok"

    assert safe.call(fn_fail_once) == "ok"


def test_adapter_basic_methods_and_bind(monkeypatch):
    conn = DummyConn()
    monkeypatch.setattr(ts3_ts3api, "connect_ts3", lambda *args, **kwargs: conn)

    adapter = ts3_ts3api.TS3APIAdapter("h", 10011, "u", "p", 1, logging.getLogger("test"))
    received = []
    adapter.register_event_handler(lambda ev: received.append(ev.kind))
    assert adapter.version() == "1.2.3"
    assert adapter.clientinfo("7") == {"clid": "7"}
    assert adapter.clientlist() == [{"clid": "1"}]
    assert adapter.hostinfo() == {"host": "h"}
    assert adapter.serverinfo() == {"server": "s"}
    adapter.close()
    assert "quit" in conn.called
