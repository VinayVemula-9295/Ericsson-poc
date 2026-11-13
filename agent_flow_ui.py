"""Prompt, cloud choices and explicit saved-plan approval for Agent."""
from __future__ import annotations

import hashlib
import json
import os
import streamlit as st
import cloud_regions
from concurrent.futures import ThreadPoolExecutor

from infrastructure_request import parse_infrastructure_request
from placement import apply_constraints, size_cluster, explicit_cluster
from poc_audit import event
from poc_ui import catalogue_options
from provider_connections import list_connections, get_connection, get_aws_terraform_runtime
from recommendation_engine import compare_options, progressive_choices


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def clear_result():
    for key in ("poc-result", "poc-selected", "poc-saved-plan", "poc-explanation", "poc-input-fingerprint", "poc-open-approval", "poc-open-success", "poc-editing", "poc-choice", "iac-draft", "iac-validation", "draft-input-hash"):
        st.session_state.pop(key, None)


def clear_draft_state():
    for key in ("poc-saved-plan", "poc-open-plan", "poc-open-approval", "poc-open-success",
                "iac-draft", "iac-validation", "draft-input-hash", "terraform-code",
                "poc-input-fingerprint"):
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if key.startswith(('tf-', 'terraform-editor-')):
            st.session_state.pop(key, None)
    for key in ('tf-live-inputs', 'tf-live-key', 'provisioning-initiator'):
        st.session_state.pop(key, None)


def edit_plan():
    st.session_state.pop("iac-validation", None)
    st.session_state.pop("poc-saved-plan", None)
    st.session_state.pop("poc-open-plan", None)
    st.session_state.pop("poc-open-approval", None)
    st.session_state["poc-editing"] = True


def request_plan_approval():
    st.session_state["poc-open-approval"] = True


def _choose_prompt():
    prompt = st.session_state.get("poc-prompt-preset")
    if prompt:
        st.session_state["poc-prompt"] = prompt


def explain(request, comparison):
    """Explain once per request and keep sensitive prompts out of external AI."""
    tiers = comparison.get("provider_tiers")
    if tiers:
        lines = []
        provider, sku = comparison.get("recommended_provider"), comparison.get("recommended_sku")
        lines.append(f"**Recommendation: {provider} {sku}**" if sku else "**No eligible recommendation is available.**")
        if request.get("worker_count"):
            lines.append(f"**Your cluster:** {request['worker_count']} workers, each requiring at least {request['min_vcpus']} vCPU and {request['min_memory_gib']:g} GiB RAM.")
        for tier in ("ASK", "Recommended", "Optimal"):
            pool = [choices[tier] for choices in tiers if choices.get(tier)]
            if pool:
                row = min(pool, key=lambda r: (r["monthly_compute_estimate"], r["provider"], r["sku"]))
                lines.append(f"**{tier}:** {row['provider']} {row['sku']} · {row['vcpus']} vCPU / {row['memory_gib']:g} GiB · ${row['monthly_compute_estimate']:.2f}/month compute.")
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
        "ASK preserves the requested CPU/RAM; unavailable exact matches are identified. "
        "Recommended is the nearest eligible hardware upgrade; Optimal adds another CPU/RAM upgrade. "
        "Neither upgrade reduces CPU or memory. Prices break ties; budgets and mandatory constraints still apply. "
        "When no upgrade exists, the tier is unavailable.")
    comparison["tier_policy"] = "progressive_hardware"
    comparison["provider_tiers"] = upgrades
    comparison.update(source_options=constrained, requested_os=request["os"], requested_prompt=request["prompt"])
    st.session_state["poc-result"] = (request, comparison, constrained, errors)
    st.session_state["poc-explanation"] = explain(request, comparison)
    event("REQUEST_EVALUATED", status="OPTIONS_RETURNED" if options else "EVIDENCE_UNAVAILABLE")


