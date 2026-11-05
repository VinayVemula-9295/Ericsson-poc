from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


VALID_PAYLOAD = {
    "region": "ap-south-1", "ami_id": "ami-0123456789abcdef0", "instance_type": "t3.micro",
    "subnet_id": "subnet-0123456789abcdef0", "security_group_id": "sg-0123456789abcdef0",
    "instance_name": "iipp-test", "owner": "Innominds-IIPP", "ttl_days": 2,
    "instance_profile_name": "", "aws_connection_id": "aws-poc", "environment": "poc",
}


def test_terraform_payload_accepts_dynamic_aws_type_name_but_rejects_malformed_name():
    from terraform_service import _validate_payload
    dynamic = dict(VALID_PAYLOAD, instance_type="m7i.48xlarge")
    assert _validate_payload(dynamic)["instance_type"] == "m7i.48xlarge"
    malformed = dict(VALID_PAYLOAD, instance_type="m7i/48xlarge")
    with pytest.raises(ValueError, match="valid EC2 instance type identifier"):
        _validate_payload(malformed)


def test_plan_workflow_generates_saved_plan_but_never_applies(monkeypatch, tmp_path):
    import terraform_service as service
    monkeypatch.setenv("ENABLE_TERRAFORM_WORKFLOW", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "is_enabled", lambda: (True, "enabled"))
    monkeypatch.setattr(service, "TF_STATE_BUCKET", "iipp-test-state-bucket")
    monkeypatch.setattr(service, "_validate_aws_references", lambda _payload: None)
    monkeypatch.setattr(service, "get_aws_terraform_runtime", lambda _connection: {
        "connection_id": "aws-poc", "connection_name": "AWS-POC", "account_id": "123456789012",
        "target_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole",
        "external_id": "test-external-id", "source_env": {},
    })
    monkeypatch.setattr(service.shutil, "which", lambda _cmd: "/usr/bin/terraform")
    calls = []
    def fake_run(args, cwd, timeout=300, extra_env=None):
        calls.append(args)
        if len(args) > 1 and args[1] == "init":
            cache = Path(cwd) / ".terraform"
            cache.mkdir(exist_ok=True)
            (cache / "provider-cache-fixture").write_bytes(b"temporary-provider-copy")
        if "-out=tfplan" in args:
            (Path(cwd) / "tfplan").write_bytes(b"saved-plan-fixture")
        if args[1:3] == ["show", "-no-color"]:
            return 0, "Plan: 1 to add, 0 to change, 0 to destroy."
        return 0, "ok"
    monkeypatch.setattr(service, "_run", fake_run)
    record = service.prepare_plan(VALID_PAYLOAD)
    assert record["status"] == "AWAITING_APPROVAL"
    assert record["plan_changes"] == "NOT APPLIED"
    assert record["plan_sha256"]
    assert [item["status"] for item in record["state_history"]] == [
        "DRAFT", "VALIDATED", "PLAN_READY", "AWAITING_APPROVAL"
    ]
    assert not any(len(call) > 1 and call[1] == "apply" for call in calls)
    plan_file = Path(tmp_path) / "terraform_deployments" / record["deployment_id"] / "tfplan"
    assert plan_file.stat().st_mode & 0o777 == 0o600
    assert not (plan_file.parent / ".terraform").exists()
    assert record["terraform_working_cache"]["status"] == "REMOVED"


def _write_pending_plan(service, tmp_path, plan_created=None):
    base = tmp_path / "terraform_deployments"
    base.mkdir(exist_ok=True)
    deployment_id = "cp-0123456789abcdef"
    directory = base / deployment_id
    directory.mkdir(mode=0o700, exist_ok=True)
    plan = directory / "tfplan"
    plan.write_bytes(b"plan bytes")
    created = plan_created or datetime.now(timezone.utc).isoformat()
    record = {"deployment_id": deployment_id, "status": "AWAITING_APPROVAL", "plan_created_at": created,
              "plan_file": "tfplan", "plan_sha256": service._sha256(plan), "updated_at": created,
              "state_backend": "s3", "state_bucket": "iipp-test-state-bucket",
              "aws_connection_id": "aws-poc",
              "target_execution_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole"}
    (directory / "deployment.json").write_text(json.dumps(record))
    return deployment_id, directory


