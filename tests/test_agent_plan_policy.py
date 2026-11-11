from agent_flow_ui import tier_choices


def test_worker_prompt_preserves_count_and_per_worker_requirements():
    from infrastructure_request import parse_infrastructure_request
    from placement import explicit_cluster
    request = parse_infrastructure_request("2 worker nodes with 2 vCPU and 1 GiB RAM")
    assert request["kind"] == "Kubernetes"
    assert (request["worker_count"], request["min_vcpus"], request["min_memory_gib"]) == (2, 2, 1)
    assert explicit_cluster(dict(monthly_compute_estimate=10), 2)["worker_monthly_usd"] == 20


def test_aws_comparison_is_broader_than_execution_policy(monkeypatch):
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3")
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1")
    base = dict(provider="AWS", region="ap-south-1", available=True, offered_in_target_location=True,
                vcpus=2, memory_gib=1, os="Linux", price_currency="USD")
    rows = [dict(base, sku="t3a.micro", monthly_compute_estimate=4),
            dict(base, sku="t3.micro", monthly_compute_estimate=5),
            dict(base, sku="t3.small", memory_gib=2, monthly_compute_estimate=10)]
    request = dict(min_vcpus=2, min_memory_gib=1, strategy="Lowest Cost")
    choices = dict(tier_choices(rows, request))
    assert choices["ASK"]["sku"] == "t3a.micro"
    assert choices["Recommended"]["sku"] == "t3.small"
    assert choices["Optimal"] is None
    rows.append(dict(base, sku="t3.medium", memory_gib=4, monthly_compute_estimate=20))
    assert dict(tier_choices(rows, request))["Optimal"]["sku"] == "t3.medium"
    request["max_monthly_budget_usd"] = 15
    assert dict(tier_choices(rows, request))["Optimal"] is None
    request.pop("max_monthly_budget_usd")
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3,t3a")
    assert dict(tier_choices(rows, request))["ASK"]["sku"] == "t3a.micro"
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "us-east-1")
    assert dict(tier_choices(rows, request))["ASK"]["sku"] == "t3a.micro"
    from agent_flow_ui import execution_restriction
    assert "region" in execution_restriction(rows[0])
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1")
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3")
    assert "family" in execution_restriction(rows[0])
    assert execution_restriction(rows[1]) is None


def test_single_profile_does_not_fill_upgrade_cards():
    row = dict(provider="AWS", sku="t3.small", region="ap-south-1", vcpus=2, memory_gib=2,
               available=True, offered_in_target_location=True, monthly_compute_estimate=10)
    choices = dict(tier_choices([row], dict(min_vcpus=2, min_memory_gib=2, strategy="Balanced")))
    assert choices["ASK"]["sku"] == "t3.small"
    assert choices["Recommended"] is choices["Optimal"] is None


def test_explicit_all_families_allows_server_and_worker_choices_but_preserves_region(monkeypatch):
    import pytest
    from policy_engine import validate_aws_launch, evaluate_aws_provision_request
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "*")
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1")
    runtime = dict(connection_name="Demo-Account", account_id="333347372766", target_role_arn="verified-role")
    for sku in ("t3a.micro", "t3a.small", "c4.large", "m7i.large"):
        validate_aws_launch("ap-south-1", sku)
        assert evaluate_aws_provision_request(dict(region="ap-south-1", instance_type=sku, environment="poc", ttl_days=2), runtime)["decision"] == "ALLOW_TO_PLAN"
    with pytest.raises(ValueError, match="region"):
        validate_aws_launch("us-east-1", "t3a.micro")
    with pytest.raises(ValueError, match="cross-environment"):
        evaluate_aws_provision_request(dict(region="ap-south-1", instance_type="t3a.micro", environment="test", ttl_days=2), runtime)
