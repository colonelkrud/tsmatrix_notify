from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TSEvent:
    kind: str
    data: dict
    correlation_id: str | None = None


CLIENT_ENTERED = "client_entered"
CLIENT_LEFT = "client_left"
CLIENT_MOVED_SELF = "client_moved_self"
CLIENT_MOVED = "client_moved"
CLIENT_KICK_CHANNEL = "client_kick_channel"
CLIENT_KICK_SERVER = "client_kick_server"
CLIENT_BANNED = "client_banned"
