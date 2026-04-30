from __future__ import annotations

import logging

from tsmatrix_notify.domain.handlers import MatrixAction
from tsmatrix_notify.ports.matrix_port import MatrixPort


def send_actions(matrix: MatrixPort, actions: list[MatrixAction], log: logging.Logger) -> None:
    for index, action in enumerate(actions, start=1):
        log.info(
            "matrix_send_attempt",
            extra={
                "correlation_id": action.correlation_id,
                "event_type": action.event_type,
                "ts3_client_id": action.clid,
                "matrix_room_id": action.room_id,
                "send_attempt": index,
            },
        )
        try:
            matrix.send_text(action.room_id, action.text, action.clid)
            log.info(
                "matrix_send_success",
                extra={
                    "correlation_id": action.correlation_id,
                    "event_type": action.event_type,
                    "ts3_client_id": action.clid,
                    "matrix_room_id": action.room_id,
                    "send_attempt": index,
                },
            )
        except Exception as exc:
            log.warning(
                "matrix_send_failure",
                extra={
                    "correlation_id": action.correlation_id,
                    "event_type": action.event_type,
                    "ts3_client_id": action.clid,
                    "matrix_room_id": action.room_id,
                    "send_attempt": index,
                    "error_type": type(exc).__name__,
                },
            )


def send_actions_if_ready(matrix: MatrixPort, actions: list[MatrixAction], log: logging.Logger) -> None:
    if not matrix.is_ready():
        log.debug("Matrix not ready; skipping %d actions", len(actions))
        return
    send_actions(matrix, actions, log)
