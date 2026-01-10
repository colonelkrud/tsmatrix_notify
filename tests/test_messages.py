from tsmatrix_notify.domain.messages import build_who_body, format_uptime


def test_format_uptime():
    assert format_uptime(0) == "0h0m0s"
    assert format_uptime(3661) == "1h1m1s"


def test_build_who_body_with_status_flags():
    join_times = {"1": 100.0}
    clientlist = [
        {"clid": "1", "client_type": "0"},
        {"clid": "2", "client_type": "1"},
    ]

    def clientinfo(clid: str):
        return {
            "client_nickname": "Alice",
            "client_away": "1",
            "client_away_message": "brb",
            "client_input_muted": "1",
            "client_output_muted": "1",
        }

    body, count = build_who_body(clientlist, clientinfo, join_times, now=160.0)

    assert count == 1
    assert "Alice" in body
    assert "away: brb" in body
    assert "mic muted" in body
    assert "spk muted" in body
