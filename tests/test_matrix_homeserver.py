import logging
from types import SimpleNamespace

import pytest
from aiohttp.client_exceptions import ClientConnectorError

from tsmatrix_notify.config import ConfigError
from tsmatrix_notify.main import (
    build_matrix_creds,
    is_transient_matrix_error,
    validate_and_normalize_homeserver,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://matrix.example.com", "https://matrix.example.com"),
        ("https://matrix.example.com/", "https://matrix.example.com"),
    ],
)
def test_validate_and_normalize_homeserver_valid(raw, expected):
    log = logging.getLogger("test")
    assert validate_and_normalize_homeserver(raw, log) == expected


@pytest.mark.parametrize(
    "raw",
    ["matrix.example.com", "", "   ", "ftp://matrix.example.com", "https://"],
)
def test_validate_and_normalize_homeserver_invalid(raw, caplog):
    log = logging.getLogger("test")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConfigError):
            validate_and_normalize_homeserver(raw, log)
    assert any("Invalid Matrix homeserver" in record.message for record in caplog.records)


def test_build_matrix_creds_reads_homeserver_once():
    class FlakyHomeserver:
        def __init__(self):
            self.calls = 0

        def __str__(self):
            self.calls += 1
            if self.calls == 1:
                return "https://matrix.example.com"
            return "not-a-url"

    hs = FlakyHomeserver()
    matrix_config = SimpleNamespace(
        homeserver=hs,
        user_id="@bot:example.com",
        access_token="token",
        session_file="session.json",
    )
    log = logging.getLogger("test")
    normalized, creds = build_matrix_creds(matrix_config, log)
    assert hs.calls == 1
    assert normalized == "https://matrix.example.com"
    assert creds.homeserver == "https://matrix.example.com"


def test_is_transient_matrix_error():
    conn_key = SimpleNamespace(host="matrix.example.com", port=443, ssl=None)
    connector_error = ClientConnectorError(conn_key, OSError("boom"))
    assert is_transient_matrix_error(TimeoutError("timeout")) is True
    assert is_transient_matrix_error(connector_error) is True
    assert is_transient_matrix_error(ConfigError("Invalid Matrix homeserver: 'bad'")) is False
