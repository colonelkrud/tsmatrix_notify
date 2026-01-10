from __future__ import annotations

from typing import Callable


def format_uptime(seconds: float) -> str:
    delta = max(0, int(seconds))
    hours, rem = divmod(delta, 3600)
    mins, secs = divmod(rem, 60)
    return f"{hours}h{mins}m{secs}s"


def format_join(nick: str) -> str:
    return f"▶️ {nick} joined TS3."


def format_leave(nick: str) -> str:
    return f"◀️ {nick} left TS3."


def format_move(nick: str, channel_id: str) -> str:
    return f"🔀 {nick} moved to channel {channel_id}"


def format_move_by(nick: str, channel_id: str, invoker: str) -> str:
    return f"🔀 {nick} was moved to {channel_id} by {invoker}"


def format_kick_channel(nick: str, reason: str) -> str:
    return f"⚠️ {nick} kicked from channel. Reason: {reason}"


def format_kick_server(nick: str, reason: str) -> str:
    return f"⚠️ {nick} kicked from server. Reason: {reason}"


def format_ban(nick: str, reason: str) -> str:
    return f"⛔️ {nick} banned. Reason: {reason}"


def build_who_body(
    clientlist: list[dict],
    clientinfo: Callable[[str], dict],
    join_times: dict[str, float],
    now: float,
) -> tuple[str, int]:
    lines: list[str] = []
    for client in clientlist:
        if client.get("client_type") != "0":
            continue
        clid = client["clid"]
        info = clientinfo(clid)
        nick = info.get("client_nickname", "?")
        started = join_times.get(clid)
        if started is not None:
            up = format_uptime(now - started)
        else:
            up = "unknown"
        parts = []
        if info.get("client_away") == "1":
            msg = info.get("client_away_message", "").strip()
            parts.append(f"away{': '+msg if msg else ''}")
        if info.get("client_input_muted") == "1":
            parts.append("mic muted")
        if info.get("client_output_muted") == "1":
            parts.append("spk muted")
        status = f" [{' / '.join(parts)}]" if parts else ""
        lines.append(f"- {nick} online: {up}{status}")

    if not lines:
        return "👥 No clients online.", 0
    return "👥 Online TS3 users:\n" + "\n".join(lines), len(lines)
