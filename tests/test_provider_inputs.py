"""Live input discovery contracts and progressive sizing; no cloud calls."""
import sys
from types import SimpleNamespace

import pytest

import provider_inputs as live
from recommendation_engine import progressive_choices, shortlist_with_upgrades


def row(sku, cpu, memory, price=50, **kwargs):
    return dict(provider="AWS", region="ap-south-1", sku=sku, vcpus=cpu, memory_gib=memory,
                monthly_compute_estimate=price, available=True, os="Linux", offered_in_target_location=True, **kwargs)


def test_tiers_are_monotonic_nearest_upgrades_and_respect_budget_and_evidence():
    options = [row("exact", 2, 2), row("same-hardware", 2, 2, 60), row("recommended", 2, 4, 70),
               row("optimal", 4, 4, 90), row("less-memory", 8, 2, 55), row("huge", 64, 128, 300),
               row("undersized", 1, 2, 10)]
    assert [r["sku"] for _, r in progressive_choices(options, 2, 2)] == ["exact", "recommended", "optimal"]
    assert [r and r["sku"] for _, r in progressive_choices(options, 2, 2, budget=80)] == ["exact", "recommended", None]
    options[2]["available"] = False
    choices = progressive_choices(options, 2, 2)
    assert choices[1][1]["sku"] == "optimal"


def test_tiers_use_nearby_meeting_minimums_and_never_repeat_single_profile():
    assert [r and r["sku"] for _, r in progressive_choices([row("nearest", 2, 4)], 2, 3)] == ["nearest", None, None]
    assert all(r is None for _, r in progressive_choices([row("below", 1, 2)], 2, 3))


def test_many_exact_profiles_do_not_hide_upgrade_profiles():
    rows = [row(str(i), 2, 2) for i in range(20)] + [row("up1", 2, 4), row("up2", 4, 4)]
    selected = shortlist_with_upgrades(rows, 2, 2, 6)
    assert len(selected) == 22
    assert [r["sku"] for _, r in progressive_choices(selected, 2, 2)] == ["0", "up1", "up2"]


@pytest.fixture
def aws(monkeypatch):
    calls = []
    subnets = [dict(SubnetId="s1", AvailabilityZone="ap-south-1a", VpcId="v1", State="available"),
               dict(SubnetId="s2", AvailabilityZone="ap-south-1b", VpcId="v1", State="available"),
               dict(SubnetId="s3", AvailabilityZone="ap-south-1c", VpcId="v2", State="available")]
    roles = [dict(Arn="cluster-role", RoleName="cluster", AssumeRolePolicyDocument={"Statement": [
        {"Effect": "Allow", "Principal": {"Service": "eks.amazonaws.com"}}]}),
        dict(Arn="node-role", RoleName="node", AssumeRolePolicyDocument={"Statement": [
            {"Effect": "Allow", "Principal": {"Service": ["ec2.amazonaws.com"]}}]})]
    def versions(**kwargs):
        calls.append(kwargs)
        return {"clusterVersions": [{"clusterVersion": "1.34", "versionStatus": "STANDARD_SUPPORT"}], "nextToken": "next"} if not kwargs.get("nextToken") else {"clusterVersions": [{"clusterVersion": "1.35", "versionStatus": "STANDARD_SUPPORT"}]}
    clients = {
        "ec2": SimpleNamespace(describe_subnets=lambda **kw: {"Subnets": subnets},
                               describe_security_groups=lambda **kw: {"SecurityGroups": [dict(GroupId="sg1", GroupName="web", VpcId="v1"), dict(GroupId="sg2", GroupName="other", VpcId="v2")]}),
        "eks": SimpleNamespace(describe_cluster_versions=versions),
        "iam": SimpleNamespace(list_roles=lambda **kw: {"Roles": roles}),
        "kms": SimpleNamespace(list_keys=lambda **kw: {"Keys": [{"KeyId": "key"}]},
                               describe_key=lambda **kw: {"KeyMetadata": dict(Arn="kms-key", KeyState="Enabled", KeyUsage="ENCRYPT_DECRYPT", KeyManager="CUSTOMER", KeySpec="SYMMETRIC_DEFAULT")})}
    monkeypatch.setattr(live, "get_connection", lambda cid: dict(provider="AWS", connection_status="CONNECTED"))
    monkeypatch.setattr(live, "get_aws_session", lambda cid, purpose: SimpleNamespace(client=lambda service, **kw: clients[service]))
    return clients, calls


