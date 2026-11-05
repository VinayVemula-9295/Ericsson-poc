import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("terraform_setup", ROOT / "scripts/configure_iipp_terraform.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_controller_covers_both_runners_without_state_delete(tmp_path):
    path = tmp_path / "policy.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate_iipp_controller_policy.py"),
                    "--bucket", "test-state", "--controller-account-id", "704298683641",
                    "--poc-account-id", "333347372766", "--test-account-id", "695729002971",
                    "--output", str(path)], check=True, capture_output=True)
    statements = json.loads(path.read_text())["Statement"]
    for prefix in ("iipp/deployments", "iipp/poc"):
        assert any(prefix + "/*" in x.get("Condition", {}).get("StringLike", {}).get("s3:prefix", []) for x in statements)
        assert any("arn:aws:s3:::test-state/" + prefix + "/*" in x.get("Resource", []) for x in statements)
    location = next(x for x in statements if x["Sid"] == "IIPPStateBucketLocation")
    assert "Condition" not in location
    deletion = [x for x in statements if "s3:DeleteObject" in x["Action"]]
    assert all(r.endswith("*.tflock") for x in deletion for r in x["Resource"])


def test_launch_conditions_do_not_block_referenced_resources():
    policy = setup.execution_policy("333347372766", "ap-south-1", "poc", "key")
    statements = {x["Sid"]: x for x in policy["Statement"]}
    instance = statements["LaunchInstances"]
    assert instance["Condition"]["StringLike"]["ec2:InstanceType"] == "t3.*"
    assert instance["Condition"]["StringEquals"]["aws:RequestTag/Environment"] == "poc"
    dependencies = statements["LaunchDependencies"]
    assert "arn:aws:ec2:ap-south-1::image/*" in dependencies["Resource"]
    assert dependencies["Condition"] == {"StringEquals": {"aws:RequestedRegion": "ap-south-1"}}
    assert all("s3:" not in str(x["Action"]) for x in policy["Statement"])


def test_eks_role_passing_and_grants_are_scoped():
    policy = setup.execution_policy("695729002971", "ap-south-1", "test", "specific-key")
    statements = {x["Sid"]: x for x in policy["Statement"]}
    for sid in ("PassClusterRole", "PassNodeRole"):
        assert statements[sid]["Resource"].startswith("arn:aws:iam::695729002971:role/IIPPEKS")
        assert statements[sid]["Condition"]["StringEquals"]["iam:PassedToService"] == "eks.amazonaws.com"
    assert statements["EKSKeyGrants"]["Resource"] == "specific-key"
    # AWS EKS explicitly does not support this grant condition during CreateCluster.
    assert "Condition" not in statements["EKSKeyGrants"]
    assert statements["CreateCluster"]["Resource"] == "*"
    assert statements["CreateCluster"]["Condition"]["StringEquals"]["aws:RequestTag/Environment"] == "test"


def test_offline_generation_requires_matching_key_accounts(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/configure_iipp_terraform.py"),
                             "--poc-key-arn", "arn:aws:kms:ap-south-1:695729002971:key/wrong",
                             "--test-key-arn", "arn:aws:kms:ap-south-1:695729002971:key/test",
                             "--output-dir", str(tmp_path / "output")], capture_output=True)
    assert result.returncode != 0
    assert not (tmp_path / "output").exists()


def session_factory(identities, calls):
    def factory(**kwargs):
        calls.append(kwargs)
        account = identities[kwargs["profile_name"]]
        def client(name):
            assert name == "sts"
            return SimpleNamespace(get_caller_identity=lambda: {
                "Account": account, "Arn": f"arn:aws:sts::{account}:assumed-role/Administrator/session"})
        return SimpleNamespace(client=client)
    return factory


def test_direct_profiles_verify_all_accounts_without_assuming_organization_role():
    calls = []
    factory = session_factory({"management": "704298683641", "demo": "333347372766", "test": "695729002971"}, calls)
    source, targets = setup.admin_sessions("ap-south-1", "management", "demo", "test", factory)
    assert len(targets) == 2
    assert [c["profile_name"] for c in calls] == ["management", "demo", "test"]
    assert all(c["region_name"] == "ap-south-1" for c in calls)


@pytest.mark.parametrize("wrong", ["management", "demo", "test"])
def test_wrong_account_profiles_fail_before_setup(wrong):
    identities = {"management": "704298683641", "demo": "333347372766", "test": "695729002971"}
    identities[wrong] = "999999999999"
    with pytest.raises(ValueError, match="non-root administrator"):
        setup.admin_sessions("ap-south-1", "management", "demo", "test", session_factory(identities, []))


def test_root_is_rejected():
    session = SimpleNamespace(client=lambda name: SimpleNamespace(get_caller_identity=lambda: {
        "Account": "704298683641", "Arn": "arn:aws:iam::704298683641:root"}))
    with pytest.raises(ValueError, match="non-root"):
        setup.verify_identity(session, "704298683641")


