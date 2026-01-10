from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from tsmatrix_notify.ports.persistence_port import PersistencePort


class FilePersistence(PersistencePort):
    def __init__(self, message_path: str, stats_path: Path, log: logging.Logger):
        self._message_path = message_path
        self._stats_path = stats_path
        self._log = log

    def load_message_catalog(self) -> tuple[list[str], list[str]]:
        try:
            with open(self._message_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            msgs = data.get("messages", [])
            apols = data.get("apologies", [])
            if not isinstance(msgs, list) or not isinstance(apols, list):
                raise ValueError("messages/apologies must be lists")
            if not msgs:
                self._log.warning("Message catalog has no 'messages'; using a tiny fallback.")
                msgs = ["Thanks!"]
            if not apols:
                self._log.warning("Message catalog has no 'apologies'; using a tiny fallback.")
                apols = ["Sorry!"]
            self._log.info("Loaded message catalog: %d messages, %d apologies", len(msgs), len(apols))
            return msgs, apols
        except Exception as exc:
            self._log.error("Failed to load message catalog %r: %s", self._message_path, exc)
            return ["Thanks!"], ["Sorry!"]

    def load_stats(self) -> dict:
        try:
            if self._stats_path.exists():
                with self._stats_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            self._log.warning("Failed to load stats from %s: %r (resetting).", self._stats_path, exc)
        return {"good": 0, "bad": 0}

    def save_stats(self, stats: dict) -> None:
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self._stats_path.parent),
                delete=False,
            ) as tmp:
                json.dump(stats, tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, self._stats_path)
        except Exception as exc:
            self._log.error("Failed to save stats to %s: %r", self._stats_path, exc)
