from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import random
import threading
import time
import traceback
from typing import Callable, Any

from aiohttp.client_exceptions import ClientConnectorError

from tsmatrix_notify.config import ConfigError
from tsmatrix_notify.ports.ts3_port import TS3Port


def is_transient_matrix_error(exc: Exception) -> bool:
    transient_types = (asyncio.TimeoutError, TimeoutError, ClientConnectorError, ConnectionError)
    if isinstance(exc, transient_types):
        return True
    if isinstance(exc, Exception) and "M_UNKNOWN" in str(exc):
        return True
    return False


def is_invalid_homeserver_error(exc: Exception) -> bool:
    if isinstance(exc, ConfigError) and "Invalid Matrix homeserver" in str(exc):
        return True
    if isinstance(exc, ValueError) and "Invalid Homeserver" in str(exc):
        return True
    return False


@dataclass
class ExponentialBackoff:
    min_delay: float = 5.0
    max_delay: float = 600.0
    factor: float = 2.0
    jitter_ratio: float = 0.5
    random_provider: Callable[[], float] = random.random

    _current: float | None = None

    def next_delay(self) -> float:
        base = self._current or self.min_delay
        jitter = self.random_provider() * (base * self.jitter_ratio)
        delay = min(self.max_delay, base + jitter)
        next_base = base * self.factor
        self._current = min(self.max_delay, next_base)
        return delay

    def reset(self) -> None:
        self._current = None


@dataclass
class SyncWatchdogState:
    stall_threshold_s: int = 60
    sync_count: int = 0
    last_successful_sync_at: float | None = None

    def mark_sync_success(self, now: float) -> None:
        self.last_successful_sync_at = now
        self.sync_count += 1

    def consume_interval_count(self) -> int:
        count = self.sync_count
        self.sync_count = 0
        return count

    def stall_context(self, now: float) -> dict[str, object]:
        seconds_since = None if self.last_successful_sync_at is None else max(0.0, now - self.last_successful_sync_at)
        iso = (
            None
            if self.last_successful_sync_at is None
            else datetime.fromtimestamp(self.last_successful_sync_at, tz=timezone.utc).isoformat()
        )
        return {
            "restart_reason": "matrix_sync_stalled",
            "last_successful_sync_at": iso,
            "seconds_since_last_sync": seconds_since,
            "configured_threshold": self.stall_threshold_s,
        }


class MatrixReconnectSupervisor:
    def __init__(
        self,
        log: logging.Logger,
        backoff: ExponentialBackoff | None = None,
        invalid_config_delay: float = 60.0,
    ) -> None:
        self._log = log
        self._backoff = backoff or ExponentialBackoff()
        self._invalid_config_delay = invalid_config_delay

    def handle_error(self, exc: Exception) -> float:
        if is_invalid_homeserver_error(exc):
            delay = self._invalid_config_delay
            self._log.error("Matrix config invalid: %s", exc)
            self._log.info("Matrix config invalid; retrying in %.1fs", delay)
            return delay
        if is_transient_matrix_error(exc):
            delay = self._backoff.next_delay()
            self._log.warning("Transient Matrix error; retrying in %.1fs: %s", delay, exc)
            return delay
        delay = self._backoff.next_delay()
        self._log.exception("Unexpected Matrix error; retrying in %.1fs", delay)
        return delay

    def reset(self) -> None:
        self._backoff.reset()

    def next_delay(self) -> float:
        return self._backoff.next_delay()


class TS3ReconnectSupervisor:
    def __init__(
        self,
        ts3: TS3Port,
        restart_event: threading.Event,
        log: logging.Logger,
        backoff: ExponentialBackoff | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._ts3 = ts3
        self._restart_event = restart_event
        self._log = log
        self._backoff = backoff or ExponentialBackoff(min_delay=2.0, max_delay=120.0)
        self._sleep = sleep

    def request_restart(self, reason: str) -> None:
        self._log.warning("TS3 restart requested: %s", reason)
        self._restart_event.set()

    def reconnect_with_backoff(self) -> None:
        while self._restart_event.is_set():
            self._restart_event.clear()
            try:
                self._log.info("TS3 reconnecting now")
                self._ts3.reconnect()
                self._backoff.reset()
                self._log.info("TS3 reconnect successful")
                return
            except Exception as exc:
                delay = self._backoff.next_delay()
                self._log.warning("TS3 reconnect failed (%r); retrying in %.1fs", exc, delay)
                self._sleep(delay)
                self._restart_event.set()


def _is_ts3_recv_thread(args: threading.ExceptHookArgs) -> bool:
    thread_name = getattr(args.thread, "name", "")
    if "_recv" in thread_name:
        return True
    tb = args.exc_traceback
    if tb:
        for frame in traceback.extract_tb(tb):
            if "ts3API" in frame.filename and "TS3Connection.py" in frame.filename:
                return True
    return False


def make_ts3_thread_excepthook(
    restart_event: threading.Event,
    log: logging.Logger,
    base_hook: Callable[[threading.ExceptHookArgs], None] | None = None,
) -> Callable[[threading.ExceptHookArgs], None]:
    resolved_base_hook: Callable[[threading.ExceptHookArgs], Any] = base_hook or threading.excepthook

    def _hook(args: threading.ExceptHookArgs) -> None:
        try:
            if _is_ts3_recv_thread(args):
                log.error(
                    "TS3 recv thread crashed, scheduling reconnect: %s",
                    args.exc_value,
                )
                restart_event.set()
        finally:
            resolved_base_hook(args)

    return _hook


def install_ts3_thread_excepthook(restart_event: threading.Event, log: logging.Logger) -> None:
    threading.excepthook = make_ts3_thread_excepthook(restart_event, log)
