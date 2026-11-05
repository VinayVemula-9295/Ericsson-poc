"""Approval behavior with a fake Terraform runner; no cloud or CLI execution."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("kind,expected", [("VM", 900), ("Kubernetes", 3600)])
def test_execution_timeout_defaults(monkeypatch, kind, expected):
    from poc_planner import execution_timeout
    monkeypatch.delenv("IIPP_KUBERNETES_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("IIPP_SERVER_TIMEOUT_SECONDS", raising=False)
    assert execution_timeout(kind) == expected


@pytest.mark.parametrize("value", ["invalid", "3599", "14401"])
def test_kubernetes_timeout_rejects_invalid_or_short_wait(monkeypatch, value):
    from poc_planner import execution_timeout
    monkeypatch.setenv("IIPP_KUBERNETES_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="IIPP_KUBERNETES_TIMEOUT_SECONDS"):
        execution_timeout("Kubernetes")


def test_execution_timeout_configuration(monkeypatch):
    from poc_planner import execution_timeout
    monkeypatch.setenv("IIPP_KUBERNETES_TIMEOUT_SECONDS", "7200")
    assert execution_timeout("Kubernetes") == 7200


def test_resource_outputs_exclude_secrets_and_complex_values():
    from poc_planner import safe_outputs
    assert safe_outputs({
        "instance_id": {"value": "i-example", "sensitive": False},
        "public_ip": {"value": "", "sensitive": False},
        "private_ip": {"value": "secret", "sensitive": True},
        "resource_name": {"value": "unknown-sensitivity"},
        "token": {"value": "secret", "sensitive": False},
        "cluster_arn": {"value": {"nested": "secret"}, "sensitive": False},
    }) == {"instance_id": "i-example", "public_ip": ""}


@pytest.mark.parametrize("outcome", ["success", "nonzero", "exception"])
def test_apply_history_persists_progress_and_terminal_status(runner, monkeypatch, outcome):
    from deployment_history import load_history
    record, directory = runner.prepare()
    tf = sys.modules["terraform_service"]
    original = tf._run
    def run(args, *a, **kw):
        if args[1] == "apply":
            saved = json.loads((directory / "record.json").read_text())
            assert saved["status"] == "APPLYING"
            rows, _ = load_history(directory.parents[1])
            assert rows[0]["status"] == "In progress"
            if outcome == "exception":
                raise RuntimeError("runner interrupted")
            return (0 if outcome == "success" else 1), ""
        return original(args, *a, **kw)
    monkeypatch.setattr(tf, "_run", run)
    runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    saved = json.loads((directory / "record.json").read_text())
    terminal = "SUCCEEDED" if outcome == "success" else "FAILED"
    assert saved["status"] == terminal
    assert saved["resource_name"] == "test-server"
    assert saved["completed_at"]
    assert [s["status"] for s in saved["state_history"]] == ["PREPARING", "AWAITING_APPROVAL", "APPLYING", terminal]
    if outcome != "success":
        assert saved["error"]


def test_planning_failure_is_saved_and_visible(runner, monkeypatch, tmp_path):
    from deployment_history import load_history
    tf = sys.modules["terraform_service"]
    def failed_init(*a, **kw):
        rows, _ = load_history(tmp_path)
        assert rows[0]["status"] == "Preparing"
        return 1, ""
    monkeypatch.setattr(tf, "_run_init", failed_init)
    with pytest.raises(ValueError, match="init failed"):
        runner.prepare()
    rows, errors = load_history(tmp_path)
    assert errors == 0 and rows[0]["status"] == "Failed"
    assert rows[0]["completed_at"] != "Unavailable"


@pytest.fixture
def runner(monkeypatch, tmp_path):
    import poc_planner as planner
    import poc_image_validation
    import provider_inputs
    token = secrets.token_urlsafe()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ENABLE_APPROVAL_EXECUTION", "true")
    monkeypatch.setenv("APPROVAL_TOKEN", token)
    calls = []
    def run(args, directory, timeout, extra_env=None):
        calls.append(list(args))
        if args[1] == "init" and not (directory / ".terraform.lock.hcl").exists():
            (directory / ".terraform.lock.hcl").write_bytes(b"provider-lock")
        if args[1] == "plan":
            (directory / "tfplan").write_bytes(b"binary-plan")
        return 0, "Plan: 1 to add, 0 to change, 0 to destroy."
    tf = SimpleNamespace(is_enabled=lambda: (True, "enabled"), TF_STATE_BUCKET="test-state-bucket",
                         TF_STATE_REGION="ap-south-1", _run=run, _run_init=run,
                         _sha256=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                         _validate_payload=lambda payload: payload, _validate_aws_references=lambda payload: None)
    monkeypatch.setitem(sys.modules, "terraform_service", tf)
    # Authorization and artifact tests are portable. Actual flock behavior remains
    # covered by the existing Linux Terraform safety suite.
    monkeypatch.setattr(planner, "_lock", lambda directory: nullcontext())
    monkeypatch.setattr(poc_image_validation, "verify", lambda *a: {"verified": True})
    monkeypatch.setattr(provider_inputs, "validate_live_inputs", lambda *a: {"status": "verified"})
    runtime = dict(account_id="123456789012", target_role_arn="arn:aws:iam::123456789012:role/execution",
                   connection_name="AWS-POC", external_id=secrets.token_urlsafe(), source_env={})
    monkeypatch.setattr(planner, "get_aws_terraform_runtime", lambda cid: runtime)
    connection = dict(provider="AWS", connection_status="CONNECTED", account_id=runtime["account_id"],
                      metadata={"client_id": "client", "tenant_id": "tenant", "subscription_id": "subscription", "project_id": "project"})
    monkeypatch.setattr(planner, "get_connection", lambda cid: connection)
    monkeypatch.setattr(planner, "get_saved_secret", lambda cid: {"client_secret": secrets.token_urlsafe(), "service_account_json": {}})
    def prepare(provider="AWS", kind="VM"):
        from poc_terraform import configuration
        candidate = dict(provider=provider, region="ap-south-1", os="Linux", sku="t3.large", vcpus=2,
                         memory_gib=8., available=True, offered_in_target_location=True, monthly_compute_estimate=50,
                         connection_id="connection", cluster={"node_count": 2})
        request = dict(kind=kind, os="Linux", min_vcpus=2, min_memory_gib=8., environment="poc", ttl_days=2)
        connection["provider"] = provider
        if provider != "AWS":
            connection["account_id"] = None
        variables = configuration(candidate, kind)["variable"]
        inputs = {name: "value" for name, spec in variables.items() if not spec.get("sensitive")}
        inputs.update(name="test-server", region="ap-south-1")
        if provider == "AWS":
            inputs["execution_role_arn"] = runtime["target_role_arn"]
            if kind == "Kubernetes":
                inputs.update(cluster_role_arn="arn:aws:iam::123456789012:role/cluster",
                              node_role_arn="arn:aws:iam::123456789012:role/node",
                              kms_key_arn="arn:aws:kms:ap-south-1:123456789012:key/01234567-89ab-cdef-0123-456789abcdef",
                              second_subnet_id="second-subnet")
        elif provider == "Azure":
            inputs["subscription_id"] = "subscription"
        else:
            inputs["project_id"] = "project"
        record = planner.prepare(candidate, request, inputs)
        return record, tmp_path / "poc_plans" / record["deployment_id"]
    return SimpleNamespace(planner=planner, tf=tf, calls=calls, token=token,
                           runtime=runtime, connection=connection, prepare=prepare)


def destroy_ready(runner, monkeypatch):
    record, directory = runner.prepare()
    runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    original = runner.tf._run
    def run(args, directory, timeout, extra_env=None):
        if args[1:3] == ["show", "-json"]:
            return 0, json.dumps({"values": {"root_module": {"resources": [
                {"mode": "managed", "type": "aws_instance", "name": "vm", "values": {"id": "i-example", "tags": {"Name": "test-server"}}}]}}})
        if args[1] == "plan" and "-destroy" in args:
            (directory / "tfplan-destroy").write_bytes(b"destroy-plan")
        return original(args, directory, timeout, extra_env=extra_env)
    monkeypatch.setattr(runner.tf, "_run", run)
    return runner.planner.prepare_destroy_plan(record["deployment_id"]), directory


def test_destroy_requires_review_and_exact_saved_plan(runner, monkeypatch):
    record, directory = destroy_ready(runner, monkeypatch)
    assert record["status"] == "DESTROY_AWAITING_APPROVAL"
    assert record["resource_identifiers"][0]["id"] == "i-example"
    with pytest.raises(ValueError, match="valid approval token"):
        runner.planner.apply_destroy_plan(record["deployment_id"], "Reviewer", "incorrect")
    result = runner.planner.apply_destroy_plan(record["deployment_id"], "Reviewer", runner.token)
    assert result["status"] == "DESTROYED"
    assert [c for c in runner.calls if c[1] == "apply"][-1][-1] == "tfplan-destroy"
    with pytest.raises(ValueError, match="not awaiting destroy"):
        runner.planner.apply_destroy_plan(record["deployment_id"], "Reviewer", runner.token)


@pytest.mark.parametrize("change", ["plan", "inputs", "backend", "target", "expired", "disabled"])
def test_destroy_rejects_changed_review_context(runner, monkeypatch, change):
    record, directory = destroy_ready(runner, monkeypatch)
    if change == "plan":
        (directory / "tfplan-destroy").write_bytes(b"changed")
    elif change == "inputs":
        (directory / "terraform.tfvars.json").write_text("{}")
    elif change == "backend":
        runner.tf.TF_STATE_BUCKET = "changed"
    elif change == "target":
        runner.connection["account_id"] = "999999999999"
    elif change == "expired":
        record["destroy_plan_created_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        runner.planner._write(directory, record)
    else:
        monkeypatch.setenv("ENABLE_APPROVAL_EXECUTION", "false")
    before = len([c for c in runner.calls if c[1] == "apply"])
    with pytest.raises(ValueError):
        runner.planner.apply_destroy_plan(record["deployment_id"], "Reviewer", runner.token)
    assert len([c for c in runner.calls if c[1] == "apply"]) == before


@pytest.mark.parametrize("provider", ["AWS", "Azure", "GCP"])
@pytest.mark.parametrize("kind", ["VM", "Kubernetes"])
def test_plan_never_applies_and_approval_applies_only_saved_binary(runner, provider, kind):
    record, directory = runner.prepare(provider, kind)
    assert record["status"] == "AWAITING_APPROVAL"
    assert not any(c[1] == "apply" for c in runner.calls)
    result = runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    assert result["status"] == "SUCCEEDED"
    assert [c for c in runner.calls if c[1] == "apply"] == [["terraform", "apply", "-input=false", "-no-color", "tfplan"]]
    assert "-lockfile=readonly" in [c for c in runner.calls if c[1] == "init"][-1]
    assert runner.token not in (directory / "record.json").read_text()
    assert runner.runtime["external_id"] not in (directory / "terraform.tfvars.json").read_text()
    with pytest.raises(ValueError, match="not awaiting approval"):
        runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)


def test_failed_live_account_validation_stops_before_terraform_init(runner, monkeypatch):
    import provider_inputs
    def unavailable(*args):
        raise ValueError("Live account validation failed for subnet id")
    monkeypatch.setattr(provider_inputs, "validate_live_inputs", unavailable)
    with pytest.raises(ValueError, match="Live account validation"):
        runner.prepare(kind="Kubernetes")
    assert not runner.calls


@pytest.mark.parametrize("provider", ["AWS", "Azure", "GCP"])
def test_cluster_saved_configuration_retains_selected_hardware_count_and_version(runner, provider):
    record, directory = runner.prepare(provider=provider, kind="Kubernetes")
    conf = json.loads((directory / "main.tf.json").read_text())
    values = json.loads((directory / "terraform.tfvars.json").read_text())
    assert record["live_input_evidence"]["status"] == "verified"
    assert values["kubernetes_version"] == "value"
    if provider == "AWS":
        workers = conf["resource"]["aws_eks_node_group"]["workers"]
        assert workers["instance_types"] == [record["candidate"]["sku"]]
        assert workers["scaling_config"][0]["desired_size"] == 2
        assert workers["version"] == "${var.kubernetes_version}"
    elif provider == "Azure":
        workers = conf["resource"]["azurerm_kubernetes_cluster"]["cluster"]["default_node_pool"][0]
        assert workers["vm_size"] == record["candidate"]["sku"]
        assert workers["node_count"] == 2
    else:
        workers = conf["resource"]["google_container_node_pool"]["workers"]
        assert workers["node_config"][0]["machine_type"] == record["candidate"]["sku"]
        assert workers["node_count"] == 2
        assert workers["version"] == "${var.kubernetes_version}"


@pytest.mark.parametrize("artifact", ["tfplan", "main.tf.json", "terraform.tfvars.json", ".terraform.lock.hcl"])
def test_changed_saved_artifact_blocks_apply(runner, artifact):
    record, directory = runner.prepare()
    (directory / artifact).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    assert not any(c[1] == "apply" for c in runner.calls)


@pytest.mark.parametrize("condition", ["expired", "future", "account", "provider", "role", "backend", "state_region", "policy", "connection"])
def test_stale_plan_identity_backend_and_policy_block_apply(runner, condition):
    record, directory = runner.prepare()
    if condition in {"expired", "future"}:
        record["created_at"] = (datetime.now(timezone.utc) + timedelta(hours=2 if condition == "future" else -2)).isoformat()
        (directory / "record.json").write_text(json.dumps(record))
    elif condition == "account":
        runner.connection["account_id"] = "999999999999"
    elif condition == "provider":
        runner.connection["provider"] = "GCP"
    elif condition == "role":
        runner.runtime["target_role_arn"] += "-changed"
    elif condition == "backend":
        runner.tf.TF_STATE_BUCKET = "changed"
    elif condition == "state_region":
        runner.tf.TF_STATE_REGION = "eu-west-1"
    elif condition == "policy":
        runner.runtime["connection_name"] = "AWS-TEST"
    else:
        runner.connection["connection_status"] = "ERROR"
    expected_error = {"expired": "expired", "future": "expired", "account": "identity changed",
                      "provider": "matching the target provider", "role": "Execution role differs",
                      "backend": "backend changed", "state_region": "backend changed",
                      "policy": "Policy denied", "connection": "matching the target provider"}[condition]
    with pytest.raises(ValueError, match=expected_error):
        runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    assert not any(c[1] == "apply" for c in runner.calls)


def test_invalid_token_missing_name_and_disabled_execution_never_apply(runner, monkeypatch):
    record, directory = runner.prepare()
    for reviewer, token in (("Reviewer", ""), ("", runner.token), ("Reviewer", secrets.token_urlsafe())):
        with pytest.raises(ValueError, match="valid approval token"):
            runner.planner.apply_saved_plan(record["deployment_id"], reviewer, token)
    monkeypatch.setenv("ENABLE_APPROVAL_EXECUTION", "false")
    with pytest.raises(ValueError, match="disabled"):
        runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    assert not any(c[1] == "apply" for c in runner.calls)


def test_failed_apply_cannot_be_retried_as_unapplied_plan(runner, monkeypatch):
    record, directory = runner.prepare()
    monkeypatch.setattr(runner.tf, "_run", lambda *a, **kw: (1, "failed"))
    result = runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)
    assert result["status"] == "FAILED"
    with pytest.raises(ValueError, match="not awaiting approval"):
        runner.planner.apply_saved_plan(record["deployment_id"], "Reviewer", runner.token)


def test_demo_account_uses_poc_policy_and_unknown_environment_still_fails(runner):
    runner.runtime["connection_name"] = "Demo-Account"
    record, _ = runner.prepare()
    assert record["requirements"]["environment"] == "poc"
    runner.runtime["connection_name"] = "Unclassified account"
    with pytest.raises(ValueError, match="Policy denied cross-environment"):
        runner.prepare()
