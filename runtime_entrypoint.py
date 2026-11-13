"""Keep the web server and optional queue worker under one container lifecycle."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def supervise(commands):
    children = []
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not stopping:
            if any(child.poll() is not None for child in children):
                return 1  # Restart the complete service if either process exits.
            time.sleep(0.5)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 15
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    os.environ.setdefault('IIPP_STREAMLIT_URL', 'http://127.0.0.1:8502')
    os.environ['IIPP_BROWSER_SESSIONS_ENABLED'] = 'true'
    commands = [[sys.executable, 'session_gateway.py'],
                [sys.executable, '-m', 'streamlit', 'run', 'app.py',
                 '--server.address=127.0.0.1', '--server.port=8502', '--server.headless=true']]
    from control_plane import enabled, users
    if enabled():
        registry = users()
        if not any(u.get('role') == 'administrator' and not u.get('disabled') for u in registry.values()):
            raise ValueError('An active control-plane administrator must be configured')
        commands.append([sys.executable, 'control_plane_worker.py'])
    return supervise(commands)


if __name__ == '__main__':
    sys.exit(main())
