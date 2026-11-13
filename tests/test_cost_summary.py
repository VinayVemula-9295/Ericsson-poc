from cost_summary import summary


def test_saved_plan_cost_summary_uses_connected_identity_and_evidence(monkeypatch):
    import provider_connections
    monkeypatch.setattr(provider_connections, "get_connection", lambda connection_id: {
        "connection_id": connection_id, "provider": "AWS", "name": "Demo Account",
        "account_id": "333347372766", "metadata": {},
    })
    result = summary({
        "connection_id": "aws-demo", "target_identity": "333347372766",
        "candidate": {"provider": "AWS", "region": "ap-south-1", "sku": "t3.small",
                      "vcpus": 2, "memory_gib": 2, "monthly_compute_estimate": 18.25},
        "requirements": {"kind": "VM"},
    })
    assert result["identity_name"] == "Demo Account"
    assert result["identity"] == "333347372766"
    assert result["monthly_compute_estimate"] == "$18.25 USD / month"
    assert result["price_available"] is True


def test_saved_plan_cost_summary_does_not_fabricate_missing_price(monkeypatch):
    import aws_data
    monkeypatch.setattr(aws_data, "get_ec2_instance_on_demand_price", lambda *a: {"available": False})
    result = summary({"candidate": {"provider": "AWS", "sku": "t3.small", "region": "ap-south-1"}})
    assert result["monthly_compute_estimate"] == "Unavailable"
    assert result["price_available"] is False


def test_cost_summary_does_not_match_name_to_wrong_account(monkeypatch):
    import aws_data
    import provider_connections
    monkeypatch.setattr(aws_data, "get_ec2_instance_on_demand_price", lambda *a: {"available": False})
    monkeypatch.setattr(provider_connections, "get_connection", lambda _: {
        "provider": "AWS", "name": "Demo Account", "account_id": "333347372766", "metadata": {},
    })
    result = summary({"connection_id": "stale", "target_identity": "695729002971",
                      "candidate": {"provider": "AWS", "region": "ap-south-1", "sku": "t3.small"}})
    assert result["identity_name"] == "Name not recorded"


def test_legacy_plan_uses_existing_live_price_integration(monkeypatch):
    import aws_data
    monkeypatch.setattr(aws_data, "get_ec2_instance_on_demand_price", lambda *a: {
        "available": True, "monthly_730h_usd": 22.5,
    })
    result = summary({"provider": "AWS", "region": "ap-south-1",
                      "requested_configuration": {"instance_type": "t3.small", "os": "Linux"}})
    assert result["monthly_compute_estimate"] == "$22.50 USD / month"
    assert result["price_available"] is True
