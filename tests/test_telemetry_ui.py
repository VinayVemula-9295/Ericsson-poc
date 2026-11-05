from copy import deepcopy

from streamlit.testing.v1 import AppTest

from telemetry_ui import comparison_rows


def test_demo_comparison_never_changes_live_inputs_or_substitutes_failed_aws():
    connections = []
    inventory = {"available": False, "error": "API failed"}
    costs = {"available": False}
    before = deepcopy((connections, inventory, costs))
    rows = comparison_rows(connections, inventory, costs)
    assert (connections, inventory, costs) == before
    assert rows[0]["Running compute"] is None
    assert rows[0]["Monthly compute (USD)"] is None
    for row in rows[1:]:
        assert row["Status"] == "Connected · Demo"
        assert row["Evidence"] == "Sample data"
        assert row["Monthly compute (USD)"] > 0


def test_live_aws_comparison_preserves_zero_and_unavailable_cost():
    rows = comparison_rows([], {"available": True, "count": 0, "region": "ap-south-1"}, {"available": False})
    assert rows[0]["Running compute"] == 0
    assert rows[0]["Monthly compute (USD)"] is None
    rows = comparison_rows([], {"available": True, "count": 2}, {"available": True, "monthly_run_rate": 8.18})
    assert rows[0]["Monthly compute (USD)"] == 8.18


def test_comparison_renders_cards_table_and_failure_evidence():
    page = AppTest.from_string('''
from telemetry_ui import render_comparison
render_comparison([], {"available": False, "error": "API failed"}, {"available": False})
''').run()
    assert not page.exception
    assert len(page.metric) == 6
    assert page.metric[0].value == "N/A"
    assert len(page.dataframe[0].value) == 3
    assert "API failed" in page.warning[0].value
