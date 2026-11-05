"""Prompt, cloud choices and explicit saved-plan approval for Agent."""
from __future__ import annotations

import hashlib
import json
import os
import streamlit as st

from infrastructure_request import parse_infrastructure_request
from placement import apply_constraints, size_cluster, explicit_cluster
from poc_audit import event
from poc_ui import catalogue_options
from provider_connections import list_connections, get_connection, get_aws_terraform_runtime
from recommendation_engine import compare_options, progressive_choices


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def clear_result():
    for key in ("poc-result", "poc-selected", "poc-saved-plan", "poc-explanation", "poc-input-fingerprint", "poc-open-approval", "poc-open-success", "poc-editing"):
        st.session_state.pop(key, None)


def edit_plan():
    st.session_state.pop("poc-saved-plan", None)
    st.session_state.pop("poc-open-plan", None)
    st.session_state.pop("poc-open-approval", None)
    st.session_state["poc-editing"] = True


def explain(request, comparison):
    """Explain once per request and keep sensitive prompts out of external AI."""
    tiers = comparison.get("provider_tiers")
    if tiers:
        lines = []
        provider, sku = comparison.get("recommended_provider"), comparison.get("recommended_sku")
        lines.append(f"**Recommendation: {provider} {sku}**" if sku else "**No eligible recommendation is available.**")
        if request.get("worker_count"):
            lines.append(f"**Your cluster:** {request['worker_count']} workers, each requiring at least {request['min_vcpus']} vCPU and {request['min_memory_gib']:g} GiB RAM.")
        for tier in ("Selectable", "Recommended", "Optimal"):
            pool = [choices[tier] for choices in tiers if choices.get(tier)]
            if pool:
                row = min(pool, key=lambda r: (r["monthly_compute_estimate"], r["provider"], r["sku"]))
                lines.append(f"**{tier}:** {row['provider']} {row['sku']} · {row['vcpus']} vCPU / {row['memory_gib']:g} GiB · ${row['monthly_compute_estimate']:.2f}/month compute." +
                             (" Shares the preceding tier because no larger eligible upgrade exists." if row.get("shared_configuration") else ""))
        lines.append("**Selection:** Closest eligible fit, then progressive hardware upgrades. Shared tiers are labeled. Compare all providers below.")
        lines.append("**Evidence:** AWS uses live API pricing when available; Azure/GCP use public catalogues. Storage, network and Kubernetes control-plane charges are excluded. Latency is ranked only with comparable measurements.")
        if request["data_classification"] in {"confidential", "restricted"}:
            lines.append("Sensitive requests use deterministic evidence; external AI is disabled.")
        return "\n\n".join(lines)
    lead = (f"**Recommended: {comparison['recommended_provider']} {comparison['recommended_sku']}**"
            if comparison.get("recommended_sku") else "**No verified recommendation is available.**")
    answer = lead + "\n\n" + comparison["recommendation_reason"]
    allowed = not request.get("processing_regions") or request.get("ai_processing_region") in request["processing_regions"]
    if request["data_classification"] in {"confidential", "restricted"} or not allowed:
        return answer + "\n\nSensitive requests use deterministic evidence; external AI is disabled."
    try:
        from agent import explain_comparison_evidence
        return explain_comparison_evidence(request["prompt"], comparison)["answer"]
    except Exception:
        return answer


