import logging

import pytest

from tsmatrix_notify.config import ConfigError, choose_paths, load_config
from tests.test_config import BASE_ENV


def test_choose_paths_uses_defaults_and_mapping(tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path / "state"), "XDG_DATA_HOME": str(tmp_path / "data")}
    session_dir, session_file, data_dir, stats_path = choose_paths(logging.getLogger("test"), env=env)
    assert session_dir
    assert session_file.endswith("matrix_session.json")
    assert data_dir.exists()
    assert stats_path.name == "bot_reviews_stats.json"


def test_invalid_numeric_parse_message():
    env = dict(BASE_ENV)
    env["TS3_PORT"] = "abc"
    with pytest.raises(ConfigError, match="TS3_PORT must be an integer"):
        load_config(logging.getLogger("test"), env=env)
