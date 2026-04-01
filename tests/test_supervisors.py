import asyncio
import logging
import threading

from tsmatrix_notify.application.supervisors import (
    ExponentialBackoff,
    MatrixReconnectSupervisor,
    TS3ReconnectSupervisor,
    make_ts3_thread_excepthook,
)


class FakeTS3:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls = 0

    def reconnect(self) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")


def test_ts3_thread_excepthook_requests_restart():
    event = threading.Event()
    log = logging.getLogger("test")
    called = {}

    def base_hook(args):
        called["base"] = True

    hook = make_ts3_thread_excepthook(event, log, base_hook=base_hook)
    thread = threading.Thread(name="Thread-1 (_recv)")
    exc = ConnectionError("reset")
    args = threading.ExceptHookArgs((type(exc), exc, None, thread))

    hook(args)

    assert event.is_set()
    assert called["base"] is True


def test_ts3_reconnect_backoff_and_retry():
    event = threading.Event()
    event.set()
    log = logging.getLogger("test")
    fake_ts3 = FakeTS3(fail_times=1)
    delays = []

    def sleep(duration: float) -> None:
        delays.append(duration)

    backoff = ExponentialBackoff(min_delay=1.0, max_delay=10.0, random_provider=lambda: 0.0)
    supervisor = TS3ReconnectSupervisor(fake_ts3, event, log, backoff=backoff, sleep=sleep)

    supervisor.reconnect_with_backoff()

    assert fake_ts3.calls == 2
    assert delays == [1.0]
    assert event.is_set() is False


def test_matrix_invalid_homeserver_uses_config_delay(caplog):
    log = logging.getLogger("test")
    supervisor = MatrixReconnectSupervisor(log, invalid_config_delay=60.0)

    with caplog.at_level(logging.INFO):
        delay = supervisor.handle_error(ValueError("Invalid Homeserver"))

    assert delay == 60.0
    assert any("Matrix config invalid" in record.message for record in caplog.records)


def test_matrix_transient_error_uses_backoff():
    log = logging.getLogger("test")
    backoff = ExponentialBackoff(min_delay=2.0, max_delay=10.0, random_provider=lambda: 0.0)
    supervisor = MatrixReconnectSupervisor(log, backoff=backoff)

    delay = supervisor.handle_error(asyncio.TimeoutError("timeout"))

    assert delay == 2.0
