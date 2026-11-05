from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"


def test_agent_is_event_driven_and_dashboard_does_not_generate_load():
    source = APP_PATH.read_text()
    assert "Agent idle — waiting for a sustained CPU threshold event" in source
    assert "No infrastructure plan or approval action is shown by default" in source
    assert "Run controlled load test" not in source
    assert "Controlled workload test" not in source
    assert "st_autorefresh(interval=30_000" in source
    assert "Approve & apply EC2 change" not in source
    assert "terraform_service" in source


def test_cost_estimation_has_price_list_and_cost_explorer_permissions():
    source = (ROOT / "aws_data.py").read_text()
    policy = (ROOT / "iam-cost-policy.json").read_text()
    assert '"pricing:GetProducts"' in policy
    assert '"ce:GetCostAndUsage"' in policy
    assert "get_running_ec2_cost_estimate" in source
    assert "not a guaranteed bill forecast" in source
    assert "clear_price_cache" in source


def test_legacy_direct_ec2_resize_is_disabled_for_terraform_only_architecture():
    source = (ROOT / "provisioning.py").read_text()
    assert "Direct EC2 API resize is disabled in IIPP" in source
    assert "modify_instance_attribute" not in source
    assert "stop_instances" not in source
    assert "start_instances" not in source


def test_trend_watcher_is_cpu_only_and_has_sustained_scale_up_and_down_logic():
    source = (ROOT / "trend.py").read_text()
    assert "high_streak >= HIGH_STREAK_REQUIRED" in source
    assert "low_streak >= LOW_STREAK_REQUIRED" in source
    assert "HIGH_CPU_PERCENT = 70.0" in source
    assert "LOW_CPU_PERCENT = 25.0" in source
    assert '"scale_up"' in source
    assert '"scale_down"' in source
    assert '"CPUUtilization"' in source
    assert "memory_percent" not in source


def test_candidate_target_types_are_display_only_and_allowlisted_for_t3():
    from provisioning import allowed_target_types
    assert allowed_target_types({"architecture": "x86_64", "instance_type": "t3.small"}) == [
        "t3.micro", "t3.small", "t3.medium", "t3.large"
    ]


