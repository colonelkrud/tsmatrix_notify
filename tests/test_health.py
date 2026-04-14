import json
import logging
import socket
import urllib.error
import urllib.request

import pytest

from tsmatrix_notify.health import HealthServer, HealthState


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_health_server_live_and_ready_transitions():
    port = _free_port()
    state = HealthState(live=True, ready=False, status="starting")
    server = HealthServer(
        host="127.0.0.1",
        port=port,
        path_live="/healthz/live",
        path_ready="/healthz/ready",
        state=state,
        log=logging.getLogger("test"),
    )
    server.start()
    try:
        live_status, live_body = _get_json(f"http://127.0.0.1:{port}/healthz/live")
        assert live_status == 200
        assert live_body["ok"] is True

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            assert response.status == 200

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/ready", timeout=2)
        assert exc.value.code == 503

        state.set_ready(True, "ready")
        ready_status, ready_body = _get_json(f"http://127.0.0.1:{port}/healthz/ready")
        assert ready_status == 200
        assert ready_body["ready"] is True
    finally:
        server.stop()
