import logging
from tsmatrix_notify.adapters.persistence_fs import FilePersistence
from tsmatrix_notify.domain.messages import build_who_body


def test_persistence_load_stats_missing_file(tmp_path):
    p = FilePersistence("missing.json", tmp_path / "stats.json", logging.getLogger("test"))
    assert p.load_stats() == {"good": 0, "bad": 0}


def test_persistence_save_stats_error_branch(tmp_path, monkeypatch, caplog):
    p = FilePersistence("missing.json", tmp_path / "stats.json", logging.getLogger("test"))
    monkeypatch.setattr("os.replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("x")))
    with caplog.at_level(logging.ERROR):
        p.save_stats({"good": 1, "bad": 2})
    assert "Failed to save stats" in caplog.text


def test_build_who_body_unknown_and_status_flags():
    clients = [{"client_type": "0", "clid": "1"}]
    join_times = {}

    def info(_clid):
        return {
            "client_nickname": "A",
            "client_away": "1",
            "client_away_message": "brb",
            "client_input_muted": "1",
            "client_output_muted": "1",
        }

    body, count = build_who_body(clients, info, join_times, now=10)
    assert count == 1
    assert "unknown" in body
    assert "away: brb" in body
