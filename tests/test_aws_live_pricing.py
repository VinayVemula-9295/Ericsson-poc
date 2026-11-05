import json

import aws_data


class FakePricingClient:
    def __init__(self, products):
        self.products = products
        self.calls = []

    def get_products(self, **kwargs):
        self.calls.append(kwargs)
        return {"PriceList": [json.dumps(item) for item in self.products]}


def _product(hourly="0.1110000000", region_code="ap-south-1", instance_type="m5a.xlarge"):
    return {
        "product": {
            "sku": "SKU-EC2-LIVE-001",
            "productFamily": "Compute Instance",
            "attributes": {
                "instanceType": instance_type,
                "regionCode": region_code,
                "location": "Asia Pacific (Mumbai)",
                "operatingSystem": "Linux",
                "tenancy": "Shared",
                "preInstalledSw": "NA",
                "licenseModel": "No License required",
                "capacitystatus": "Used",
            },
        },
        "terms": {
            "OnDemand": {
                "SKU-EC2-LIVE-001.JRTCKXETXF": {
                    "effectiveDate": "2026-11-01T00:00:00Z",
                    "priceDimensions": {
                        "SKU-EC2-LIVE-001.JRTCKXETXF.6YS6EN2CT7": {
                            "unit": "Hrs",
                            "beginRange": "0",
                            "endRange": "Inf",
                            "pricePerUnit": {"USD": hourly},
                        }
                    },
                }
            }
        },
        "publicationDate": "2026-11-03T00:00:00Z",
    }


def test_aws_price_list_queries_region_code_and_returns_actual_price_metadata(monkeypatch):
    fake = FakePricingClient([_product()])
    monkeypatch.setattr(aws_data, "_pricing_client", lambda: fake)
    monkeypatch.setenv("AWS_PRICING_API_REGION", "us-east-1")
    monkeypatch.setenv("CLOUDPILOT_PRICE_CACHE_TTL_SECONDS", "300")
    aws_data.clear_price_cache()

    result = aws_data.get_ec2_instance_on_demand_price(
        "m5a.xlarge", "Linux/UNIX", "default", "ap-south-1"
    )

    assert result["available"] is True
    assert result["hourly_usd"] == 0.111
    assert result["monthly_730h_usd"] == 81.03
    assert result["price_source"] == "AWS Price List Query API · AmazonEC2 GetProducts"
    assert result["effective_date"] == "2026-11-01T00:00:00Z"
    assert result["publication_date"] == "2026-11-03T00:00:00Z"
    assert result["cache_status"] == "MISS"
    assert any(
        {f["Field"]: f["Value"] for f in call["Filters"]}.get("regionCode") == "ap-south-1"
        for call in fake.calls
    )


def test_aws_price_list_cache_is_expiring_and_can_be_forced_fresh(monkeypatch):
    fake = FakePricingClient([_product()])
    monkeypatch.setattr(aws_data, "_pricing_client", lambda: fake)
    monkeypatch.setenv("CLOUDPILOT_PRICE_CACHE_TTL_SECONDS", "300")
    aws_data.clear_price_cache()

    first = aws_data.get_ec2_instance_on_demand_price("m5a.xlarge", region="ap-south-1")
    second = aws_data.get_ec2_instance_on_demand_price("m5a.xlarge", region="ap-south-1")
    assert first["cache_status"] == "MISS"
    assert second["cache_status"] == "HIT"
    assert len(fake.calls) == 1

    aws_data.clear_price_cache()
    third = aws_data.get_ec2_instance_on_demand_price("m5a.xlarge", region="ap-south-1")
    assert third["cache_status"] == "MISS"
    assert len(fake.calls) == 2


def test_aws_price_lookup_refuses_ambiguous_rates(monkeypatch):
    fake = FakePricingClient([_product("0.111"), _product("0.222")])
    monkeypatch.setattr(aws_data, "_pricing_client", lambda: fake)
    aws_data.clear_price_cache()

    result = aws_data.get_ec2_instance_on_demand_price("m5a.xlarge", region="ap-south-1")
    assert result["available"] is False
    assert "multiple distinct" in result["error"].lower()
    assert result["cache_status"] == "NOT_CACHED"


def test_aws_price_lookup_does_not_cache_failures(monkeypatch):
    class DeniedPricingClient:
        def __init__(self):
            self.calls = 0

        def get_products(self, **kwargs):
            self.calls += 1
            raise RuntimeError("AccessDenied: pricing:GetProducts")

    fake = DeniedPricingClient()
    monkeypatch.setattr(aws_data, "_pricing_client", lambda: fake)
    aws_data.clear_price_cache()

    first = aws_data.get_ec2_instance_on_demand_price("m5a.xlarge", region="ap-south-1")
    second = aws_data.get_ec2_instance_on_demand_price("m5a.xlarge", region="ap-south-1")
    assert first["available"] is False and second["available"] is False
    assert fake.calls >= 2