def test_apply_uses_same_checksummed_plan_only_after_explicit_approval(monkeypatch, tmp_path):
    import terraform_service as service
    monkeypatch.setenv("ENABLE_TERRAFORM_WORKFLOW", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "is_enabled", lambda: (True, "enabled"))
    monkeypatch.setattr(service, "TF_STATE_BUCKET", "iipp-test-state-bucket")
    monkeypatch.setattr(service, "get_aws_terraform_runtime", lambda _connection: {
        "target_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole",
        "external_id": "test-external-id", "source_env": {},
    })
    deployment_id, directory = _write_pending_plan(service, tmp_path)
    calls = []
    def fake_run(args, cwd, timeout=300, extra_env=None):
        calls.append(args)
        if len(args) > 1 and args[1] == "init":
            cache = Path(cwd) / ".terraform"
            cache.mkdir(exist_ok=True)
            (cache / "provider-cache-fixture").write_bytes(b"temporary-provider-copy")
        if args[1] == "output":
            return 0, "{}"
        return 0, "applied fixture"
    monkeypatch.setattr(service, "_run", fake_run)
    result = service.apply_saved_plan(deployment_id, "demo-reviewer")
    assert result["status"] == "SUCCEEDED"
    apply_calls = [call for call in calls if call[1] == "apply"]
    assert len(apply_calls) == 1
    assert apply_calls[0][-1] == str(directory / "tfplan")
    assert any("-lockfile=readonly" in arg for call in calls for arg in call)
    assert not (directory / ".terraform").exists()
    assert calls[0][1] == "init"


def test_apply_rejects_modified_saved_plan(monkeypatch, tmp_path):
    import terraform_service as service
    monkeypatch.setenv("ENABLE_TERRAFORM_WORKFLOW", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "is_enabled", lambda: (True, "enabled"))
    monkeypatch.setattr(service, "TF_STATE_BUCKET", "iipp-test-state-bucket")
    monkeypatch.setattr(service, "get_aws_terraform_runtime", lambda _connection: {
        "target_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole",
        "external_id": "test-external-id", "source_env": {},
    })
    deployment_id, directory = _write_pending_plan(service, tmp_path)
    (directory / "tfplan").write_bytes(b"changed plan")
    with pytest.raises(ValueError, match="checksum changed"):
        service.apply_saved_plan(deployment_id, "demo-reviewer")


