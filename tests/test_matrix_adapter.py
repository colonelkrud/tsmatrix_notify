import asyncio
import logging
import threading
import time

import pytest

from tsmatrix_notify.adapters.matrix_simplematrixbotlib import MatrixBotAdapter, MatrixSendQueueConfig, MatrixSendQueueFull


class DummyAPI:
    def __init__(self):
        self.calls = []
        self.failures = []

    async def send_text_message(self, room_id, text):
        self.calls.append((room_id, text))
        if self.failures:
            raise self.failures.pop(0)


class DummyBot:
    def __init__(self):
        self.api = DummyAPI()
        self.async_client = type("C", (), {"access_token": "tok"})()


class LoopThread:
    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever)
        self.thread.start()
        return self.loop

    def __exit__(self, *_exc):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_send_text_success():
    bot = DummyBot()
    with LoopThread() as loop:
        adapter = MatrixBotAdapter(bot, loop, logging.getLogger("test"))
        adapter.send_text("!r", "hello", clid="1", correlation_id="c1", event_type="joined")
        _wait_for(lambda: bot.api.calls == [("!r", "hello")])
        asyncio.run_coroutine_threadsafe(adapter.close(), loop).result(timeout=2)


def test_send_text_retries_with_backoff(caplog):
    bot = DummyBot()
    bot.api.failures = [RuntimeError("send failed")]
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    with LoopThread() as loop:
        adapter = MatrixBotAdapter(
            bot,
            loop,
            logging.getLogger("test"),
            MatrixSendQueueConfig(max_size=10, retry_max_attempts=2, retry_min_backoff_s=1, retry_max_backoff_s=10, retry_jitter_ratio=0),
            sleep=fake_sleep,
            random_provider=lambda: 0.0,
        )
        with caplog.at_level(logging.WARNING):
            adapter.send_text("!r", "hello", correlation_id="c1")
            _wait_for(lambda: len(bot.api.calls) == 2)
        assert sleeps == [1.0]
        assert "matrix_send_retry" in caplog.text
        asyncio.run_coroutine_threadsafe(adapter.close(), loop).result(timeout=2)


def test_bounded_queue_overflow_drops_newest(caplog):
    bot = DummyBot()
    blocker = asyncio.Event()

    async def blocked_sleep(_delay):
        await blocker.wait()

    bot.api.failures = [RuntimeError("first blocks retries")]
    with LoopThread() as loop:
        adapter = MatrixBotAdapter(
            bot,
            loop,
            logging.getLogger("test"),
            MatrixSendQueueConfig(max_size=1, retry_max_attempts=2, retry_min_backoff_s=30, retry_max_backoff_s=30),
            sleep=blocked_sleep,
        )
        with caplog.at_level(logging.WARNING):
            adapter.send_text("!r", "first")
            adapter.send_text("!r", "second")
            with pytest.raises(MatrixSendQueueFull):
                adapter.send_text("!r", "third")
        assert "matrix_send_queue_overflow_drop" in caplog.text
        loop.call_soon_threadsafe(blocker.set)
        asyncio.run_coroutine_threadsafe(adapter.close(), loop).result(timeout=2)


def test_is_ready_and_loop_property():
    bot = DummyBot()
    loop = asyncio.new_event_loop()
    adapter = MatrixBotAdapter(bot, loop, logging.getLogger("test"))
    assert adapter.is_ready() is True
    bot.async_client.access_token = None
    assert adapter.is_ready() is False
    next_loop = asyncio.new_event_loop()
    adapter.loop = next_loop
    assert adapter.loop is next_loop
    loop.run_until_complete(adapter.close())
    loop.close()
    next_loop.close()
