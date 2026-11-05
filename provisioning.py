"""Compatibility helpers for IIPP's Terraform-only provisioning model.

This module intentionally contains no direct EC2 mutation operations. Resource
changes must be represented by Terraform configuration and a reviewed saved
plan in terraform_service.py.
"""
from __future__ import annotations

import os
from typing import Any


X86_TARGET_TYPES = ["t3.micro", "t3.small", "t3.medium", "t3.large"]
ARM_TARGET_TYPES = ["t4g.micro", "t4g.small", "t4g.medium", "t4g.large"]


def allowed_target_types(instance: dict[str, Any]) -> list[str]:
    """Return display-only candidate families; this function never changes AWS."""
    architecture = instance.get("architecture", "x86_64")
    current_type = str(instance.get("instance_type", ""))
    candidates = ARM_TARGET_TYPES if architecture == "arm64" else X86_TARGET_TYPES
    configured = [x.strip() for x in os.getenv("IIPP_ALLOWED_INSTANCE_TYPES", ",".join(candidates)).split(",") if x.strip()]
    family = "t4g." if architecture == "arm64" else "t3."
    if current_type.startswith("t2."):
        family = "t2."
    return [x for x in configured if x.startswith(family)]


def execute_approved_resize(instance_id: str, target_type: str, reviewer: str) -> dict[str, Any]:
    """Block the retired direct EC2 resize path; IIPP provisions with Terraform only."""
    raise RuntimeError(
        "Direct EC2 API resize is disabled in IIPP. Use the Terraform provisioning workflow "
        "to create and review a saved infrastructure plan before applying changes."
    )