def test_eks_live_versions_roles_keys_and_dependent_subnets(aws):
    _, calls = aws
    evidence = live.discover("aws-1", "ap-south-1", "Kubernetes")
    assert not evidence["errors"]
    assert [v["value"] for v in evidence["choices"]["kubernetes_version"]] == ["1.34", "1.35"]
    assert calls == [dict(includeAll=False), dict(includeAll=False, nextToken="next")]
    assert evidence["choices"]["cluster_role_arn"][0]["value"] == "cluster-role"
    assert evidence["choices"]["node_role_arn"][0]["value"] == "node-role"
    assert [s["value"] for s in live.compatible_choices("second_subnet_id", evidence, {"subnet_id": "s1"})] == ["s2"]
    assert "image_id" not in evidence["choices"]


def test_vm_images_and_security_groups_use_selected_account(aws, monkeypatch):
    import poc_catalog
    calls = []
    monkeypatch.setattr(poc_catalog, "aws_images", lambda *args: calls.append(args) or [{"id": "ami-live", "name": "Windows_Server-live"}])
    evidence = live.discover("aws-2", "ap-south-1", "VM", "Windows")
    assert calls == [("aws-2", "ap-south-1", "Windows")]
    assert [s["value"] for s in live.compatible_choices("security_group_id", evidence, {"subnet_id": "s1"})] == ["sg1"]
    assert evidence["choices"]["image_id"][0]["value"] == "ami-live"


def test_failed_discovery_never_returns_old_or_sample_versions(aws):
    clients, _ = aws
    def denied(**kw):
        raise RuntimeError("secret must not reach UI")
    clients["eks"].describe_cluster_versions = denied
    evidence = live.discover("aws-1", "ap-south-1", "Kubernetes")
    assert "kubernetes_version" not in evidence["choices"]
    assert "kubernetes_version" in evidence["errors"]
    assert "secret" not in str(evidence)
    assert evidence["choices"]["subnet_id"]


def test_access_denial_identifies_permission_and_api_without_raw_message(aws):
    from botocore.exceptions import ClientError
    clients, _ = aws
    def denied(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private credential details"}}, "DescribeClusterVersions")
    clients["eks"].describe_cluster_versions = denied
    evidence = live.discover("aws-1", "ap-south-1", "Kubernetes")
    assert "eks:DescribeClusterVersions" in evidence["errors"]["kubernetes_version"]
    assert "private credential" not in str(evidence)
    assert evidence["diagnostics"][0]["operation"] == "DescribeClusterVersions"


def test_one_inaccessible_key_does_not_hide_usable_keys(aws):
    clients, _ = aws
    clients["kms"].list_keys = lambda **kwargs: {"Keys": [{"KeyId": "denied"}, {"KeyId": "allowed"}]}
    original = clients["kms"].describe_key
    def describe(**kwargs):
        if kwargs["KeyId"] == "denied":
            raise RuntimeError("secret")
        return original(**kwargs)
    clients["kms"].describe_key = describe
    evidence = live.discover("aws-1", "ap-south-1", "Kubernetes")
    assert evidence["choices"]["kms_key_arn"][0]["value"] == "kms-key"
    assert "kms:DescribeKey" in evidence["errors"]["kms_key_arn"]
    assert "secret" not in str(evidence)


def test_aks_reads_subscription_versions_values_not_arm_value(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.identity", SimpleNamespace(ClientSecretCredential=lambda *a: SimpleNamespace(
        get_token=lambda *a: SimpleNamespace(token="ephemeral"))))
    monkeypatch.setattr(live, "get_connection", lambda cid: dict(provider="Azure", connection_status="CONNECTED", metadata=dict(tenant_id="t", client_id="c", subscription_id="sub")))
    monkeypatch.setattr(live, "get_saved_secret", lambda cid: {"client_secret": "ephemeral"})
    urls = []
    def get(url, token, host):
        urls.append(url)
        if "kubernetesVersions" in url:
            return {"values": [dict(version="1.34", patchVersions={"1.34.2": {}}), dict(version="1.35", isPreview=True, patchVersions={"1.35.0": {}})]}
        return {"value": []}
    monkeypatch.setattr(live, "_json", get)
    evidence = live.discover("azure-1", "centralindia", "Kubernetes")
    assert not evidence["errors"]
    assert evidence["choices"]["kubernetes_version"] == [live.choice("1.34.2")]
    assert all("/subscriptions/sub/" in url for url in urls)


