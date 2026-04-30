import logging

from tsmatrix_notify.application.dispatcher import send_actions_if_ready
from tsmatrix_notify.domain.handlers import MatrixAction
from tsmatrix_notify.health import HealthState


class NotReadyMatrix:
    def is_ready(self):
        return False


def test_dispatcher_not_ready_branch(caplog):
    actions = [MatrixAction(room_id="r", text="t", clid="1")]
    with caplog.at_level(logging.DEBUG):
        send_actions_if_ready(NotReadyMatrix(), actions, logging.getLogger("test"))
    assert "Matrix not ready" in caplog.text


def test_health_state_snapshot_and_setters():
    st = HealthState()
    st.set_ready(True, "ready")
    st.set_live(False, "down")
    snap = st.snapshot()
    assert snap["ready"] is True
    assert snap["live"] is False
