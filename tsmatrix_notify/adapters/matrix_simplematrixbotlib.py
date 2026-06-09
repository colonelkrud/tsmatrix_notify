from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import random

import simplematrixbotlib as botlib

from tsmatrix_notify.ports.matrix_port import MatrixPort


@dataclass(frozen=True)
class MatrixSendQueueConfig:
    max_size: int = 100
    retry_max_attempts: int = 5
    retry_min_backoff_s: float = 1.0
    retry_max_backoff_s: float = 30.0
    retry_jitter_ratio: float = 0.25
    enqueue_timeout_s: float = 1.0


@dataclass(frozen=True)
class QueuedMatrixMessage:
    room_id: str
    text: str
    clid: str | None
    correlation_id: str
    event_type: str


class MatrixSendQueueFull(RuntimeError):
    """Raised when the bounded Matrix send queue is full."""


class MatrixBotAdapter(MatrixPort):
    def __init__(
        self,
        bot: botlib.Bot,
        loop: asyncio.AbstractEventLoop,
        log: logging.Logger,
        queue_config: MatrixSendQueueConfig | None = None,
        *,
        sleep=asyncio.sleep,
        random_provider=random.random,
    ):
        self._bot = bot
        self._loop = loop
        self._log = log
        self._queue_config = queue_config or MatrixSendQueueConfig()
        self._queue: asyncio.Queue[QueuedMatrixMessage | None] = asyncio.Queue(maxsize=self._queue_config.max_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._sleep = sleep
        self._random_provider = random_provider
        self._closed = False

    def send_text(
        self,
        room_id: str,
        text: str,
        clid: str | None = None,
        *,
        correlation_id: str | None = None,
        event_type: str | None = None,
    ) -> None:
        msg = self._make_message(room_id, text, clid, correlation_id, event_type)
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._enqueue_nowait(msg)
            return
        fut = asyncio.run_coroutine_threadsafe(self._enqueue(msg), self._loop)
        fut.result(timeout=self._queue_config.enqueue_timeout_s)

    async def send_text_async(
        self,
        room_id: str,
        text: str,
        clid: str | None = None,
        *,
        correlation_id: str | None = None,
        event_type: str | None = None,
    ) -> None:
        msg = self._make_message(room_id, text, clid, correlation_id, event_type)
        await self._enqueue(msg)

    def _make_message(
        self,
        room_id: str,
        text: str,
        clid: str | None,
        correlation_id: str | None,
        event_type: str | None,
    ) -> QueuedMatrixMessage:
        if self._closed:
            raise RuntimeError("Matrix send queue is closed")
        return QueuedMatrixMessage(
            room_id=room_id,
            text=text,
            clid=clid,
            correlation_id=correlation_id or "-",
            event_type=event_type or "matrix_message",
        )

    async def _enqueue(self, msg: QueuedMatrixMessage) -> None:
        self._enqueue_nowait(msg)

    def _enqueue_nowait(self, msg: QueuedMatrixMessage) -> None:
        self._ensure_worker()
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull as exc:
            self._log.warning(
                "matrix_send_queue_overflow_drop",
                extra={
                    "correlation_id": msg.correlation_id,
                    "event_type": msg.event_type,
                    "ts3_client_id": msg.clid,
                    "matrix_room_id": msg.room_id,
                    "queue_size": self._queue.qsize(),
                    "queue_max_size": self._queue_config.max_size,
                    "drop_policy": "drop_newest",
                    "error_type": type(exc).__name__,
                },
            )
            raise MatrixSendQueueFull("Matrix send queue is full; dropped newest message") from exc
        self._log.info(
            "matrix_send_queued",
            extra={
                "correlation_id": msg.correlation_id,
                "event_type": msg.event_type,
                "ts3_client_id": msg.clid,
                "matrix_room_id": msg.room_id,
                "queue_size": self._queue.qsize(),
            },
        )

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = self._loop.create_task(self._send_worker())

    async def _send_worker(self) -> None:
        while True:
            msg = await self._queue.get()
            if msg is None:
                self._queue.task_done()
                return
            try:
                await self._send_with_retries(msg)
            finally:
                self._queue.task_done()

    async def _send_with_retries(self, msg: QueuedMatrixMessage) -> None:
        base_delay = self._queue_config.retry_min_backoff_s
        for attempt in range(1, self._queue_config.retry_max_attempts + 1):
            try:
                self._log.info(
                    "matrix_send_attempt",
                    extra={
                        "correlation_id": msg.correlation_id,
                        "event_type": msg.event_type,
                        "ts3_client_id": msg.clid,
                        "matrix_room_id": msg.room_id,
                        "send_attempt": attempt,
                    },
                )
                await self._bot.api.send_text_message(msg.room_id, msg.text)
                self._log.info(
                    "matrix_send_success",
                    extra={
                        "correlation_id": msg.correlation_id,
                        "event_type": msg.event_type,
                        "ts3_client_id": msg.clid,
                        "matrix_room_id": msg.room_id,
                        "send_attempt": attempt,
                    },
                )
                return
            except Exception as exc:  # noqa: BLE001 - adapter must isolate send failures
                if attempt >= self._queue_config.retry_max_attempts:
                    self._log.warning(
                        "matrix_send_failure",
                        extra={
                            "correlation_id": msg.correlation_id,
                            "event_type": msg.event_type,
                            "ts3_client_id": msg.clid,
                            "matrix_room_id": msg.room_id,
                            "send_attempt": attempt,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return
                delay = min(self._queue_config.retry_max_backoff_s, base_delay)
                jitter = self._random_provider() * delay * self._queue_config.retry_jitter_ratio
                wait = min(self._queue_config.retry_max_backoff_s, delay + jitter)
                self._log.warning(
                    "matrix_send_retry",
                    extra={
                        "correlation_id": msg.correlation_id,
                        "event_type": msg.event_type,
                        "ts3_client_id": msg.clid,
                        "matrix_room_id": msg.room_id,
                        "send_attempt": attempt,
                        "retry_delay_s": wait,
                        "error_type": type(exc).__name__,
                    },
                )
                await self._sleep(wait)
                base_delay = min(self._queue_config.retry_max_backoff_s, base_delay * 2)

    async def close(self, *, drain: bool = True, timeout_s: float = 10.0) -> None:
        self._closed = True
        if drain:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout_s)
            except asyncio.TimeoutError:
                self._log.warning("matrix_send_queue_drain_timeout", extra={"queue_size": self._queue.qsize()})
        if self._worker_task and not self._worker_task.done():
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout_s)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                await asyncio.gather(self._worker_task, return_exceptions=True)

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