def test_gke_fetches_project_versions_networks_and_service_accounts(monkeypatch):
    monkeypatch.setattr(live, "get_connection", lambda cid: dict(provider="GCP", connection_status="CONNECTED", metadata=dict(project_id="project-live")))
    monkeypatch.setattr(live, "get_saved_secret", lambda cid: {"service_account_json": {}})
    credentials = SimpleNamespace(refresh=lambda request: None)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", SimpleNamespace(Credentials=SimpleNamespace(from_service_account_info=lambda *a, **kw: credentials)))
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        data = {"items": []}
        if "serverConfig" in url:
            data = dict(validMasterVersions=["1.34.2-gke.1", "1.35.0-gke.1"], validNodeVersions=["1.34.2-gke.1"])
        elif "subnetworks" in url:
            data = {"items": [dict(selfLink="subnet", name="private", network="network", secondaryIpRanges=[dict(rangeName="pods"), dict(rangeName="services")])]}
        elif "serviceAccounts" in url:
            data = {"accounts": [dict(email="node@example.invalid"), dict(email="disabled@example.invalid", disabled=True)]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", SimpleNamespace(Request=lambda: None, AuthorizedSession=lambda cred: SimpleNamespace(get=get)))
    evidence = live.discover("gcp-1", "asia-south1", "Kubernetes")
    assert not evidence["errors"]
    assert evidence["choices"]["kubernetes_version"] == [live.choice("1.34.2-gke.1")]
    assert evidence["choices"]["node_service_account"] == [live.choice("node@example.invalid")]
    assert [r["value"] for r in live.compatible_choices("service_range_name", evidence, {"subnet_id": "subnet", "pod_range_name": "pods"})] == ["services"]
    assert all("projects/project-live/" in url for url in urls)


def test_plan_rechecks_hardware_and_rejects_wrong_eks_network(aws, monkeypatch):
    clients, _ = aws
    clients["ec2"].describe_instance_types = lambda **kw: {"InstanceTypes": [dict(
        InstanceType="worker", VCpuInfo={"DefaultVCpus": 2}, MemoryInfo={"SizeInMiB": 8192},
        ProcessorInfo={"SupportedArchitectures": ["x86_64"]}, SupportedRootDeviceTypes=["ebs"],
        SupportedVirtualizationTypes=["hvm"], SupportedUsageClasses=["on-demand"])]}
    clients["ec2"].describe_instance_type_offerings = lambda **kw: {"InstanceTypeOfferings": [{"InstanceType": "worker"}]}
    candidate = row("worker", 2, 8, connection_id="aws-1")
    inputs = dict(subnet_id="s1", second_subnet_id="s2", kubernetes_version="1.34", cluster_role_arn="cluster-role", node_role_arn="node-role", kms_key_arn="kms-key")
    assert live.validate_live_inputs(candidate, {"kind": "Kubernetes"}, inputs)["status"] == "verified"
    with pytest.raises(ValueError, match="second subnet"):
        live.validate_live_inputs(candidate, {"kind": "Kubernetes"}, dict(inputs, second_subnet_id="s3"))
    with pytest.raises(ValueError, match="hardware changed"):
        live.validate_live_inputs(dict(candidate, vcpus=4), {"kind": "Kubernetes"}, inputs)


def test_kubernetes_prompt_carries_sizing_without_advanced_fields():
    from infrastructure_request import parse_infrastructure_request
    request = parse_infrastructure_request("EKS 4 vCPU 16 GiB RAM nodes, 7 replicas, 0.5 CPU per pod, 2 GiB per pod")
    assert (request["min_vcpus"], request["min_memory_gib"]) == (4, 16)
    assert (request["replicas"], request["pod_cpu"], request["pod_memory_gib"]) == (7, .5, 2)