def tier_choices(rows, request):
    choices = progressive_choices(rows, request["min_vcpus"], request["min_memory_gib"],
                                  request.get("max_monthly_budget_usd"), request["strategy"])
    result = []
    for tier, row in choices:
        label = 'ASK' if tier == 'Selectable' else tier
        if label == 'ASK' and request.get('requested_sku'):
            eligible = compare_options(rows, request['min_vcpus'], request['min_memory_gib'],
                                       request.get('max_monthly_budget_usd'), request['strategy'])['eligible_options']
            row = next((r for r in eligible if r.get('sku') == request['requested_sku'] and
                        r.get('vcpus') == request['min_vcpus'] and r.get('memory_gib') == request['min_memory_gib']), None)
        if label == 'ASK' and row and (row['vcpus'] != request['min_vcpus'] or row['memory_gib'] != request['min_memory_gib']):
            row = None
        result.append((label, row))
    return result


def execution_restriction(row):
    if row.get("provider") != "AWS":
        return f"{row.get('provider')} plan and apply are not enabled in this release."
    from policy_engine import validate_aws_launch
    try:
        validate_aws_launch(row["region"], row["sku"])
    except ValueError as exc:
        return str(exc)
    return None


def render():
    context = (st.session_state.get("iipp_active_provider"), st.session_state.get("iipp_active_connection_id"))
    changed_context = st.session_state.get("poc-context") != context
    if changed_context:
        clear_result()
        st.session_state["poc-context"] = context
    pending = st.session_state.pop('reuse-pending', None)
    if pending:
        clear_result()
        st.session_state['reuse-source'] = pending
        st.session_state['provisioning-mode'] = 'Use Saved Configuration'
        st.session_state['poc-form-version'] = st.session_state.get('poc-form-version', 0) + 1
    from control_plane import enabled as governed
    if st.session_state.pop('cp-edit-requirements', False):
        st.session_state['provisioning-mode'] = 'New Deployment'
        clear_result()
    if st.session_state.get('provisioning-mode') not in {'New Deployment', 'Use Saved Configuration'}:
        st.session_state['provisioning-mode'] = 'New Deployment'
    mode = st.radio('Infrastructure Provisioning', ['New Deployment', 'Use Saved Configuration'], horizontal=True, key='provisioning-mode',
                    format_func=lambda value: 'Use Existing Server Configurations' if value == 'Use Saved Configuration' else value)
    if mode == 'Use Saved Configuration':
        from reuse_ui import render as render_reuse
        render_reuse()
        return
    st.session_state.pop('reuse-draft', None)
    connections = [c for c in list_connections() if c.get("connection_status") == "CONNECTED"]
    def load_regions(provider):
        try:
            return cloud_regions.load_catalogue(provider)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=3) as executor:
        catalogues = dict(zip(("AWS", "Azure", "GCP"), executor.map(load_regions, ("AWS", "Azure", "GCP"))))
    st.markdown("## Describe workload and constraints")
    templates = [
        'Need a Windows VM with 2 vCPU and 4 GiB RAM',
        'Create a Linux VM with 4 vCPU and 16 GiB RAM.',
        'create a Windows VM with 4 vCPU and 16 GiB RAM.',
        'Create a Kubernetes cluster with 2 Linux workers, each with 4 vCPU and 16 GiB RAM',
    ]
    st.selectbox('Choose Predefined Prompts', templates, index=None,
                 placeholder='Need a Windows VM with 2 vCPU and 4 GiB RAM',
                 key='poc-prompt-preset', label_visibility='collapsed', on_change=_choose_prompt)
    with st.form("agent-prompt", border=False):
        st.markdown("### Select Region")
        regions, contexts = st.columns(3), []
        for column, (provider, default) in zip(regions, (("AWS", "ap-south-1"), ("Azure", "centralindia"), ("GCP", "asia-south1"))):
            with column:
                catalogue = catalogues[provider]
                rows = catalogue["regions"] if catalogue else []
                values = [r["value"] for r in rows]
                labels = {r["value"]: r["label"] for r in rows}
                region_key = "poc-region-" + provider
                if st.session_state.get(region_key) not in values:
                    st.session_state[region_key] = default if default in values else next(iter(values), None)
                region = st.selectbox(provider + " Region", values or [None], key=region_key,
                                      format_func=lambda value, names=labels: names.get(value, "Unavailable"),
                                      disabled=not values)
                if not catalogue:
                    st.caption("Official region list unavailable. This provider will be skipped; reload to retry.")
                    continue
                matching = [c for c in connections if c["provider"] == provider]
                preferred_connection = st.session_state.get('reuse-draft', {}).get('connection_id') or context[1]
                conn = next((c for c in matching if c["connection_id"] == preferred_connection), next(iter(matching), {}))
                zone = conn.get("metadata", {}).get("default_zone", "")
                if provider == "GCP":
                    zones = next(r["zones"] for r in rows if r["value"] == region)
                    zone = zone if zone in zones else zones[0]
                contexts.append((provider, region.strip(), zone, conn.get("connection_id")))
        with st.container(key="agent-composer"):
            prompt_column, send_column = st.columns([8, 1], vertical_alignment="center")
            prompt_column.text_area("Describe your infrastructure requirements", key="poc-prompt", height=90,
                                   label_visibility="collapsed", placeholder="Example: I need 2 vCPU and 1 GiB RAM Linux server")
            with send_column:
                submit = st.form_submit_button("Evaluate", icon=":material/arrow_upward:", type="primary")
    if submit:
        clear_result()
        try:
            prompt = st.session_state["poc-prompt"].strip()
            if not prompt:
                raise ValueError("Describe your infrastructure requirements first.")
            if not contexts:
                raise ValueError("No official region catalogues are available. Reload and try again.")
            request = parse_infrastructure_request(prompt)
            if st.session_state.get('reuse-draft') and prompt == st.session_state['reuse-draft']['prompt']:
                request['requested_sku'] = st.session_state['reuse-draft'].get('requested_sku')
            request.update(environment="poc", ttl_days=2, latency_evidence=[])
            if request["requires_residency_confirmation"] and not request["residency_regions"]:
                raise ValueError("Include permitted region IDs in your prompt for this residency requirement.")
            if request["kind"] == "Kubernetes" and request["os"] == "Windows":
                raise ValueError("Managed Kubernetes currently requires Linux workers.")
            with st.spinner("Finding configuration"):
                collect(request, contexts, (request["pod_cpu"], request["pod_memory_gib"],
                                            request["replicas"], request["tolerate_node_failure"]))
        except (ValueError, KeyError, TypeError) as exc:
            st.error(str(exc))
    saved = st.session_state.get("poc-result")
    if not saved:
        return
    request, comparison, options, errors = saved
    if governed():
        with st.expander('Evaluated requirements'):
            st.write({k: request.get(k) for k in ('kind', 'os', 'min_vcpus', 'min_memory_gib', 'budget_monthly', 'worker_count', 'residency_regions') if request.get(k) is not None})
            st.caption('Evaluation discovers options and estimates only. Infrastructure is created after plan review and independent approval.')
    st.markdown("### Requested workload")
    st.info(f"{request['os']} {request['kind']} · {request['min_vcpus']} vCPU / {request['min_memory_gib']:g} GiB RAM" +
            (f" · {request['worker_count']} workers" if request.get('worker_count') else ""))
    st.markdown(st.session_state["poc-explanation"])
    for error in errors:
        st.warning(error)
    if request["defaults_used"]:
        st.caption("Inferred requirements: " + "; ".join(request["defaults_used"]))
    selected_now = st.session_state.get("poc-selected") or {}
    selected_signature = (selected_now.get("provider"), selected_now.get("sku"), selected_now.get("region"))
    for provider in ("AWS", "Azure", "GCP"):
        st.markdown(f"### {provider}")
        provider_rows = [r for r in options if r["provider"] == provider]
        choices = tier_choices(provider_rows, request)
        for column, (tier, evidence_row) in zip(st.columns(3), choices):
            illustrative = evidence_row is None and provider in {"Azure", "GCP"}
            row = evidence_row
            if illustrative:
                # Workload-derived display sample only; never used for pricing, selection or execution.
                cpu, ram = request["min_vcpus"], request["min_memory_gib"]
                if tier == "Recommended":
                    cpu, ram = max(cpu + 1, int(cpu * 1.25 + .999)), max(ram + 1, round(ram * 1.25, 1))
                elif tier == "Optimal":
                    cpu, ram = max(cpu + 2, int(cpu * 1.5 + .999)), max(ram + 2, round(ram * 1.5, 1))
                row = {"provider": provider, "sku": "Illustrative sample", "region": "Not verified",
                       "vcpus": cpu, "memory_gib": ram, "os": request["os"],
                       "monthly_compute_estimate": None, "illustrative": True}
            with column:
                signature = (provider, row.get("sku"), row.get("region")) if row else None
                selected_card = signature is not None and signature == selected_signature and not illustrative
                card_key = f"agent-config-card-{provider.lower()}-{tier.lower()}"
                if selected_card:
                    st.markdown(f'<style>.st-key-{card_key} [data-testid="stVerticalBlockBorderWrapper"]{{border:2px solid #633bd3!important;box-shadow:0 0 0 3px #633bd31a}}</style>', unsafe_allow_html=True)
                with st.container(border=True, key=card_key):
                    st.markdown("#### " + tier)
                    if selected_card:
                        st.success("Selected configuration")
                    if row is None:
                        st.info("No verified configuration is available for this request.")
                        st.caption("The provider returned no eligible option; no sample is shown for AWS.")
                        continue
                    if illustrative:
                        st.warning("Illustrative only · not live-verified")
                        st.markdown("**Sample configuration**")
                    else:
                        st.markdown("**" + str(row.get("sku") or "Configuration") + "**")
                    if request["kind"] == "Kubernetes":
                        st.markdown("**" + {"AWS": "Amazon EKS", "Azure": "Azure AKS", "GCP": "Google GKE"}[provider] + "**")
                    st.write(f"{row['vcpus']} vCPU · {row['memory_gib']:g} GiB · {row['os']}")
                    st.caption(row.get("region", "Region unavailable"))
                    st.caption({"ASK": "Requested capacity; exact matches only",
                                "Recommended": "Balanced eligible configuration",
                                "Optimal": "Lowest-priced eligible configuration"}[tier])
                    if request["kind"] == "Kubernetes" and not illustrative:
                        cluster = row.get("cluster", {})
                        st.write(f"{cluster.get('node_count', '—')} worker nodes · CPU/RAM shown per node")
                    price = row.get("monthly_compute_estimate")
                    st.metric("Estimated compute cost / month", f"${price:.2f}" if isinstance(price, (int, float)) else "Unavailable")
                    if illustrative:
                        st.caption("Sample values are derived from your request. No provider capacity or price was verified.")
                    elif price is None:
                        st.caption("Pricing is unavailable from the configured pricing source.")
                    elif provider in {"Azure", "GCP"}:
                        st.caption("Public catalogue estimate; not proof of connected-account capacity.")
                    else:
                        st.caption("Monthly compute estimate; other infrastructure charges may apply.")
                    restriction = execution_restriction(row)
                    if restriction:
                        st.caption(restriction)
                    enabled = not illustrative and not selected_card
                    if st.button("Use This Configuration", key=f"use-{provider}-{tier}",
                                 use_container_width=True, disabled=not enabled,
                                 help="Sample cards cannot be selected." if illustrative else restriction):
                        previous_selection = st.session_state.get("poc-selected")
                        try:
                            with st.spinner("Finding configuration"):
                                clear_draft_state()
                                st.session_state["poc-selected"] = dict(row)
                                st.session_state["poc-form-version"] = st.session_state.get("poc-form-version", 0) + 1
                                st.session_state['poc-configuration-notice'] = True
                                st.session_state['poc-scroll-to-inputs'] = True
                                event("CONFIGURATION_SELECTED", provider=provider, sku=row["sku"], region=row["region"])
                        except Exception:
                            if previous_selection is None:
                                st.session_state.pop("poc-selected", None)
                            else:
                                st.session_state["poc-selected"] = previous_selection
                            st.session_state.pop('poc-configuration-notice', None)
                            st.session_state.pop('poc-scroll-to-inputs', None)
                            st.error("Configuration selection failed. Choose the configuration again or refresh discovery.")
    selected = st.session_state.get("poc-selected")
    if selected:
        if st.session_state.pop('poc-configuration-notice', False):
            st.info('Fill in the form below to generate your infrastructure configuration.')
        if st.session_state.pop('poc-scroll-to-inputs', False):
            st.html("<script>window.parent.document.getElementById('infrastructure-configuration-inputs')?.scrollIntoView({behavior:'smooth',block:'start'});</script>", unsafe_allow_javascript=True)
        render_inputs(selected, request)


