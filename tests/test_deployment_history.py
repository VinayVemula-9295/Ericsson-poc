"""Persisted history and read-only UI; no cloud or Terraform execution."""
import json

from streamlit.testing.v1 import AppTest

from deployment_history import load_history


def save(root, folder, name, record):
    directory = root / folder / name
    directory.mkdir(parents=True)
    filename = "record.json" if folder == "poc_plans" else "deployment.json"
    (directory / filename).write_text(json.dumps(record), encoding="utf-8")
    return directory


def test_all_statuses_and_clouds_survive_reads_without_exposing_artifacts(tmp_path):
    statuses = ["PREPARING", "AWAITING_APPROVAL", "APPLYING", "SUCCEEDED", "FAILED", "REJECTED"]
    for i, status in enumerate(statuses):
        directory = save(tmp_path, "poc_plans", f"poc-{i}", {
            "deployment_id": f"poc-{i}", "status": status,
            "created_at": f"2026-11-{i+1:02d}T00:00:00Z", "target_identity": "target",
            "candidate": {"provider": ["AWS", "Azure", "GCP"][i % 3], "region": "region"},
            "plan_summary": "PRIVATE_ARTIFACT_MARKER",
        })
        (directory / "terraform.tfvars.json").write_text(json.dumps({"name": "existing-server", "private_field": "PRIVATE_ARTIFACT_MARKER"}))
    rows, errors = load_history(tmp_path)
    assert errors == 0
    assert len(rows) == 6
    assert rows[0]["deployment_id"] == "poc-5"
    assert {r["status"] for r in rows} == {"Preparing", "Awaiting approval", "In progress", "Completed", "Failed", "Rejected"}
    assert all(r["name"] == "existing-server" for r in rows)
    assert "PRIVATE_ARTIFACT_MARKER" not in json.dumps(rows)
    assert load_history(tmp_path) == (rows, 0)


def test_legacy_records_missing_fields_and_corruption_do_not_hide_valid_history(tmp_path):
    save(tmp_path, "terraform_deployments", "legacy", {
        "deployment_id": "legacy", "status": "SUCCEEDED", "created_at": "2026-11-01T01:00:00+01:00",
        "requested_configuration": {"instance_name": "old-server", "account_id": "account", "region": "region"},
    })
    directory = save(tmp_path, "poc_plans", "invalid", {})
    (directory / "record.json").write_text("{")
    save(tmp_path, "poc_plans", "invalid-shape", {"deployment_id": "bad", "status": "FAILED", "candidate": [1]})
    save(tmp_path, "poc_plans", "minimal", {"deployment_id": "minimal", "status": "FAILED"})
    rows, errors = load_history(tmp_path)
    assert errors == 2 and len(rows) == 2
    assert rows[0]["name"] == "old-server"
    assert rows[0]["target"] == "account"
    assert rows[1]["target"] == "Unavailable"


def test_missing_history_is_read_only(tmp_path):
    root = tmp_path / "absent"
    assert load_history(root) == ([], 0)
    assert not root.exists()


def test_history_page_refresh_and_details_read_persisted_records(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    source = "from deployment_history_ui import render\nrender()"
    page = AppTest.from_string(source).run()
    assert not page.exception
    assert any("No deployments" in item.value for item in page.info)
    directory = save(tmp_path, "poc_plans", "poc-ui", {
        "deployment_id": "poc-ui", "resource_name": "saved-server", "status": "SUCCEEDED",
        "candidate": {"provider": "Azure", "region": "centralindia"}, "target_identity": "subscription",
        "state_history": [{"status": "APPLYING", "timestamp": "2026-11-05T00:00:00Z"},
                          {"status": "SUCCEEDED", "timestamp": "2026-11-05T00:01:00Z"}],
    })
    before = (directory / "record.json").read_bytes()
    page.button(key="refresh-deployment-history").click().run()
    assert not page.exception
    assert any(item.value == "saved-server" for item in page.text)
    page.button(key="deployment-details-poc_plans:poc-ui").click().run(timeout=15)
    assert not page.exception
    assert len(page.dataframe) == 1
    assert (directory / "record.json").read_bytes() == before


def test_success_table_filters_statuses_and_has_unique_details(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for status in ("SUCCEEDED", "FAILED", "APPLYING", "DESTROYED"):
        save(tmp_path, "poc_plans", status, {
            "deployment_id": status, "resource_name": status, "status": status,
        })
    page = AppTest.from_string("from deployment_history_ui import render\nrender()").run()
    assert not page.exception
    assert [item.value for item in page.subheader] == ["Successfully deployed", "History"]
    texts = [item.value for item in page.text]
    assert texts.count("SUCCEEDED") == 2
    for status in ("FAILED", "APPLYING", "DESTROYED"):
        assert texts.count(status) == 1
    keys = [button.key for button in page.button]
    assert len(keys) == len(set(keys))
    page.button(key="successful-deployment-details-poc_plans:SUCCEEDED").click().run()
    assert not page.exception
