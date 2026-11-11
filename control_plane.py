"""Persistent reviewed execution queue. No cloud calls occur during submission."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

_identity = ContextVar('control_plane_identity', default=None)
_execution = ContextVar('control_plane_execution', default=False)
SOURCES = {'poc_plans': ('poc-[0-9a-f]{32}', 'record.json'),
           'terraform_deployments': ('cp-[0-9a-f]{16}', 'deployment.json')}


def enabled():
    return os.getenv('CONTROL_PLANE_ENABLED', 'false').lower() == 'true'


def now():
    return datetime.now(timezone.utc)


def users():
    """Read a protected identity registry, never persist passwords in the database."""
    return json.loads(Path(os.environ['CONTROL_PLANE_USERS_FILE']).read_text(encoding='utf-8'))


def authenticate(username, password):
    import hmac
    user = users().get(username)
    if not user:
        # Do equivalent work for unknown users.
        hashlib.pbkdf2_hmac('sha256', password.encode(), b'unknown-user', 600000)
        return None
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(user['salt']), 600000).hex()
    return username if hmac.compare_digest(digest, user['password_hash']) else None


@contextmanager
def identity(username, executing=False):
    a, b = _identity.set(username), _execution.set(executing)
    try:
        yield
    finally:
        _identity.reset(a)
        _execution.reset(b)


def principal():
    username = _identity.get()
    if not username:
        try:
            import streamlit as st
            username = st.session_state.get('control-plane-user')
        except (ImportError, RuntimeError):
            pass
    user = users().get(username)
    if not user or user.get('disabled'):
        raise ValueError('Sign in with an active account')
    return dict(user, username=username)


def require(roles, customer=None):
    user = principal()
    if user.get('role') not in roles:
        raise ValueError('Your role cannot perform this action')
    if customer and user['role'] != 'administrator' and customer not in user.get('customers', []):
        raise ValueError('Customer access denied')
    return user


@contextmanager
def database():
    root = Path(os.getenv('DATA_DIR', '/data'))
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'control-plane.sqlite3'
    db = sqlite3.connect(path, timeout=30)
    os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS scopes(connection_id TEXT PRIMARY KEY, customer TEXT NOT NULL,
          project TEXT NOT NULL, organization TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, source TEXT NOT NULL, deployment_id TEXT NOT NULL,
          customer TEXT NOT NULL, project TEXT NOT NULL, requester TEXT NOT NULL, reviewer TEXT,
          fingerprint TEXT NOT NULL, due TEXT NOT NULL, expires TEXT NOT NULL, status TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
          UNIQUE(source, deployment_id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
          actor TEXT NOT NULL, action TEXT NOT NULL, timestamp TEXT NOT NULL);
        ''')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def scope(connection_id):
    with database() as db:
        row = db.execute('SELECT * FROM scopes WHERE connection_id=?', (connection_id,)).fetchone()
        return dict(row) if row else None


def assign_scope(connection_id, customer, project, organization):
    require({'administrator'})
    if not all(str(x).strip() for x in (connection_id, customer, project, organization)):
        raise ValueError('Connection, customer, project and organization are required')
    with database() as db:
        if db.execute("SELECT 1 FROM requests WHERE status IN ('PENDING','APPROVED','RUNNING')").fetchone():
            raise ValueError('Resolve active requests before changing connection ownership')
        db.execute('INSERT OR REPLACE INTO scopes VALUES (?,?,?,?)',
                   (connection_id, customer.strip(), project.strip(), organization.strip()))


def authorize_connection(connection_id, roles=('administrator', 'requester')):
    if not enabled():
        return
    target = scope(connection_id)
    if not target:
        raise ValueError('An administrator must assign this connection to a customer/project')
    return require(set(roles), target['customer'])


def visible_connection(connection_id):
    if not enabled():
        return True
    user = principal()
    target = scope(connection_id)
    return user['role'] == 'administrator' or bool(target and target['customer'] in user.get('customers', []))


def artifact(source, deployment_id):
    if source not in SOURCES or not re.fullmatch(SOURCES[source][0], deployment_id):
        raise ValueError('Invalid saved plan identifier')
    root = Path(os.getenv('DATA_DIR', '/data')) / source
    directory = root / deployment_id
    if directory.is_symlink() or directory.resolve().parent != root.resolve():
        raise ValueError('Invalid saved plan directory')
    record = json.loads((directory / SOURCES[source][1]).read_text(encoding='utf-8'))
    if record.get('status') != 'AWAITING_APPROVAL':
        raise ValueError('Generate a fresh plan awaiting approval')
    plan = directory / 'tfplan'
    if plan.is_symlink() or not plan.is_file() or hashlib.sha256(plan.read_bytes()).hexdigest() != record.get('plan_sha256'):
        raise ValueError('Saved plan changed or is unavailable')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()
                if p.is_file() and (p.name.endswith(('.tf', '.tf.json')) or p.name in
                                   {'terraform.tfvars.json', '.terraform.lock.hcl'})}
    stamp = hashlib.sha256(json.dumps([record, manifest], sort_keys=True).encode()).hexdigest()
    created = datetime.fromisoformat((record.get('plan_created_at') or record['created_at']).replace('Z', '+00:00'))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    expires = created + timedelta(hours=1 if source == 'poc_plans' else 4)
    connection = record.get('connection_id') or record.get('aws_connection_id')
    target = scope(connection)
    if not target:
        raise ValueError('Assign the target connection to a customer/project first')
    return record, stamp, expires, target


def audit(db, request_id, actor, action):
    db.execute('INSERT INTO events(request_id,actor,action,timestamp) VALUES (?,?,?,?)',
               (request_id, actor, action, now().isoformat()))


def submit(source, deployment_id, due=None):
    record, stamp, expires, target = artifact(source, deployment_id)
    user = require({'administrator', 'requester'}, target['customer'])
    due = due or now()
    if due.tzinfo is None or due >= expires or now() >= expires:
        raise ValueError('Execution must fall within the saved plan validity window; generate a fresh plan for later execution')
    due = due.astimezone(timezone.utc)
    request_id = uuid.uuid4().hex
    with database() as db:
        db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (request_id, source, deployment_id, target['customer'], target['project'], user['username'], None,
                    stamp, max(due, now()).isoformat(), expires.isoformat(), 'PENDING', now().isoformat(), now().isoformat(), ''))
        audit(db, request_id, user['username'], 'SUBMITTED')
    return request_id


def requests():
    user = principal()
    with database() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM requests ORDER BY created DESC')]
    return [r for r in rows if user['role'] == 'administrator' or r['customer'] in user.get('customers', [])]


def timeline(request_id):
    if not any(r['id'] == request_id for r in requests()):
        raise ValueError('Request access denied')
    with database() as db:
        return [dict(r) for r in db.execute('SELECT actor,action,timestamp FROM events WHERE request_id=? ORDER BY id', (request_id,))]


def decide(request_id, approve, note=''):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if not row or row['status'] != 'PENDING':
            raise ValueError('Request is no longer pending')
        user = require({'administrator', 'approver'}, row['customer'])
        if user['username'] == row['requester']:
            raise ValueError('A different user must review this request')
        if approve:
            _, stamp, expires, target = artifact(row['source'], row['deployment_id'])
            if stamp != row['fingerprint'] or now() >= expires or target['customer'] != row['customer'] or target['project'] != row['project']:
                raise ValueError('Plan expired or changed; generate a new plan and request')
        status = 'APPROVED' if approve else 'REJECTED'
        db.execute('UPDATE requests SET status=?,reviewer=?,updated=?,note=? WHERE id=?',
                   (status, user['username'], now().isoformat(), note[:1000], request_id))
        audit(db, request_id, user['username'], status)


def guard_apply(source, deployment_id):
    if not enabled():
        return
    if not _execution.get():
        raise ValueError('Submit this saved plan through Approvals & schedules')
    user = require({'administrator', 'approver'})
    with database() as db:
        row = db.execute("SELECT * FROM requests WHERE source=? AND deployment_id=? AND status='RUNNING' AND reviewer=?",
                         (source, deployment_id, user['username'])).fetchone()
    if not row:
        raise ValueError('No claimed approved request matches this execution')
    require({'administrator', 'approver'}, row['customer'])


def run_once(executor=None):
    """Claim once transactionally; interrupted jobs require operator reconciliation."""
    if not enabled():
        raise ValueError('Enable the control plane before starting its worker')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM requests WHERE status='APPROVED' AND due<=? ORDER BY due LIMIT 1", (now().isoformat(),)).fetchone()
        if not row:
            return False
        row = dict(row)
        db.execute("UPDATE requests SET status='RUNNING',updated=? WHERE id=?", (now().isoformat(), row['id']))
        audit(db, row['id'], 'worker', 'CLAIMED')
    status, note = 'FAILED', 'Execution failed; inspect protected runner diagnostics and state before retrying'
    try:
        with identity(row['requester']):
            require({'administrator', 'requester'}, row['customer'])
        with identity(row['reviewer'], executing=True):
            require({'administrator', 'approver'}, row['customer'])
            _, stamp, expires, target = artifact(row['source'], row['deployment_id'])
            if now() >= expires or stamp != row['fingerprint'] or target['customer'] != row['customer'] or target['project'] != row['project']:
                status, note = 'NEEDS_REVIEW', 'Plan expired or changed; generate a fresh plan and approval'
            else:
                result = (executor or execute)(row)
                status = 'SUCCEEDED' if result.get('status') == 'SUCCEEDED' else 'FAILED'
                note = '' if status == 'SUCCEEDED' else note
    except ValueError:
        status, note = 'NEEDS_REVIEW', 'Authorization or plan validation failed; review before resubmitting'
    except Exception:
        pass
    with database() as db:
        db.execute('UPDATE requests SET status=?,updated=?,note=? WHERE id=?', (status, now().isoformat(), note, row['id']))
        audit(db, row['id'], 'worker', status)
    return True


def execute(row):
    from approval import validate
    token = os.getenv('APPROVAL_TOKEN', '')
    if not token:
        for path in (Path('/run/secrets/cloudpilot-approval-token'), Path.home() / '.cloudpilot-approval-token'):
            if path.is_file():
                token = path.read_text(encoding='utf-8').strip()
                break
    validate(row['reviewer'], token)
    if row['source'] == 'poc_plans':
        from poc_planner import apply_saved_plan
        return apply_saved_plan(row['deployment_id'], row['reviewer'], token)
    from terraform_service import apply_saved_plan
    return apply_saved_plan(row['deployment_id'], row['reviewer'])
