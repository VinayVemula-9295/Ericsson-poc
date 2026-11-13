"""Opaque browser sessions and single-use login grants backed by SQLite."""
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path


def _database():
    root = Path(os.getenv('DATA_DIR', '/data'))
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'browser-sessions.sqlite3'
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS grants (grant_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS identity_proofs (proof_hash TEXT PRIMARY KEY, session_hash TEXT NOT NULL, expires REAL NOT NULL)')
    try:
        os.chmod(path, 0o600)
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                os.chmod(sidecar, 0o600)
    except OSError:
        pass
    return path


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def issue_grant(user):
    grant = secrets.token_urlsafe(32)
    with sqlite3.connect(_database()) as db:
        db.execute('INSERT INTO grants(grant_hash, username, role, expires) VALUES(?,?,?,?)',
                   (_digest(grant), user['username'], user['role'], time.time() + 90))
    return grant


def exchange_grant(grant):
    if not isinstance(grant, str) or len(grant) > 256:
        return None
    token = secrets.token_urlsafe(48)
    now = time.time()
    with sqlite3.connect(_database()) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT username, role FROM grants WHERE grant_hash=? AND consumed=0 AND expires>?',
                         (_digest(grant), now)).fetchone()
        if not row:
            return None
        db.execute('UPDATE grants SET consumed=1 WHERE grant_hash=?', (_digest(grant),))
        db.execute('INSERT INTO sessions(token_hash,username,role,created,last_seen) VALUES(?,?,?,?,?)',
                   (_digest(token), row[0], row[1], now, now))
    return token


def restore(token):
    if not isinstance(token, str) or len(token) > 256:
        return None
    return restore_hash(_digest(token))


def restore_hash(session_hash):
    """Revalidate a session reference held only in trusted server state."""
    if not isinstance(session_hash, str) or len(session_hash) != 64:
        return None
    now = time.time()
    idle = max(60, int(os.getenv('IIPP_SESSION_IDLE_SECONDS', '1800')))
    absolute = max(idle, int(os.getenv('IIPP_SESSION_MAX_SECONDS', '28800')))
    with sqlite3.connect(_database()) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM sessions WHERE token_hash=? AND revoked=0', (session_hash,)).fetchone()
        if not row:
            return None
        if now - row['last_seen'] >= idle or now - row['created'] >= absolute:
            db.execute('UPDATE sessions SET revoked=1 WHERE token_hash=?', (session_hash,))
            return None
        db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (now, session_hash))
    registry_file = os.getenv('CONTROL_PLANE_USERS_FILE')
    registry_enabled = os.getenv('CONTROL_PLANE_ENABLED', 'false').lower() == 'true'
    if registry_enabled or (registry_file and Path(registry_file).is_file()):
        try:
            import control_plane as cp
            user = cp.users().get(row['username'])
            if not user or user.get('disabled') or user.get('role') != row['role']:
                revoke_hash(session_hash)
                return None
        except (OSError, ValueError, KeyError):
            revoke_hash(session_hash)
            return None
    return {'username': row['username'], 'role': row['role'], 'created': row['created'],
            'last_seen': now, 'session_key': session_hash}


def identity_proof(token):
    """Return a short-lived one-use proof, never a client-supplied identity."""
    identity = restore(token)
    if not identity:
        return None
    proof = secrets.token_urlsafe(32)
    with sqlite3.connect(_database()) as db:
        db.execute('DELETE FROM identity_proofs WHERE expires<=?', (time.time(),))
        db.execute('INSERT INTO identity_proofs VALUES(?,?,?)',
                   (_digest(proof), identity['session_key'], time.time() + 30))
    return {'proof': proof}


def consume_identity_proof(proof):
    if not isinstance(proof, str) or len(proof) > 256:
        return None
    with sqlite3.connect(_database()) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT session_hash FROM identity_proofs WHERE proof_hash=? AND expires>?',
                         (_digest(proof), time.time())).fetchone()
        db.execute('DELETE FROM identity_proofs WHERE proof_hash=?', (_digest(proof),))
    return restore_hash(row[0]) if row else None


def revoke(token):
    if isinstance(token, str) and len(token) <= 256:
        revoke_hash(_digest(token))


def revoke_hash(session_hash):
    with sqlite3.connect(_database()) as db:
        db.execute('UPDATE sessions SET revoked=1 WHERE token_hash=?', (session_hash,))

