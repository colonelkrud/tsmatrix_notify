import argparse
import asyncio
import logging
from types import SimpleNamespace

import pytest

from tsmatrix_notify import main
from tsmatrix_notify.config import ConfigError, MatrixConfig


def test_validate_and_normalize_homeserver_ok():
    assert main.validate_and_normalize_homeserver("https://example.org/", logging.getLogger("test")) == "https://example.org"


def test_validate_and_normalize_homeserver_bad():
    with pytest.raises(ConfigError):
        main.validate_and_normalize_homeserver("not-a-url", logging.getLogger("test"))


def test_await_homeserver_ready_stops_immediately():
    stop_event = SimpleNamespace(is_set=lambda: True)
    ok = asyncio.run(main.await_homeserver_ready("https://example.org", logging.getLogger("test"), stop_event=stop_event))
    assert ok is False


def test_await_homeserver_ready_success(monkeypatch):
    calls = {"n": 0}

    async def fake_probe(*_args, **_kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    async def fake_sleep(_s: float):
        return None

    monkeypatch.setattr(main, "probe_homeserver", fake_probe)
    monkeypatch.setattr(main.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    ok = asyncio.run(main.await_homeserver_ready("https://example.org", logging.getLogger("test"), min_backoff=0.01, max_backoff=0.01))
    assert ok is True


def test_probe_homeserver_false_on_empty():
    assert asyncio.run(main.probe_homeserver("", logging.getLogger("test"))) is False


def test_build_matrix_creds_uses_normalized():
    cfg = MatrixConfig("https://example.org/", "@u:example.org", "tok", "!r:example.org", "/tmp/session.json")
    hs, creds = main.build_matrix_creds(cfg, logging.getLogger("test"))
    assert hs == "https://example.org"
    assert creds.homeserver == "https://example.org"


def test_parse_args_flags(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog", "--debug", "--watchdog"])
    args = main.parse_args()
    assert isinstance(args, argparse.Namespace)
    assert args.debug is True
    assert args.watchdog is True
