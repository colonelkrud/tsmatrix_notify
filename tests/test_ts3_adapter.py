import logging

from ts3API.utilities import TS3ConnectionClosedException

from tsmatrix_notify.adapters.ts3_ts3api import TS3Safe


class DummyConn:
    def __init__(self, fail_once=False):
        self.fail_once = fail_once
        self.calls = 0

    def ping(self):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise TS3ConnectionClosedException("closed")
        return "ok"

    def stop_keepalive_loop(self):
        return None

    def quit(self):
        return None


def test_ts3safe_reconnects_on_closed():
    conns = [DummyConn(fail_once=True), DummyConn()]

    def factory():
        return conns.pop(0)

    safe = TS3Safe(factory, post_connect=None, log=logging.getLogger("test"))

    result = safe.call(lambda c: c.ping())

    assert result == "ok"
    assert safe.conn.calls == 1
