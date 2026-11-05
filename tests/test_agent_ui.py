"""Agent entry-point checks; request/plan interactions live in test_poc_ui.py."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def agent_page(monkeypatch, tmp_path):
    import provider_connections
    import agent_flow_ui
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr(provider_connections, "list_connections", lambda: [])
    monkeypatch.setattr(agent_flow_ui, "list_connections", lambda: [])
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    page.session_state["navigation"] = "Agent"
    # Users with an old browser session must receive the new UI too.
    page.session_state["extended-poc"] = False
    return page.run(timeout=20)


def test_existing_sessions_receive_new_prompt_without_toggle(agent_page):
    assert not agent_page.exception
    assert len(agent_page.text_area) == 1
    assert agent_page.text_area[0].placeholder == "Ask Intelligent Provisioning agent"
    assert not agent_page.toggle
    assert any(b.label == "Send" for b in agent_page.button)
    assert not any("Kubernetes execution" in b.label for b in agent_page.button)


def test_workspace_removes_intro_and_entire_evidence_sidebar(agent_page):
    assert not agent_page.exception
    content = "\n".join(item.value for item in agent_page.markdown)
    for removed in ("Describe the outcome. Review the plan.", "Decision evidence", "Cloud targets", "Audit trail"):
        assert removed not in content
    assert "Describe workload and constraints" in content


def test_empty_submission_and_initial_render_do_not_query_clouds(agent_page, monkeypatch):
    import agent_flow_ui
    monkeypatch.setattr(agent_flow_ui, "collect", lambda *a: pytest.fail("Unexpected cloud lookup"))
    agent_page.text_area(key="poc-prompt").input("   ")
    next(b for b in agent_page.button if b.label == "Send").click().run(timeout=20)
    assert not agent_page.exception
    assert any("requirements first" in item.value for item in agent_page.error)


def test_agent_does_not_render_another_accounts_cached_inventory(agent_page):
    agent_page.session_state["iipp-live-inventory-result"] = {
        "connection_id": "different-account", "ok": True, "checked_at": "2026-11-04T00:00:00Z",
    }
    agent_page.run(timeout=20)
    assert not agent_page.exception
    content = "\n".join(item.value for item in agent_page.markdown)
    assert "Last inventory read" not in content
    assert "Live AWS account inventory" not in content
