from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _option(provider, sku, hourly, vcpus=2, memory=8, region="us-test-1"):
    return {
        "available": True, "provider": provider, "region": region, "sku": sku,
        "vcpus": vcpus, "memory_gib": memory, "os": "Linux",
        "hourly_price": hourly, "price_currency": "USD",
        "monthly_compute_estimate": hourly * 730,
        "monthly_storage_estimate": None, "monthly_network_estimate": None,
        "monthly_total_estimate": None, "telemetry_status": "unavailable",
        "capacity_status": "not verified", "provisioning_enabled": False,
        "price_source": "fixture", "price_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


def test_normalized_option_keeps_missing_fields_unavailable_not_zero():
    from cloud_adapters.normalization import normalize_cloud_option

    normalized = normalize_cloud_option({"provider": "Azure", "available": False, "error": "no matching price"})
    assert normalized["hourly_price"] is None
    assert normalized["monthly_compute_estimate"] is None
    assert normalized["monthly_total_estimate"] is None
    assert normalized["telemetry_status"] == "unavailable"
    assert normalized["provisioning_enabled"] is False


def test_normalizer_rejects_non_finite_prices():
    from cloud_adapters.normalization import normalize_cloud_option

    normalized = normalize_cloud_option({
        "provider": "AWS", "available": True, "vcpus": 2, "memory_gib": 8,
        "hourly_price": float("nan"), "monthly_compute_estimate": float("inf"), "price_currency": "usd",
    })
    assert normalized["hourly_price"] is None
    assert normalized["monthly_compute_estimate"] is None
    assert normalized["price_currency"] == "USD"


def test_recommendation_strategies_have_distinct_scoring_and_keep_latency_unranked():
    from recommendation_engine import compare_options

    options = [
        _option("AWS", "exact-fit", 0.07, 2, 8),
        _option("Azure", "oversized", 0.02, 4, 16),
    ]
    balanced = compare_options(options, 2, 8, strategy="Balanced")
    best_fit = compare_options(options, 2, 8, strategy="Best Fit for Workload")
    assert balanced["recommended_sku"] == "exact-fit"
    assert best_fit["recommended_sku"] == "exact-fit"
    latency = compare_options(options, 2, 8, strategy="Lowest Latency")
    assert latency["recommended_sku"] is None
    assert all(x.get("score") is None for x in latency["eligible_options"])
    assert "latency" in latency["recommendation_reason"].lower()


def test_comparison_excludes_unknown_currency_and_over_budget_options():
    from recommendation_engine import compare_options

    options = [
        _option("AWS", "good", 0.05),
        {**_option("GCP", "eur", 0.03), "price_currency": "EUR"},
        _option("Azure", "expensive", 2.0),
    ]
    result = compare_options(options, 2, 8, max_monthly_budget=100, strategy="Lowest Cost")
    assert result["recommended_sku"] == "good"
    reasons = [x["eligibility_reason"] for x in result["excluded_options"]]
    assert any("currency" in reason.lower() for reason in reasons)
    assert any("budget" in reason.lower() for reason in reasons)



def test_four_vcpu_nine_gib_selects_lowest_cost_meeting_profile_not_closest_overpriced_profile():
    from recommendation_engine import compare_options
    from instance_matching import rank_candidate_options

    options = [
        {**_option("AWS", "c5n.xlarge", 157.68 / 730, 4, 10.5), "offered_in_target_location": True},
        {**_option("AWS", "m5a.xlarge", 81.03 / 730, 4, 16), "offered_in_target_location": True},
        {**_option("AWS", "m6a.xlarge", 81.103 / 730, 4, 16), "offered_in_target_location": True},
        {**_option("AWS", "m5ad.xlarge", 97.82 / 730, 4, 16), "offered_in_target_location": True},
        {**_option("Azure", "Standard_D4s_v5", 147.46 / 730, 4, 16), "spec_source": "public catalogue"},
        {**_option("GCP", "n2-standard-4", 170.3002 / 730, 4, 16), "spec_source": "public catalogue"},
    ]
    result = compare_options(options, 4, 9, strategy="Balanced")
    assert result["recommended_provider"] == "AWS"
    assert result["recommended_sku"] == "m5a.xlarge"
    assert result["optimal_sku"] == "m5a.xlarge"
    ranked = rank_candidate_options(options, 4, 9)
    safe_aws = [x for x in ranked if x["provider"] == "AWS" and x["hardware_fit"] in {"exact", "meets"}]
    assert safe_aws[0]["sku"] == "m5a.xlarge"


def test_exact_match_has_priority_over_lower_cost_oversized_profile():
    from recommendation_engine import compare_options

    options = [
        {**_option("AWS", "exact-4x9", 0.25, 4, 9), "offered_in_target_location": True},
        {**_option("AWS", "cheap-4x16", 0.05, 4, 16), "offered_in_target_location": True},
    ]
    result = compare_options(options, 4, 9, strategy="Lowest Cost")
    assert result["recommended_sku"] == "exact-4x9"
    assert result["optimal_sku"] == "exact-4x9"


def test_lowest_latency_is_not_claimed_without_measured_latency_but_cost_optimal_is_returned():
    from recommendation_engine import compare_options

    options = [
        {**_option("AWS", "low-cost", 0.05, 4, 16), "offered_in_target_location": True},
        {**_option("AWS", "higher-cost", 0.10, 4, 16), "offered_in_target_location": True},
    ]
    result = compare_options(options, 4, 9, strategy="Lowest Latency")
    assert result["recommended_sku"] is None
    assert result["optimal_sku"] == "low-cost"
    assert result["latency_measured"] is False


def test_comparison_explanation_is_sanitized_and_kept_to_six_to_eight_lines(monkeypatch):
    import agent

    monkeypatch.setattr(agent, "complete_turn", lambda *args, **kwargs: {
        "text": "<thinking>hidden chain content</thinking>\n**Recommended: AWS · m5a.xlarge**",
        "provider": "Amazon Bedrock", "model_id": "apac.amazon.nova-lite-v1:0", "region": "ap-south-1",
    })
    result = agent.explain_comparison_evidence("4 vCPU and 9 GB RAM", {
        "requirements": {"min_vcpus": 4, "min_memory_gib": 9},
        "strategy": "Balanced", "recommended_provider": "AWS", "recommended_sku": "m5a.xlarge",
        "optimal_provider": "AWS", "optimal_sku": "m5a.xlarge", "optimal_monthly_compute_estimate": 81.03,
        "latency_measured": False,
        "source_options": [{"provider": "AWS", "sku": "m5a.xlarge", "available": True,
                            "vcpus": 4, "memory_gib": 16, "monthly_compute_estimate": 81.03,
                            "offered_in_target_location": True}],
    })
    lines = [line for line in result["answer"].splitlines() if line.strip()]
    assert 6 <= len(lines) <= 8
    assert "m5a.xlarge" in result["answer"]
    assert "<thinking>" not in result["answer"]
    assert "hidden chain content" not in result["answer"]
    assert result["execution_status"] == "READ_ONLY_COMPARISON_EXPLANATION"

def test_azure_price_parser_uses_only_unambiguous_primary_linux_hourly_meter(monkeypatch):
    from cloud_adapters import azure

    monkeypatch.setattr(azure, "_get_json", lambda _url: {
        "Items": [
            {"serviceName": "Virtual Machines", "armRegionName": "centralindia", "armSkuName": "Standard_D2s_v5",
             "priceType": "Consumption", "unitOfMeasure": "1 Hour", "meterName": "D2s v5", "productName": "Dsv5 Series",
             "retailPrice": 0.1, "currencyCode": "USD", "isPrimaryMeterRegion": True},
        ],
        "NextPageLink": None,
    })
    result = azure.get_azure_vm_price("centralindia", "Standard_D2s_v5")
    assert result["available"] is True
    assert result["hourly_price"] == pytest.approx(0.1)
    assert result["monthly_compute_estimate"] == pytest.approx(73.0)
    assert result["telemetry_status"].startswith("unavailable")
    assert result["provisioning_enabled"] is False


def test_azure_parser_does_not_guess_when_multiple_primary_rates(monkeypatch):
    from cloud_adapters import azure

    def fake(_url):
        common = {"serviceName": "Virtual Machines", "armRegionName": "centralindia", "armSkuName": "Standard_D2s_v5",
                  "priceType": "Consumption", "unitOfMeasure": "1 Hour", "meterName": "D2s v5", "productName": "Dsv5 Series",
                  "currencyCode": "USD", "isPrimaryMeterRegion": True}
        return {"Items": [{**common, "retailPrice": 0.1}, {**common, "retailPrice": 0.11}], "NextPageLink": None}
    monkeypatch.setattr(azure, "_get_json", fake)
    result = azure.get_azure_vm_price("centralindia", "Standard_D2s_v5")
    assert result["available"] is False
    assert "multiple distinct" in result["error"].lower()


def test_gcp_current_catalog_endpoint_and_cpu_ram_price_aggregation(monkeypatch):
    from cloud_adapters import gcp

    monkeypatch.setattr(gcp, "_catalog_api_key", lambda: "test-key")
    def fake(url):
        if "/services?" in url:
            assert "pageSize=5000" in url
            return {"services": [{"name": "services/COMPUTE_ID", "displayName": " Compute Engine "}]}
        if "/skus?" in url:
            def sku(desc, nanos):
                return {
                    "description": desc, "serviceRegions": ["asia-south1"],
                    "category": {"usageType": "OnDemand", "resourceFamily": "Compute"},
                    "pricingInfo": [{"effectiveTime": "2026-01-01T00:00:00Z", "pricingExpression": {
                        "usageUnit": "h", "tieredRates": [{"startUsageAmount": 0, "unitPrice": {"units": 0, "nanos": nanos}}]
                    }}],
                    "skuId": desc.replace(" ", "-"),
                }
            return {"skus": [
                # City-specific prices should win over a wider APAC price when both apply.
                sku("N2 Instance Core running in APAC", 50_000_000),
                sku("N2 Instance RAM running in APAC", 6_000_000),
                sku("N2 Instance Core running in Mumbai", 30_000_000),
                sku("N2 Instance RAM running in Mumbai", 4_000_000),
            ]}
        raise AssertionError(f"Unexpected URL {url}")
    monkeypatch.setattr(gcp, "_get_json", fake)
    result = gcp.get_gcp_vm_price("asia-south1", "n2-standard-2")
    assert result["available"] is True
    assert result["hourly_price"] == pytest.approx(0.092)
    assert result["monthly_compute_estimate"] == pytest.approx(round(0.092 * 730, 4))
    assert "/v1/services/COMPUTE_ID/skus" in result["price_source"]
    assert result["provisioning_enabled"] is False


def test_gcp_api_errors_do_not_echo_query_api_key(monkeypatch):
    from cloud_adapters import gcp
    monkeypatch.setattr(gcp, "_catalog_api_key", lambda: "secret-key-123")
    monkeypatch.setattr(gcp, "_get_json", lambda _url: (_ for _ in ()).throw(RuntimeError("request failed")))
    result = gcp.get_gcp_vm_price()
    assert result["available"] is False
    assert "secret-key-123" not in result["error"]


def test_openai_compatible_response_normalizes_tool_call_and_rejects_bad_arguments():
    from llm_provider import _parse_openai_response

    response = {"choices": [{"message": {"content": "Checking AWS now.", "tool_calls": [{
        "id": "call-1", "function": {"name": "get_workload_metrics", "arguments": '{"instance_id":"i-0123456789abcdef0"}'},
    }]}, "finish_reason": "tool_calls"}]}
    result = _parse_openai_response(response, "OpenAI", "gpt-test")
    assert result["text"] == "Checking AWS now."
    assert result["tool_calls"][0]["arguments"]["instance_id"] == "i-0123456789abcdef0"
    response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not-json"
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _parse_openai_response(response, "OpenAI", "gpt-test")


def test_provider_selection_requires_key_and_does_not_fallback_silently(monkeypatch):
    from llm_provider import complete_turn
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("llm_provider._secret_or_env", lambda *_args: "")
    with pytest.raises(RuntimeError, match="API key is not configured"):
        complete_turn([{"role": "user", "text": "hello"}], "system", [])


def test_tool_argument_validation_is_read_only_allowlisted():
    from agent import _validate_tool_args
    assert _validate_tool_args("get_workload_metrics", {"instance_id": "i-0123456789abcdef0"})["instance_id"] == "i-0123456789abcdef0"
    with pytest.raises(ValueError, match="not on Intelligent Provisioning Platform"):
        _validate_tool_args("run_shell_command", {"cmd": "id"})
    with pytest.raises(ValueError, match="Unsupported tool argument"):
        _validate_tool_args("get_workload_metrics", {"instance_id": "i-0123456789abcdef0", "shell": "id"})


def test_normalizer_does_not_split_string_warnings_into_characters():
    from cloud_adapters.normalization import normalize_cloud_option

    result = normalize_cloud_option({
        "provider": "AWS", "warnings": "compute only; storage excluded",
    })
    assert result["warnings"] == ["compute only; storage excluded"]


def test_http_provider_error_redacts_configured_secret(monkeypatch):
    import io
    import urllib.error
    import llm_provider

    secret = "test-super-secret-value-123"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    def fake_urlopen(request, timeout=60):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {},
            io.BytesIO(f'{{"error":"invalid key {secret}"}}'.encode()),
        )

    monkeypatch.setattr(llm_provider.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as exc_info:
        llm_provider._http_json("https://provider.example.test/v1", {}, {})
    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_azure_public_pricing_retries_transient_http_429(monkeypatch):
    import io
    import urllib.error
    from cloud_adapters import azure

    calls = {"count": 0}
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"Items": [], "NextPageLink": null}'

    def fake_urlopen(request, timeout=15):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"rate limited"))
        return FakeResponse()

    monkeypatch.setattr(azure.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(azure.time, "sleep", lambda _seconds: None)
    assert azure._get_json("https://prices.example.test") == {"Items": [], "NextPageLink": None}
    assert calls["count"] == 2


def test_infrastructure_prompt_parser_handles_vpc_typo_and_ram():
    from infrastructure_request import parse_infrastructure_request

    result = parse_infrastructure_request("I need 4 vpc 16gb ram Linux infra, choose the cheapest")
    assert result["min_vcpus"] == 4
    assert result["min_memory_gib"] == 16.0
    assert result["os"] == "Linux"
    assert result["strategy"] == "Lowest Cost"
    assert result["defaults_used"] == []


def test_infrastructure_prompt_parser_reports_defaults_when_values_missing():
    from infrastructure_request import parse_infrastructure_request

    result = parse_infrastructure_request("Please create Linux infrastructure")
    assert result["min_vcpus"] == 2
    assert result["min_memory_gib"] == 8.0
    assert len(result["defaults_used"]) == 2


def test_cloud_comparison_uses_live_aws_discovery_and_curated_azure_gcp_poc_profiles(monkeypatch):
    import cloud_comparison as comparison

    seen = {"aws": [], "azure": [], "gcp": []}
    def aws(region, min_vcpus, min_memory_gib):
        seen["aws"].append((region, min_vcpus, min_memory_gib))
        return [{"available": True, "provider": "AWS", "region": region, "sku": "live-cpu.xlarge",
                 "vcpus": 4, "memory_gib": 16, "offered_in_target_location": True,
                 "spec_source": "AWS EC2 DescribeInstanceTypes API (live response)", "hourly_price": 0.1,
                 "price_currency": "USD", "monthly_compute_estimate": 73.0}]
    def azure(region, sku):
        seen["azure"].append(sku)
        return {"available": False, "provider": "Azure", "region": region, "sku": sku, "error": "fixture"}
    def gcp(region, machine_type):
        seen["gcp"].append(machine_type)
        return {"available": False, "provider": "GCP", "region": region, "sku": machine_type, "error": "fixture"}
    monkeypatch.setattr(comparison, "discover_aws_vm_options", aws)
    monkeypatch.setattr(comparison, "get_azure_vm_price", azure)
    monkeypatch.setattr(comparison, "get_gcp_vm_price", gcp)
    rows = comparison.get_provider_options("ap-south-1", "centralindia", "asia-south1", 4, 16)
    assert seen["aws"] == [("ap-south-1", 4, 16)]
    assert rows[0]["sku"] == "live-cpu.xlarge"
    assert rows[0]["spec_source"] == "AWS EC2 DescribeInstanceTypes API (live response)"
    assert seen["azure"] and seen["gcp"]
    assert all("Standard_" in sku for sku in seen["azure"])
    assert all("n2-standard" in sku for sku in seen["gcp"])


def test_cloud_comparison_does_not_require_aws_sku_to_be_predefined(monkeypatch):
    import cloud_comparison as comparison

    live_names = []
    def aws(region, min_vcpus, min_memory_gib):
        live_names.extend(["c7i.xlarge", "m7i.xlarge", "t3.large"])
        return [
            {"available": False, "provider": "AWS", "region": region, "sku": "c7i.xlarge",
             "vcpus": 4, "memory_gib": 8, "offered_in_target_location": True,
             "error": "price unavailable", "spec_source": "AWS EC2 DescribeInstanceTypes API (live response)"},
            {"available": False, "provider": "AWS", "region": region, "sku": "m7i.xlarge",
             "vcpus": 4, "memory_gib": 16, "offered_in_target_location": True,
             "error": "price unavailable", "spec_source": "AWS EC2 DescribeInstanceTypes API (live response)"},
            {"available": False, "provider": "AWS", "region": region, "sku": "t3.large",
             "vcpus": 2, "memory_gib": 8, "offered_in_target_location": True,
             "error": "price unavailable", "spec_source": "AWS EC2 DescribeInstanceTypes API (live response)"},
        ]
    monkeypatch.setattr(comparison, "discover_aws_vm_options", aws)
    monkeypatch.setattr(comparison, "get_azure_vm_price", lambda region, sku: {
        "available": False, "provider": "Azure", "region": region, "sku": sku, "error": "fixture"})
    monkeypatch.setattr(comparison, "get_gcp_vm_price", lambda region, sku: {
        "available": False, "provider": "GCP", "region": region, "sku": sku, "error": "fixture"})
    rows = comparison.get_provider_options("ap-south-1", "centralindia", "asia-south1", 4, 8)
    aws_rows = [row for row in rows if row["provider"] == "AWS"]
    assert {row["sku"] for row in aws_rows} == {"c7i.xlarge", "m7i.xlarge", "t3.large"}
    assert len(live_names) == 3


def test_aws_discovery_reads_specs_from_paginated_live_ec2_apis_without_sku_catalogue(monkeypatch):
    from cloud_adapters import aws

    offered_pages = [
        {"InstanceTypeOfferings": [{"InstanceType": "c7i.xlarge"}], "NextToken": "next-page"},
        {"InstanceTypeOfferings": [{"InstanceType": "m7i.xlarge"}, {"InstanceType": "t3.large"}]},
    ]
    spec_data = {
        "c7i.xlarge": (4, 8192),
        "m7i.xlarge": (4, 16384),
        "t3.large": (2, 8192),
    }
    class FakeEC2:
        def describe_instance_type_offerings(self, **kwargs):
            assert kwargs["LocationType"] == "region"
            assert kwargs["Filters"] == [{"Name": "location", "Values": ["ap-south-1"]}]
            return offered_pages.pop(0)
        def describe_instance_types(self, InstanceTypes):
            assert set(InstanceTypes) == set(spec_data)
            return {"InstanceTypes": [{
                "InstanceType": name,
                "VCpuInfo": {"DefaultVCpus": spec_data[name][0]},
                "MemoryInfo": {"SizeInMiB": spec_data[name][1]},
                "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
                "SupportedRootDeviceTypes": ["ebs"],
                "SupportedVirtualizationTypes": ["hvm"],
                "SupportedUsageClasses": ["on-demand", "spot"],
                "CurrentGeneration": True,
                "BareMetal": False,
            } for name in InstanceTypes]}
    class FakeSTS:
        def get_caller_identity(self):
            return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/CloudPilotRole/session"}
    def fake_client(service, **kwargs):
        return FakeSTS() if service == "sts" else FakeEC2()
    monkeypatch.setattr(aws.boto3, "client", fake_client)
    monkeypatch.setattr(aws, "get_ec2_instance_on_demand_price", lambda sku, *args: {
        "available": True, "hourly_usd": 0.1, "monthly_730h_usd": 73.0})

    rows = aws.discover_aws_vm_options("ap-south-1", 4, 8, candidate_limit=10)
    by_sku = {row["sku"]: row for row in rows}
    assert by_sku["c7i.xlarge"]["vcpus"] == 4
    assert by_sku["c7i.xlarge"]["memory_gib"] == 8
    assert by_sku["c7i.xlarge"]["spec_source"] == "AWS EC2 DescribeInstanceTypes API (live response)"
    assert by_sku["c7i.xlarge"]["offered_in_target_location"] is True
    assert by_sku["c7i.xlarge"]["account_id"] == "123456789012"
    assert "PROFILE_TYPES" not in aws.__dict__


def test_agent_ui_explains_exact_match_and_nearest_alternatives():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "Exact match check and closest configurations" in source
    assert "hardware_match" in source
    assert "Undersized alternatives are shown for comparison" in source
    assert "Azure/GCP results use the POC's curated reference profiles" in source


def test_candidate_matching_distinguishes_exact_over_and_undersized():
    from instance_matching import rank_candidate_options, exact_match_summary

    options = [
        {"provider": "AWS", "sku": "m6i.xlarge", "vcpus": 4, "memory_gib": 16,
         "offered_in_target_location": True, "available": True, "monthly_compute_estimate": 100},
        {"provider": "AWS", "sku": "t3.large", "vcpus": 2, "memory_gib": 8,
         "offered_in_target_location": True, "available": True, "monthly_compute_estimate": 50},
        {"provider": "AWS", "sku": "c6i.xlarge", "vcpus": 4, "memory_gib": 8,
         "offered_in_target_location": True, "available": False, "monthly_compute_estimate": None},
    ]
    ranked = rank_candidate_options(options, 4, 8)
    assert [x["sku"] for x in ranked] == ["c6i.xlarge", "m6i.xlarge", "t3.large"]
    assert ranked[0]["hardware_fit"] == "exact"
    assert ranked[1]["hardware_fit"] == "meets"
    assert ranked[2]["hardware_fit"] == "below"
    summary = exact_match_summary(options, 4, 8)
    assert summary["aws_verified_exact"][0]["sku"] == "c6i.xlarge"


def test_unverified_aws_exact_profile_is_not_claimed_as_regional_match():
    from instance_matching import exact_match_summary, rank_candidate_options

    options = [{"provider": "AWS", "sku": "c6i.xlarge", "vcpus": 4, "memory_gib": 8,
                "offered_in_target_location": None, "available": False,
                "error": "UnauthorizedOperation"}]
    summary = exact_match_summary(options, 4, 8)
    assert not summary["aws_verified_exact"]
    assert summary["aws_unverified_exact"][0]["sku"] == "c6i.xlarge"
    ranked = rank_candidate_options(options, 4, 8)
    assert ranked[0]["selectable_for_aws_plan"] is False


def test_terraform_payload_accepts_4v_cpu_profile_and_keeps_requirements():
    from terraform_service import _validate_payload
    valid_payload = {
        "region": "ap-south-1", "ami_id": "ami-0123456789abcdef0", "instance_type": "m6i.xlarge",
        "subnet_id": "subnet-0123456789abcdef0", "security_group_id": "sg-0123456789abcdef0",
        "instance_name": "cloudpilot-test", "owner": "CloudPilot-POC", "ttl_days": 2, "instance_profile_name": "",
    }
    payload = _validate_payload({**valid_payload, "min_vcpus": 4, "min_memory_gib": 16})
    assert payload["instance_type"] == "m6i.xlarge"
    assert payload["min_vcpus"] == 4
    assert payload["min_memory_gib"] == 16
    c6i_payload = _validate_payload({**valid_payload, "instance_type": "c6i.xlarge", "min_vcpus": 4, "min_memory_gib": 8})
    assert c6i_payload["instance_type"] == "c6i.xlarge"


def test_aws_create_selection_requires_explicit_region_offering_and_request_fit():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert 'option.get("offered_in_target_location") is True' in source
    assert "disabled=not enabled" not in source[source.find('Create AWS infrastructure — prepare saved plan')-700:source.find('Create AWS infrastructure — prepare saved plan')+100]
    assert "if not enabled:" in source[source.find('Create AWS infrastructure — prepare saved plan'):]


def test_gcp_missing_api_key_keeps_curated_specs_but_no_fake_price(monkeypatch):
    from cloud_adapters import gcp

    monkeypatch.setattr(gcp, "_catalog_api_key", lambda: "")
    result = gcp.get_gcp_vm_price("asia-south1", "n2-standard-4")
    assert result["available"] is False
    assert result["vcpus"] == 4
    assert result["memory_gib"] == 16.0
    assert result.get("hourly_price") is None
    assert "GOOGLE_CLOUD_BILLING_API_KEY" in result["error"]
    assert result.get("monthly_compute_estimate") is None


def test_aws_offering_permission_error_preserves_context_but_blocks_verified_selection(monkeypatch):
    from cloud_adapters import aws

    class FakeEC2:
        def describe_instance_type_offerings(self, **kwargs):
            raise RuntimeError("UnauthorizedOperation ec2:DescribeInstanceTypeOfferings")

    monkeypatch.setattr(aws.boto3, "client", lambda *args, **kwargs: FakeEC2())
    result = aws.get_aws_vm_option("ap-south-1", "m6i.xlarge")
    assert result["available"] is False
    assert result["vcpus"] is None
    assert result["memory_gib"] is None
    assert result["offered_in_target_location"] is None
    assert "ec2:DescribeInstanceTypeOfferings" in result["error"]
    assert "ec2:DescribeInstanceTypes" in result["error"]
    assert "No live AWS specification fallback used" in result["spec_source"]
    assert result.get("hourly_price") is None


def test_work_view_separates_controller_and_selected_target_inventory_with_cost_estimate():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
    assert 'heading.subheader("Connected accounts")' in source
    assert 'st.subheader("Controller account resources · estimated monthly compute cost")' in source
    assert '"Estimated compute / month (USD)"' in source
    assert "render_comparison(list_connections(), inventory, ec2_costs)" in source
    assert "AWS service costs (actual MTD from Cost Explorer)" in source
    work = source.split("def render_telemetry() -> None:", 1)[1].split("def ", 1)[0]
    assert "render_configured_provider_inventory()" not in work
    assert "Continuous workload watch" not in work
    assert "TARGET_INSTANCE_ID is not configured" not in work
    assert 'refresh.button("Refresh"' in source


def test_agent_no_longer_renders_connected_aws_workload_monitor_or_strategy_budget_controls():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
    agent_view = source.split("def render_agent() -> None:", 1)[1].split("PRICE_CACHE_TTL_SECONDS", 1)[0]
    assert "Connected AWS workload monitor" not in agent_view
    assert "get_metric_series" not in agent_view
    prompt_flow = source.split("def _render_infrastructure_request_flow() -> None:", 1)[1].split("def render_cloud_comparison()", 1)[0]
    assert '"Recommendation strategy"' not in prompt_flow
    assert '"Monthly compute budget in USD (optional)"' not in prompt_flow
    assert "Example: 4 vCPU, 16 GiB RAM" in prompt_flow


def test_agent_cloud_connections_lists_saved_provider_ids_without_secret_arns(monkeypatch):
    import agent
    monkeypatch.setattr(agent, "list_running_instances", lambda: {"available": False})
    monkeypatch.setattr("provider_connections.list_connections", lambda: [{
        "connection_id": "aws-aws-poc", "provider": "AWS", "name": "AWS-POC",
        "connection_status": "CONNECTED", "account_id": "123456789012",
        "secret_arn": "arn:aws:secretsmanager:secret:do-not-expose"
    }])
    result = agent._execute_tool("get_cloud_connections", {}, "")
    assert result["connections"][0]["connection_id"] == "aws-aws-poc"
    assert "secret_arn" not in str(result)


def test_agent_inventory_tool_requires_a_connected_saved_connection(monkeypatch):
    import agent
    monkeypatch.setattr("provider_connections.get_connection", lambda _cid: {
        "connection_id": "aws-aws-poc", "provider": "AWS", "name": "AWS-POC",
        "connection_status": "CONNECTED"
    })
    monkeypatch.setattr("provider_connections.configured_inventory", lambda _cid: {
        "ok": True, "provider": "AWS", "account_id": "123456789012",
        "sampled_instances": 1, "resources": [{"name": "demo", "instance_id": "i-12345678",
                                                   "private_ip": "10.0.0.5", "type": "t3.micro"}],
        "inventory_source": "EC2 DescribeInstances", "checked_at": "now"
    })
    result = agent._execute_tool("get_connected_provider_inventory",
                                 {"connection_id": "aws-aws-poc"}, "")
    assert result["account_id"] == "123456789012"
    assert result["resources"][0]["instance_id"] == "i-12345678"
    assert "private_ip" not in str(result)



def test_configured_aws_telemetry_uses_live_identity_and_monitoring_compatible_periods(monkeypatch):
    from provider_connections import configured_aws_metrics

    instance_id = "i-0123456789abcdef0"
    account_id = "123456789012"
    monkeypatch.setattr("provider_connections.get_connection", lambda _connection_id: {
        "connection_id": "aws-poc", "provider": "AWS", "connection_status": "CONNECTED",
        "account_id": account_id, "metadata": {"region": "ap-south-1"}, "secret_arn": "arn:aws:secretsmanager:ap-south-1:123456789012:secret:test",
    })
    monkeypatch.setattr("provider_connections._get_secret", lambda _arn: {})

    periods = []

    class FakeSession:
        def client(self, service, **_kwargs):
            if service == "sts":
                return type("STS", (), {"get_caller_identity": lambda self: {"Account": account_id}})()
            if service == "ec2":
                return type("EC2", (), {"describe_instances": lambda self, **_kw: {
                    "Reservations": [{"Instances": [{"InstanceId": instance_id, "InstanceType": "t3.small",
                                                        "State": {"Name": "running"}}]}]
                }})()
            if service == "cloudwatch":
                class CloudWatch:
                    def list_metrics(self, **_kwargs):
                        return {"Metrics": [{"Dimensions": [
                            {"Name": "InstanceId", "Value": instance_id}, {"Name": "path", "Value": "/"}
                        ]}]}

                    def get_metric_statistics(self, **kwargs):
                        periods.append((kwargs["Namespace"], kwargs["MetricName"], kwargs["Period"]))
                        stat = "Sum" if "Sum" in kwargs["Statistics"] else "Average"
                        return {"Datapoints": [{"Timestamp": datetime.now(timezone.utc), stat: 12.5,
                                                 "Maximum": 15.0}]}
                return CloudWatch()
            raise AssertionError(f"Unexpected AWS service: {service}")

    monkeypatch.setattr("provider_connections._assumed_aws_session", lambda *_args, **_kwargs: FakeSession())
    result = configured_aws_metrics("aws-poc", instance_id, hours=1)

    assert result["ok"] is True
    assert result["instance_id"] == instance_id
    period_by_metric = {(namespace, name): period for namespace, name, period in periods}
    assert period_by_metric[("AWS/EC2", "CPUUtilization")] == 300
    assert period_by_metric[("AWS/EC2", "NetworkIn")] == 300
    assert period_by_metric[("CWAgent", "mem_used_percent")] == 60
    assert period_by_metric[("CWAgent", "disk_used_percent")] == 60
    assert result["metrics"]["CPU"]["available"] is True


def test_inventory_refresh_marks_failed_connection_unavailable_and_clears_stale_identity(monkeypatch):
    import provider_connections as pc

    connection_id = "aws-aws-poc"
    row = {
        "connection_id": connection_id, "provider": "AWS", "connection_status": "CONNECTED",
        "account_id": "123456789012", "inventory_source": "old inventory",
        "secret_arn": "arn:aws:secretsmanager:ap-south-1:123456789012:secret:iipp/test",
        "metadata": {},
    }
    stored = {"connections": [dict(row)]}
    monkeypatch.setattr(pc, "get_connection", lambda _cid: dict(row))
    monkeypatch.setattr(pc, "_get_secret", lambda _arn: {})
    monkeypatch.setattr(pc, "test_provider_credentials", lambda *_args: {
        "ok": False, "error": "AccessDenied", "checked_at": "2026-11-03T00:00:00+00:00"
    })
    monkeypatch.setattr(pc, "_read_all", lambda: stored)
    monkeypatch.setattr(pc, "_write_all", lambda value: stored.update(value))

    result = pc.configured_inventory(connection_id)

    assert result["ok"] is False
    updated = stored["connections"][0]
    assert updated["connection_status"] == "ERROR"
    assert "account_id" not in updated
    assert "inventory_source" not in updated
    assert updated["last_test_error"] == "AccessDenied"


def test_inventory_refresh_keeps_verified_identity_when_successful(monkeypatch):
    import provider_connections as pc

    connection_id = "aws-aws-test"
    row = {"connection_id": connection_id, "provider": "AWS", "connection_status": "ERROR",
           "secret_arn": "secret-arn", "metadata": {}}
    stored = {"connections": [dict(row)]}
    monkeypatch.setattr(pc, "get_connection", lambda _cid: dict(row))
    monkeypatch.setattr(pc, "_get_secret", lambda _arn: {})
    monkeypatch.setattr(pc, "test_provider_credentials", lambda *_args: {
        "ok": True, "account_id": "210987654321", "inventory_source": "EC2 DescribeInstances",
        "checked_at": "2026-11-03T00:00:00+00:00"
    })
    monkeypatch.setattr(pc, "_read_all", lambda: stored)
    monkeypatch.setattr(pc, "_write_all", lambda value: stored.update(value))

    result = pc.configured_inventory(connection_id)

    assert result["ok"] is True
    updated = stored["connections"][0]
    assert updated["connection_status"] == "CONNECTED"
    assert updated["account_id"] == "210987654321"
    assert updated["inventory_source"] == "EC2 DescribeInstances"
    assert updated["last_test_error"] is None


def test_measured_latency_is_used_only_when_comparable_measurements_exist():
    from recommendation_engine import compare_options

    options = [
        {**_option("AWS", "cheap-slower", 0.05, 4, 16),
         "offered_in_target_location": True, "measured_latency_ms": 48.0},
        {**_option("AWS", "faster-pricier", 0.10, 4, 16),
         "offered_in_target_location": True, "measured_latency_ms": 12.0},
    ]
    result = compare_options(options, 4, 9, strategy="Lowest Latency")
    assert result["latency_measured"] is True
    assert result["recommended_sku"] == "faster-pricier"
    assert result["optimal_sku"] == "cheap-slower"
    assert all(x["latency_score"] is not None for x in result["eligible_options"])


def test_parser_handles_vpcu_typo_and_gib_server_prompt():
    from infrastructure_request import parse_infrastructure_request

    parsed = parse_infrastructure_request("I need 4 Vpcu and 8Gib Server")
    assert parsed["min_vcpus"] == 4
    assert parsed["min_memory_gib"] == 8.0
    assert parsed["defaults_used"] == []


def test_aws_shortlist_always_keeps_exact_profile_even_if_beyond_nearest_limit():
    from cloud_adapters.aws import _shortlist

    def spec(name, cpu, memory_gib):
        return {
            "InstanceType": name,
            "VCpuInfo": {"DefaultVCpus": cpu},
            "MemoryInfo": {"SizeInMiB": int(memory_gib * 1024)},
            "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
            "SupportedRootDeviceTypes": ["ebs"],
            "SupportedVirtualizationTypes": ["hvm"],
            "SupportedUsageClasses": ["on-demand"],
            "CurrentGeneration": True,
        }

    rows = [spec(f"c-exact-{i}.xlarge", 4, 8) for i in range(13)]
    rows.extend(spec(f"m-family-{i}.large", 4, 16 + i) for i in range(15))
    result = _shortlist(rows, required_vcpus=4, required_memory_gib=8, limit=12)
    names = [x["InstanceType"] for x in result]
    # All exact matches survive the limit, with room for the next upgrades.
    assert len(names) == 15
    assert {"m-family-0.large", "m-family-1.large"}.issubset(names)
    assert sum(name.startswith("c-exact-") for name in names) == 13
