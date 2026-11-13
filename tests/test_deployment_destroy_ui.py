"""Review sequencing with mocked runner; never executes Terraform."""
import json
import secrets
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest


def test_successful_row_exposes_destroy_without_executing(monkeypatch):
    import deployment_history_ui as ui
    row = dict(key="test-success", provider="AWS", raw_status="SUCCEEDED", name="server", target="Demo",
               region="ap-south-1", status="Successfully deployed", created_at="2026-11-06")
    monkeypatch.setattr(ui, "load_history", lambda: ([row], 0))
    page = AppTest.from_string("from deployment_history_ui import render\nrender()", default_timeout=15).run()
    assert not page.exception
    destroy = [b for b in page.button if b.label == "Destroy"]
    assert len(destroy) == 1
    assert destroy[0].key == "successful-destroy-test-success"


def test_destroy_dialog_requires_submit_then_validation(monkeypatch):
    import deployment_history_ui as ui
    did = "poc-" + "a" * 32
    calls = []
    token = secrets.token_urlsafe()
    def prepare(deployment_id):
        calls.append("plan")
        return dict(status="DESTROY_AWAITING_APPROVAL", destroy_plan_summary="Plan: delete managed VM")
    def apply(deployment_id, reviewer, supplied):
        assert reviewer == "Reviewer" and supplied == token
        calls.append("apply")
        return dict(status="DESTROYED", resource_identifiers=[{"type": "aws_instance", "id": "i-example", "name": "server"}])
    monkeypatch.setattr(ui, "_service", lambda row: SimpleNamespace(prepare_destroy_plan=prepare, apply_destroy_plan=apply))
    row = dict(key="poc_plans:" + did, source="poc_plans", deployment_id=did, name="server", target="target", raw_status="SUCCEEDED")
    page = AppTest.from_string('''
import streamlit as st
from deployment_history_ui import _destroy, _destroy_success
pending = st.session_state.pop("open-destroy", None)
row = ROW_PLACEHOLDER
if pending or "destroy-review-" + row["key"] in st.session_state:
    row = pending or row
    _destroy(row)
details = st.session_state.get("destroy-success-details")
if details:
    _destroy_success(details)
'''.replace('ROW_PLACEHOLDER', repr(row)), default_timeout=15)
    page.session_state['open-destroy'] = row
    page = page.run()
    assert not page.exception
    assert calls == ["plan"] and not page.text_input
    page.button(key="destroy-review-" + row["key"] + "-submit").click().run()
    assert not page.exception and len(page.text_input) == 2
    page.text_input[0].set_value("Reviewer")
    page.text_input[1].set_value(token)
    page.checkbox[0].check().run()
    page.button(key="destroy-review-" + row["key"] + "-approve").click().run()
    assert not page.exception
    assert calls == ["plan", "apply"]
    assert page.session_state['destroy-success-details']['Name'] == 'server'
    assert page.session_state['destroy-success-details']['ID'] == 'i-example'
    assert page.session_state['destroy-success-details']['Type'] == 'aws_instance'
    assert any(button.label == 'Close' for button in page.button)
    page.button(key='close-destroy-success').click().run()
    assert 'destroy-success-details' not in page.session_state
    assert calls == ['plan', 'apply']


def test_resume_loads_only_saved_summary(monkeypatch, tmp_path):
    from deployment_history import load_destroy_review
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    did = "cp-" + "b" * 16
    path = tmp_path / "terraform_deployments" / did
    path.mkdir(parents=True)
    (path / "deployment.json").write_text(json.dumps(dict(status="DESTROY_AWAITING_APPROVAL", destroy_plan_summary="delete", unrelated="private")))
    assert load_destroy_review("terraform_deployments", did) == dict(status="DESTROY_AWAITING_APPROVAL", destroy_plan_summary="delete")
