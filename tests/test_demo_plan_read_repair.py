"""The targeted Demo repair cannot change trust, roles or unrelated permissions."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("demo_read_repair", Path(__file__).resolve().parents[1] / "scripts/repair_demo_plan_reads.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


@pytest.fixture
def account():
    class Missing(Exception):
        pass
    state = dict(writes=[], policy=None)
    def get_policy(**kwargs):
        if state["policy"] is None:
            raise Missing()
        return {"PolicyDocument": state["policy"]}
    def put_policy(**kwargs):
        state["writes"].append(kwargs)
        state["policy"] = json.loads(kwargs["PolicyDocument"])
    iam = SimpleNamespace(exceptions=SimpleNamespace(NoSuchEntityException=Missing),
        get_role=lambda **kw: {"Role": {"Arn": f"arn:aws:iam::{repair.ACCOUNT}:role/{repair.ROLE}"}},
        get_role_policy=get_policy, put_role_policy=put_policy)
    identity = dict(Account=repair.ACCOUNT, Arn=f"arn:aws:iam::{repair.ACCOUNT}:user/admin")
    session = SimpleNamespace(client=lambda name: iam if name == "iam" else SimpleNamespace(get_caller_identity=lambda: identity))
    return session, state, identity


def test_only_required_discovery_reads_are_added():
    statements = repair.discovery_policy()["Statement"]
    actions = set()
    for statement in statements:
        value = statement["Action"]
        actions.update([value] if isinstance(value, str) else value)
    assert actions == {"iam:ListRoles", "eks:DescribeClusterVersions", "kms:ListKeys", "kms:DescribeKey"}
    assert statements[-1]["Resource"] == f"arn:aws:kms:ap-south-1:{repair.ACCOUNT}:key/*"


def test_preview_makes_no_writes(account):
    session, state, _ = account
    result = repair.repair(session, "ap-south-1")
    assert result["changed"] and not result["executed"]
    assert state["writes"] == []


def test_repair_preserves_existing_statements_and_backs_up_before_write(account, tmp_path):
    session, state, _ = account
    old = {"Version": "2012-10-17", "Statement": [{"Sid": "ListClusterAndWorkerRoles", "Effect": "Allow", "Action": "ec2:DescribeInstances", "Resource": "*"}]}
    state["policy"] = old
    result = repair.repair(session, "ap-south-1", True, tmp_path / "backup")
    assert result["executed"]
    assert old["Statement"][0] in state["policy"]["Statement"]
    assert json.loads((tmp_path / "backup/previous-policy.json").read_text())["previous_policy"] == old
    assert len({s["Sid"] for s in state["policy"]["Statement"]}) == len(state["policy"]["Statement"])
    assert not repair.repair(session, "ap-south-1", True, tmp_path / "unused")["changed"]
    assert len(state["writes"]) == 1


@pytest.mark.parametrize("kind", ["root", "wrong_account"])
def test_identity_is_checked_before_iam_writes(account, tmp_path, kind):
    session, state, identity = account
    if kind == "root":
        identity["Arn"] = f"arn:aws:iam::{repair.ACCOUNT}:root"
    else:
        identity["Account"] = "695729002971"
    with pytest.raises(ValueError, match="Non-root Demo"):
        repair.repair(session, "ap-south-1", True, tmp_path / "backup")
    assert not state["writes"]


def test_no_iam_write_if_backup_cannot_be_created(account, tmp_path):
    session, state, _ = account
    with pytest.raises(FileExistsError):
        repair.repair(session, "ap-south-1", True, tmp_path)
    assert state["writes"] == []
