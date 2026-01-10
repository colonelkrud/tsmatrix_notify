from __future__ import annotations

import asyncio
import logging

import simplematrixbotlib as botlib

from tsmatrix_notify.ports.matrix_port import MatrixPort


class MatrixBotAdapter(MatrixPort):
    def __init__(self, bot: botlib.Bot, loop: asyncio.AbstractEventLoop, log: logging.Logger):
        self._bot = bot
        self._loop = loop
        self._log = log

    def send_text(self, room_id: str, text: str, clid: str | None = None) -> None:
        coro = self._bot.api.send_text_message(room_id, text)
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        fut.add_done_callback(self._log_result(clid))

    def _log_result(self, clid: str | None):
        def _on_done(fut):
            try:
                fut.result()
                self._log.info("Matrix message sent%s", f" for clid={clid}" if clid else "")
            except Exception as exc:
                self._log.error(
                    "Failed to send Matrix message%s: %s",
                    f" for clid={clid}" if clid else "",
                    exc,
                )

        return _on_done

    def is_ready(self) -> bool:
        client = getattr(self._bot, "async_client", None)
        return bool(client and getattr(client, "access_token", None))

    @property
    def bot(self) -> botlib.Bot:
        return self._bot

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    @loop.setter
    def loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
