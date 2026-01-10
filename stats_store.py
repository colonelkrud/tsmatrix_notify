import json
import logging
import os
import tempfile
from pathlib import Path


def make_stats_helpers(stats_path: Path, log: logging.Logger):
    def load_stats():
        try:
            if stats_path.exists():
                with stats_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            log.warning("Failed to load stats from %s: %r (resetting).", stats_path, exc)
        return {"good": 0, "bad": 0}

    def save_stats(stats):
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(stats_path.parent),
                delete=False,
            ) as tmp:
                json.dump(stats, tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name
            os.replace(tmp_path, stats_path)
        except Exception as exc:
            log.error("Failed to save stats to %s: %r", stats_path, exc)

    def get_summary_message(stats):
        good = int(stats.get("good", 0))
        bad = int(stats.get("bad", 0))
        total = good + bad
        return f"Review summary — 👍: {good}, 👎: {bad} total reviews: {total}"

    return load_stats, save_stats, get_summary_message