def test_trend_engine_emits_scale_up_then_scale_down(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trend import evaluate_trend

    now = datetime.now(timezone.utc)
    state_path = tmp_path / "trend-state.json"
    instance = {"available": True, "state": "running", "instance_type": "t3.micro"}

    def metric_series(values, mem_values=None, minutes_ago=None):
        cpu_points = []
        memory_points = []
        if minutes_ago is None:
            minutes_ago = [len(values) - index - 1 for index in range(len(values))]
        for index, value in enumerate(values):
            ts = (now - timedelta(minutes=minutes_ago[index])).isoformat()
            cpu_points.append({"timestamp": ts, "average": value, "maximum": value})
            if mem_values and mem_values[index] is not None:
                memory_points.append({"timestamp": ts, "average": mem_values[index], "maximum": mem_values[index]})
        return {"metrics": {"CPU": {"points": cpu_points}, "Memory": {"points": memory_points}}}

    up = evaluate_trend(metric_series([75, 78], [65, 66], [5, 4]), instance, state_path)
    assert up and up["trend_signal"] == "scale_up" and up["suggested_instance_type"] == "t3.small"

    instance["instance_type"] = "t3.small"
    down = evaluate_trend(metric_series([20, 18, 17], [35, 34, 33], [2, 1, 0]), instance, state_path)
    assert down and down["trend_signal"] == "scale_down" and down["suggested_instance_type"] == "t3.micro"
    assert "no aws changes have been made" in down["note"].lower()


def test_memory_spike_alone_does_not_trigger_agent(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trend import evaluate_trend

    now = datetime.now(timezone.utc)
    cpu_points = [
        {"timestamp": (now - timedelta(minutes=1)).isoformat(), "average": 20},
        {"timestamp": now.isoformat(), "average": 22},
    ]
    memory_points = [
        {"timestamp": point["timestamp"], "average": 95} for point in cpu_points
    ]
    metrics = {"metrics": {"CPU": {"points": cpu_points}, "Memory": {"points": memory_points}}}
    instance = {"available": True, "state": "running", "instance_type": "t3.micro"}
    assert evaluate_trend(metrics, instance, tmp_path / "state.json") is None


def test_new_spike_can_retrigger_after_prior_signal_clears(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trend import evaluate_trend

    now = datetime.now(timezone.utc)
    state_path = tmp_path / "state.json"
    instance = {"available": True, "state": "running", "instance_type": "t3.micro"}

    def metrics(values):
        return {"metrics": {"CPU": {"points": [
            {"timestamp": (now - timedelta(minutes=len(values) - i - 1)).isoformat(), "average": value}
            for i, value in enumerate(values)
        ]}}}

    first = evaluate_trend(metrics([75, 80]), instance, state_path)
    assert first and first["trend_signal"] == "scale_up"
    assert evaluate_trend(metrics([40, 45]), instance, state_path) is None
    second = evaluate_trend(metrics([76, 82]), instance, state_path)
    assert second and second["trend_signal"] == "scale_up"


def test_agent_has_read_only_candidate_discovery_tool_and_never_claims_spare_capacity():
    agent_source = (ROOT / "agent.py").read_text()
    aws_source = (ROOT / "aws_data.py").read_text()
    policy = (ROOT / "iam-policy-example.json").read_text()
    assert '"name": "discover_instance_type_options"' in agent_source
    assert "describe_instance_type_offerings" in aws_source
    assert "does not guarantee spare On-Demand capacity" in aws_source
    assert '"ec2:DescribeInstanceTypeOfferings"' in policy
    assert '"ec2:DescribeInstanceTypes"' in policy


def test_global_header_has_provider_account_configure_and_no_right_logo():
    app = (ROOT / "app.py").read_text()
    assert (ROOT / "assets" / "innominds-logo.jpg").exists()
    assert 'st.image(str(LOGO_PATH), width=180)' in app  # shared header brand
    assert 'st.image(str(DASHBOARD_IMAGE_PATH), width=210)' not in app
    assert 'st.selectbox("Cloud provider", ["AWS", "Azure", "GCP"]' in app
    assert 'selector_label = {"AWS": "AWS account", "Azure": "Azure subscription", "GCP": "GCP project"}' in app
    assert 'key="iipp-global-configure"' in app
    assert 'st.session_state.pop("iipp_open_provider_config_dialog", False)' in app
    assert 'st.session_state.pop("iipp_open_connection_success_dialog", False)' in app
    assert 'st.session_state["iipp_open_provider_config_dialog"] = True' in app
    assert '@st.dialog("Configure cloud provider", width="large")' in app
    assert '@st.dialog("Cloud connection verified", width="small")' in app
    assert 'st.radio("Workspace", ["Agent", "Telemetry", "Deployment"]' in app
    assert 'placeholder="Describe your infrastructure requirements…"' in app
    assert "AWS_PROFILES" not in (ROOT / "cloud_comparison.py").read_text()
    assert "PROFILE_TYPES" not in (ROOT / "cloud_adapters" / "aws.py").read_text()
    assert "instance_type_options = [" not in app
    assert "Use selected configuration" in app
    assert "Scale-down datapoints" in app
    assert "_directional_instance_candidates" not in app


def test_destroy_workflow_lives_in_deployments():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    ui = (ROOT / "deployment_history_ui.py").read_text(encoding="utf-8")
    assert "render_work_resource_lifecycle" not in app
    assert 'st.button("Destroy"' in ui
    assert 'st.button("Submit"' in ui
    assert 'service.apply_destroy_plan(row["deployment_id"], reviewer, token)' in ui


def test_agent_response_is_concise_and_active_cloud_context_is_shared():
    app = (ROOT / "app.py").read_text()
    agent = (ROOT / "agent.py").read_text()
    assert "_active_cloud_selection" in app
    assert "aws_connection_id = active_aws_id or None" in app
    assert "6–8 short, readable lines" in agent
    assert "meeting or exceeding every hard CPU/RAM requirement" in agent
    assert "No internal reasoning" in agent


def test_llm_recommendation_can_prefill_a_terraform_plan_but_never_applies_directly():
    agent_source = (ROOT / "agent.py").read_text()
    app_source = (ROOT / "app.py").read_text()
    assert "recommended_instance_type" in agent_source
    assert "Preferred instance type" in agent_source
    assert "initial_type = suggested_instance_type or" in app_source
    assert "Create AWS infrastructure — prepare saved plan" in app_source
    assert "Approve & apply EC2 change" not in app_source
    assert "execute_approved_resize" not in app_source


def test_discovery_compares_live_offered_same_family_types(monkeypatch):
    import aws_data

    class FakeEC2:
        def describe_instance_type_offerings(self, **kwargs):
            assert kwargs["LocationType"] == "availability-zone"
            assert any(f["Name"] == "location" and f["Values"] == ["ap-south-1a"] for f in kwargs["Filters"])
            return {"InstanceTypeOfferings": [
                {"InstanceType": "t3.micro"},
                {"InstanceType": "t3.small"},
                {"InstanceType": "t3.large"},
            ]}

        def describe_instance_types(self, InstanceTypes):
            sizes = {"t3.micro": 1024, "t3.small": 2048, "t3.large": 8192}
            return {"InstanceTypes": [
                {"InstanceType": name,
                 "VCpuInfo": {"DefaultVCpus": 2},
                 "MemoryInfo": {"SizeInMiB": sizes[name]},
                 "NetworkInfo": {"NetworkPerformance": "Up to 5 Gigabit"},
                 "BurstablePerformanceSupported": True,
                 "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]}}
                for name in InstanceTypes
            ]}

    monkeypatch.delenv("CLOUDPILOT_ALLOWED_INSTANCE_TYPES", raising=False)
    monkeypatch.setattr(aws_data, "_ec2_client", lambda: FakeEC2())
    monkeypatch.setattr(aws_data, "_region", lambda: "ap-south-1")
    monkeypatch.setattr(aws_data, "get_target_instance_details", lambda _id: {
        "available": True, "instance_id": "i-test", "instance_type": "t3.micro",
        "availability_zone": "ap-south-1a", "platform_details": "Linux/UNIX", "tenancy": "default"
    })
    rates = {"t3.micro": 0.01, "t3.small": 0.02, "t3.medium": 0.04, "t3.large": 0.08}
    monkeypatch.setattr(aws_data, "get_ec2_instance_on_demand_price", lambda instance_type, *args, **kwargs: {
        "available": True, "hourly_usd": rates[instance_type], "monthly_730h_usd": round(rates[instance_type] * 730, 4)
    })

    result = aws_data.discover_instance_type_options("i-test")
    by_type = {item["instance_type"]: item for item in result["options"]}
    assert result["available"] is True
    assert by_type["t3.small"]["offered_in_target_location"] is True
    assert by_type["t3.small"]["vcpus"] == 2
    assert by_type["t3.small"]["memory_gib"] == 2
    assert by_type["t3.large"]["memory_gib"] == 8
    assert by_type["t3.large"]["monthly_delta_vs_current_usd"] == 51.1


def test_scale_down_diagnostic_explains_smallest_instance_type(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trend import evaluate_trend, get_trend_diagnostics

    now = datetime.now(timezone.utc)
    cpu_points = [
        {"timestamp": (now - timedelta(minutes=2 - i)).isoformat(), "average": value, "maximum": value}
        for i, value in enumerate([20, 18, 17])
    ]
    instance = {"available": True, "state": "running", "instance_type": "t3.micro"}
    state_path = tmp_path / "state.json"
    assert evaluate_trend({"metrics": {"CPU": {"points": cpu_points}}}, instance, state_path) is None
    status = get_trend_diagnostics(state_path)
    assert status["low_streak"] == 3
    assert status["scale_down_available"] is False
    assert "smallest allowlisted type" in status["message"]


def test_directional_approval_options_only_allow_downsize_targets():
    from scaling_policy import directional_instance_candidates
    offered = {"t3.micro", "t3.small", "t3.medium", "t3.large"}
    allowed = ["t3.micro", "t3.small", "t3.medium", "t3.large"]
    assert directional_instance_candidates(allowed, offered, "t3.small", "scale_down") == ["t3.micro"]
    assert directional_instance_candidates(allowed, offered, "t3.small", "scale_up") == ["t3.medium", "t3.large"]



def test_post_resize_cooldown_blocks_immediate_reverse_recommendation(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trend import evaluate_trend, get_trend_diagnostics, start_post_change_cooldown

    state_path = tmp_path / "trend-state.json"
    start_post_change_cooldown(state_path, "t3.small", cooldown_minutes=15)
    now = datetime.now(timezone.utc)
    cpu_points = [
        {"timestamp": (now - timedelta(minutes=3 - i)).isoformat(), "average": value, "maximum": value}
        for i, value in enumerate([20, 18, 17])
    ]
    instance = {"available": True, "state": "running", "instance_type": "t3.small"}
    event = evaluate_trend({"metrics": {"CPU": {"points": cpu_points}}}, instance, state_path)
    status = get_trend_diagnostics(state_path)
    assert event is None
    assert status["low_streak"] == 0
    assert status["cooldown_active"] is True
    assert "cooldown active" in status["message"].lower()


def test_approval_clears_stale_plan_and_execution_receipt_ui():
    app = (ROOT / "app.py").read_text()
    assert "start_post_change_cooldown(TREND_STATE_PATH" in app
    assert "TREND_EVENT_PATH.unlink(missing_ok=True)" in app
    assert "LATEST_PLAN_PATH.unlink(missing_ok=True)" in app
    assert 'st.json(result)' not in app
    assert "Change Execution Receipt" not in app



def test_product_branding_and_agent_use_single_provisioning_prompt():
    app = APP_PATH.read_text(encoding="utf-8")
    assert 'PRODUCT_NAME = "Intelligent Provisioning Platform"' in app
    assert 'page_title=PRODUCT_NAME' in app
    assert 'InfraMind' not in app
    assert 'Think Intelligent. Provision Anywhere.' not in app
    assert 'def _render_agent_chat' not in app
    assert 'agent-chat-input' not in app
    assert '"Suggested prompts"' not in app
    assert 'st.form_submit_button("Find options"' in app
    assert '"Advanced options"' in app
    assert 'if not prompt.strip():' in app


def test_live_connected_account_inventory_panel_is_removed_from_work_and_agent():
    app = APP_PATH.read_text()
    work_block = app.split("def render_telemetry() -> None:", 1)[1].split("def _fallback_plan", 1)[0]
    agent_block = app.split("def render_agent() -> None:", 1)[1].split("PRICE_CACHE_TTL_SECONDS", 1)[0]
    assert "render_configured_provider_inventory()" not in work_block
    assert "render_configured_provider_inventory" not in agent_block


def test_configure_dialog_uses_one_shot_events_and_has_close_button():
    app = APP_PATH.read_text()
    provider_ui = (APP_PATH.parent / "provider_ui.py").read_text()
    assert 'st.session_state.pop("iipp_open_provider_config_dialog", False)' in app
    assert 'st.session_state.pop("iipp_open_connection_success_dialog", False)' in app
    assert 'st.button("Close", key="iipp-config-close"' in provider_ui
    assert 'st.session_state["iipp_open_connection_success_dialog"] = True' in provider_ui


def test_agent_answer_sanitizer_removes_internal_thinking_blocks():
    from agent import sanitize_agent_answer
    raw = "<thinking>private analysis that must not render</thinking>\n**Recommended: c6i.xlarge**\n- Meets the request."
    clean = sanitize_agent_answer(raw)
    assert "private analysis" not in clean
    assert "<thinking>" not in clean
    assert "</thinking>" not in clean
    assert "**Recommended: c6i.xlarge**" in clean


def test_agent_answer_sanitizer_handles_unclosed_thinking_tag():
    from agent import sanitize_agent_answer
    clean = sanitize_agent_answer("**Recommended: c6i.xlarge**\n<thinking>internal reasoning continues")
    assert clean == "**Recommended: c6i.xlarge**"


def test_recommendation_card_highlights_exact_or_nearest_safe_aws_option():
    app = APP_PATH.read_text()
    assert "RECOMMENDED AWS CONFIGURATION" in app
    assert "CLOSEST AWS CONFIGURATION · NOT ELIGIBLE FOR PROVISIONING" in app
    assert "Exact match check and closest configurations" in app
    assert "Choose from all returned priced configurations that meet the request" in app
    assert "Detailed comparison and exclusions" in app
