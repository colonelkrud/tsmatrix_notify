import asyncio
import logging
from types import SimpleNamespace

from tsmatrix_notify import main


def test_sync_level_and_setup_logger_levels():
    log = main.setup_logger(debug=False, trace=False)
    assert log.level == logging.INFO
    log2 = main.setup_logger(debug=True, trace=False)
    assert log2.level == logging.DEBUG
    log3 = main.setup_logger(debug=False, trace=True)
    assert log3.level == main.SYNC_LEVEL_NUM


def test_sync_method_executes_when_enabled(caplog):
    log = logging.getLogger("sync-test")
    log.setLevel(main.SYNC_LEVEL_NUM)
    with caplog.at_level(main.SYNC_LEVEL_NUM):
        log.sync("hello %s", "world")
    assert "hello world" in caplog.text


def test_shutdown_bot_success_and_error(caplog):
    class C:
        async def close(self):
            return None

    bot = SimpleNamespace(async_client=C())
    asyncio.run(main.shutdown_bot(bot, logging.getLogger("test")))

    class Bad:
        async def close(self):
            raise RuntimeError("x")

    with caplog.at_level(logging.ERROR):
        asyncio.run(main.shutdown_bot(SimpleNamespace(async_client=Bad()), logging.getLogger("test")))
    assert "Error during bot shutdown" in caplog.text


def test_probe_homeserver_failure_branch(monkeypatch):
    class BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, *_a, **_k):
            raise RuntimeError("down")

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda: BoomSession())
    ok = asyncio.run(main.probe_homeserver("https://example.org", logging.getLogger("test")))
    assert ok is False


def test_connect_matrix_creates_bot(monkeypatch):
    creds = SimpleNamespace(username="u", homeserver="https://h")
    dummy = object()
    monkeypatch.setattr(main.botlib, "Bot", lambda c: dummy)
    assert main.connect_matrix(creds, logging.getLogger("test")) is dummy
