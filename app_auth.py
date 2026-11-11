"""Server-side admin authentication and bounded sessions, without browser secrets."""
import hashlib
import hmac
import os
import threading
import time
from pathlib import Path

_attempts = {}
_lock = threading.Lock()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()


def uses_registry():
    return os.getenv('CONTROL_PLANE_ENABLED', 'false').lower() == 'true' or bool(os.getenv('CONTROL_PLANE_USERS_FILE') and Path(os.environ['CONTROL_PLANE_USERS_FILE']).is_file())


def configured():
    return bool(uses_registry() or
                (os.getenv('IIPP_ADMIN_USERNAME') and
                 (os.getenv('IIPP_ADMIN_PASSWORD') or (os.getenv('IIPP_ADMIN_PASSWORD_HASH') and os.getenv('IIPP_ADMIN_SALT')))))


def authenticate(username, password):
    key = username.casefold().strip()[:128]
    with _lock:
        recent = [stamp for stamp in _attempts.get(key, []) if time.monotonic() - stamp < 300]
        if len(recent) >= 5:
            return None
        if len(_attempts) >= 4096:
            _attempts.clear()
        _attempts[key] = recent + [time.monotonic()]
    if uses_registry():
        import control_plane as cp
        authenticated = cp.authenticate(username, password)
        user = cp.users().get(authenticated) if authenticated else None
        allowed = user and not user.get('disabled') and (cp.enabled() or user.get('role') == 'administrator')
        result = dict(username=username, role=user['role']) if allowed else None
    else:
        salt = os.getenv('IIPP_ADMIN_SALT', '00' * 16)
        supplied = password_hash(password, salt)
        expected = os.getenv('IIPP_ADMIN_PASSWORD_HASH')
        if not expected and os.getenv('IIPP_ADMIN_PASSWORD'):
            expected = password_hash(os.environ['IIPP_ADMIN_PASSWORD'], salt)
        valid = hmac.compare_digest(supplied, expected or '0' * 64)
        valid &= hmac.compare_digest(username.encode(), os.getenv('IIPP_ADMIN_USERNAME', '').encode())
        result = dict(username=username, role='administrator') if configured() and valid else None
    if result:
        with _lock:
            _attempts.pop(key, None)
    return result


def session_valid(session, now=None):
    current = time.time() if now is None else now
    auth = session.get('iipp-auth') or {}
    idle = max(60, int(os.getenv('IIPP_SESSION_IDLE_SECONDS', '1800')))
    maximum = max(idle, int(os.getenv('IIPP_SESSION_MAX_SECONDS', '28800')))
    if not auth or current - auth.get('last_seen', 0) >= idle or current - auth.get('created', 0) >= maximum:
        return False
    auth['last_seen'] = current
    session['iipp-auth'] = auth
    return True
