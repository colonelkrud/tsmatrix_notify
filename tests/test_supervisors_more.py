import logging
import threading

from tsmatrix_notify.application import supervisors


def test_excepthook_non_target_thread_calls_base_only():
    event = threading.Event()
    called = {"base": 0}

    def base(_args):
        called["base"] += 1

    hook = supervisors.make_ts3_thread_excepthook(event, logging.getLogger("test"), base_hook=base)
    thr = threading.Thread(name="worker")
    exc = RuntimeError("boom")
    hook(threading.ExceptHookArgs((type(exc), exc, None, thr)))
    assert called["base"] == 1
    assert event.is_set() is False


def test_install_ts3_thread_excepthook_sets_global(monkeypatch):
    event = threading.Event()
    old = threading.excepthook
    supervisors.install_ts3_thread_excepthook(event, logging.getLogger("test"))
    assert threading.excepthook is not old


def test_request_restart_sets_event():
    e = threading.Event()

    class T:
        def reconnect(self):
            return None

    sup = supervisors.TS3ReconnectSupervisor(T(), e, logging.getLogger("test"))
    sup.request_restart("reason")
    assert e.is_set() is True
