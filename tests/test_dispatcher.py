import logging

from tests.fakes.fake_matrix import FakeMatrix
from tsmatrix_notify.application.dispatcher import send_actions, send_actions_if_ready
from tsmatrix_notify.domain.handlers import MatrixAction


def test_send_actions_handles_failures(caplog):
    matrix = FakeMatrix(fail=True)
    actions = [MatrixAction(room_id="room", text="hello", clid="1")]

    with caplog.at_level(logging.WARNING):
        send_actions(matrix, actions, logging.getLogger("test"))

    assert "matrix_send_failure" in caplog.text


def test_send_actions_if_ready_skips_when_not_ready(caplog):
    matrix = FakeMatrix()
    matrix.ready = False
    actions = [MatrixAction(room_id="room", text="hello", clid="1")]

    with caplog.at_level(logging.DEBUG):
        send_actions_if_ready(matrix, actions, logging.getLogger("test"))

    assert matrix.messages == []
    assert "Matrix not ready" in caplog.text


def test_send_actions_logs_correlation_id(caplog):
    matrix = FakeMatrix()
    actions = [MatrixAction(room_id="room", text="hello", clid="1", correlation_id="corr-1", event_type="client_entered")]
    with caplog.at_level(logging.INFO):
        send_actions(matrix, actions, logging.getLogger("test"))
    assert any(r.message == "matrix_send_success" and getattr(r, "correlation_id", None) == "corr-1" for r in caplog.records)


def test_send_actions_failure_logs_correlation_id(caplog):
    matrix = FakeMatrix(fail=True)
    actions = [MatrixAction(room_id="room", text="hello", clid="1", correlation_id="corr-2", event_type="client_left")]
    with caplog.at_level(logging.WARNING):
        send_actions(matrix, actions, logging.getLogger("test"))
    assert any(r.message == "matrix_send_failure" and getattr(r, "correlation_id", None) == "corr-2" for r in caplog.records)
