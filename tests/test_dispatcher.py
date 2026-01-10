import logging

from tests.fakes.fake_matrix import FakeMatrix
from tsmatrix_notify.application.dispatcher import send_actions, send_actions_if_ready
from tsmatrix_notify.domain.handlers import MatrixAction


def test_send_actions_handles_failures(caplog):
    matrix = FakeMatrix(fail=True)
    actions = [MatrixAction(room_id="room", text="hello", clid="1")]

    with caplog.at_level(logging.WARNING):
        send_actions(matrix, actions, logging.getLogger("test"))

    assert "Matrix send failed" in caplog.text


def test_send_actions_if_ready_skips_when_not_ready(caplog):
    matrix = FakeMatrix()
    matrix.ready = False
    actions = [MatrixAction(room_id="room", text="hello", clid="1")]

    with caplog.at_level(logging.DEBUG):
        send_actions_if_ready(matrix, actions, logging.getLogger("test"))

    assert matrix.messages == []
    assert "Matrix not ready" in caplog.text
