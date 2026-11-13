import runtime_entrypoint as runtime


def test_worker_exit_stops_web_process(monkeypatch):
    class Process:
        def __init__(self, exited=False):
            self.exited = exited
            self.terminated = False
        def poll(self):
            return 0 if self.exited else None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout=None):
            return 0
    web, worker = Process(), Process(True)
    processes = iter([web, worker])
    monkeypatch.setattr(runtime.subprocess, 'Popen', lambda command: next(processes))
    monkeypatch.setattr(runtime.signal, 'signal', lambda *args: None)
    assert runtime.supervise([['web'], ['worker']]) == 1
    assert web.terminated


def test_enabled_runtime_starts_gateway_web_and_worker(monkeypatch):
    import control_plane
    monkeypatch.setenv('IIPP_BROWSER_SESSIONS_ENABLED', 'false')
    monkeypatch.setenv('IIPP_STREAMLIT_URL', 'http://127.0.0.1:8502')
    monkeypatch.setattr(control_plane, 'enabled', lambda: True)
    monkeypatch.setattr(control_plane, 'users', lambda: {'admin': {'role': 'administrator'}})
    calls = []
    monkeypatch.setattr(runtime, 'supervise', lambda commands: calls.extend(commands) or 0)
    assert runtime.main() == 0
    assert len(calls) == 3
    assert calls[0][-1] == 'session_gateway.py'
    assert '--server.address=127.0.0.1' in calls[1]
    assert '--server.port=8502' in calls[1]
    assert calls[2][-1] == 'control_plane_worker.py'


def test_missing_admin_refuses_startup(monkeypatch):
    import control_plane
    import pytest
    monkeypatch.setenv('IIPP_BROWSER_SESSIONS_ENABLED', 'false')
    monkeypatch.setenv('IIPP_STREAMLIT_URL', 'http://127.0.0.1:8502')
    monkeypatch.setattr(control_plane, 'enabled', lambda: True)
    monkeypatch.setattr(control_plane, 'users', lambda: {})
    with pytest.raises(ValueError):
        runtime.main()
