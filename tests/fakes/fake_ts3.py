from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tsmatrix_notify.domain.events import TSEvent


class FakeTS3ConnectionClosedException(ConnectionError):
    """Test-only stand-in for TS3ConnectionClosedException."""


@dataclass(frozen=True)
class TS3ScriptedStep:
    action: str
    payload: object | None = None


class FakeTS3Client:
    def __init__(self):
        self._handler: Callable[[TSEvent], None] | None = None
        self._clients: dict[str, dict[str, str]] = {}
        self._connected = True
        self._recv_thread_alive = True
        self._reconnect_attempts = 0
        self._reconnect_failures_remaining = 0
        self._version = "fake"
        self._hostinfo = {"instance_uptime": "0"}
        self._serverinfo = {"virtualserver_uptime": "0"}

    def set_reconnect_failures(self, failures: int) -> None:
        self._reconnect_failures_remaining = max(0, failures)

    def set_version_response(self, value: str) -> None:
        self._version = value

    def set_client_snapshot(self, clients: list[dict[str, str]]) -> None:
        self._clients = {c["clid"]: dict(c) for c in clients}

    @property
    def reconnect_attempts(self) -> int:
        return self._reconnect_attempts

    def version(self) -> str:
        self._ensure_connected()
        return self._version

    def clientlist(self):
        self._ensure_connected()
        return [dict(v) for _, v in sorted(self._clients.items(), key=lambda i: i[0])]

    def clientinfo(self, clid: str):
        self._ensure_connected()
        return dict(self._clients[clid])

    def hostinfo(self):
        self._ensure_connected()
        return dict(self._hostinfo)

    def serverinfo(self):
        self._ensure_connected()
        return dict(self._serverinfo)

    def add_client(self, clid: str, nickname: str, **extra):
        entry = {
            "clid": clid,
            "client_type": "0",
            "client_nickname": nickname,
            "client_away": extra.get("client_away", "0"),
            "client_away_message": extra.get("client_away_message", ""),
            "client_input_muted": extra.get("client_input_muted", "0"),
            "client_output_muted": extra.get("client_output_muted", "0"),
        }
        self._clients[clid] = entry
        return entry

    def remove_client(self, clid: str):
        self._clients.pop(clid, None)

    def register_event_handler(self, handler):
        self._handler = handler

    def emit(self, event: TSEvent):
        self._ensure_connected()
        self._ensure_recv_thread_alive()
        if self._handler:
            self._handler(event)

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._reconnect_attempts += 1
        if self._reconnect_failures_remaining > 0:
            self._reconnect_failures_remaining -= 1
            raise OSError("simulated reconnect failure")
        self._connected = True
        self._recv_thread_alive = True

    def crash_recv_thread(self) -> None:
        self._recv_thread_alive = False

    def script(self, steps: list[TS3ScriptedStep]) -> None:
        for step in steps:
            if step.action == "event":
                self.emit(step.payload)  # type: ignore[arg-type]
            elif step.action == "disconnect":
                self.disconnect()
            elif step.action == "reconnect":
                self.reconnect()
            elif step.action == "crash_recv":
                self.crash_recv_thread()
            elif step.action == "add_client":
                payload = step.payload or {}
                self.add_client(**payload)  # type: ignore[arg-type]
            elif step.action == "remove_client":
                self.remove_client(str(step.payload))
            else:
                raise ValueError(f"Unknown fake TS3 script action: {step.action}")

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise FakeTS3ConnectionClosedException("TS3 connection closed")

    def _ensure_recv_thread_alive(self) -> None:
        if not self._recv_thread_alive:
            raise RuntimeError("recv-thread crashed")
