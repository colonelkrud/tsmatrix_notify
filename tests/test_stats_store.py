import json
import logging

from tsmatrix_notify.adapters.persistence_fs import FilePersistence


def test_stats_round_trip(tmp_path):
    stats_path = tmp_path / "stats.json"
    persistence = FilePersistence(str(tmp_path / "messages.json"), stats_path, logging.getLogger("test"))

    initial = persistence.load_stats()
    assert initial == {"good": 0, "bad": 0}

    persistence.save_stats({"good": 2, "bad": 1})
    loaded = persistence.load_stats()

    assert loaded == {"good": 2, "bad": 1}


def test_stats_corrupted_file_resets(tmp_path):
    stats_path = tmp_path / "stats.json"
    stats_path.write_text("{broken", encoding="utf-8")
    persistence = FilePersistence(str(tmp_path / "messages.json"), stats_path, logging.getLogger("test"))

    loaded = persistence.load_stats()

    assert loaded == {"good": 0, "bad": 0}


def test_stats_atomic_replace(tmp_path):
    stats_path = tmp_path / "stats.json"
    persistence = FilePersistence(str(tmp_path / "messages.json"), stats_path, logging.getLogger("test"))

    persistence.save_stats({"good": 1, "bad": 0})
    assert json.loads(stats_path.read_text(encoding="utf-8")) == {"good": 1, "bad": 0}
