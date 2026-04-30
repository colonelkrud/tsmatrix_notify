import logging
from pathlib import Path

import pytest

from tsmatrix_notify.config import ConfigError, load_config, redact_secret


BASE_ENV = {
    "TS3_HOST": "127.0.0.1",
    "TS3_PORT": "10011",
    "TS3_USER": "serveradmin",
    "TS3_PASSWORD": "secret",
    "TS3_VSERVER_ID": "1",
    "MATRIX_HOMESERVER": "https://matrix.example.com",
    "MATRIX_USER_ID": "@bot:example.com",
    "MATRIX_ACCESS_TOKEN": "token1234",
    "MATRIX_ROOM_ID": "!roomid:example.com",
}


def test_load_config_missing_env():
    with pytest.raises(ConfigError):
        load_config(logging.getLogger("test"), env={})


def test_load_config_with_overrides(tmp_path):
    env = dict(BASE_ENV)
    env.update({"MATRIX_SESSION_DIR": str(tmp_path / "session"), "TSMATRIX_DATA_DIR": str(tmp_path / "data")})
    cfg = load_config(logging.getLogger("test"), env=env)
    assert cfg.ts3.port == 10011
    assert cfg.matrix.room_id == "!roomid:example.com"
    assert Path(cfg.matrix.session_file).parent == Path(env["MATRIX_SESSION_DIR"])


@pytest.mark.parametrize("key,val", [("TS3_PORT", "0"), ("TS3_PORT", "70000"), ("TS3_VSERVER_ID", "0"), ("HEALTHCHECK_PORT", "99999")])
def test_numeric_validation_errors(key, val):
    env = dict(BASE_ENV)
    env[key] = val
    with pytest.raises(ConfigError):
        load_config(logging.getLogger("test"), env=env)


@pytest.mark.parametrize(
    "key,val",
    [
        ("MATRIX_HOMESERVER", "matrix.example.com"),
        ("MATRIX_USER_ID", "bot:example.com"),
        ("MATRIX_ROOM_ID", "room:example.com"),
    ],
)
def test_matrix_identifier_validation(key, val):
    env = dict(BASE_ENV)
    env[key] = val
    with pytest.raises(ConfigError):
        load_config(logging.getLogger("test"), env=env)


def test_health_paths_normalize():
    env = dict(BASE_ENV)
    env["HEALTHCHECK_PATH_LIVE"] = "live"
    env["HEALTHCHECK_PATH_READY"] = "ready"
    cfg = load_config(logging.getLogger("test"), env=env)
    assert cfg.health.path_live == "/live"
    assert cfg.health.path_ready == "/ready"


def test_secret_redaction_in_logs(caplog):
    with caplog.at_level(logging.INFO):
        load_config(logging.getLogger("test"), env=dict(BASE_ENV))
    assert "token1234" not in caplog.text
    assert "to***34" in caplog.text
    assert redact_secret("abcd") == "****"
