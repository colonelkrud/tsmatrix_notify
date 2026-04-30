from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import platform
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised when configuration is invalid or incomplete."""


MATRIX_USER_ID_RE = re.compile(r"^@[^:\s]+:[^:\s]+$")
MATRIX_ROOM_ID_RE = re.compile(r"^![^:\s]+:[^:\s]+$")
MATRIX_ROOM_ALIAS_RE = re.compile(r"^#[^:\s]+:[^:\s]+$")


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
    health: "HealthConfig"


@dataclass(frozen=True)
class HealthConfig:
    host: str
    port: int
    path_live: str
    path_ready: str


def _require_non_empty(name: str, value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ConfigError(f"{name} must be set and non-empty.")
    return normalized


def _require_int(name: str, value: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}.")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{name} must be <= {maximum}.")
    return parsed


def _validate_matrix_homeserver(hs: str) -> str:
    parsed = urlparse(hs)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("MATRIX_HOMESERVER must be a valid http/https URL with a host.")
    return hs.rstrip("/")


def _normalize_health_path(path: str, default_path: str) -> str:
    raw = (path or default_path).strip() or default_path
    return raw if raw.startswith("/") else f"/{raw}"


def redact_secret(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def log_config_summary(log: logging.Logger, cfg: "AppConfig") -> None:
    log.info(
        "Config loaded: TS3=%s:%d vserver=%d Matrix=%s user=%s room=%s token=%s",
        cfg.ts3.host,
        cfg.ts3.port,
        cfg.ts3.vserver_id,
        cfg.matrix.homeserver,
        cfg.matrix.user_id,
        cfg.matrix.room_id,
        redact_secret(cfg.matrix.access_token),
    )


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

    ts3_host = _require_non_empty("TS3_HOST", env.get("TS3_HOST", "127.0.0.1"))
    ts3_port = _require_int("TS3_PORT", env.get("TS3_PORT", "10011"), minimum=1, maximum=65535)
    ts3_user = _require_non_empty("TS3_USER", env.get("TS3_USER", ""))
    ts3_password = _require_non_empty("TS3_PASSWORD", env.get("TS3_PASSWORD", ""))
    ts3_vserver_id = _require_int("TS3_VSERVER_ID", env.get("TS3_VSERVER_ID", "1"), minimum=1)

    matrix_homeserver = _validate_matrix_homeserver(_require_non_empty("MATRIX_HOMESERVER", env.get("MATRIX_HOMESERVER", "")))
    matrix_user_id = _require_non_empty("MATRIX_USER_ID", env.get("MATRIX_USER_ID", ""))
    matrix_access_token = _require_non_empty("MATRIX_ACCESS_TOKEN", env.get("MATRIX_ACCESS_TOKEN", ""))
    matrix_room_id = _require_non_empty("MATRIX_ROOM_ID", env.get("MATRIX_ROOM_ID", ""))

    if not MATRIX_USER_ID_RE.match(matrix_user_id):
        raise ConfigError("MATRIX_USER_ID must look like @user:server.")
    if not (MATRIX_ROOM_ID_RE.match(matrix_room_id) or MATRIX_ROOM_ALIAS_RE.match(matrix_room_id)):
        raise ConfigError("MATRIX_ROOM_ID must look like !room:server or #alias:server.")

    bot_messages_file = env.get("BOT_MESSAGES_FILE", "bot_messages.json")
    watchdog_timeout = _require_int("WATCHDOG_TIMEOUT", env.get("WATCHDOG_TIMEOUT", "1800"), minimum=1)
    health_host = (env.get("HEALTHCHECK_HOST", "0.0.0.0") or "0.0.0.0").strip()
    health_port = _require_int("HEALTHCHECK_PORT", env.get("HEALTHCHECK_PORT", "8080"), minimum=1, maximum=65535)
    health_path_live = _normalize_health_path(env.get("HEALTHCHECK_PATH_LIVE", "/healthz/live"), "/healthz/live")
    health_path_ready = _normalize_health_path(env.get("HEALTHCHECK_PATH_READY", "/healthz/ready"), "/healthz/ready")

    ts3 = TS3Config(host=ts3_host, port=ts3_port, user=ts3_user, password=ts3_password, vserver_id=ts3_vserver_id)
    matrix = MatrixConfig(homeserver=matrix_homeserver, user_id=matrix_user_id, access_token=matrix_access_token, room_id=matrix_room_id, session_file=session_file)
    cfg = AppConfig(
        ts3=ts3,
        matrix=matrix,
        bot_messages_file=bot_messages_file,
        watchdog_timeout=watchdog_timeout,
        session_dir=session_dir,
        data_dir=data_dir,
        stats_path=stats_path,
        health=HealthConfig(host=health_host, port=health_port, path_live=health_path_live, path_ready=health_path_ready),
    )
    log_config_summary(log, cfg)
    return cfg
