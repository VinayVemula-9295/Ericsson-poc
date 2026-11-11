"""Ensure repository modules are importable with both `pytest` and `python -m pytest`."""
from __future__ import annotations

import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_string = str(ROOT)
if root_string not in sys.path:
    sys.path.insert(0, root_string)


@pytest.fixture
def region_catalogues(monkeypatch):
    import cloud_regions
    def load(provider):
        codes = {"AWS": ["ap-south-1", "us-east-1"], "Azure": ["centralindia", "eastus"],
                 "GCP": ["asia-south1", "us-central1"]}[provider]
        return dict(regions=[dict(value=c, label=c, zones=[c + "-b"] if provider == "GCP" else []) for c in codes],
                    source=cloud_regions.SOURCES[provider], retrieved_at="2026-11-05T00:00:00Z")
    monkeypatch.setattr(cloud_regions, "load_catalogue", load)
