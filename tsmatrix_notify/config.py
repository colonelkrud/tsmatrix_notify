from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import platform
import tempfile
from pathlib import Path


class ConfigError(ValueError):
    """Raised when configuration is invalid or incomplete."""


@dataclass(frozen=True)
class TS3Config:
    host: str
    port: int
    user: str
    password: str
    vserver_id: int


@dataclass(frozen=True)
class MatrixConfig:
    homeserver: str
    user_id: str
    access_token: str
    room_id: str
    session_file: str


@dataclass(frozen=True)
class AppConfig:
    ts3: TS3Config
    matrix: MatrixConfig
    bot_messages_file: str
    watchdog_timeout: int
    session_dir: str
    data_dir: Path
    stats_path: Path


def choose_paths(log: logging.Logger, env: dict[str, str] | None = None) -> tuple[str, str, Path, Path]:
    env = env or os.environ
    is_windows = platform.system().lower().startswith("win")

    if is_windows:
        local_app = env.get("LOCALAPPDATA") or tempfile.gettempdir()
        state_root = Path(local_app) / "TSMatrixNotify"
        data_root = Path(local_app) / "TSMatrixNotify"
        session_dir_default = state_root / "session"
        data_dir_default = data_root / "data"
    else:
        home = Path.home()
        state_home = Path(env.get("XDG_STATE_HOME", home / ".local" / "state"))
        data_home = Path(env.get("XDG_DATA_HOME", home / ".local" / "share"))
        session_dir_default = state_home / "tsmatrix_notify" / "session"
        data_dir_default = data_home / "tsmatrix_notify"

    session_dir = Path(env.get("MATRIX_SESSION_DIR", str(session_dir_default)))
    data_dir = Path(env.get("TSMATRIX_DATA_DIR", str(data_dir_default)))

    def ensure_dir(p: Path, label: str) -> Path:
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception as exc:
            log.warning("Failed to create %s dir %s (%r); falling back to temp", label, p, exc)
            fallback = Path(tempfile.gettempdir()) / "tsmatrix_notify" / label
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    session_dir = ensure_dir(session_dir, "session")
    data_dir = ensure_dir(data_dir, "data")

    session_file = env.get("MATRIX_SESSION_FILE")
    if session_file:
        session_file = str(session_file)
    else:
        session_file = str(session_dir / "matrix_session.json")

    stats_path = data_dir / "bot_reviews_stats.json"
    return str(session_dir), session_file, data_dir, stats_path


def load_config(log: logging.Logger, env: dict[str, str] | None = None) -> AppConfig:
    env = env or os.environ
    session_dir, session_file, data_dir, stats_path = choose_paths(log, env=env)

    def _get_int(name: str, default: str) -> int:
        value = env.get(name, default)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} must be an integer (got {value!r}).") from exc

    ts3_host = env.get("TS3_HOST", "127.0.0.1")
    ts3_port = _get_int("TS3_PORT", "10011")
    ts3_user = env.get("TS3_USER", "")
    ts3_password = env.get("TS3_PASSWORD", "")
    ts3_vserver_id = _get_int("TS3_VSERVER_ID", "1")

    matrix_homeserver = env.get("MATRIX_HOMESERVER", "")
    matrix_user_id = env.get("MATRIX_USER_ID", "")
    matrix_access_token = env.get("MATRIX_ACCESS_TOKEN", "")
    matrix_room_id = env.get("MATRIX_ROOM_ID", "")

    bot_messages_file = env.get("BOT_MESSAGES_FILE", "bot_messages.json")
    watchdog_timeout = _get_int("WATCHDOG_TIMEOUT", "1800")

    missing = []
    if not ts3_user:
        missing.append("TS3_USER")
    if not ts3_password:
        missing.append("TS3_PASSWORD")
    if not matrix_homeserver:
        missing.append("MATRIX_HOMESERVER")
    if not matrix_user_id:
        missing.append("MATRIX_USER_ID")
    if not matrix_access_token:
        missing.append("MATRIX_ACCESS_TOKEN")
    if not matrix_room_id:
        missing.append("MATRIX_ROOM_ID")

    if missing:
        raise ConfigError("Missing required environment variables: " + ", ".join(missing))

    ts3 = TS3Config(
        host=ts3_host,
        port=ts3_port,
        user=ts3_user,
        password=ts3_password,
        vserver_id=ts3_vserver_id,
    )
    matrix = MatrixConfig(
        homeserver=matrix_homeserver,
        user_id=matrix_user_id,
        access_token=matrix_access_token,
        room_id=matrix_room_id,
        session_file=session_file,
    )
    return AppConfig(
        ts3=ts3,
        matrix=matrix,
        bot_messages_file=bot_messages_file,
        watchdog_timeout=watchdog_timeout,
        session_dir=session_dir,
        data_dir=data_dir,
        stats_path=stats_path,
    )
