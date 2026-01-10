import json
import logging

from tsmatrix_notify.adapters.persistence_fs import FilePersistence


def test_load_message_catalog_valid(tmp_path):
    payload = {"messages": ["hi"], "apologies": ["sorry"]}
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    persistence = FilePersistence(str(path), tmp_path / "stats.json", logging.getLogger("test"))
    msgs, apols = persistence.load_message_catalog()

    assert msgs == ["hi"]
    assert apols == ["sorry"]


def test_load_message_catalog_missing_keys(tmp_path):
    payload = {}
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    persistence = FilePersistence(str(path), tmp_path / "stats.json", logging.getLogger("test"))
    msgs, apols = persistence.load_message_catalog()

    assert msgs
    assert apols


def test_load_message_catalog_wrong_types(tmp_path):
    payload = {"messages": "nope", "apologies": []}
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    persistence = FilePersistence(str(path), tmp_path / "stats.json", logging.getLogger("test"))
    msgs, apols = persistence.load_message_catalog()

    assert msgs
    assert apols


def test_load_message_catalog_malformed_json(tmp_path):
    path = tmp_path / "messages.json"
    path.write_text("{broken", encoding="utf-8")

    persistence = FilePersistence(str(path), tmp_path / "stats.json", logging.getLogger("test"))
    msgs, apols = persistence.load_message_catalog()

    assert msgs
    assert apols
