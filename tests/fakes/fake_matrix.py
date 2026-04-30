from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class FakeSyncResponse:
    token: str


class FakeMatrix:
    def __init__(self, fail: bool = False):
        self.messages = []
        self.fail = fail
        self.ready = True
        self.send_failures: list[Exception] = []
        self.whoami_failures: list[Exception] = []
        self.sync_callbacks = []
        self.sync_emitted = 0

    def send_text(self, room_id: str, text: str, clid=None):
        if self.fail:
            raise RuntimeError("send failed")
        if self.send_failures:
            raise self.send_failures.pop(0)
        self.messages.append((room_id, text, clid))

    def is_ready(self) -> bool:
        return self.ready

    def queue_send_failure(self, exc: Exception) -> None:
        self.send_failures.append(exc)

    def queue_whoami_failure(self, exc: Exception) -> None:
        self.whoami_failures.append(exc)

    async def whoami(self) -> dict[str, str]:
        if self.whoami_failures:
            raise self.whoami_failures.pop(0)
        return {"user_id": "@bot:example.com"}

    def add_sync_callback(self, callback):
        self.sync_callbacks.append(callback)

    def emit_sync(self, token: str = "s1") -> None:
        response = FakeSyncResponse(token=token)
        for callback in self.sync_callbacks:
            callback(response)
        self.sync_emitted += 1

    async def stall_sync(self, timeout: float) -> None:
        await asyncio.sleep(timeout)