def collect(request, contexts, sizing):
    options, errors = [], []
    catalogue_options.clear()
    from aws_data import clear_price_cache
    clear_price_cache()
    for provider, region, zone, connection_id in contexts:
        try:
            rows = catalogue_options(provider, region, zone, request["os"], request["min_vcpus"],
                                     request["min_memory_gib"], connection_id)
            quota = {}
            if provider == "AWS" and connection_id and request.get("require_quota"):
                from poc_catalog import aws_quota
                quota = aws_quota(connection_id, region)
            for raw in rows:
                row = dict(raw, connection_id=connection_id, zone=zone, **quota)
                if request["kind"] == "Kubernetes":
                    try:
                        row["cluster"] = (explicit_cluster(row, request["worker_count"]) if request.get("worker_count")
                                          else size_cluster(row, *sizing))
                        row["node_monthly_compute_estimate"] = row.get("monthly_compute_estimate")
                        row["monthly_compute_estimate"] = row["cluster"]["worker_monthly_usd"]
                    except (KeyError, TypeError, ValueError):
                        row.update(available=False, error="This worker cannot fit a workload pod after system overhead")
                options.append(row)
        except Exception:
            errors.append(f"{provider} evidence unavailable. Check the connection and catalogue permissions.")
    constrained = apply_constraints(options, request, request.get("latency_evidence", []))
    comparison = compare_options(constrained, request["min_vcpus"], request["min_memory_gib"],
                                 request.get("max_monthly_budget_usd"), request["strategy"])
    upgrades = [dict(tier_choices([r for r in constrained if r["provider"] == p], request))
                for p in ("AWS", "Azure", "GCP")]
    for tier, prefix in (("Recommended", "recommended"), ("Optimal", "optimal")):
        pool = [tiers[tier] for tiers in upgrades if tiers[tier]]
        winner = min(pool, key=lambda r: (r["monthly_compute_estimate"], r["provider"], r["sku"])) if pool else None
        comparison[prefix + "_provider"] = winner["provider"] if winner else None
        comparison[prefix + "_sku"] = winner["sku"] if winner else None
        if prefix == "optimal":
            comparison["optimal_monthly_compute_estimate"] = winner["monthly_compute_estimate"] if winner else None
    comparison["recommendation_reason"] = (
        "Selectable matches the requested CPU/RAM, or the closest configuration meeting both minimums. "
        "Recommended is the nearest eligible hardware upgrade; Optimal adds another CPU/RAM upgrade. "
        "Neither upgrade reduces CPU or memory. Prices break ties; budgets and mandatory constraints still apply. "
        "When no upgrade exists, tiers share the preceding eligible configuration.")
    comparison["tier_policy"] = "progressive_hardware"
    comparison["provider_tiers"] = upgrades
    comparison.update(source_options=constrained, requested_os=request["os"], requested_prompt=request["prompt"])
    st.session_state["poc-result"] = (request, comparison, constrained, errors)
    st.session_state["poc-explanation"] = explain(request, comparison)
    event("REQUEST_EVALUATED", status="OPTIONS_RETURNED" if options else "EVIDENCE_UNAVAILABLE")


