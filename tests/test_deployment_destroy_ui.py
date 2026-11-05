"""Review sequencing with mocked runner; never executes Terraform."""
import json
import secrets
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest


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
        return dict(status="DESTROYED", resource_identifiers=[{"id": "i-example", "name": "server"}])
    monkeypatch.setattr(ui, "_service", lambda row: SimpleNamespace(prepare_destroy_plan=prepare, apply_destroy_plan=apply))
    row = dict(key="poc_plans:" + did, source="poc_plans", deployment_id=did, name="server", target="target", raw_status="SUCCEEDED")
    page = AppTest.from_string("from deployment_history_ui import _destroy\n_destroy(" + repr(row) + ")", default_timeout=15).run()
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
    assert "Successfully destroyed server" in page.success[0].value


def test_resume_loads_only_saved_summary(monkeypatch, tmp_path):
    from deployment_history import load_destroy_review
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    did = "cp-" + "b" * 16
    path = tmp_path / "terraform_deployments" / did
    path.mkdir(parents=True)
    (path / "deployment.json").write_text(json.dumps(dict(status="DESTROY_AWAITING_APPROVAL", destroy_plan_summary="delete", unrelated="private")))
    assert load_destroy_review("terraform_deployments", did) == dict(status="DESTROY_AWAITING_APPROVAL", destroy_plan_summary="delete")
