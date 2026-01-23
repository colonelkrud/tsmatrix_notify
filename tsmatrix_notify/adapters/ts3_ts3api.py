from __future__ import annotations

import logging
import socket
import time
import threading
from typing import Callable

from ts3API.TS3Connection import TS3Connection, TS3QueryException
import ts3API.Events as Events
from ts3API.utilities import TS3ConnectionClosedException

from tsmatrix_notify.domain import events as domain_events
from tsmatrix_notify.domain.events import TSEvent
from tsmatrix_notify.ports.ts3_port import TS3Port


def _join_keepalive_if_present(conn, log, timeout=2.0):
    try:
        t = getattr(conn, "_keepalive_thread", None)
        if isinstance(t, threading.Thread) and t.is_alive():
            t.join(timeout)
    except Exception:
        log.debug("keepalive join best-effort failed", exc_info=True)


def connect_ts3(host, port, user, pw, vsid, log):
    while True:
        try:
            log.debug("Testing TCP %s:%d …", host, port)
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
        except Exception as exc:
            log.error("Network unreachable: %s", exc)
            time.sleep(5)
            continue

        try:
            conn = TS3Connection(host, port)
            log.debug("Logging in as %r", user)
            conn.login(user, pw)
            log.info("TS3 login successful")

            ver = conn.version()
            if isinstance(ver, dict):
                ver_str = f"{ver.get('version','<unknown>')} build={ver.get('build','?')} on {ver.get('platform','?')}"
            else:
                parsed = getattr(ver, "parsed", None)
                ver_str = parsed[0]["version"] if parsed else "<unknown>"
            log.info("ServerQuery version: %s", ver_str)

            conn.use(sid=vsid)
            log.info("Using virtual server %d", vsid)
            return conn

        except TS3QueryException as exc:
            msg = str(exc).lower()
            if "insufficient client permissions" in msg:
                log.error("Insufficient TS3 permissions; aborting")
                raise
            log.error("TS3QueryException: %s", exc)
            time.sleep(5)

        except Exception as exc:
            log.error("TS3 connection error: %s", exc)
            time.sleep(5)


class TS3Safe:
    def __init__(self, connect_factory, post_connect, log, notify_binder=None):
        self._connect_factory = connect_factory
        self._post_connect = post_connect
        self._notify_binder = notify_binder
        self._log = log
        self._conn = connect_factory()
        setattr(self._conn, "_notifies_bound", False)
        if self._post_connect:
            self._post_connect(self._conn)

    @property
    def conn(self):
        return self._conn

    def _clean_close(self, conn: TS3Connection):
        if not conn:
            return
        try:
            try:
                conn.stop_keepalive_loop()
            except Exception:
                pass
            _join_keepalive_if_present(conn, self._log)
            try:
                conn.quit()
            except Exception:
                pass
        except Exception:
            self._log.exception("TS3Safe: error while closing old TS3 connection")

    def _reconnect(self):
        self._log.warning("TS3 reconnecting…")
        old = self._conn
        self._clean_close(old)

        new_conn = self._connect_factory()
        setattr(new_conn, "_notifies_bound", False)

        self._conn = new_conn
        self._log.info("TS3 reconnected")

        try:
            if self._post_connect:
                self._post_connect(self._conn)
            if self._notify_binder:
                self._notify_binder(self._conn, self._log)
        except Exception:
            self._log.exception("TS3 post-connect/notify-binder hook failed")

    def reconnect(self) -> None:
        self._reconnect()

    def call(self, fn, *args, **kwargs):
        try:
            return fn(self._conn, *args, **kwargs)
        except (TS3ConnectionClosedException, OSError) as exc:
            self._log.warning("TS3 call failed (%r); retrying after reconnect", exc)
            self._reconnect()
            return fn(self._conn, *args, **kwargs)


