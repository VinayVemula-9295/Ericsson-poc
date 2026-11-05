"""Bedrock Converse tool-using provisioning agent; tools are read-only."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from dataclasses import dataclass, asdict

from llm_provider import complete_turn

from aws_data import (discover_instance_type_options, get_metric_series, get_monthly_cost_summary,
                      get_running_ec2_cost_estimate, list_running_instances, get_target_instance_details)
from cloud_comparison import run_comparison


_INTERNAL_REASONING_TAGS = ("thinking", "analysis", "reasoning", "scratchpad", "chain_of_thought")
_INTERNAL_BLOCK_RE = re.compile(
    r"<(thinking|analysis|reasoning|scratchpad|chain_of_thought)\b[^>]*>.*?</\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_OPEN_INTERNAL_BLOCK_RE = re.compile(
    r"<(?:thinking|analysis|reasoning|scratchpad|chain_of_thought)\b[^>]*>.*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_FINAL_BLOCK_RE = re.compile(r"<(?:final|answer)\b[^>]*>(.*?)</(?:final|answer)\s*>", flags=re.IGNORECASE | re.DOTALL)


def sanitize_agent_answer(value: Any) -> str:
    """Return only user-facing text; never render internal model reasoning tags."""
    text = str(value or "")
    final_blocks = _FINAL_BLOCK_RE.findall(text)
    if final_blocks:
        text = final_blocks[-1]
    text = _INTERNAL_BLOCK_RE.sub("", text)
    text = _OPEN_INTERNAL_BLOCK_RE.sub("", text)
    for tag in _INTERNAL_REASONING_TAGS:
        text = re.sub(rf"</?{tag}\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:final|answer)\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    cleaned = text.strip()
    return cleaned or "I couldn't produce a clear recommendation from the available evidence. Please refine the request or try again."

SYSTEM_PROMPT = """
You are Intelligent Provisioning Platform, the Innominds multi-cloud intelligent provisioning platform, a cloud provisioning reasoning agent for a telecom service provider.
Your task is to analyze a sustained CPU-threshold event and propose a safe, reviewable infrastructure plan.
The business objective is to respond to sudden workload changes (the customer example is a stable network serving 200 million subscribers that experiences a spike) while accounting for capacity, latency, data sovereignty/privacy, cost and cloud eligibility.

