from streamlit.testing.v1 import AppTest
import pytest


def test_admin_gate_and_sign_out_clear_session(monkeypatch):
    import secrets
    import app_auth
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    monkeypatch.delenv('CONTROL_PLANE_USERS_FILE', raising=False)
    monkeypatch.setenv('IIPP_ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('IIPP_ADMIN_PASSWORD', password)
    app_auth._attempts.clear()
    page = AppTest.from_string('''
import streamlit as st
from shell_ui import require_demo_login, header_profile, sidebar_profile
require_demo_login()
with st.sidebar:
    sidebar_profile()
header_profile()
st.write('Workspace loaded')
''').run()
    assert not page.exception
    assert not any(x.value == 'Workspace loaded' for x in page.markdown)
    page.text_input[0].input('admin')
    page.text_input[1].input(password)
    page.button[0].click().run()
    assert page.session_state['demo-profile']['initials'] == 'AD'
    assert 'Demo profile' not in '\n'.join(c.value for c in page.caption)
    assert not any(b.label == 'Infrastructure & Monitoring' for b in page.button)
    page.session_state['poc-saved-plan'] = {'deployment_id': 'pending'}
    next(b for b in page.button if b.label == 'Sign out').click().run()
    assert not page.exception
    assert any(b.label == 'Sign in' for b in page.button)
    assert 'poc-saved-plan' not in page.session_state


@pytest.mark.parametrize('status', ['FAILED', 'DESTROYED', 'APPLYING', 'AWAITING_APPROVAL', 'REJECTED'])
def test_unsuccessful_saved_configuration_cannot_bypass_reuse_filter(tmp_path, monkeypatch, status):
    import json
    from deployment_history import load_reuse
    from reuse_artifacts import load
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    did = 'poc-' + 'a' * 32
    directory = tmp_path / 'poc_plans' / did
    directory.mkdir(parents=True)
    (directory / 'record.json').write_text(json.dumps(dict(status=status)))
    for reader in (load, load_reuse):
        with pytest.raises(ValueError, match='successful'):
            reader('poc_plans', did)