def render_inputs(selected, request, reuse_from=None):
    from poc_terraform import configuration
    from provider_inputs import discover, compatible_choices
    st.markdown('<div id="infrastructure-configuration-inputs"></div>', unsafe_allow_html=True)
    st.subheader("Infrastructure Configuration Inputs")
    restriction = execution_restriction(selected)
    if restriction:
        for key in ("poc-saved-plan", "poc-open-plan", "poc-open-approval", "poc-open-success"):
            st.session_state.pop(key, None)
        st.warning("You can edit this configuration. Planning and deployment are restricted: " + restriction)
        st.caption(restriction)
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
    reused_connection = st.session_state.get('reuse-draft', {}).get('connection_id')
    preferred = reused_connection if reused_connection in ids else selected.get("connection_id") if selected.get("connection_id") in ids else active if active in ids else ids[0]
    cid = st.selectbox(selected["provider"] + " account", ids, index=ids.index(preferred),
                       format_func=lambda value: names[value], key=f"tf-account-{version}-{selected['provider']}")
    conn = get_connection(cid)
    if not conn or conn.get("provider") != selected["provider"] or conn.get("connection_status") != "CONNECTED":
        st.session_state.pop("poc-saved-plan", None)
        st.error("This account is no longer connected. Test it in Configure.")
        return
    selected, request = dict(selected, connection_id=cid), dict(request)
    inputs = {}
    saved = st.session_state.get('reuse-draft', {})
    if reuse_from and (cid != saved.get('connection_id') or selected['region'] != saved.get('region')):
        st.session_state['reuse-draft'] = dict(saved, inputs={'name': saved.get('inputs', {}).get('name', '')})
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
    if st.session_state.get("tf-live-key") != evidence_key:
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
        st.warning("Live values unavailable for: " + ", ".join(n.replace("_", " ") for n in evidence["errors"]) + ". Check permissions and test the account in Configure.")
        for field, error in evidence["errors"].items():
            st.caption(field.replace("_", " ") + ": " + str(error))
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
            manual_arn = name in {"cluster_role_arn", "node_role_arn", "kms_key_arn"}
            choices = compatible_choices(name, evidence, inputs)
            widget_key = f"tf-input-{evidence_key}-{name}"
            column = columns[index % 2]
            if manual_arn and choices:
                if column.checkbox("Enter " + label + " manually", key=widget_key + "-override"):
                    choices = []
            if choices:
                values = [r["value"] for r in choices]
                names_by_id = {r["value"]: r["label"] for r in choices}
                if st.session_state.get(widget_key) not in values:
                    st.session_state.pop(widget_key, None)
                saved_value = st.session_state.get('reuse-draft', {}).get('inputs', {}).get(name)
                if widget_key not in st.session_state and saved_value in values:
                    st.session_state[widget_key] = saved_value
                inputs[name] = column.selectbox(label, values, format_func=lambda v, names=names_by_id: names[v], key=widget_key)
            else:
                inputs[name] = column.text_input(label,
                          value=selected.get("zone", "") if name == "zone" else str(st.session_state.get("reuse-draft", {}).get("inputs", {}).get(name, spec.get("default", ""))),
                          key=widget_key + "-manual").strip()
                if name in evidence.get("choices", {}) or name in evidence.get("errors", {}):
                    column.caption("No compatible live values returned. Planning requires live account validation.")
        if request["kind"] == "Kubernetes" and request.get("worker_count"):
            request["worker_count"] = st.number_input("Worker nodes", 1, 1000, request["worker_count"], key=f"tf-workers-{version}-{cid}", disabled=bool(reuse_from))
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
    inputs["region"] = selected["region"]
    from terraform_draft import generate, validate
    if reuse_from:
        from reuse_ui import plan_controls
        plan_controls(selected, request, inputs, reuse_from)
        return
    input_hash = fingerprint((selected, request, inputs, conn.get('last_tested_at')))
    if st.session_state.get('draft-input-hash') != input_hash:
        for key in ('poc-saved-plan', 'poc-open-approval', 'iac-draft', 'iac-validation'):
            st.session_state.pop(key, None)
        st.session_state['draft-input-hash'] = input_hash
    initiator = st.text_input('Initiated by', key='provisioning-initiator', help='User-supplied identity recorded separately from the approver.')
    if st.button('Configure / Terraform Plan', type='primary'):
        st.session_state['iac-draft'] = json.dumps(generate(selected, request, inputs), indent=2)
        st.session_state['terraform-code'] = st.session_state['iac-draft']
        st.session_state.pop('iac-validation', None)
    if 'iac-draft' not in st.session_state:
        return
    st.caption('Terraform configuration and input values have been generated. If you are familiar with Terraform, you can edit the script; otherwise, leave it unchanged and proceed to the next step.')
    from streamlit_ace import st_ace
    st.markdown('#### Optional Terraform script')
    code = st_ace(value=st.session_state['terraform-code'], language='json', theme='tomorrow_night',
                  height=420, font_size=14, tab_size=2, auto_update=True,
                  key='terraform-editor-' + fingerprint(st.session_state['iac-draft']))
    if code is None:
        code = st.session_state['terraform-code']
    st.session_state['terraform-code'] = code
    code_hash = fingerprint((code, initiator))
    if st.session_state.get('iac-validation') != code_hash:
        st.session_state.pop('iac-validation', None)
        st.session_state.pop('poc-saved-plan', None)
        st.session_state.pop('poc-open-approval', None)
    if st.button('Validate Configuration'):
        try:
            validate(code, selected, request, inputs)
            st.session_state['iac-validation'] = code_hash
            st.success('Terraform configuration has been validated successfully.')
        except ValueError as exc:
            st.session_state.pop('iac-validation', None)
            st.session_state.pop('poc-saved-plan', None)
            st.session_state.pop('poc-open-approval', None)
            st.error(str(exc))
    payload_hash = fingerprint((input_hash, code, initiator))
    st.session_state['poc-input-fingerprint'] = payload_hash
    if st.session_state.get('iac-validation') == code_hash and st.button(
            'Generate Terraform Plan', type='primary', key='generate-terraform-plan',
            disabled=bool(restriction) or not initiator.strip()):
        try:
            draft = validate(code, selected, request, inputs)
            from poc_planner import prepare
            with st.spinner('Validating configuration and generating a saved plan…'):
                record = prepare(selected, dict(request, initiated_by=initiator.strip(),
                    source_deployment=st.session_state.get('reuse-draft', {}).get('source_deployment')),
                    draft['inputs'], request.get('latency_evidence', []))
            st.session_state['poc-saved-plan'] = record
            st.session_state['poc-open-plan'] = True
        except ImportError:
            st.error('Terraform planning requires the Linux application runner.')
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            detail = str(exc)
            if any(token in detail.lower() for token in ('accessdenied', 'unauthorizedoperation', 'not authorized to perform', 'no identity-based policy allows')):
                st.error('AWS authorization denied the plan: ' + detail[:700])
            else:
                st.error('Planning failed. Review protected runner diagnostics. No infrastructure was applied.')
    record = st.session_state.get("poc-saved-plan")
    if record:
        st.caption("Saved plan · " + record["deployment_id"] + " · " + record["status"])
        from dialog_guard import claim_dialog
        if st.button("View Terraform plan"):
            st.session_state["poc-open-plan"] = True
        if record["status"] == "SUCCEEDED":
            st.success("Infrastructure created successfully.")
        elif record["status"] == "FAILED":
            st.error("Apply failed. Review Terraform state and runner diagnostics before planning another change.")
        elif st.button("Terraform apply", type="primary", key="terraform-apply"):
            st.session_state["poc-open-approval"] = True
        if st.session_state.get("poc-open-approval") and claim_dialog():
            approval_dialog(record["deployment_id"], payload_hash)
        if st.session_state.get("poc-open-plan") and claim_dialog():
            st.session_state.pop("poc-open-plan", None)
            plan_dialog(record)
        elif st.session_state.get("poc-open-success") and claim_dialog():
            st.session_state.pop("poc-open-success", None)
            success_dialog(record)


