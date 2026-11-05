from agent_flow_ui import tier_choices


def test_worker_prompt_preserves_count_and_per_worker_requirements():
    from infrastructure_request import parse_infrastructure_request
    from placement import explicit_cluster
    request = parse_infrastructure_request("2 worker nodes with 2 vCPU and 1 GiB RAM")
    assert request["kind"] == "Kubernetes"
    assert (request["worker_count"], request["min_vcpus"], request["min_memory_gib"]) == (2, 2, 1)
    assert explicit_cluster(dict(monthly_compute_estimate=10), 2)["worker_monthly_usd"] == 20


def test_aws_cards_choose_exact_match_permitted_by_execution_policy(monkeypatch):
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3")
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1")
    base = dict(provider="AWS", region="ap-south-1", available=True, offered_in_target_location=True,
                vcpus=2, memory_gib=1, os="Linux", price_currency="USD")
    rows = [dict(base, sku="t3a.micro", monthly_compute_estimate=4),
            dict(base, sku="t3.micro", monthly_compute_estimate=5),
            dict(base, sku="t3.small", memory_gib=2, monthly_compute_estimate=10)]
    request = dict(min_vcpus=2, min_memory_gib=1, strategy="Lowest Cost")
    choices = dict(tier_choices(rows, request))
    assert choices["Selectable"]["sku"] == "t3.micro"
    assert choices["Recommended"]["sku"] == "t3.small"
    assert choices["Optimal"]["sku"] == "t3.small"
    assert choices["Optimal"]["shared_configuration"]
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3,t3a")
    assert dict(tier_choices(rows, request))["Selectable"]["sku"] == "t3a.micro"
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "us-east-1")
    assert all(row is None for _, row in tier_choices(rows, request))
