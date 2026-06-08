from __future__ import annotations

from typing import Protocol


class MatrixPort(Protocol):
    def send_text(
        self,
        room_id: str,
        text: str,
        clid: str | None = None,
        *,
        correlation_id: str | None = None,
        event_type: str | None = None,
    ) -> None: ...

    async def send_text_async(
        self,
        room_id: str,
        text: str,
        clid: str | None = None,
        *,
        correlation_id: str | None = None,
        event_type: str | None = None,
    ) -> None: ...

    def is_ready(self) -> bool: ...
