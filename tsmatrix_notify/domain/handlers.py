from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from tsmatrix_notify.domain import events
from tsmatrix_notify.domain.messages import (
    format_ban,
    format_join,
    format_kick_channel,
    format_kick_server,
    format_leave,
    format_move,
    format_move_by,
)
from tsmatrix_notify.domain.state import AppState


@dataclass(frozen=True)
class MatrixAction:
    room_id: str
    text: str
    clid: str | None = None
    correlation_id: str | None = None
    event_type: str | None = None


def handle_ts_event(event: events.TSEvent, state: AppState, room_id: str, now: float) -> list[MatrixAction]:
    data = event.data
    actions: list[MatrixAction] = []
    text: str | None = None

    if event.kind == events.CLIENT_ENTERED:
        clid = data["clid"]
        nick = data.get("client_nickname", "?")
        state.client_names[clid] = nick
        state.join_times[clid] = now
        text = format_join(nick)
    elif event.kind == events.CLIENT_MOVED_SELF:
        clid, new = data["clid"], data["ctid"]
        nick = state.client_names.get(clid, "?")
        text = format_move(nick, new)
    elif event.kind == events.CLIENT_MOVED:
        clid, new = data["clid"], data["ctid"]
        nick = state.client_names.get(clid, "?")
        invoker = data.get("invokername", "<unknown>")
        text = format_move_by(nick, new, invoker)
    elif event.kind == events.CLIENT_LEFT:
        clid = data["clid"]
        nick = state.client_names.pop(clid, "<unknown>")
        state.join_times.pop(clid, None)
        text = format_leave(nick)
    elif event.kind == events.CLIENT_KICK_CHANNEL:
        clid, reason = data["clid"], data.get("reasonmsg", "")
        nick = state.client_names.get(clid, "?")
        text = format_kick_channel(nick, reason)
        state.client_names.pop(clid, None)
        state.join_times.pop(clid, None)
    elif event.kind == events.CLIENT_KICK_SERVER:
        clid, reason = data["clid"], data.get("reasonmsg", "")
        nick = state.client_names.get(clid, "?")
        text = format_kick_server(nick, reason)
        state.client_names.pop(clid, None)
        state.join_times.pop(clid, None)
    elif event.kind == events.CLIENT_BANNED:
        cldbid = data.get("cldbid", "")
        reason = data.get("reasonmsg", "")
        nick = state.client_names.get(cldbid, "<unknown>")
        text = format_ban(nick, reason)
        state.client_names.pop(cldbid, None)
        state.join_times.pop(cldbid, None)

    if text:
        actions.append(
            MatrixAction(
                room_id=room_id,
                text=text,
                clid=data.get("clid"),
                correlation_id=event.correlation_id,
                event_type=event.kind,
            )
        )
    return actions


def reconcile_presence(
    clientlist: Iterable[dict],
    state: AppState,
    room_id: str,
    now: float,
) -> list[MatrixAction]:
    actions: list[MatrixAction] = []
    known = set(state.client_names.keys())
    current: set[str] = set()
    for client in clientlist:
        if client.get("client_type") != "0":
            continue
        clid = client["clid"]
        current.add(clid)
        if clid not in known:
            nick = client.get("client_nickname", "?")
            state.client_names[clid] = nick
            state.join_times[clid] = now
            actions.append(MatrixAction(room_id=room_id, text=format_join(nick), clid=clid))
    for clid in list(known - current):
        nick = state.client_names.pop(clid, "<unknown>")
        state.join_times.pop(clid, None)
        actions.append(MatrixAction(room_id=room_id, text=format_leave(nick), clid=clid))
    return actions
