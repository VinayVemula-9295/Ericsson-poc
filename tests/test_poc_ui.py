"""Prompt routing, cloud choices, saved-plan dialogs and stale-plan protection."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def page(monkeypatch, tmp_path):
    import provider_connections
    import agent_flow_ui as flow
    import agent
    import aws_data
    import provider_inputs
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    # This UI fixture uses synthetic SKU names; explicitly allow those names.
    monkeypatch.setenv("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "AWS-sku-1,AWS-sku-2,AWS-sku-4")
    monkeypatch.setenv("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1")
    connections = [dict(connection_id=p, provider=p, name=p + "-POC", connection_status="CONNECTED",
                        account_id="123456789012" if p == "AWS" else None,
                        metadata={"subscription_id": "subscription", "project_id": "project"})
                   for p in ("AWS", "Azure", "GCP")]
    lookup = lambda cid: next((c for c in connections if c["connection_id"] == cid), None)
    monkeypatch.setattr(provider_connections, "list_connections", lambda: connections)
    monkeypatch.setattr(provider_connections, "get_connection", lookup)
    monkeypatch.setattr(flow, "list_connections", lambda: connections)
    monkeypatch.setattr(flow, "get_connection", lookup)
    monkeypatch.setattr(flow, "get_aws_terraform_runtime", lambda cid: {"target_role_arn": "target-role"})
    monkeypatch.setattr(aws_data, "clear_price_cache", lambda: None)
    monkeypatch.setattr(provider_inputs, "discover", lambda *a: {"choices": {}, "errors": {}})
    monkeypatch.setattr(agent, "explain_comparison_evidence", lambda *a: {"answer": "Automatic verified explanation"})
    calls = []
    def options(provider, region, zone, os_name, cpu, memory, cid):
        calls.append((provider, cpu, memory, cid))
        return [dict(provider=provider, region=region, sku=provider + "-sku-" + str(factor), os=os_name,
                     vcpus=cpu, memory_gib=memory * factor, available=True, offered_in_target_location=True,
                     price_currency="USD", monthly_compute_estimate=50 * factor, price_source="Test catalogue")
                for factor in (1, 2, 4)]
    options.clear = lambda: None
    monkeypatch.setattr(flow, "catalogue_options", options)
    result = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    result.session_state["navigation"] = "Agent"
    result.run(timeout=20)
    assert not result.exception
    return result, calls


def send(page, prompt):
    page.text_area(key="poc-prompt").input(prompt)
    next(b for b in page.button if b.label == "Send").click().run(timeout=20)
    assert not page.exception


def test_prompt_is_clean_and_sends_directly_to_three_cloud_choices(page):
    ui, calls = page
    assert calls == []
    assert ui.text_area(key="poc-prompt").placeholder == "Ask Intelligent Provisioning agent"
    assert not ui.toggle
    send(ui, "4 vCPU 16 GiB RAM Windows server")
    assert len(calls) == 3
    assert ui.session_state["poc-result"][0]["kind"] == "VM"
    assert ui.session_state["poc-result"][0]["os"] == "Windows"
    assert [t.label for t in ui.tabs] == ["AWS", "Azure", "GCP"]
    assert len([b for b in ui.button if b.label == "Use configuration"]) == 9
    assert any("**Recommendation:" in m.value for m in ui.markdown)
    assert not ui.dataframe
    assert not any(b.label in {"Explain verified evidence", "Research official documentation"} for b in ui.button)


def test_server_os_between_memory_and_server_keeps_requested_memory(page):
    ui, calls = page
    send(ui, "4 vCPU 16 GiB Linux server")
    assert ui.session_state["poc-result"][0]["min_memory_gib"] == 16
    assert all(c[2] == 16 for c in calls)


def test_kubernetes_keyword_returns_worker_sizing_and_cluster_inputs(page):
    ui, calls = page
    send(ui, "Kubernetes 4 vCPU 16 GiB RAM")
    assert ui.session_state["poc-result"][0]["kind"] == "Kubernetes"
    row = ui.session_state["poc-result"][2][0]
    assert row["cluster"]["node_count"] == 2
    assert row["monthly_compute_estimate"] == 100
    ui.button(key="use-AWS-Recommended").click().run(timeout=20)
    assert not ui.exception
    labels = [i.label for i in ui.text_input]
    assert "Cluster name" in labels and "Kubernetes version" in labels
    assert "Cluster IAM role ARN" in labels
    assert "VM name" not in labels and "AMI ID" not in labels


def test_constraints_stop_lookup_until_explicit_region_ids(page):
    ui, calls = page
    send(ui, "Windows 4 vCPU 16 GiB RAM must stay in India")
    assert not calls
    assert any("Include permitted region IDs" in e.value for e in ui.error)


def test_new_requests_and_context_changes_clear_old_plans(page):
    ui, calls = page
    send(ui, "2 vCPU 8 GiB Linux server")
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    assert "VM name" in [i.label for i in ui.text_input]
    assert "AMI ID" in [i.label for i in ui.text_input]
    send(ui, "Windows Kubernetes 4 vCPU 8 GiB")
    assert "poc-selected" not in ui.session_state
    assert "poc-result" not in ui.session_state
    send(ui, "2 vCPU 8 GiB Linux server")
    ui.selectbox(key="iipp_active_provider").select("Azure").run(timeout=20)
    assert not ui.exception
    assert "poc-result" not in ui.session_state
    assert [i.label for i in ui.text_input] == ["AWS region", "Azure region", "GCP region"]


def test_plan_dialog_approval_and_edits_invalidate_plan(page, monkeypatch):
    import poc_planner
    ui, calls = page
    prepared, applied = [], []
    def prepare(selected, request, inputs, latency):
        prepared.append(inputs)
        return dict(deployment_id="poc-" + "a" * 32, status="AWAITING_APPROVAL",
                    plan_sha256="checksum", plan_summary="Plan: 1 to add, 0 to change, 0 to destroy.")
    monkeypatch.setattr(poc_planner, "prepare", prepare)
    monkeypatch.setattr(poc_planner, "apply_saved_plan", lambda *a: applied.append(a))
    send(ui, "2 vCPU 8 GiB Linux server")
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    for label, value in (("VM name", "web-server"), ("AMI ID", "ami-0123456789abcdef0"),
                         ("Subnet ID", "subnet-0123456789abcdef0"), ("Security group ID", "sg-0123456789abcdef0")):
        next(i for i in ui.text_input if i.label == label).input(value)
    ui.button(key="generate-terraform-plan").click().run(timeout=20)
    assert not ui.exception
    assert len(prepared) == 1 and not applied
    assert any("Plan: 1 to add" in c.value for c in ui.code)
    ui.button(key="close-terraform-plan").click().run(timeout=20)
    ui.button(key="terraform-apply").click().run(timeout=20)
    assert not ui.exception
    token_input = ui.text_input(key="tf-approval-token").proto
    assert token_input.type == token_input.PASSWORD
    assert not applied
    next(i for i in ui.text_input if i.label == "VM name").input("changed-server")
    ui.run(timeout=20)
    assert not ui.exception
    assert "poc-saved-plan" not in ui.session_state
    assert not any(b.label == "Terraform apply" for b in ui.button)


def test_sensitive_prompt_uses_deterministic_explanation(page, monkeypatch):
    import agent
    monkeypatch.setattr(agent, "explain_comparison_evidence", lambda *a: pytest.fail("Sensitive request sent to AI"))
    ui, calls = page
    send(ui, "confidential Linux server 2 vCPU 8 GiB")
    assert any("external AI is disabled" in m.value for m in ui.markdown)


@pytest.mark.parametrize("kind", ["server", "cluster"])
def test_edit_button_preserves_inputs_and_regenerates_plan(page, monkeypatch, kind):
    import poc_planner
    from poc_terraform import configuration
    ui, _ = page
    prepared = []
    def prepare(selected, request, inputs, latency):
        prepared.append((selected, request, dict(inputs)))
        config = configuration(selected, request["kind"])
        if kind == "cluster":
            scaling = config["resource"]["aws_eks_node_group"]["workers"]["scaling_config"][0]
            assert scaling == dict(desired_size=2, min_size=2, max_size=2)
        return dict(deployment_id="poc-" + str(len(prepared)) * 32, status="AWAITING_APPROVAL",
                    plan_sha256="checksum", plan_summary="Create " + inputs["name"])
    monkeypatch.setattr(poc_planner, "prepare", prepare)
    prompt = "I need a Kubernetes cluster of 2 worker nodes with 2 vCPU and 1 GiB RAM" if kind == "cluster" else "2 vCPU 1 GiB RAM server"
    send(ui, prompt)
    if kind == "cluster":
        assert ui.session_state["poc-result"][0]["worker_count"] == 2
        assert all(row["cluster"]["node_count"] == 2 for row in ui.session_state["poc-result"][2])
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    for field in ui.text_input:
        if field.label not in {"AWS region", "Azure region", "GCP region"}:
            field.input("test-original")
    ui.button(key="generate-terraform-plan").click().run(timeout=20)
    assert not ui.exception
    old_id = ui.session_state["poc-saved-plan"]["deployment_id"]
    ui.button(key="edit-terraform-plan").click().run(timeout=20)
    assert "poc-saved-plan" not in ui.session_state
    assert not any(b.label == "Terraform apply" for b in ui.button)
    name = next(i for i in ui.text_input if i.label == ("Cluster name" if kind == "cluster" else "VM name"))
    assert name.value == "test-original"
    name.input("test-edited").run(timeout=20)
    ui.button(key="generate-terraform-plan").click().run(timeout=20)
    assert not ui.exception
    assert prepared[-1][2]["name"] == "test-edited"
    assert ui.session_state["poc-saved-plan"]["deployment_id"] != old_id
    assert any("test-edited" in c.value for c in ui.code)


def test_success_popup_shows_resource_details_once(page):
    ui, _ = page
    send(ui, "2 vCPU 1 GiB RAM server")
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    ui.session_state["poc-saved-plan"] = dict(deployment_id="poc-" + "a" * 32, status="SUCCEEDED",
        resource_details=dict(resource_name="created-vm", instance_id="i-example", private_ip="10.0.1.1", public_ip=""))
    ui.session_state["poc-open-success"] = True
    ui.run(timeout=20)
    assert not ui.exception
    assert any("i-example" in m.value for m in ui.markdown)
    assert any("Not assigned" in m.value for m in ui.markdown)
    ui.button(key="close-resource-success").click().run(timeout=20)
    assert not any("i-example" in m.value for m in ui.markdown)


def test_failed_discovery_replaces_old_choices_and_plan(page, monkeypatch):
    import agent_flow_ui
    ui, calls = page
    send(ui, "2 vCPU 8 GiB Linux server")
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    def fail(*args):
        raise ValueError("Provider unavailable")
    fail.clear = lambda: None
    monkeypatch.setattr(agent_flow_ui, "catalogue_options", fail)
    send(ui, "4 vCPU 16 GiB Linux server")
    assert "poc-selected" not in ui.session_state
    assert "poc-saved-plan" not in ui.session_state
    assert ui.session_state["poc-result"][0]["min_vcpus"] == 4
    assert len(ui.session_state["poc-result"][3]) == 3
    assert not any(b.label == "Use configuration" for b in ui.button)


def test_one_undersized_kubernetes_candidate_does_not_drop_other_workers(page, monkeypatch):
    import agent_flow_ui
    ui, calls = page
    def workers(provider, region, zone, os_name, cpu, memory, cid):
        base = dict(provider=provider, region=region, os=os_name, available=True,
                    offered_in_target_location=True, monthly_compute_estimate=50, price_currency="USD")
        return [dict(base, sku="too-small", vcpus=1, memory_gib=.25),
                dict(base, sku="worker-fit", vcpus=4, memory_gib=16)]
    workers.clear = lambda: None
    monkeypatch.setattr(agent_flow_ui, "catalogue_options", workers)
    send(ui, "Kubernetes 4 vCPU 16 GiB RAM")
    comparison = ui.session_state["poc-result"][1]
    assert comparison["recommended_sku"] == "worker-fit"  # Shared eligible configuration.
    assert all(row["sku"] == "worker-fit" for row in comparison["eligible_options"])


def test_live_aws_catalogue_keeps_new_families_and_unavailable_evidence(monkeypatch):
    import poc_ui
    rows = [dict(sku="t3a.large", vcpus=2, memory_gib=8, available=True),
            dict(sku="m8i.large", vcpus=2, memory_gib=8, available=True),
            dict(sku="accelerated-worker", accelerated=True),
            dict(sku="no-matching-profile", available=False, error="Live evidence unavailable")]
    monkeypatch.setattr(poc_ui, "discover_aws_vm_options", lambda *a, **kw: rows)
    poc_ui.catalogue_options.clear()
    try:
        result = poc_ui.catalogue_options("AWS", "ap-south-1", "", "Linux", 2, 8., None)
        assert [r["sku"] for r in result] == ["t3a.large", "m8i.large", "no-matching-profile"]
    finally:
        poc_ui.catalogue_options.clear()


def test_advanced_options_only_have_provider_regions(page):
    ui, _ = page
    assert [i.label for i in ui.text_input] == ["AWS region", "Azure region", "GCP region"]
    assert not ui.number_input
    assert not ui.checkbox
    assert not any("connection" in s.label.lower() for s in ui.selectbox)


@pytest.mark.parametrize("provider", ["AWS", "Azure", "GCP"])
@pytest.mark.parametrize("kind", ["VM", "Kubernetes"])
def test_use_configuration_opens_only_matching_provider_accounts_and_live_values(page, monkeypatch, provider, kind):
    import provider_inputs
    ui, _ = page
    lookups = []
    def discover(cid, region, target, os_name):
        lookups.append((cid, target))
        return dict(provider=provider, retrieved_at="2026-11-05T00:00:00Z", choices={
            "image_id": [dict(value="image-live", label="Provider image")],
            "subnet_id": [dict(value="subnet-live", label="Live private subnet", zone="a", vpc_id="vpc")],
            "second_subnet_id": [dict(value="subnet-second", label="Other zone", zone="b", vpc_id="vpc")],
            "kubernetes_version": [dict(value="1.34", label="1.34")],
        }, errors={})
    monkeypatch.setattr(provider_inputs, "discover", discover)
    send(ui, f"{kind} 2 vCPU 8 GiB RAM Linux")
    ui.button(key=f"use-{provider}-Selectable").click().run(timeout=20)
    assert not ui.exception
    selector = ui.selectbox(key=f"tf-account-1-{provider}")
    assert selector.options == [provider + "-POC"]
    assert lookups == [(provider, kind)]
    assert next(s for s in ui.selectbox if s.label == "Subnet ID").value == "subnet-live"
    if kind == "Kubernetes":
        assert next(s for s in ui.selectbox if s.label == "Kubernetes version").value == "1.34"
    else:
        assert next(s for s in ui.selectbox if s.label == ("AMI ID" if provider == "AWS" else "Image ID")).value == "image-live"


def test_public_choice_can_choose_account_without_resending_prompt(page):
    ui, calls = page
    send(ui, "2 vCPU 8 GiB RAM Linux server")
    for row in ui.session_state["poc-result"][2]:
        row["connection_id"] = None
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    assert not ui.exception
    assert ui.selectbox(key="tf-account-1-AWS").value == "AWS"
    assert any(i.label == "VM name" for i in ui.text_input)
    assert len(calls) == 3
    assert not any("send the request again" in i.value for i in ui.info)


def test_account_switch_discards_plan_and_loads_new_account_values(page, monkeypatch):
    import agent_flow_ui as flow
    import provider_inputs
    ui, _ = page
    original = flow.list_connections()
    second = dict(original[0], connection_id="AWS-2", name="AWS-TEST")
    connections = original + [second]
    monkeypatch.setattr(flow, "list_connections", lambda: connections)
    monkeypatch.setattr(flow, "get_connection", lambda cid: next((c for c in connections if c["connection_id"] == cid), None))
    lookups = []
    monkeypatch.setattr(provider_inputs, "discover", lambda cid, *a: lookups.append(cid) or {"choices": {}, "errors": {}})
    send(ui, "Kubernetes 2 vCPU 8 GiB RAM")
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    ui.session_state["poc-saved-plan"] = {"deployment_id": "old-plan"}
    ui.selectbox(key="tf-account-1-AWS").select("AWS-2").run(timeout=20)
    assert not ui.exception
    assert "poc-saved-plan" not in ui.session_state
    assert lookups == ["AWS", "AWS-2"]
    assert not any(b.label == "Terraform apply" for b in ui.button)


def test_failed_refresh_discards_plan_and_previous_live_values(page, monkeypatch):
    import provider_inputs
    ui, _ = page
    monkeypatch.setattr(provider_inputs, "discover", lambda *a: {"choices": {"subnet_id": [{"value": "live-subnet", "label": "Live subnet"}]}, "errors": {}})
    send(ui, "Linux server 2 vCPU 8 GiB RAM")
    ui.button(key="use-AWS-Selectable").click().run(timeout=20)
    ui.session_state["poc-saved-plan"] = {"deployment_id": "old-plan"}
    def fail(*a):
        raise RuntimeError("connection failed")
    monkeypatch.setattr(provider_inputs, "discover", fail)
    ui.button(key="tf-refresh-live").click().run(timeout=20)
    assert not ui.exception
    assert "poc-saved-plan" not in ui.session_state
    assert ui.session_state["tf-live-inputs"]["choices"] == {}
    assert not any(s.label == "Subnet ID" for s in ui.selectbox)