@st.dialog("Terraform plan", width="large")
def plan_dialog(record):
    st.code(record["plan_summary"], language="hcl")
    st.caption("Plan checksum: " + record["plan_sha256"])
    close, spacer, actions = st.columns([1, 4, 2])
    edit, apply = actions.columns(2, gap="small")
    if close.button("Close", key="close-terraform-plan"):
        st.rerun()
    if edit.button("Edit", key="edit-terraform-plan", on_click=edit_plan, use_container_width=True, disabled=record.get("status") != "AWAITING_APPROVAL"):
        st.rerun()
    if apply.button("Apply", key="apply-terraform-plan", type="primary", on_click=request_plan_approval, use_container_width=True,
                    disabled=record.get("status") != "AWAITING_APPROVAL"):
        st.rerun()


def _reset_new_deployment():
    preserved = {key: st.session_state.get(key) for key in (
        'iipp-auth', 'control-plane-user', 'demo-profile', 'iipp_active_provider',
        'iipp_active_connection_id', 'iipp_provider_after_save', 'iipp_connection_after_save')}
    for key in list(st.session_state):
        if key.startswith(('poc-', 'iac-', 'tf-', 'reuse-', 'terraform-editor-')) or key in {
            'terraform-code', 'tf-live-inputs', 'tf-live-key', 'draft-input-hash',
            'poc-input-fingerprint', 'provisioning-initiator'}:
            st.session_state.pop(key, None)
    st.session_state.update({key: value for key, value in preserved.items() if value is not None})
    st.session_state['provisioning-mode'] = 'New Deployment'


