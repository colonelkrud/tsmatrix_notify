from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class HealthState:
    live: bool = True
    ready: bool = False
    status: str = "starting"

    def __post_init__(self):
        self._lock = threading.Lock()

    def set_ready(self, ready: bool, status: str):
        with self._lock:
            self.ready = ready
            self.status = status

    def set_live(self, live: bool, status: str):
        with self._lock:
            self.live = live
            self.status = status

    def snapshot(self) -> dict[str, str | bool]:
        with self._lock:
            return {"live": self.live, "ready": self.ready, "status": self.status}


class HealthServer:
    def __init__(
        self,
        host: str,
        port: int,
        path_live: str,
        path_ready: str,
        state: HealthState,
        log: logging.Logger,
    ):
        self._host = host
        self._port = port
        self._path_live = path_live
        self._path_ready = path_ready
        self._state = state
        self._log = log
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        state = self._state
        path_live = self._path_live
        path_ready = self._path_ready

        class Handler(BaseHTTPRequestHandler):
            def _write_json(self, code: int, payload: dict[str, str | bool]):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # pylint: disable=invalid-name
                snap = state.snapshot()
                if self.path == path_live:
                    code = HTTPStatus.OK if snap["live"] else HTTPStatus.SERVICE_UNAVAILABLE
                    self._write_json(code, {"ok": bool(snap["live"]), **snap})
                    return
                if self.path == path_ready:
                    code = HTTPStatus.OK if snap["ready"] else HTTPStatus.SERVICE_UNAVAILABLE
                    self._write_json(code, {"ok": bool(snap["ready"]), **snap})
                    return
                if self.path == "/":
                    self._write_json(HTTPStatus.OK, {"service": "tsmatrix_notify", **snap})
                    return
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def log_message(self, fmt, *args):  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log.info(
            "Health server listening on http://%s:%s (live=%s ready=%s)",
            self._host,
            self._port,
            self._path_live,
            self._path_ready,
        )

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
