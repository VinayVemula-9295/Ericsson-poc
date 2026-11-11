import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("review_iam", SCRIPTS / "review_iipp_iam.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_plan_role_reads_are_scoped_to_target_account():
    policy = review.inventory_policy("333347372766")
    reads = next(s for s in policy["Statement"] if s["Sid"] == "ValidateAccountPlanRoles")
    assert reads == dict(Sid="ValidateAccountPlanRoles", Effect="Allow", Action="iam:GetRole",
                         Resource="arn:aws:iam::333347372766:role/*")
    with pytest.raises(ValueError):
        review.inventory_policy("*")


def test_bootstrap_preserves_external_id_and_rejects_broad_trust():
    controller = "arn:aws:iam::704298683641:role/CloudPilotReadTargetMetricsRole"
    trust = {"Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"AWS": controller},
                             "Condition": {"StringEquals": {"sts:ExternalId": "test-only-external-id"}}}]}
    snapshot = {"role": {"AssumeRolePolicyDocument": trust}}
    assert review.connection_trust(snapshot, controller) is trust
    trust["Statement"][0]["Principal"] = {"AWS": "*"}
    with pytest.raises(ValueError, match="restrict"):
        review.connection_trust(snapshot, controller)


def test_server_only_never_installs_placeholder_key_or_eks_writes():
    full = review.execution_policy("695729002971", "ap-south-1", "test", "placeholder-key")
    policy = review.server_policy(full)
    assert "placeholder-key" not in str(policy)
    assert "eks:CreateCluster" not in str(policy)
    assert "ec2:TerminateInstances" in str(policy)
    assert "aws:ResourceTag/Environment" in str(policy)


def test_new_execution_trust_uses_saved_id_without_changing_inventory():
    controller = "arn:aws:iam::704298683641:role/CloudPilotReadTargetMetricsRole"
    trust = {"Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"AWS": controller}}]}
    snapshot = {"role": {"AssumeRolePolicyDocument": trust}}
    result = review.connection_trust(snapshot, controller, "test-only-saved-id")
    assert result["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == "test-only-saved-id"
    assert "Condition" not in trust["Statement"][0]
    with pytest.raises(ValueError, match="External ID"):
        review.connection_trust(snapshot, controller)


def test_instance_profile_role_passing_is_account_scoped():
    full = review.execution_policy("695729002971", "ap-south-1", "test", "key",
                                   "arn:aws:iam::695729002971:role/approved-server-role")
    statements = {s["Sid"]: s for s in full["Statement"]}
    assert statements["ReadInstanceProfiles"]["Resource"] == "arn:aws:iam::695729002971:instance-profile/*"
    assert statements["PassInstanceRole"]["Resource"].endswith("/approved-server-role")


def test_snapshot_includes_managed_versions_and_boundary():
    from configure_iipp_terraform import role_snapshot
    class IAM:
        def get_role(self, **kw):
            return {"Role": {"PermissionsBoundary": {"PermissionsBoundaryArn": "boundary"}}}
        def get_paginator(self, name):
            rows = [{"PolicyNames": ["inline"]}] if name == "list_role_policies" else [
                {"AttachedPolicies": [{"PolicyArn": "managed"}]}]
            return SimpleNamespace(paginate=lambda **kw: rows)
        def get_role_policy(self, **kw):
            return {"PolicyDocument": {"Statement": []}}
        def get_policy(self, **kw):
            return {"Policy": {"DefaultVersionId": "v2"}}
        def get_policy_version(self, **kw):
            assert kw["VersionId"] == "v2"
            return {"PolicyVersion": {"Document": {"Statement": []}}}
    snapshot = role_snapshot(IAM(), "role")
    assert set(snapshot["managed_documents"]) == {"managed", "boundary"}
    assert "inline" in snapshot["inline"]
