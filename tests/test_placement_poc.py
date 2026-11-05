import io
import json
import zipfile
from datetime import datetime, timezone, timedelta
import pytest
from placement import validate_latency, evaluate, size_cluster, apply_constraints
from poc_terraform import configuration, package
from infrastructure_request import parse_infrastructure_request

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)

def probe(**kw):
    return dict(origin="Mumbai", provider="AWS", region="ap-south-1", metric="p95", method="https", window="5m", measured_at=NOW.isoformat(), value_ms=12, **kw)

def test_latency_must_be_fresh_comparable_and_finite():
    candidate = dict(provider="AWS", region="ap-south-1", os="Windows")
    request = dict(os="Windows", latency_target_ms=20, latency_origin="Mumbai")
    assert evaluate(candidate, request, [probe()], NOW)["status"] == "pass"
    assert evaluate(candidate, request, [probe()], NOW + timedelta(minutes=16))["status"] == "unknown"
    assert evaluate(candidate, dict(request, latency_origin="Delhi"), [probe()], NOW)["status"] == "unknown"
    bad = probe()
    bad["value_ms"] = float("nan")
    with pytest.raises(ValueError):
        validate_latency([bad], NOW)

def test_policy_missing_security_and_residency_fail_closed():
    candidate = dict(provider="Azure", region="centralindia", os="Linux", available=True)
    assert evaluate(candidate, dict(os="Linux", residency_regions=["ap-south-1"]))["status"] == "fail"
    row = apply_constraints([candidate], dict(os="Linux", encryption_verified=True))[0]
    assert row["available"] is False
    assert row["policy_result"]["status"] == "unknown"

def test_old_or_unproven_latency_is_not_reused_for_ranking():
    candidate = dict(provider="AWS", region="ap-south-1", os="Linux", measured_latency_ms=1, latency_ms=1)
    evaluate(candidate, dict(os="Linux"))
    assert "measured_latency_ms" not in candidate
    assert "latency_ms" not in candidate

def test_cluster_packing_accounts_for_overhead_and_failure():
    result = size_cluster(dict(vcpus=4, memory_gib=8, monthly_compute_estimate=100), 1, 2, 7)
    assert result["pods_per_node"] == 3
    assert result["node_count"] == 4
    assert result["worker_monthly_usd"] == 400
    assert result["total_monthly_usd"] is None
    with pytest.raises(ValueError):
        size_cluster(dict(vcpus=1, memory_gib=1), 2, 2, 1)

@pytest.mark.parametrize("provider,sku", [("AWS", "t3.large"), ("Azure", "Standard_D2s_v5"), ("GCP", "n2-standard-2")])
@pytest.mark.parametrize("os_name,kind", [("Linux", "VM"), ("Windows", "VM"), ("Linux", "Kubernetes")])
def test_packages_have_pinned_providers_remote_state_and_no_credentials(provider, sku, os_name, kind):
    candidate = dict(provider=provider, region="test-region", sku=sku, os=os_name, cluster={"node_count": 3})
    config = configuration(candidate, kind)
    assert config["terraform"]["backend"] == {"s3": {}}
    assert all(p["version"].startswith("= ") for p in config["terraform"]["required_providers"].values())
    with zipfile.ZipFile(io.BytesIO(package(candidate, {}, kind))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["status"] == "CONFIGURATION_ONLY"
        assert manifest["live_execution_validated"] is False
        assert "terraform.tfvars.json" not in archive.namelist()
    if provider == "Azure" and os_name == "Windows":
        assert config["variable"]["admin_password"]["sensitive"] is True
        assert "default" not in config["variable"]["admin_password"]

def test_windows_kubernetes_is_rejected():
    with pytest.raises(ValueError):
        configuration(dict(provider="AWS", region="ap-south-1", os="Windows", sku="t3.large"), "Kubernetes")

def test_prompt_extracts_constraints_without_inventing_country_mapping():
    request = parse_infrastructure_request("Windows 4 vCPU 16 GiB RAM under 100 USD, latency 20ms, must stay only in India")
    assert request["os"] == "Windows"
    assert request["latency_target_ms"] == 20
    assert request["requires_residency_confirmation"] is True
    assert request["residency_regions"] == []

def test_aws_windows_pricing_cannot_accept_linux_products():
    from aws_data import _parse_ondemand_hourly_products
    product = {"product": {"attributes": {"operatingSystem": "Linux"}}, "terms": {"OnDemand": {"a": {"priceDimensions": {"a": {"unit": "Hrs", "pricePerUnit": {"USD": "0.1"}}}}}}}
    assert _parse_ondemand_hourly_products([product], "ap-south-1", "t3.large", None, "Windows") is None
    product["product"]["attributes"].update(operatingSystem="Windows", licenseModel="License included")
    assert _parse_ondemand_hourly_products([product], "ap-south-1", "t3.large", None, "Windows")["hourly_usd"] == .1