def test_controller_policy_generator_includes_price_list_read_permission():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "scripts" / "generate_iipp_controller_policy.py").read_text()
    assert '"pricing:GetProducts"' in source


def test_agent_comparison_forces_fresh_price_and_displays_price_provenance():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "agent_flow_ui.py").read_text()
    compare_flow = source.split("def collect", 1)[1].split("def ", 1)[0]
    assert "catalogue_options.clear()" in compare_flow
    assert "clear_price_cache()" in compare_flow
    assert "Price checked (UTC)" in source
    assert 'row.get("price_source")' in source


def test_agent_llm_receives_actual_aws_price_source_and_timestamp(monkeypatch):
    import agent

    captured = {}

    def fake_complete(messages, system, tools, **kwargs):
        captured["prompt"] = messages[0]["text"]
        return {
            "text": "**Recommended: AWS · m5a.xlarge**\n• Request checked.\n• Price fetched live.\n• Hardware fits.\n• Regional offer verified.\n• Quota remains unverified.\n• Latency is not measured.",
            "provider": "Amazon Bedrock", "model_id": "apac.amazon.nova-lite-v1:0",
        }

    monkeypatch.setattr(agent, "complete_turn", fake_complete)
    comparison = {
        "requirements": {"min_vcpus": 4, "min_memory_gib": 9},
        "strategy": "Lowest Cost",
        "recommended_provider": "AWS", "recommended_sku": "m5a.xlarge",
        "optimal_provider": "AWS", "optimal_sku": "m5a.xlarge",
        "optimal_monthly_compute_estimate": 81.03,
        "recommendation_reason": "Lowest live-priced eligible candidate",
        "latency_measured": False,
        "price_cache_ttl_seconds": 300,
        "source_options": [{
            "provider": "AWS", "region": "ap-south-1", "sku": "m5a.xlarge",
            "available": True, "vcpus": 4, "memory_gib": 16,
            "hourly_price": 0.111, "price_currency": "USD",
            "monthly_compute_estimate": 81.03,
            "price_source": "AWS Price List Query API · AmazonEC2 GetProducts",
            "price_retrieved_at": "2026-11-03T16:00:00+00:00",
            "price_effective_date": "2026-11-01T00:00:00Z",
            "price_publication_date": "2026-11-03T00:00:00Z",
            "price_model": "On-Demand · Linux · Shared tenancy",
            "offered_in_target_location": True,
        }],
    }
    result = agent.explain_comparison_evidence("4 vCPU 9 GiB lowest cost", comparison)
    assert "81.03" in captured["prompt"]
    assert "AWS Price List Query API" in captured["prompt"]
    assert "2026-11-03T16:00:00+00:00" in captured["prompt"]
    assert result["execution_status"] == "READ_ONLY_COMPARISON_EXPLANATION"


def test_normalization_does_not_mislabel_inventory_timestamp_as_price_timestamp():
    from cloud_adapters.normalization import normalize_cloud_option

    option = normalize_cloud_option({
        "provider": "AWS", "region": "ap-south-1", "service_type": "EC2",
        "sku": "m5a.xlarge", "vcpus": 4, "memory_gib": 16, "os": "Linux",
        "hourly_price": None, "price_currency": "USD",
        "monthly_compute_estimate": None, "available": False,
        "price_retrieved_at": None, "retrieved_at": "2026-11-03T16:00:00+00:00",
    })
    assert option["price_retrieved_at"] is None
    assert option["retrieved_at"] == "2026-11-03T16:00:00+00:00"


def test_aws_candidate_has_no_fallback_price_when_price_list_is_unavailable(monkeypatch):
    from cloud_adapters import aws as aws_adapter

    monkeypatch.setattr(aws_adapter, "get_ec2_instance_on_demand_price", lambda *args, **kwargs: {
        "available": False, "error": "AccessDenied: pricing:GetProducts", "cache_status": "NOT_CACHED"
    })
    option = aws_adapter._to_price_option({
        "InstanceType": "m5a.xlarge",
        "VCpuInfo": {"DefaultVCpus": 4},
        "MemoryInfo": {"SizeInMiB": 16384},
        "CurrentGeneration": True,
    }, "ap-south-1", 845, "695729002971")

    assert option["available"] is False
    assert option["hourly_price"] is None
    assert option["monthly_compute_estimate"] is None
    assert option["price_retrieved_at"] is None
    assert "pricing:GetProducts" in option["error"]