def test_apply_rejects_stale_and_future_saved_plans(monkeypatch, tmp_path):
    import terraform_service as service
    monkeypatch.setenv("ENABLE_TERRAFORM_WORKFLOW", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "is_enabled", lambda: (True, "enabled"))
    monkeypatch.setattr(service, "TF_STATE_BUCKET", "iipp-test-state-bucket")
    monkeypatch.setattr(service, "get_aws_terraform_runtime", lambda _connection: {
        "target_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole",
        "external_id": "test-external-id", "source_env": {},
    })
    old_id, _ = _write_pending_plan(service, tmp_path, (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())
    with pytest.raises(ValueError, match="older than four hours"):
        service.apply_saved_plan(old_id, "reviewer")
    future_id = "cp-fedcba9876543210"
    future_dir = tmp_path / "terraform_deployments" / future_id
    future_dir.mkdir(mode=0o700)
    plan = future_dir / "tfplan"
    plan.write_bytes(b"plan bytes")
    future_record = {"deployment_id": future_id, "status": "AWAITING_APPROVAL",
                     "plan_created_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                     "plan_file": "tfplan", "plan_sha256": service._sha256(plan),
                     "state_backend": "s3", "state_bucket": "iipp-test-state-bucket",
                     "aws_connection_id": "aws-poc",
                     "target_execution_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole"}
    (future_dir / "deployment.json").write_text(json.dumps(future_record))
    with pytest.raises(ValueError, match="future"):
        service.apply_saved_plan(future_id, "reviewer")


def test_deployment_lock_blocks_concurrent_approval(monkeypatch, tmp_path):
    import terraform_service as service
    monkeypatch.setenv("ENABLE_TERRAFORM_WORKFLOW", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "is_enabled", lambda: (True, "enabled"))
    deployment_id, directory = _write_pending_plan(service, tmp_path)
    lock_path = directory / ".operation.lock"
    with lock_path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already running"):
            service.apply_saved_plan(deployment_id, "reviewer")
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_destroy_plan_can_be_rebuilt_after_previous_plan_failure(monkeypatch, tmp_path):
    import terraform_service as service

    monkeypatch.setenv("ENABLE_TERRAFORM_WORKFLOW", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "is_enabled", lambda: (True, "enabled"))
    base = tmp_path / "terraform_deployments"
    base.mkdir(exist_ok=True)
    deployment_id = "cp-0123456789abcdef"
    directory = base / deployment_id
    directory.mkdir(mode=0o700)
    (directory / "main.tf").write_text("# fixture")
    (directory / "terraform.tfvars.json").write_text("{}")
    (directory / "deployment.json").write_text(json.dumps({
        "deployment_id": deployment_id, "status": "DESTROY_PLAN_FAILED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state_history": [{"status": "SUCCEEDED"}, {"status": "DESTROY_PLAN_FAILED"}],
    }))

    calls = []
    def fake_run(args, cwd, timeout=300, extra_env=None):
        calls.append(args)
        if len(args) > 1 and args[1] == "init":
            cache = Path(cwd) / ".terraform"
            cache.mkdir(exist_ok=True)
            (cache / "provider-cache-fixture").write_bytes(b"temporary-provider-copy")
        if "-out=tfplan-destroy" in args:
            (Path(cwd) / "tfplan-destroy").write_bytes(b"saved-destroy-plan")
        if args[1:3] == ["show", "-no-color"]:
            return 0, "Plan: 0 to add, 0 to change, 1 to destroy."
        return 0, "ok"

    monkeypatch.setattr(service, "_run", fake_run)
    result = service.prepare_destroy_plan(deployment_id)
    assert result["status"] == "DESTROY_AWAITING_APPROVAL"
    assert result["destroy_plan_sha256"]
    assert (directory / "tfplan-destroy").stat().st_mode & 0o777 == 0o600
    assert any("-out=tfplan-destroy" in call for call in calls)
    assert not any(len(call) > 1 and call[1] == "apply" for call in calls)
    assert not (directory / ".terraform").exists()


def test_aws_reference_validation_checks_type_offering_in_selected_subnet_az(monkeypatch):
    import terraform_service as service

    class FakeEC2:
        def describe_images(self, ImageIds):
            assert ImageIds == [VALID_PAYLOAD["ami_id"]]
            return {"Images": [{"ImageId": ImageIds[0], "State": "available", "Architecture": "x86_64", "PlatformDetails": "Linux/UNIX"}]}

        def describe_subnets(self, SubnetIds):
            return {"Subnets": [{"SubnetId": SubnetIds[0], "VpcId": "vpc-0123456789abcdef0", "AvailabilityZone": "ap-south-1a"}]}

        def describe_security_groups(self, GroupIds):
            return {"SecurityGroups": [{"GroupId": GroupIds[0], "VpcId": "vpc-0123456789abcdef0"}]}

        def describe_instance_type_offerings(self, **kwargs):
            assert kwargs["LocationType"] == "availability-zone"
            assert any(f["Name"] == "location" and f["Values"] == ["ap-south-1a"] for f in kwargs["Filters"])
            return {"InstanceTypeOfferings": [{"InstanceType": "t3.micro"}]}

        def describe_instance_types(self, InstanceTypes):
            return {"InstanceTypes": [{"InstanceType": "t3.micro", "VCpuInfo": {"DefaultVCpus": 2},
                "MemoryInfo": {"SizeInMiB": 1024}, "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
                "SupportedRootDeviceTypes": ["ebs"], "SupportedVirtualizationTypes": ["hvm"],
                "SupportedUsageClasses": ["on-demand"], "BareMetal": False}]}

    monkeypatch.setattr(service.boto3, "client", lambda *args, **kwargs: FakeEC2())
    payload = service._validate_payload({**VALID_PAYLOAD, "aws_connection_id": ""})
    service._validate_aws_references(payload)


def test_aws_reference_validation_rejects_type_not_offered_in_selected_subnet_az(monkeypatch):
    import terraform_service as service

    class FakeEC2:
        def describe_images(self, ImageIds):
            return {"Images": [{"ImageId": ImageIds[0], "State": "available", "Architecture": "x86_64", "PlatformDetails": "Linux/UNIX"}]}

        def describe_subnets(self, SubnetIds):
            return {"Subnets": [{"SubnetId": SubnetIds[0], "VpcId": "vpc-1", "AvailabilityZone": "ap-south-1a"}]}

        def describe_security_groups(self, GroupIds):
            return {"SecurityGroups": [{"GroupId": GroupIds[0], "VpcId": "vpc-1"}]}

        def describe_instance_type_offerings(self, **kwargs):
            return {"InstanceTypeOfferings": []}

    monkeypatch.setattr(service.boto3, "client", lambda *args, **kwargs: FakeEC2())
    with pytest.raises(ValueError, match="Availability Zone ap-south-1a"):
        service._validate_aws_references(service._validate_payload({**VALID_PAYLOAD, "aws_connection_id": ""}))


def test_s3_backend_init_arguments_use_unique_state_key_and_native_lockfile(monkeypatch):
    import terraform_service as service
    monkeypatch.setattr(service, "TF_STATE_BUCKET", "iipp-central-state-test")
    monkeypatch.setattr(service, "TF_STATE_REGION", "ap-south-1")
    monkeypatch.setattr(service, "TF_STATE_PREFIX", "iipp/deployments")
    args = service._backend_init_args("cp-0123456789abcdef")
    assert "-backend-config=bucket=iipp-central-state-test" in args
    assert "-backend-config=key=iipp/deployments/cp-0123456789abcdef/terraform.tfstate" in args
    assert "-backend-config=use_lockfile=true" in args
    assert not any("access_key" in x.lower() or "secret_key" in x.lower() for x in args)


def test_policy_engine_denies_unapproved_instance_family_and_allows_limited_poc():
    from policy_engine import evaluate_aws_provision_request
    from terraform_service import _validate_payload
    payload = _validate_payload(VALID_PAYLOAD)
    runtime = {"account_id": "123456789012", "connection_name": "AWS-POC", "target_role_arn": "arn:aws:iam::123456789012:role/CloudPilotTerraformExecutionRole"}
    result = evaluate_aws_provision_request(payload, runtime)
    assert result["decision"] == "ALLOW_TO_PLAN"
    payload["instance_type"] = "m7i.large"
    with pytest.raises(ValueError, match="Policy denied instance family"):
        evaluate_aws_provision_request(payload, runtime)