class TS3APIAdapter(TS3Port):
    def __init__(self, host, port, user, password, vserver_id, log: logging.Logger):
        self._log = log
        self._handler: Callable[[TSEvent], None] | None = None

        def _factory():
            return connect_ts3(host, port, user, password, vserver_id, log)

        def _post_connect(conn: TS3Connection):
            setattr(conn, "_notifies_bound", getattr(conn, "_notifies_bound", False))
            if self._handler:
                self._bind_notifies(conn)
            try:
                conn.start_keepalive_loop()
            except Exception:
                pass

        self._safe = TS3Safe(_factory, _post_connect, log, notify_binder=lambda c, l: self._bind_notifies(c))

    def version(self) -> str:
        def _version(conn: TS3Connection):
            ver = conn.version()
            if isinstance(ver, dict):
                return ver.get("version", "<unknown>")
            parsed = getattr(ver, "parsed", None)
            return parsed[0]["version"] if parsed else "<unknown>"

        return self._safe.call(_version)

    def clientlist(self) -> list[dict]:
        return self._safe.call(lambda c: c.clientlist())

    def clientinfo(self, clid: str) -> dict:
        return self._safe.call(lambda c: c.clientinfo(clid))

    def hostinfo(self) -> dict:
        return self._safe.call(lambda c: c.hostinfo())

    def serverinfo(self) -> dict:
        return self._safe.call(lambda c: c.serverinfo())

    def register_event_handler(self, handler: Callable[[TSEvent], None]) -> None:
        self._handler = handler
        self._bind_notifies(self._safe.conn)

    def _bind_notifies(self, conn: TS3Connection) -> None:
        if getattr(conn, "_notifies_bound", False):
            self._log.debug("TS3 notify handlers already bound for this connection; skipping")
            return
        if not self._handler:
            return

        def on_ts3_event(*args, **kwargs):
            ev = kwargs.get("event")
            if ev is None and args:
                ev = args[-1]
            if ev is None:
                self._log.warning("TS3 notify callback invoked without an event: args=%r kwargs=%r", args, kwargs)
                return
            ts_event = self._translate_event(ev)
            if ts_event is None:
                self._log.debug("Unhandled TS3 event: %s", type(ev).__name__)
                return
            self._handler(ts_event)

        conn.register_for_server_events(on_ts3_event)
        conn.register_for_channel_events(0, on_ts3_event)
        conn._notifies_bound = True
        self._log.info("TS3 events subscribed")

    def _translate_event(self, ev) -> TSEvent | None:
        data = getattr(ev, "_data", {})
        if isinstance(ev, Events.ClientEnteredEvent):
            return TSEvent(domain_events.CLIENT_ENTERED, data)
        if isinstance(ev, Events.ClientMovedSelfEvent):
            return TSEvent(domain_events.CLIENT_MOVED_SELF, data)
        if isinstance(ev, Events.ClientMovedEvent):
            return TSEvent(domain_events.CLIENT_MOVED, data)
        if isinstance(ev, Events.ClientLeftEvent):
            return TSEvent(domain_events.CLIENT_LEFT, data)
        if isinstance(ev, Events.ClientKickFromChannelEvent):
            return TSEvent(domain_events.CLIENT_KICK_CHANNEL, data)
        if isinstance(ev, Events.ClientKickFromServerEvent):
            return TSEvent(domain_events.CLIENT_KICK_SERVER, data)
        if isinstance(ev, Events.ClientBanEvent) or type(ev).__name__.lower().startswith("ban"):
            return TSEvent(domain_events.CLIENT_BANNED, data)
        return None

    def start_keepalive(self) -> None:
        try:
            self._safe.conn.start_keepalive_loop()
        except Exception:
            pass

    def stop_keepalive(self) -> None:
        try:
            self._safe.conn.stop_keepalive_loop()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.stop_keepalive()
            _join_keepalive_if_present(self._safe.conn, self._log)
            self._safe.conn.quit()
        except Exception:
            self._log.exception("Error stopping TS3 connection")

    def reconnect(self) -> None:
        self._safe.reconnect()