@st.dialog("Resources created successfully", width="large", on_dismiss=_reset_new_deployment)
def success_dialog(record):
    st.success("The Terraform Apply completed successfully.")
    details = dict(record.get("resource_details", {}))
    details.setdefault("resource_name", record.get("resource_name", "Unavailable"))
    details["region"] = record.get("candidate", {}).get("region", "Unavailable")
    for key, value in details.items():
        st.write(f"**{key.replace('_', ' ').capitalize()}:** {value or 'Not assigned'}")
    if record.get("outputs_unavailable"):
        st.info("Resource details could not be retrieved. Deployment succeeded; inspect the saved deployment before retrying.")
    st.button("Close", key="close-resource-success", on_click=_reset_new_deployment, type="primary")


@st.dialog("Approve Terraform apply", width="large")
def approval_dialog(deployment_id, payload_hash):
    record = st.session_state.get("poc-saved-plan", {})
    if record.get("deployment_id") != deployment_id or st.session_state.get("poc-input-fingerprint") != payload_hash:
        st.error("Configuration changed. Generate and review a new plan.")
        return
    from control_plane import enabled
    if enabled():
        from control_plane_ui import submission
        source = 'terraform_deployments' if deployment_id.startswith('cp-') else 'poc_plans'
        submission(source, deployment_id)
        return
    from cost_summary import render as render_cost_summary
    render_cost_summary(record)
    st.caption("Apply saved plan " + deployment_id)
    st.code(record["plan_summary"], language="hcl")
    with st.form("terraform-approval", clear_on_submit=True):
        reviewer = st.text_input("Approver name", key="tf-approver")
        token = st.text_input("Approval token", type="password", key="tf-approval-token")
        acknowledge = st.checkbox("I reviewed this exact plan and the displayed cost estimate, including any unavailable pricing notice.")
        apply = st.form_submit_button("Approve and apply", type="primary")
    if apply:
        try:
            if not acknowledge:
                raise ValueError("Review and acknowledge the saved plan first.")
            if deployment_id.startswith('cp-'):
                from approval import validate as validate_approval
                validate_approval(reviewer, token)
                from terraform_service import apply_saved_plan as legacy_apply
                apply_saved_plan = lambda did, who, approval_token: legacy_apply(did, who)
            else:
                from poc_planner import apply_saved_plan
            with st.spinner("Applying the approved Terraform plan…"):
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
