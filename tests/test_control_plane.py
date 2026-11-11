import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest
import control_plane as cp


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'true')
    registry = {
        'admin': {'role': 'administrator'},
        'lead': {'role': 'requester', 'customers': ['Acme']},
        'reviewer': {'role': 'approver', 'customers': ['Acme']},
        'outsider': {'role': 'approver', 'customers': ['Other']},
        'viewer': {'role': 'viewer', 'customers': ['Acme']},
    }
    monkeypatch.setattr(cp, 'users', lambda: registry)
    with cp.identity('admin'):
        cp.assign_scope('conn', 'Acme', 'Checkout', 'Engineering')
    deployment = 'poc-' + 'a' * 32
    directory = tmp_path / 'poc_plans' / deployment
    directory.mkdir(parents=True)
    (directory / 'tfplan').write_bytes(b'plan')
    (directory / 'main.tf.json').write_text('{}')
    record = {'status': 'AWAITING_APPROVAL', 'created_at': cp.now().isoformat(),
              'connection_id': 'conn', 'plan_sha256': hashlib.sha256(b'plan').hexdigest()}
    (directory / 'record.json').write_text(json.dumps(record))
    return deployment, directory, registry


def submit(deployment, due=None):
    with cp.identity('lead'):
        return cp.submit('poc_plans', deployment, due)


def approve(request_id):
    with cp.identity('reviewer'):
        cp.decide(request_id, True)


def test_role_customer_and_independent_review(setup):
    dep, _, _ = setup
    with cp.identity('viewer'), pytest.raises(ValueError):
        cp.submit('poc_plans', dep)
    request_id = submit(dep)
    with cp.identity('outsider'):
        assert cp.requests() == []
        assert not cp.visible_connection('conn')
        with pytest.raises(ValueError):
            cp.decide(request_id, True)
    with cp.identity('lead'), pytest.raises(ValueError):
        cp.decide(request_id, True)
    approve(request_id)
    with cp.identity('reviewer'), pytest.raises(ValueError):
        cp.decide(request_id, False)


def test_queue_exactly_one_claim_and_persisted_audit(setup):
    dep, _, _ = setup
    request_id = submit(dep)
    approve(request_id)
    calls = []
    def execute(row):
        calls.append(row['id'])
        cp.guard_apply(row['source'], row['deployment_id'])
        return {'status': 'SUCCEEDED'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cp.run_once(execute), range(2)))
    assert sorted(results) == [False, True]
    assert calls == [request_id]
    with cp.identity('lead'):
        assert cp.requests()[0]['status'] == 'SUCCEEDED'
    with cp.database() as db:
        assert [r[0] for r in db.execute('SELECT action FROM events ORDER BY id')] == ['SUBMITTED', 'APPROVED', 'CLAIMED', 'SUCCEEDED']


def test_changes_after_approval_require_new_review(setup):
    dep, directory, _ = setup
    approve(submit(dep))
    (directory / 'main.tf.json').write_text('{"changed":true}')
    assert cp.run_once(lambda _: pytest.fail('Changed configuration executed'))
    with cp.identity('lead'):
        assert cp.requests()[0]['status'] == 'NEEDS_REVIEW'


def test_schedule_expiry_and_duplicate_submission(setup, monkeypatch):
    dep, _, _ = setup
    start = cp.now()
    with pytest.raises(ValueError):
        submit(dep, start + timedelta(hours=2))
    request_id = submit(dep, start + timedelta(minutes=10))
    with pytest.raises(sqlite3.IntegrityError):
        submit(dep)
    approve(request_id)
    assert cp.run_once(lambda _: pytest.fail('Ran early')) is False
    monkeypatch.setattr(cp, 'now', lambda: start + timedelta(hours=2))
    assert cp.run_once(lambda _: pytest.fail('Expired plan executed'))
    with cp.identity('lead'):
        assert cp.requests()[0]['status'] == 'NEEDS_REVIEW'


def test_rejection_revocation_and_interruption_never_replay(setup):
    dep, _, registry = setup
    request_id = submit(dep)
    approve(request_id)
    registry['reviewer']['disabled'] = True
    assert cp.run_once(lambda _: pytest.fail('Revoked reviewer executed'))
    with cp.database() as db:
        db.execute("UPDATE requests SET status='RUNNING' WHERE id=?", (request_id,))
    assert not cp.run_once(lambda _: pytest.fail('Interrupted run replayed'))


def test_rejected_request_never_runs(setup):
    dep, _, _ = setup
    request_id = submit(dep)
    with cp.identity('reviewer'):
        cp.decide(request_id, False, 'Budget review required')
    assert not cp.run_once(lambda _: pytest.fail('Rejected request executed'))


def test_interactive_apply_is_blocked_and_plan_path_checked(setup):
    dep, _, _ = setup
    with pytest.raises(ValueError):
        cp.guard_apply('poc_plans', dep)
    with pytest.raises(ValueError):
        cp.artifact('poc_plans', '../other')


def test_password_authentication(setup):
    _, _, registry = setup
    salt = '00' * 16
    registry['lead'].update(salt=salt, password_hash=hashlib.pbkdf2_hmac('sha256', b'correct', bytes.fromhex(salt), 600000).hex())
    assert cp.authenticate('lead', 'correct') == 'lead'
    assert cp.authenticate('lead', 'incorrect') is None
    assert cp.authenticate('unknown', 'incorrect') is None


def test_approval_inbox_requires_ack_and_displays_activity(setup, monkeypatch):
    from streamlit.testing.v1 import AppTest
    dep, _, registry = setup
    submit(dep)
    monkeypatch.setattr(cp, 'principal', lambda: dict(registry['reviewer'], username='reviewer'))
    page = AppTest.from_string('from control_plane_ui import render\nrender()').run(timeout=20)
    assert not page.exception
    approve_button = next(b for b in page.button if b.label == 'Approve')
    assert approve_button.disabled
    page.checkbox[0].check().run(timeout=20)
    next(b for b in page.button if b.label == 'Approve').click().run(timeout=20)
    assert not page.exception
    assert cp.requests()[0]['status'] == 'APPROVED'
    assert cp.timeline(cp.requests()[0]['id'])[-1]['action'] == 'APPROVED'


def test_scoped_dashboard_has_no_sample_resources(setup, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import provider_connections
    _, _, registry = setup
    monkeypatch.setattr(cp, 'principal', lambda: dict(registry['lead'], username='lead'))
    monkeypatch.setattr(provider_connections, 'list_connections', lambda: [])
    page = AppTest.from_string('from control_plane_dashboard import render\nrender()').run(timeout=20)
    assert not page.exception
    assert page.metric[0].value == '0'
    assert page.metric[4].value == 'Unavailable'
    assert sum(c.value == 'POC demo' for c in page.caption) == 2


def test_customer_organization_context_sets_shared_target(setup, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import provider_connections
    monkeypatch.setattr(provider_connections, 'list_connections', lambda: [
        {'connection_id': 'conn', 'provider': 'AWS', 'name': 'Sandbox'}])
    page = AppTest.from_string('from control_plane_ui import context_selector\ncontext_selector()').run(timeout=20)
    assert not page.exception
    assert [box.value for box in page.selectbox] == ['Acme', 'Engineering', 'Checkout', 'AWS', 'conn']
    assert page.session_state['iipp_active_connection_id'] == 'conn'