def test_missing_or_expired_profile_never_falls_back():
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("SSO session expired")
    with pytest.raises(RuntimeError, match="expired"):
        setup.admin_sessions("ap-south-1", "management", "demo", "test", factory)
    assert calls == [dict(profile_name="management", region_name="ap-south-1")]


def test_partial_target_profiles_are_rejected_without_aws_calls():
    with pytest.raises(ValueError, match="both"):
        setup.admin_sessions("ap-south-1", "management", "demo", None, lambda **kw: pytest.fail("Unexpected AWS call"))


def test_check_access_cannot_execute(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/configure_iipp_terraform.py"),
                             "--check-access", "--execute", "--output-dir", str(tmp_path / "output")], capture_output=True, text=True)
    assert result.returncode != 0 and "cannot be combined" in result.stderr
    assert not (tmp_path / "output").exists()


def test_offline_generation_never_loads_named_profiles(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/configure_iipp_terraform.py"),
                             "--management-profile", "missing-management", "--poc-profile", "missing-demo", "--test-profile", "missing-test",
                             "--poc-key-arn", "arn:aws:kms:ap-south-1:333347372766:key/demo",
                             "--test-key-arn", "arn:aws:kms:ap-south-1:695729002971:key/test",
                             "--output-dir", str(tmp_path / "output")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "output").glob("*-execution-policy.json"))) == 2


def test_missing_profile_reports_setup_command():
    from botocore.exceptions import ProfileNotFound
    def factory(**kwargs):
        raise ProfileNotFound(profile=kwargs["profile_name"])
    with pytest.raises(ValueError, match="aws configure sso --profile management"):
        setup.admin_sessions("ap-south-1", "management", "demo", "test", factory)


def test_expired_sso_reports_login_command():
    from botocore.exceptions import UnauthorizedSSOTokenError
    def factory(**kwargs):
        raise UnauthorizedSSOTokenError()
    with pytest.raises(ValueError, match="aws sso login --profile management"):
        setup.admin_sessions("ap-south-1", "management", "demo", "test", factory)


def load_state_setup():
    spec = importlib.util.spec_from_file_location("state_setup", ROOT / "scripts/configure_iipp_state.py")
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    return state


def test_state_setup_rejects_kms_before_any_cloud_write(monkeypatch, tmp_path):
    import boto3
    state = load_state_setup()
    policy_path = tmp_path / "policy.json"
    policy_path.write_text('{}')
    monkeypatch.setattr(sys, "argv", ["setup", "--execute", "--management-profile", "management",
                                    "--controller-policy", str(policy_path), "--backup-dir", str(tmp_path / "backup")])
    class S3:
        def head_bucket(self, **kwargs):
            pass
        def get_bucket_policy(self, **kwargs):
            return {"Policy": json.dumps({"Version": "2012-10-17", "Statement": []})}
        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Enabled"}
        def get_bucket_encryption(self, **kwargs):
            return {"ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}}]}}
        def get_public_access_block(self, **kwargs):
            return {}
        def __getattr__(self, name):
            pytest.fail("Unexpected S3 mutation: " + name)
    clients = {
        "sts": SimpleNamespace(get_caller_identity=lambda: {"Account": state.ACCOUNT, "Arn": f"arn:aws:sts::{state.ACCOUNT}:assumed-role/Admin/session"}),
        "s3": S3(), "iam": SimpleNamespace(get_role_policy=lambda **kwargs: {"PolicyDocument": {}})}
    def factory(**kwargs):
        assert kwargs["profile_name"] == "management"
        return SimpleNamespace(client=lambda name: clients[name])
    monkeypatch.setattr(boto3, "Session", factory)
    with pytest.raises(ValueError, match="no settings changed"):
        state.main()
    assert (tmp_path / "backup" / "previous.json").is_file()


def test_state_setup_rejects_root_before_accessing_bucket(monkeypatch, tmp_path):
    import boto3
    state = load_state_setup()
    path = tmp_path / "policy.json"
    path.write_text('{}')
    monkeypatch.setattr(sys, "argv", ["setup", "--execute", "--controller-policy", str(path), "--backup-dir", str(tmp_path / "backup")])
    def client(name):
        assert name == "sts", "No bucket or IAM access is allowed before identity validation"
        return SimpleNamespace(get_caller_identity=lambda: {"Account": state.ACCOUNT, "Arn": f"arn:aws:iam::{state.ACCOUNT}:root"})
    monkeypatch.setattr(boto3, "Session", lambda **kwargs: SimpleNamespace(client=client))
    with pytest.raises(ValueError, match="Non-root"):
        state.main()
    assert not (tmp_path / "backup").exists()
