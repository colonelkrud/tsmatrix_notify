from __future__ import annotations

from typing import Protocol


class PersistencePort(Protocol):
    def load_message_catalog(self) -> tuple[list[str], list[str]]: ...

    def load_stats(self) -> dict: ...

    def save_stats(self, stats: dict) -> None: ...
