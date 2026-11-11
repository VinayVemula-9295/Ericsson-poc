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


def test_enabled_runtime_starts_both_processes(monkeypatch):
    import control_plane
    monkeypatch.setattr(control_plane, 'enabled', lambda: True)
    monkeypatch.setattr(control_plane, 'users', lambda: {'admin': {'role': 'administrator'}})
    calls = []
    monkeypatch.setattr(runtime, 'supervise', lambda commands: calls.extend(commands) or 0)
    assert runtime.main() == 0
    assert len(calls) == 2
    assert calls[1][-1] == 'control_plane_worker.py'


def test_missing_admin_refuses_startup(monkeypatch):
    import control_plane
    import pytest
    monkeypatch.setattr(control_plane, 'enabled', lambda: True)
    monkeypatch.setattr(control_plane, 'users', lambda: {})
    with pytest.raises(ValueError):
        runtime.main()
