from __future__ import annotations

import logging

from tsmatrix_notify.domain.handlers import MatrixAction
from tsmatrix_notify.ports.matrix_port import MatrixPort


def send_actions(matrix: MatrixPort, actions: list[MatrixAction], log: logging.Logger) -> None:
    for action in actions:
        try:
            matrix.send_text(action.room_id, action.text, action.clid)
        except Exception as exc:
            log.warning("Matrix send failed for clid=%s: %r", action.clid, exc)


def send_actions_if_ready(matrix: MatrixPort, actions: list[MatrixAction], log: logging.Logger) -> None:
    if not matrix.is_ready():
        log.debug("Matrix not ready; skipping %d actions", len(actions))
        return
    send_actions(matrix, actions, log)