Rules:
- Use the provided AWS tools to fetch current telemetry, running inventory, instance-type options for the target AZ, and cost context before recommending anything. For POC/TEST member accounts, use get_cloud_connections, then get_connected_provider_inventory, then get_connected_aws_workload_metrics for the exact account and instance. Do not assume the controller account is the target account.
- Before making claims about connected providers, call get_cloud_connections. Only treat a provider/account as connected when the saved connection status is CONNECTED; then call get_connected_provider_inventory for fresh resource evidence. Do not claim any provider is connected merely because its form exists.
- Hard constraints and unmeasured values must be called out. The customer notes mention latency, privacy and data sovereignty but specify no numeric latency SLO or complete residency policy. Do not invent a 10 ms target. Current workload region is ap-south-1; the configured APAC Bedrock inference profile may process requests in another APAC region, so strict India-only processing is not verified.
- Distinguish the measured QuickCart CPU profile from the separate 200-million-subscriber business example. Never extrapolate a small QuickCart test into telecom production capacity.
- Recommend only changes supported by the observed evidence. The input is a sustained CPU-threshold event emitted by deterministic code from distinct, fresh CloudWatch datapoints. It has a `trend_signal` of `scale_up` or `scale_down`; explicitly explain the threshold and suggested instance type. Do not recommend a change based only on load-test RPS, memory, disk, or assumptions. If memory/disk metrics are missing, say so.
- Scale-up example: t3.micro -> t3.small after CPU >= 70% for at least 2 distinct fresh datapoints. Scale-down example: t3.small -> t3.micro after CPU <= 25% for at least 3 distinct fresh datapoints. These are POC thresholds, not telecom production SLOs.
- For a deterministic `scale_down` event, if the event's suggested smaller type is still allowlisted and listed by the live offering lookup, provide it as a conservative Terraform-planning recommendation; do not return `none` solely because the event is low CPU. Clearly state that demand/headroom and application health must be validated before a Terraform plan is prepared. Return `none` only if no smaller target is offered/allowlisted or the live evidence contradicts the event.
- For scale-up, compare all offered, allowlisted same-family candidate sizes using live EC2 type specifications and Price List estimates. Prefer the smallest reasonable first step only when the evidence supports it; explain why larger options are not selected. Do not say a size will definitely control traffic unless measured requirements support that conclusion.
- For T3 instances, be precise: t3.micro, t3.small, t3.medium, and t3.large each have 2 vCPUs; the larger sizes primarily add memory and a higher CPU-credit baseline (for example, micro 10%, small 20%, large 30% baseline per vCPU). A t3.small can be a sensible conservative first step, but it does not add vCPUs, so persistent CPU-bound load may need a different family or benchmark validation.
- `offered_in_target_location` means EC2 lists the instance type as offered in the target AZ/region. It does not prove immediate spare capacity. Never claim reserved or guaranteed capacity. Actual changes require reviewer approval.
- Produce a concise Markdown infrastructure plan with: event summary, evidence (include metric timestamps), recommendation, placement decision, capacity assumptions, cost impact (or explicitly unavailable), policy checks, rollback/validation, Terraform plan draft, and approval gate.
- Include a machine-readable Markdown line exactly like `Preferred instance type: t3.small` (or another type from the live `offered_options` and configured allowlist), or `Preferred instance type: none` if no Terraform-managed capacity change is justified. Never choose a type that the live tool did not list as offered in the target location. This is recommendation evidence only; all resource changes must pass through Terraform plan and human approval. For scale-up from t3.micro, prefer t3.small as a conservative first step only when appropriate; if CPU remains the bottleneck, state that t3.small still has 2 vCPUs and post-change validation is required.
- Terraform/HCL is a DRAFT ONLY. Do not call tools that provision or mutate resources. The plan must clearly state NOT APPLIED and that a human approval plus a reviewed plan is required.
- Never make up current prices, memory metrics, latency results, or cloud capacity.
""".strip()

TOOL_CONFIG = {
    "tools": [
        {"toolSpec": {
            "name": "get_workload_metrics",
            "description": "Read actual CloudWatch CPU, memory and disk metrics for an EC2 instance over the last hour.",
            "inputSchema": {"json": {"type": "object", "properties": {"instance_id": {"type": "string", "description": "EC2 instance ID"}}, "required": ["instance_id"]}},
        }},
        {"toolSpec": {
            "name": "list_compute_capacity",
            "description": "List currently running EC2 instances in the configured AWS region.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }},
        {"toolSpec": {
            "name": "discover_instance_type_options",
            "description": "Read current EC2 instance-type offerings in the workload target's Availability Zone, get live type specs (vCPUs, memory, network), and estimate Linux on-demand price. This checks offerings, not spare capacity; it does not launch or resize resources.",
            "inputSchema": {"json": {"type": "object", "properties": {"instance_id": {"type": "string", "description": "Target workload EC2 instance ID"}}, "required": ["instance_id"]}},
        }},
        {"toolSpec": {
            "name": "get_cost_context",
            "description": "Read account month-to-date Cost Explorer costs and an AWS Price List compute-only monthly estimate for currently running EC2 instances.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }},
        {"toolSpec": {
            "name": "get_cloud_connections",
            "description": "List sanitized saved AWS, Azure and GCP connection identities and their last tested status. This does not refresh inventory; call get_connected_provider_inventory with a listed connection_id for fresh live resource data. Never expose credentials or secret ARNs.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }},
        {"toolSpec": {
            "name": "get_connected_provider_inventory",
            "description": "Read a fresh, bounded sample of real resources from one previously listed CONNECTED AWS, Azure or GCP account connection. This is read-only inventory; it never provisions or changes resources. Use only a connection_id returned by get_cloud_connections.",
            "inputSchema": {"json": {"type": "object", "properties": {"connection_id": {"type": "string", "description": "Exact connection_id returned by get_cloud_connections"}}, "required": ["connection_id"]}},
        }},
        {"toolSpec": {
            "name": "get_connected_aws_workload_metrics",
            "description": "Fetch fresh EC2 CPU, memory, disk and network telemetry from CloudWatch for an instance in a previously listed CONNECTED AWS account. Supply the exact connection_id and instance_id found in live inventory. Read-only; no infrastructure changes.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "connection_id": {"type": "string", "description": "Exact AWS connection_id returned by get_cloud_connections"},
                "instance_id": {"type": "string", "description": "EC2 instance ID returned by live inventory"}
            }, "required": ["connection_id", "instance_id"]}},
        }},
        {"toolSpec": {
            "name": "get_deployment_status",
            "description": "Read a sanitized list of recent Intelligent Provisioning Platform Terraform deployment states. Never applies, destroys or changes infrastructure.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }},
        {"toolSpec": {
            "name": "compare_public_cloud_pricing",
            "description": "Query public AWS, Azure Retail Prices, and Google Cloud Billing Catalog prices for comparable Linux VM profile options. Azure/GCP are pricing only; no account telemetry or provisioning is connected for those providers.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "min_vcpus": {"type": "integer", "minimum": 1},
                "min_memory_gib": {"type": "number", "minimum": 0.5},
                "strategy": {"type": "string", "enum": ["Lowest Cost", "Balanced", "Lowest Latency", "Best Fit for Workload"]},
                "max_monthly_budget_usd": {"type": "number", "minimum": 0},
                "aws_region": {"type": "string"}, "azure_region": {"type": "string"}, "gcp_region": {"type": "string"},
                "aws_connection_id": {"type": "string", "description": "Optional verified AWS connection_id from get_cloud_connections"}
            }, "required": []}},
        }},
    ]
}


# Provider-neutral tool specs are validated by our code and then translated to each LLM API.
TOOL_SPECS = [
    {
        "name": tool["toolSpec"]["name"],
        "description": tool["toolSpec"].get("description", ""),
        "parameters": tool["toolSpec"].get("inputSchema", {}).get("json", {"type": "object", "properties": {}}),
    }
    for tool in TOOL_CONFIG["tools"]
]
TOOL_SPEC_BY_NAME = {tool["name"]: tool for tool in TOOL_SPECS}


@dataclass
class AgentTurnResult:
    status: str
    provider: str
    model_id: str
    region: str | None
    answer: str
    tool_calls: list[dict[str, Any]]
    execution_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_tool_args(name: str, args: Any) -> dict[str, Any]:
    """Fail closed for unknown tools, malformed arguments and out-of-range values."""
    if name not in TOOL_SPEC_BY_NAME:
        raise ValueError(f"Tool {name!r} is not on Intelligent Provisioning Platform's read-only allowlist.")
    if not isinstance(args, dict):
        raise ValueError("Tool input must be a JSON object.")
    schema = TOOL_SPEC_BY_NAME[name]["parameters"]
    required = schema.get("required", [])
    for field in required:
        if field not in args:
            raise ValueError(f"Required tool argument {field!r} is missing.")
    unknown = set(args) - set(schema.get("properties", {}))
    if unknown:
        raise ValueError(f"Unsupported tool argument(s): {', '.join(sorted(unknown))}.")
    for field, value in args.items():
        prop = schema.get("properties", {}).get(field, {})
        expected = prop.get("type")
        if expected == "string" and not isinstance(value, str):
            raise ValueError(f"Tool argument {field!r} must be a string.")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"Tool argument {field!r} must be an integer.")
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"Tool argument {field!r} must be numeric.")
        if "minimum" in prop and float(value) < float(prop["minimum"]):
            raise ValueError(f"Tool argument {field!r} must be at least {prop['minimum']}.")
        if "enum" in prop and value not in prop["enum"]:
            raise ValueError(f"Tool argument {field!r} must be one of the documented values.")
    for field in ("instance_id",):
        if field in args and not re.fullmatch(r"i-[0-9a-fA-F]{8,17}", args[field]):
            raise ValueError("EC2 instance ID must use the expected i-... identifier format.")
    if "connection_id" in args and not re.fullmatch(r"[a-z0-9-]{3,50}", args["connection_id"]):
        raise ValueError("connection_id must be a saved provider connection identifier.")
    for field in ("aws_region", "azure_region", "gcp_region"):
        if field in args and (not args[field] or len(args[field]) > 40 or not re.fullmatch(r"[A-Za-z0-9-]+", args[field])):
            raise ValueError(f"Tool argument {field!r} is not a valid region identifier.")
    if "max_monthly_budget_usd" in args and float(args["max_monthly_budget_usd"]) <= 0:
        raise ValueError("max_monthly_budget_usd must be greater than zero when supplied.")
    return dict(args)


def _llm_provider_label() -> str:
    provider = os.getenv("LLM_PROVIDER", "bedrock").strip().lower()
    return {"bedrock": "Amazon Bedrock", "openai": "OpenAI", "anthropic": "Anthropic Claude",
            "gemini": "Google Gemini", "google": "Google Gemini", "local": "Local OpenAI-compatible"}.get(provider, provider)


def _run_tool_loop(initial_text: str, system_prompt: str, target_id: str, max_tool_rounds: int = 4) -> dict[str, Any]:
    history: list[dict[str, Any]] = [{"role": "user", "text": initial_text}]
    tool_calls: list[dict[str, Any]] = []
    last_turn: dict[str, Any] = {}
    for _ in range(max_tool_rounds + 1):
        last_turn = complete_turn(history, system_prompt, TOOL_SPECS, max_tokens=1800, temperature=0.2)
        calls = last_turn.get("tool_calls", [])
        text = str(last_turn.get("text", "") or "")
        history.append({"role": "assistant", "text": text, "tool_calls": calls})
        if not calls:
            result = AgentTurnResult(
                status="completed", provider=str(last_turn.get("provider", _llm_provider_label())),
                model_id=str(last_turn.get("model_id", "unknown")), region=last_turn.get("region"),
                answer=text or "The model returned no text response. Please retry or inspect provider logs.",
                tool_calls=tool_calls, execution_status="READ_ONLY_ONLY",
            ).to_dict()
            result["usage"] = last_turn.get("usage", {})
            result["stop_reason"] = last_turn.get("stop_reason")
            return result
        for call in calls:
            name = str(call.get("name", ""))
            call_id = str(call.get("id") or f"cloudpilot_{uuid.uuid4().hex[:16]}")
            try:
                args = _validate_tool_args(name, call.get("arguments", {}))
                tool_result = _execute_tool(name, args, target_id)
                status = "success"
            except Exception as exc:
                args = call.get("arguments", {}) if isinstance(call.get("arguments"), dict) else {}
                tool_result, status = {"error": f"{type(exc).__name__}: {exc}"}, "error"
            tool_calls.append({"name": name, "input": args, "status": status})
            history.append({"role": "tool", "tool_call_id": call_id, "name": name,
                            "result": tool_result, "status": status})
    raise RuntimeError("The agent exceeded the maximum tool-call rounds without producing a response.")


def _execute_tool(name: str, args: dict[str, Any], target_id: str) -> dict[str, Any]:
    if name == "get_workload_metrics":
        instance_id = str(args.get("instance_id") or target_id)
        return {"metrics": get_metric_series(instance_id), "instance": get_target_instance_details(instance_id)}
    if name == "list_compute_capacity":
        return list_running_instances()
    if name == "discover_instance_type_options":
        instance_id = str(args.get("instance_id") or target_id)
        return discover_instance_type_options(instance_id)
    if name == "get_cost_context":
        inventory = list_running_instances()
        instances = inventory.get("instances", []) if inventory.get("available") else []
        return {
            "cost_explorer": get_monthly_cost_summary(),
            "running_ec2_monthly_estimate": get_running_ec2_cost_estimate(instances),
            "running_instance_inventory_available": inventory.get("available", False),
        }
    if name == "get_cloud_connections":
        from provider_connections import list_connections
        public_fields = ("connection_id", "provider", "name", "connection_status",
                         "account_id", "subscription_id", "project_id",
                         "last_tested_at", "inventory_source", "updated_at")
        rows = [{key: row.get(key) for key in public_fields if row.get(key) is not None}
                for row in list_connections()]
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "connections": rows,
            "note": "Connection status is based on the last successful test. Query get_connected_provider_inventory for fresh live resource evidence. Credentials and Secrets Manager ARNs are not returned.",
        }
    if name == "get_connected_provider_inventory":
        from provider_connections import configured_inventory, get_connection
        connection_id = str(args.get("connection_id") or "").strip()
        if not connection_id:
            raise ValueError("connection_id is required.")
        connection = get_connection(connection_id)
        if not connection or connection.get("connection_status") != "CONNECTED":
            raise ValueError("Choose a connection_id returned by get_cloud_connections with CONNECTED status.")
        result = configured_inventory(connection_id)
        if not result.get("ok"):
            return {"ok": False, "provider": connection.get("provider"),
                    "connection_name": connection.get("name"),
                    "error": result.get("error", "Live inventory request failed."),
                    "checked_at": result.get("checked_at")}
        safe_resources = []
        for item in result.get("resources", []):
            if not isinstance(item, dict):
                continue
            safe_resources.append({key: item.get(key) for key in (
                "name", "instance_id", "type", "state", "availability_zone",
                "id", "machine_type", "zone", "status", "type", "location"
            ) if item.get(key) is not None})
        return {key: result.get(key) for key in (
            "ok", "provider", "account_id", "identity", "region",
            "subscription_id", "subscription_name", "subscription_state",
            "project_id", "service_account_email", "sampled_resources",
            "sampled_instances", "inventory_source", "checked_at"
        ) if result.get(key) is not None} | {"connection_name": connection.get("name"),
                                                "resources": safe_resources,
                                                "note": "Read-only sample returned by the provider's live inventory API; list limits apply."}
    if name == "get_connected_aws_workload_metrics":
        from provider_connections import configured_aws_metrics, get_connection
        connection_id = str(args.get("connection_id") or "").strip()
        instance_id = str(args.get("instance_id") or "").strip()
        connection = get_connection(connection_id)
        if not connection or connection.get("provider") != "AWS" or connection.get("connection_status") != "CONNECTED":
            raise ValueError("Use a CONNECTED AWS connection_id returned by get_cloud_connections.")
        return configured_aws_metrics(connection_id, instance_id, hours=1)
    if name == "get_deployment_status":
        from terraform_service import list_deployments
        records = list_deployments()
        safe_records = []
        for record in records[:20]:
            config = record.get("requested_configuration") or {}
            safe_records.append({
                "deployment_id": record.get("deployment_id"), "provider": record.get("provider", "AWS"),
                "region": record.get("region"), "status": record.get("status"),
                "created_at": record.get("created_at"), "updated_at": record.get("updated_at"),
                "instance_name": config.get("instance_name"), "instance_type": config.get("instance_type"),
                "plan_changes": record.get("plan_changes"),
            })
        return {"count": len(records), "recent_deployments": safe_records,
                "note": "Read-only status summary. This tool never applies Terraform or destroys resources."}
    if name == "compare_public_cloud_pricing":
        budget = args.get("max_monthly_budget_usd")
        return run_comparison(
            aws_region=str(args.get("aws_region") or os.getenv("AWS_REGION", "ap-south-1")),
            azure_region=str(args.get("azure_region") or os.getenv("AZURE_PRICING_REGION", "centralindia")),
            gcp_region=str(args.get("gcp_region") or os.getenv("GCP_PRICING_REGION", "asia-south1")),
            min_vcpus=int(args.get("min_vcpus", 2)),
            min_memory_gib=float(args.get("min_memory_gib", 8)),
            max_monthly_budget=float(budget) if budget is not None and float(budget) > 0 else None,
            strategy=str(args.get("strategy") or "Balanced"),
            aws_connection_id=str(args.get("aws_connection_id") or "") or None,
        )
    return {"error": f"Tool {name} is not allowed."}


def invoke_agent(event: dict[str, Any], max_tool_rounds: int = 4) -> dict[str, Any]:
    """Reason over a deterministic sustained CPU event using read-only cloud tools."""
    provider = os.getenv("LLM_PROVIDER", "bedrock").strip().lower()
    if provider == "bedrock" and os.getenv("BEDROCK_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("BEDROCK_ENABLED is false. Set it to true to enable the Bedrock LLM agent.")
    region = os.getenv("AWS_REGION", "ap-south-1")
    load_context = json.dumps(event, indent=2, default=str)
    initial_text = (
        "Analyze this sustained CPU-threshold event for the Innominds Intelligent Provisioning Platform POC. "
        "Use get_workload_metrics, list_compute_capacity, discover_instance_type_options, and get_cost_context before preparing the plan. "
        "If trend_signal and suggested_instance_type are present, explicitly evaluate that proposed direction against current metrics and current instance type. "
        "Return evidence-based Markdown.\n\nCPU threshold event:\n" + load_context
    )
    result = _run_tool_loop(initial_text, SYSTEM_PROMPT, os.getenv("TARGET_INSTANCE_ID", ""), max_tool_rounds)
    result["answer"] = sanitize_agent_answer(result.get("answer", ""))
    result["execution_status"] = "NOT_APPLIED"
    result["event_id"] = event.get("event_id")
    preferred_match = re.search(
        r"(?im)^\s*(?:\*\*)?Preferred instance type(?:\*\*)?\s*:\s*`?([a-z0-9.]+|none)`?\s*$",
        result.get("answer", ""),
    )
    proposed = preferred_match.group(1).strip().lower() if preferred_match else None
    # Only expose machine-readable choices in the configured instance type policy.
    configured = {x.strip().lower() for x in os.getenv("CLOUDPILOT_ALLOWED_INSTANCE_TYPES", "t2.micro,t2.small,t2.medium,t2.large,t3.micro,t3.small,t3.medium,t3.large,t3a.micro,t3a.small,t3a.medium,t3a.large,t4g.micro,t4g.small,t4g.medium,t4g.large").split(",") if x.strip()}
    if proposed and proposed != "none" and proposed not in configured:
        result["recommendation_validation_warning"] = "LLM preferred type was not in the configured allowlist and was discarded."
        proposed = None
    result["recommended_instance_type"] = None if proposed == "none" else proposed
    result["recommendation_is_none"] = bool(preferred_match and proposed == "none")
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    return result


CHAT_SYSTEM_PROMPT = """
You are Intelligent Provisioning Platform, a read-only multi-cloud infrastructure advisor. Respect the selected provider and account; never silently switch accounts. Use fresh provider inventory and the comparison evidence supplied with the request. Never invent CPU/RAM, price, account availability, quota, spare capacity or latency. First prefer an eligible exact CPU/RAM match; if none exists, recommend only a configuration meeting or exceeding every hard CPU/RAM requirement. Within that tier, optimize the user's stated priority: measured latency only when real comparable measurements exist; otherwise use verified availability evidence and compute price. If latency is not measured, say so plainly and do not call a region-based guess a latency result. Public Azure/GCP catalog prices are not proof of connected-account capacity or provisioning readiness. Regional AWS offering is not proof of account quota or spare capacity. Explain the selected option, why it beats the next alternatives, the monthly compute estimate, hardware fit, and any evidence limitation. Output 6–8 short, readable lines, with the first line bold and the recommended SKU highlighted. No internal reasoning, analysis, XML tags, preamble or generic filler. Never execute infrastructure changes; Terraform plan, policy checks and explicit human approval are required.
""".strip()


def explain_comparison_evidence(request: str, comparison: dict[str, Any]) -> dict[str, Any]:
    """Ask the configured LLM for a concise explanation of deterministic comparison evidence.

    No tools are supplied: the model can explain the already-retrieved data but cannot
    refresh inventory, alter ranking, or invoke provisioning actions.
    """
    fields = (
        "provider", "region", "sku", "available", "vcpus", "memory_gib",
        "hourly_price", "price_currency", "monthly_compute_estimate",
        "offered_in_target_location", "availability_evidence", "capacity_status",
        "telemetry_status", "price_source", "price_retrieved_at", "price_effective_date",
        "price_publication_date", "price_sku", "price_model", "pricing_api_region",
        "price_cache_status", "price_cache_ttl_seconds", "price_assumptions", "spec_source",
        "hardware_fit", "hardware_match", "latency_ms", "measured_latency_ms",
        "eligibility", "eligibility_reason", "cluster", "node_monthly_compute_estimate",
    )
    evidence = [{key: option.get(key) for key in fields if key in option}
                for option in comparison.get("source_options", [])]
    payload = {
        "user_request": request,
        "requirements": comparison.get("requirements"),
        "strategy": comparison.get("strategy"),
        "recommended": {"provider": comparison.get("recommended_provider"),
                        "sku": comparison.get("recommended_sku")},
        "optimal" if comparison.get("tier_policy") == "progressive_hardware" else "cost_optimal": {"provider": comparison.get("optimal_provider"),
                         "sku": comparison.get("optimal_sku"),
                         "monthly_compute_estimate": comparison.get("optimal_monthly_compute_estimate")},
        "recommendation_reason": comparison.get("recommendation_reason"),
        "tier_policy": comparison.get("tier_policy", "strategy_within_hardware_fit"),
        "provider_tiers": comparison.get("provider_tiers", []),
        "price_freshness": {
            "aws_source": "AWS Price List Query API · AmazonEC2 GetProducts",
            "aws_lookup_runs_on_each_new_compare_request": True,
            "cache_ttl_seconds": comparison.get("price_cache_ttl_seconds"),
            "aws_prices_are_public_on_demand_rates_not_account_specific_bills": True,
        },
        "latency_measured": comparison.get("latency_measured"),
        "options": evidence,
        "notices": comparison.get("notices", []),
    }
    system = (
        "You are Intelligent Provisioning Platform. Explain the supplied deterministic multi-cloud comparison for an engineering manager. "
        "The ranking and numbers are authoritative; do not change them. AWS price rows come from the live AWS Price List Query API lookup performed for this compare request; use the exact returned hourly/monthly rates and price_retrieved_at timestamp. "
        "If AWS price lookup failed or has no price, explicitly say price unavailable and never substitute a sample, screenshot value, cached fixture or guessed rate. Never invent latency, account capacity, "
        "quota, price, SKU specs or service availability. Follow tier_policy and recommendation_reason exactly. "
        "For progressive_hardware, Selectable is exact or nearest fit, Recommended is the next CPU/RAM upgrade, "
        "and Optimal is a further upgrade, not a cheapest-price label. Never repeat the same hardware in these tiers. "
        "For strategy_within_hardware_fit, prefer exact hardware matches. Only recommend profiles meeting both minimums. "
        "Explain hardware headroom without claiming measured performance or latency improvements. "
        "If latency is unmeasured, explicitly state that it is not ranked. Distinguish AWS regional offering from "
        "actual launch capacity. Clearly label Azure/GCP public catalog estimates when account inventory is not verified. "
        "Return exactly 6–8 short Markdown lines, one thought per line. First line must be a bold recommendation. "
        "No internal reasoning, no XML/HTML tags, no preamble, and no claims of resource creation."
    )
    response = complete_turn(
        [{"role": "user", "text": "Explain this comparison using only the evidence below:\n" + json.dumps(payload, indent=2, default=str)}],
        system, [], max_tokens=600, temperature=0.15,
    )
    answer = sanitize_agent_answer(response.get("text", ""))
    # Keep the visual output within the requested executive summary length.
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(lines) < 6:
        requirements = comparison.get("requirements") or {}
        optimal_provider = comparison.get("optimal_provider") or "not available"
        optimal_sku = comparison.get("optimal_sku") or "no eligible option"
        exact_options = [x for x in evidence if x.get("vcpus") == requirements.get("min_vcpus")
                         and x.get("memory_gib") == requirements.get("min_memory_gib")
                         and x.get("available") is True]
        additions = [
            f"• Request: {requirements.get('min_vcpus', 'N/A')} vCPU / {requirements.get('min_memory_gib', 'N/A')} GiB RAM.",
            "• Exact hardware match: found in the retrieved price/spec candidates." if exact_options else "• Exact hardware match: none in the retrieved price/spec candidates; the closest eligible profile meets both minimums.",
            f"• {'Optimal hardware upgrade' if comparison.get('tier_policy') == 'progressive_hardware' else 'Cost-optimal option'}: {optimal_provider} · {optimal_sku}.",
            "• Latency: no comparable live end-to-end measurements are available, so no latency winner is claimed.",
            "• Availability: regional offering is evidence only; account quota and immediate spare capacity remain unverified.",
            "• Next step: select the preferred profile and prepare a policy-checked Terraform plan for human review.",
        ]
        for addition in additions:
            if len(lines) >= 6:
                break
            if addition not in lines:
                lines.append(addition)
    if len(lines) > 8:
        lines = lines[:8]
    answer = "\n".join(lines)
    return {
        "answer": answer,
        "provider": response.get("provider", "unknown"),
        "model_id": response.get("model_id", "unknown"),
        "region": response.get("region"),
        "execution_status": "READ_ONLY_COMPARISON_EXPLANATION",
    }


def invoke_agent_query(query: str, max_tool_rounds: int = 4) -> dict[str, Any]:
    """Run natural-language requests through the selected LLM with read-only tools."""
    if not query or not query.strip():
        raise ValueError("Enter a question for the Intelligent Provisioning Platform agent.")
    provider = os.getenv("LLM_PROVIDER", "bedrock").strip().lower()
    if provider == "bedrock" and os.getenv("BEDROCK_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("BEDROCK_ENABLED is false. Set it to true to enable the Bedrock LLM agent.")
    initial_text = (
        "User request: " + query.strip() + "\n\n"
        "Use read-only tools for current AWS evidence and public pricing where relevant. "
        "Do not run or claim an infrastructure change. If a value is missing, state that it is unavailable."
    )
    result = _run_tool_loop(initial_text, CHAT_SYSTEM_PROMPT, os.getenv("TARGET_INSTANCE_ID", ""), max_tool_rounds)
    result["answer"] = sanitize_agent_answer(result.get("answer", ""))
    result["execution_status"] = "READ_ONLY_ONLY"
    return result