def tier_choices(rows, request):
    families = {v.strip() for v in os.getenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3").split(",") if v.strip()}
    regions = {v.strip() for v in os.getenv("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1").split(",") if v.strip()}
    rows = [row for row in rows if row.get("provider") != "AWS" or
            (row.get("sku", "").split(".")[0] in families and row.get("region") in regions)]
    choices = progressive_choices(rows, request["min_vcpus"], request["min_memory_gib"],
                                  request.get("max_monthly_budget_usd"), request["strategy"])
    result, previous = [], None
    for tier, row in choices:
        row = row or previous
        result.append((tier, dict(row, shared_configuration=True) if row and row is previous else row))
        previous = row
    return result


def render():
    context = (st.session_state.get("iipp_active_provider"), st.session_state.get("iipp_active_connection_id"))
    changed_context = st.session_state.get("poc-context") != context
    if changed_context:
        clear_result()
        st.session_state["poc-context"] = context
    connections = [c for c in list_connections() if c.get("connection_status") == "CONNECTED"]
    st.markdown("## Describe workload and constraints")
    with st.form("agent-prompt", border=False):
        with st.container(key="agent-composer"):
            prompt_column, send_column = st.columns([8, 1], vertical_alignment="center")
            prompt_column.text_area("Ask Intelligent Provisioning agent", key="poc-prompt", height=90,
                                   label_visibility="collapsed", placeholder="Ask Intelligent Provisioning agent")
            with send_column:
                submit = st.form_submit_button("Send", icon=":material/arrow_upward:", type="primary")
        with st.expander("Advanced options"):
            regions, contexts = st.columns(3), []
            for column, (provider, default) in zip(regions, (("AWS", "ap-south-1"), ("Azure", "centralindia"), ("GCP", "asia-south1"))):
                with column:
                    region = st.text_input(provider + " region", default)
                    matching = [c for c in connections if c["provider"] == provider]
                    conn = next((c for c in matching if c["connection_id"] == context[1]), next(iter(matching), {}))
                    zone = conn.get("metadata", {}).get("default_zone", "")
                    if provider == "GCP" and not zone.startswith(region.strip() + "-"):
                        zone = region.strip() + "-a"
                    contexts.append((provider, region.strip(), zone, conn.get("connection_id")))
    if submit:
        clear_result()
        try:
            prompt = st.session_state["poc-prompt"].strip()
            if not prompt:
                raise ValueError("Describe your infrastructure requirements first.")
            request = parse_infrastructure_request(prompt)
            request.update(environment="poc", ttl_days=2, latency_evidence=[])
            if request["requires_residency_confirmation"] and not request["residency_regions"]:
                raise ValueError("Include permitted region IDs in your prompt for this residency requirement.")
            if request["kind"] == "Kubernetes" and request["os"] == "Windows":
                raise ValueError("Managed Kubernetes currently requires Linux workers.")
            with st.spinner("Finding configurations and explaining provider evidence…"):
                collect(request, contexts, (request["pod_cpu"], request["pod_memory_gib"],
                                            request["replicas"], request["tolerate_node_failure"]))
        except (ValueError, KeyError, TypeError) as exc:
            st.error(str(exc))
    saved = st.session_state.get("poc-result")
    if not saved:
        return
    request, comparison, options, errors = saved
    st.markdown(st.session_state["poc-explanation"])
    for error in errors:
        st.warning(error)
    if request["defaults_used"]:
        st.caption("Inferred requirements: " + "; ".join(request["defaults_used"]))
    st.caption(f"{'Kubernetes Linux workers' if request['kind'] == 'Kubernetes' else request['os'] + ' servers'} · "
               f"{request['min_vcpus']} vCPU · {request['min_memory_gib']:g} GiB RAM "
               + ("per node" if request["kind"] == "Kubernetes" else "per server"))
    st.caption("Public compute estimates exclude storage and networking. Regional offerings do not guarantee capacity. "
               "Azure/GCP prices use public catalogues." +
               (" Kubernetes estimates cover workers; control-plane costs and version compatibility require validation." if request["kind"] == "Kubernetes" else ""))
    tabs = st.tabs(["AWS", "Azure", "GCP"])
    for tab, provider in zip(tabs, ("AWS", "Azure", "GCP")):
        with tab:
            if provider == "AWS":
                st.caption("AWS choices follow the runner's approved instance-family and region policy.")
            for column, (tier, row) in zip(st.columns(3), tier_choices([r for r in options if r["provider"] == provider], request)):
                with column, st.container(border=True):
                    st.markdown("#### " + tier)
                    if row is None:
                        st.caption("No eligible configuration with the required evidence." if tier == "Selectable" else
                                   "No larger eligible configuration is available for this tier.")
                        continue
                    st.markdown("**" + row["sku"] + "**")
                    if row.get("shared_configuration"):
                        st.caption("Shared configuration: no larger eligible upgrade is available.")
                    if request["kind"] == "Kubernetes":
                        st.markdown("**" + {"AWS": "Amazon EKS", "Azure": "Azure AKS", "GCP": "Google GKE"}[provider] + "**")
                    st.write(f"{row['vcpus']} vCPU · {row['memory_gib']:g} GiB · {row['os']}")
                    st.caption(row["region"])
                    st.caption("Same eligible hardware as the preceding tier" if row.get("shared_configuration") else {"Selectable": "Exact or nearest fit to your request",
                                "Recommended": "Next CPU/RAM upgrade from Selectable",
                                "Optimal": "Next CPU/RAM upgrade from Recommended"}[tier])
                    if request["kind"] == "Kubernetes":
                        st.write(f"{row['cluster']['node_count']} worker nodes · CPU/RAM shown per node")
                        price = row.get("node_monthly_compute_estimate")
                        if price is not None:
                            st.caption(f"Per worker compute: ${price:.2f}/month")
                    st.metric("Estimated compute / month", f"${row['monthly_compute_estimate']:.2f}")
                    if row.get("price_source"):
                        st.caption(str(row["price_source"]))
                    if row.get("price_retrieved_at"):
                        st.caption("Price checked (UTC): " + str(row["price_retrieved_at"]))
                    if st.button("Use configuration", key=f"use-{provider}-{tier}", use_container_width=True):
                        st.session_state["poc-selected"] = dict(row)
                        st.session_state.pop("poc-saved-plan", None)
                        st.session_state.pop("poc-input-fingerprint", None)
                        st.session_state["poc-form-version"] = st.session_state.get("poc-form-version", 0) + 1
                        event("CONFIGURATION_SELECTED", provider=provider, sku=row["sku"], region=row["region"])
    selected = st.session_state.get("poc-selected")
    if selected:
        render_inputs(selected, request)


def render_inputs(selected, request):
    from poc_terraform import configuration
    from provider_inputs import discover, compatible_choices
    st.subheader("Terraform inputs")
    if st.session_state.pop("poc-editing", False):
        st.info("Edit the preserved inputs below, then generate and review a new Terraform plan.")
    st.write(f"{selected['provider']} · {selected['sku']} · {selected['region']} · {request['kind']}")
    accounts = [c for c in list_connections() if c.get("provider") == selected["provider"] and c.get("connection_status") == "CONNECTED"]
    if not accounts:
        st.session_state.pop("poc-saved-plan", None)
        st.info("No tested " + selected["provider"] + " account is available. Add and test an account in Configure, then return here.")
        return
    version = st.session_state.get("poc-form-version", 0)
    ids = [c["connection_id"] for c in accounts]
    names = {c["connection_id"]: c.get("name", c["connection_id"]) for c in accounts}
    active = st.session_state.get("iipp_active_connection_id")
    preferred = selected.get("connection_id") if selected.get("connection_id") in ids else active if active in ids else ids[0]
    cid = st.selectbox(selected["provider"] + " account", ids, index=ids.index(preferred),
                       format_func=lambda value: names[value], key=f"tf-account-{version}-{selected['provider']}")
    conn = get_connection(cid)
    if not conn or conn.get("provider") != selected["provider"] or conn.get("connection_status") != "CONNECTED":
        st.session_state.pop("poc-saved-plan", None)
        st.error("This account is no longer connected. Test it in Configure.")
        return
    selected, request = dict(selected, connection_id=cid), dict(request)
    inputs = {}
    metadata = conn.get("metadata", {})
    if selected["provider"] == "AWS":
        try:
            inputs["execution_role_arn"] = get_aws_terraform_runtime(selected["connection_id"])["target_role_arn"]
        except Exception:
            st.error("The selected account needs a verified Terraform execution role in Configure.")
            st.session_state.pop("poc-saved-plan", None)
            return
    elif selected["provider"] == "Azure":
        inputs["subscription_id"] = metadata.get("subscription_id") or conn.get("subscription_id")
    else:
        inputs["project_id"] = metadata.get("project_id") or conn.get("project_id")
    labels = {"name": "Cluster name" if request["kind"] == "Kubernetes" else "VM name",
              "subnet_id": "Subnet ID", "second_subnet_id": "Second subnet ID (different availability zone)",
              "security_group_id": "Security group ID", "image_id": "AMI ID" if selected["provider"] == "AWS" else "Image ID",
              "cluster_role_arn": "Cluster IAM role ARN", "node_role_arn": "Worker node IAM role ARN",
              "kms_key_arn": "KMS key ARN", "kubernetes_version": "Kubernetes version"}
    evidence_key = fingerprint((version, cid, selected["provider"], selected["region"], request["kind"], selected["os"], conn.get("last_tested_at")))
    refresh = st.button("Refresh live values", key="tf-refresh-live")
    if refresh or st.session_state.get("tf-live-key") != evidence_key:
        st.session_state.pop("poc-saved-plan", None)
        # Drop old evidence before a new lookup, so a failure cannot reuse it.
        st.session_state["tf-live-inputs"] = {"choices": {}, "errors": {}}
        st.session_state["tf-live-key"] = evidence_key
        try:
            with st.spinner("Loading live values from the selected account…"):
                st.session_state["tf-live-inputs"] = discover(cid, selected["region"], request["kind"], selected["os"])
        except Exception:
            st.session_state["tf-live-inputs"] = {"choices": {}, "errors": {"account": "Live account lookup failed. Check credentials and read permissions in Configure."}}
    evidence = st.session_state["tf-live-inputs"]
    if evidence.get("retrieved_at"):
        st.caption("Account values checked (UTC): " + evidence["retrieved_at"])
    if evidence.get("errors"):
        st.warning("Live values unavailable for: " + ", ".join(n.replace("_", " ") for n in evidence["errors"]) + ". Check the account's read permissions and refresh.")
        for field, error in evidence["errors"].items():
            st.caption(field.replace("_", " ") + ": " + str(error))
    if selected["provider"] == "AWS":
        with st.expander("Required AWS roles and permissions"):
            st.markdown("""**Inventory role:** EC2 Describe APIs, `eks:DescribeClusterVersions`,
`iam:ListRoles`, `kms:ListKeys`, and `kms:DescribeKey` in the target account.

**Terraform execution role:** permissions to create, describe, tag and manage the
planned EC2 or EKS resources; `iam:PassRole` scoped to the selected cluster and
worker roles; KMS key usage and grants permitted by the key policy. EKS may need
`iam:CreateServiceLinkedRole` restricted to its service names on first use.
The controller separately needs access to the configured S3 state and lock objects.

**Cluster role:** trust `eks.amazonaws.com`; attach `AmazonEKSClusterPolicy` or equivalent.
**Worker role:** trust `ec2.amazonaws.com`; attach `AmazonEKSWorkerNodePolicy`
and `AmazonEC2ContainerRegistryPullOnly` or equivalent. Supply CNI permissions
through a dedicated service-account role or the worker role.

Choose existing roles, two subnets in different availability zones of one VPC,
and an enabled customer-managed symmetric KMS key. Private workers require
working DNS and access to EKS, ECR, S3 and other required services through NAT
or VPC endpoints. Missing prerequisites require administrator setup; Refresh
does not create them. See `iam-plan-inputs-policy.json` for discovery reads.""")
    specs = configuration(selected, request["kind"])["variable"]
    # Ordinary widgets rerun on edits, invalidating the displayed plan immediately.
    with st.container(border=True):
        columns = st.columns(2)
        # Network parents render before children so dependent lists update immediately.
        order = {name: i for i, name in enumerate(("name", "resource_group", "network_id", "subnet_id", "second_subnet_id", "security_group_id", "network_security_group_id", "pod_range_name", "service_range_name"))}
        for index, (name, spec) in enumerate(sorted(specs.items(), key=lambda pair: order.get(pair[0], 100))):
            if name in inputs or spec.get("sensitive") or name == "region":
                continue
            label = labels.get(name, name.replace("_", " ").capitalize())
            choices = compatible_choices(name, evidence, inputs)
            widget_key = f"tf-input-{version}-{cid}-{name}"
            column = columns[index % 2]
            if choices:
                values = [r["value"] for r in choices]
                names_by_id = {r["value"]: r["label"] for r in choices}
                if st.session_state.get(widget_key) not in values:
                    st.session_state.pop(widget_key, None)
                inputs[name] = column.selectbox(label, values, format_func=lambda v, names=names_by_id: names[v], key=widget_key)
            else:
                inputs[name] = column.text_input(label,
                          value=selected.get("zone", "") if name == "zone" else str(spec.get("default", "")),
                          key=widget_key + "-manual").strip()
                if name in evidence.get("choices", {}) or name in evidence.get("errors", {}):
                    column.caption("No compatible live values returned. Planning requires live account validation.")
        if request["kind"] == "Kubernetes" and request.get("worker_count"):
            request["worker_count"] = st.number_input("Worker nodes", 1, 1000, request["worker_count"], key=f"tf-workers-{version}-{cid}")
            node = dict(selected, monthly_compute_estimate=selected.get("node_monthly_compute_estimate"))
            selected["cluster"] = explicit_cluster(node, request["worker_count"])
            selected["monthly_compute_estimate"] = selected["cluster"]["worker_monthly_usd"]
            st.caption(f"Each worker: {selected['vcpus']} vCPU / {selected['memory_gib']:g} GiB RAM. Pod scheduling capacity is not verified.")
        elif request["kind"] == "Kubernetes":
            st.caption("Kubernetes workload sizing")
            sizing = st.columns(3)
            request["replicas"] = sizing[0].number_input("Workload replicas", 1, 100000, request.get("replicas", 3), key=f"tf-replicas-{version}-{cid}")
            request["pod_cpu"] = sizing[1].number_input("CPU request per pod", .01, 128., float(request.get("pod_cpu", .5)), key=f"tf-pod-cpu-{version}-{cid}")
            request["pod_memory_gib"] = sizing[2].number_input("Memory GiB per pod", .01, 4096., float(request.get("pod_memory_gib", 1.)), key=f"tf-pod-memory-{version}-{cid}")
            request["tolerate_node_failure"] = st.checkbox("Tolerate one node failure", value=request.get("tolerate_node_failure", True), key=f"tf-spare-{version}-{cid}")
            try:
                node = dict(selected, monthly_compute_estimate=selected.get("node_monthly_compute_estimate"))
                selected["cluster"] = size_cluster(node, request["pod_cpu"], request["pod_memory_gib"], request["replicas"], request["tolerate_node_failure"])
                selected["monthly_compute_estimate"] = selected["cluster"]["worker_monthly_usd"]
                st.caption(f"Plan sizing: {selected['cluster']['node_count']} worker nodes · {selected['cluster']['pods_per_node']} pods per node")
            except ValueError as exc:
                st.session_state.pop("poc-saved-plan", None)
                st.error(str(exc))
                return
        with st.expander("Advanced deployment settings"):
            request["environment"] = st.selectbox("Deployment environment", ["poc", "test"], key=f"tf-environment-{version}-{cid}")
            request["ttl_days"] = st.number_input("Resource TTL (days)", 1, 30, 2, key=f"tf-ttl-{version}-{cid}")
    inputs["region"] = selected["region"]
    payload_hash = fingerprint((selected, request, inputs, conn.get("last_tested_at")))
    if st.session_state.get("poc-input-fingerprint") != payload_hash:
        st.session_state.pop("poc-saved-plan", None)
        st.session_state["poc-input-fingerprint"] = payload_hash
    if st.button("Generate Terraform plan", type="primary", key="generate-terraform-plan"):
        try:
            if any(not value for value in inputs.values()):
                raise ValueError("Complete all Terraform inputs before generating the plan.")
            from poc_planner import prepare
            with st.spinner("Generating saved Terraform plan…"):
                record = prepare(selected, request, inputs, request.get("latency_evidence", []))
            st.session_state["poc-saved-plan"] = record
            st.session_state["poc-open-plan"] = True
        except ImportError:
            st.error("Terraform planning requires the Linux application runner.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Planning failed. Check the runner, provider permissions and inputs. No infrastructure was applied.")
    record = st.session_state.get("poc-saved-plan")
    if record:
        st.caption("Saved plan · " + record["deployment_id"] + " · " + record["status"])
        if st.button("View Terraform plan"):
            st.session_state["poc-open-plan"] = True
        if record["status"] == "SUCCEEDED":
            st.success("Infrastructure created successfully.")
        elif record["status"] == "FAILED":
            st.error("Apply failed. Review Terraform state and runner diagnostics before planning another change.")
        elif st.button("Terraform apply", type="primary", key="terraform-apply") or st.session_state.get("poc-open-approval"):
            st.session_state["poc-open-approval"] = True
            approval_dialog(record["deployment_id"], payload_hash)
        if st.session_state.pop("poc-open-plan", False):
            plan_dialog(record)
        elif st.session_state.pop("poc-open-success", False):
            success_dialog(record)


@st.dialog("Terraform plan", width="large")
def plan_dialog(record):
    st.code(record["plan_summary"], language="hcl")
    st.caption("Plan checksum: " + record["plan_sha256"])
    edit, close = st.columns(2)
    if edit.button("Edit", key="edit-terraform-plan", on_click=edit_plan, disabled=record.get("status") != "AWAITING_APPROVAL"):
        st.rerun()
    if close.button("Close", key="close-terraform-plan"):
        st.rerun()


@st.dialog("Resources created successfully", width="large")
def success_dialog(record):
    st.success("The approved Terraform plan completed successfully.")
    details = dict(record.get("resource_details", {}))
    details.setdefault("resource_name", record.get("resource_name", "Unavailable"))
    details["region"] = record.get("candidate", {}).get("region", "Unavailable")
    for key, value in details.items():
        st.write(f"**{key.replace('_', ' ').capitalize()}:** {value or 'Not assigned'}")
    if record.get("outputs_unavailable"):
        st.info("Resource details could not be retrieved. Deployment succeeded; inspect the saved deployment before retrying.")
    if st.button("Close", key="close-resource-success"):
        st.rerun()


@st.dialog("Approve Terraform apply", width="large")
def approval_dialog(deployment_id, payload_hash):
    record = st.session_state.get("poc-saved-plan", {})
    if record.get("deployment_id") != deployment_id or st.session_state.get("poc-input-fingerprint") != payload_hash:
        st.error("Configuration changed. Generate and review a new plan.")
        return
    st.caption("Apply saved plan " + deployment_id)
    st.code(record["plan_summary"], language="hcl")
    with st.form("terraform-approval", clear_on_submit=True):
        reviewer = st.text_input("Approver name", key="tf-approver")
        token = st.text_input("Approval token", type="password", key="tf-approval-token")
        acknowledge = st.checkbox("I reviewed this exact Terraform plan and approve creating these resources.")
        apply = st.form_submit_button("Approve and apply", type="primary")
    if apply:
        try:
            if not acknowledge:
                raise ValueError("Review and acknowledge the saved plan first.")
            from poc_planner import apply_saved_plan
            with st.spinner("Applying the approved saved Terraform plan…"):
                result = apply_saved_plan(deployment_id, reviewer, token)
            st.session_state["poc-saved-plan"] = result
            st.session_state.pop("poc-open-approval", None)
            st.session_state["poc-open-success"] = result.get("status") == "SUCCEEDED"
            st.rerun()
        except ImportError:
            st.error("Terraform apply requires the Linux application runner.")
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Apply did not complete. Check the protected runner diagnostics and Terraform state before retrying.")
