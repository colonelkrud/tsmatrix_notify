import json
import logging


def load_message_catalog(path: str, log: logging.Logger):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        msgs = data.get("messages", [])
        apols = data.get("apologies", [])
        if not isinstance(msgs, list) or not isinstance(apols, list):
            raise ValueError("messages/apologies must be lists")
        if not msgs:
            log.warning("Message catalog has no 'messages'; using a tiny fallback.")
            msgs = ["Thanks!"]
        if not apols:
            log.warning("Message catalog has no 'apologies'; using a tiny fallback.")
            apols = ["Sorry!"]
        log.info("Loaded message catalog: %d messages, %d apologies", len(msgs), len(apols))
        return msgs, apols
    except Exception as exc:
        log.error("Failed to load message catalog %r: %s", path, exc)
        return ["Thanks!"], ["Sorry!"]
