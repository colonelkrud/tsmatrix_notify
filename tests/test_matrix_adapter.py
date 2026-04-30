import asyncio
import logging

from tsmatrix_notify.adapters.matrix_simplematrixbotlib import MatrixBotAdapter


class DummyFuture:
    def __init__(self, error=None):
        self._error = error

    def add_done_callback(self, cb):
        cb(self)

    def result(self):
        if self._error:
            raise self._error
        return None


class DummyAPI:
    def __init__(self):
        self.calls = []

    async def send_text_message(self, room_id, text):
        self.calls.append((room_id, text))


class DummyBot:
    def __init__(self):
        self.api = DummyAPI()
        self.async_client = type("C", (), {"access_token": "tok"})()


def test_send_text_success(monkeypatch):
    bot = DummyBot()
    adapter = MatrixBotAdapter(bot, asyncio.new_event_loop(), logging.getLogger("test"))
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", lambda coro, _loop: (asyncio.run(coro), DummyFuture())[1])
    adapter.send_text("!r", "hello", clid="1")
    assert bot.api.calls == [("!r", "hello")]


def test_send_text_error_callback(monkeypatch, caplog):
    bot = DummyBot()
    adapter = MatrixBotAdapter(bot, asyncio.new_event_loop(), logging.getLogger("test"))
    def _fail(coro, _loop):
        coro.close()
        return DummyFuture(error=RuntimeError("send failed"))
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _fail)
    adapter.send_text("!r", "hello")
    assert "Failed to send Matrix message" in caplog.text


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
