import logging
from pathlib import Path

import pytest

from tsmatrix_notify.config import ConfigError, load_config


def test_load_config_missing_env():
    with pytest.raises(ConfigError):
        load_config(logging.getLogger("test"), env={})


def test_load_config_with_overrides(tmp_path):
    env = {
        "TS3_HOST": "127.0.0.1",
        "TS3_PORT": "10011",
        "TS3_USER": "serveradmin",
        "TS3_PASSWORD": "secret",
        "TS3_VSERVER_ID": "1",
        "MATRIX_HOMESERVER": "https://matrix.example.com",
        "MATRIX_USER_ID": "@bot:example.com",
        "MATRIX_ACCESS_TOKEN": "token",
        "MATRIX_ROOM_ID": "!roomid:example.com",
        "MATRIX_SESSION_DIR": str(tmp_path / "session"),
        "TSMATRIX_DATA_DIR": str(tmp_path / "data"),
    }

    cfg = load_config(logging.getLogger("test"), env=env)

    assert cfg.ts3.port == 10011
    assert cfg.matrix.room_id == "!roomid:example.com"
    assert Path(cfg.matrix.session_file).parent == Path(env["MATRIX_SESSION_DIR"])
