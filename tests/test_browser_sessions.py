import json

import browser_sessions
from deployment_history import mask_account_identifier


def _configure_store(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    monkeypatch.delenv('CONTROL_PLANE_USERS_FILE', raising=False)


def test_grants_are_single_use_and_browser_sessions_are_isolated(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    user = {'username': 'admin', 'role': 'administrator'}
    first_grant = browser_sessions.issue_grant(user)
    first_session = browser_sessions.exchange_grant(first_grant)
    assert first_session
    assert browser_sessions.exchange_grant(first_grant) is None
    second_session = browser_sessions.exchange_grant(browser_sessions.issue_grant(user))
    assert second_session and second_session != first_session
    assert browser_sessions.restore(first_session)['username'] == 'admin'
    assert browser_sessions.restore(second_session)['username'] == 'admin'
    assert browser_sessions.restore('invalid-cookie-value') is None


def test_idle_and_absolute_expiry_use_fixed_creation_time(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    monkeypatch.setenv('IIPP_SESSION_IDLE_SECONDS', '60')
    monkeypatch.setenv('IIPP_SESSION_MAX_SECONDS', '120')
    now = [1000.0]
    monkeypatch.setattr(browser_sessions.time, 'time', lambda: now[0])
    session = browser_sessions.exchange_grant(browser_sessions.issue_grant(
        {'username': 'admin', 'role': 'administrator'}))
    now[0] = 1050.0
    assert browser_sessions.restore(session)
    now[0] = 1090.0
    assert browser_sessions.restore(session)
    now[0] = 1121.0
    assert browser_sessions.restore(session) is None


def test_disabled_registry_user_is_revoked(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    registry = tmp_path / 'users.json'
    registry.write_text(json.dumps({'admin': {'role': 'administrator', 'disabled': False}}))
    monkeypatch.setenv('CONTROL_PLANE_USERS_FILE', str(registry))
    session = browser_sessions.exchange_grant(browser_sessions.issue_grant(
        {'username': 'admin', 'role': 'administrator'}))
    assert browser_sessions.restore(session)
    registry.write_text(json.dumps({'admin': {'role': 'administrator', 'disabled': True}}))
    assert browser_sessions.restore(session) is None


def test_missing_optional_registry_file_does_not_revoke_browser_session(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    monkeypatch.setenv('CONTROL_PLANE_USERS_FILE', str(tmp_path / 'not-mounted-users.json'))
    session = browser_sessions.exchange_grant(browser_sessions.issue_grant(
        {'username': 'admin', 'role': 'administrator'}))
    assert browser_sessions.restore(session)['username'] == 'admin'


def test_saved_configuration_masks_account_identifiers():
    assert mask_account_identifier('333347372766') == '33xxxxxxx766'
    assert mask_account_identifier('arn:aws:iam::333347372766:role/runner') == 'arn:aws:iam::33xxxxxxx766:role/runner'


def test_browser_identity_requires_single_use_server_proof(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    token = browser_sessions.exchange_grant(browser_sessions.issue_grant(
        {'username': 'admin', 'role': 'administrator'}))
    proof = browser_sessions.identity_proof(token)['proof']
    identity = browser_sessions.consume_identity_proof(proof)
    assert identity['username'] == 'admin'
    assert browser_sessions.consume_identity_proof(proof) is None
    assert browser_sessions.consume_identity_proof('forged-proof') is None
    browser_sessions.revoke(token)
    assert browser_sessions.restore_hash(identity['session_key']) is None
