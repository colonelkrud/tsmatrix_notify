import argparse
import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest

from tsmatrix_notify import main
from tsmatrix_notify.config import ConfigError, MatrixConfig, MatrixSendConfig


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
    cfg = MatrixConfig("https://example.org/", "@u:example.org", "tok", "!r:example.org", "/tmp/session.json", MatrixSendConfig(100, 5, 1.0, 30.0, 0.25))
    hs, creds = main.build_matrix_creds(cfg, logging.getLogger("test"))
    assert hs == "https://example.org"
    assert creds.homeserver == "https://example.org"


def test_parse_args_flags(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog", "--debug", "--watchdog"])
    args = main.parse_args()
    assert isinstance(args, argparse.Namespace)
    assert args.debug is True
    assert args.watchdog is True


def test_logging_formatter_renders_correlation_id_and_defaults(capsys):
    log = main.setup_logger(debug=False, trace=False)
    try:
        log.info("matrix_send_attempt", extra={"correlation_id": "corr-123"})
        rendered = capsys.readouterr().err
        assert "corr=corr-123" in rendered
        assert "event=-" in rendered
        assert "endpoint=-" in rendered
    finally:
        for handler in list(log.handlers):
            log.removeHandler(handler)


def test_sync_validation_log_filter_cancels_bot_task_after_threshold(caplog):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        async def sleeper():
            await asyncio.sleep(10)

        task = loop.create_task(sleeper())
        tracker = main.SyncValidationFailureTracker(logging.getLogger("test"), reconnect_threshold=1)
        health = main.HealthState(live=True, ready=True, status="ready")
        restart_requested = threading.Event()
        filt = main._SyncValidationLogFilter(tracker, loop, lambda: task, health, restart_requested)
        record = logging.LogRecord(
            "nio", logging.ERROR, __file__, 1,
            "Error validating response: 'next_batch' is a required property", (), None,
        )
        with caplog.at_level(logging.WARNING):
            assert filt.filter(record) is True
        loop.run_until_complete(asyncio.sleep(0))
        assert task.cancelled() is True
        assert restart_requested.is_set() is True
        assert health.ready is False
        assert "matrix_sync_validation_failure" in caplog.text
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_sync_validation_cancel_uses_restart_backoff_instead_of_raising(caplog):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        async def sleeper():
            await asyncio.sleep(10)

        task = loop.create_task(sleeper())
        task.cancel()
        restart_requested = threading.Event()
        restart_requested.set()
        health = main.HealthState(live=True, ready=True, status="ready")
        supervisor = main.MatrixReconnectSupervisor(logging.getLogger("test"))

        with caplog.at_level(logging.WARNING):
            delay = main._run_matrix_bot_task(
                loop,
                task,
                use_watchdog=False,
                watchdog_timeout=1,
                sync_validation_restart_requested=restart_requested,
                matrix_supervisor=supervisor,
                health_state=health,
                log=logging.getLogger("test"),
            )

        assert delay is not None
        assert delay > 0
        assert health.ready is False
        assert "matrix_sync_exception" in caplog.text
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_operator_cancelled_error_still_raises_without_sync_restart():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        async def sleeper():
            await asyncio.sleep(10)

        task = loop.create_task(sleeper())
        task.cancel()
        health = main.HealthState(live=True, ready=True, status="ready")
        supervisor = main.MatrixReconnectSupervisor(logging.getLogger("test"))

        with pytest.raises(asyncio.CancelledError):
            main._run_matrix_bot_task(
                loop,
                task,
                use_watchdog=False,
                watchdog_timeout=1,
                sync_validation_restart_requested=threading.Event(),
                matrix_supervisor=supervisor,
                health_state=health,
                log=logging.getLogger("test"),
            )
    finally:
        loop.close()
        asyncio.set_event_loop(None)
