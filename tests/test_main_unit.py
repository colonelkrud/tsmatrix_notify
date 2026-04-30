import argparse
import asyncio
import logging
from types import SimpleNamespace

import pytest

from tsmatrix_notify import main
from tsmatrix_notify.config import ConfigError, MatrixConfig


def test_validate_and_normalize_homeserver_ok():
    log = logging.getLogger("test")
    assert main.validate_and_normalize_homeserver("https://example.org/", log) == "https://example.org"


def test_validate_and_normalize_homeserver_bad():
    log = logging.getLogger("test")
    with pytest.raises(ConfigError):
        main.validate_and_normalize_homeserver("not-a-url", log)


@pytest.mark.asyncio
async def test_await_homeserver_ready_stops(monkeypatch):
    log = logging.getLogger("test")
    stop_event = SimpleNamespace(is_set=lambda: True)
    ok = await main.await_homeserver_ready("https://example.org", log, stop_event=stop_event)
    assert ok is False


@pytest.mark.asyncio
async def test_await_homeserver_ready_success(monkeypatch):
    log = logging.getLogger("test")
    calls = {"n": 0}

    async def fake_probe(*_args, **_kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(main, "probe_homeserver", fake_probe)
    monkeypatch.setattr(main.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _s: asyncio.sleep(0))
    ok = await main.await_homeserver_ready("https://example.org", log, min_backoff=0.01, max_backoff=0.01)
    assert ok is True


def test_build_matrix_creds_uses_normalized():
    cfg = MatrixConfig(
        homeserver="https://example.org/",
        user_id="@u:example.org",
        access_token="tok",
        room_id="!r:example.org",
        session_file="/tmp/session.json",
    )
    hs, creds = main.build_matrix_creds(cfg, logging.getLogger("test"))
    assert hs == "https://example.org"
    assert creds.homeserver == "https://example.org"


def test_parse_args_flags(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog", "--debug", "--watchdog"])
    args = main.parse_args()
    assert isinstance(args, argparse.Namespace)
    assert args.debug is True
    assert args.watchdog is True
