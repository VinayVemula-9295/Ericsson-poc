"""Backend approval checks shared by deployment lifecycle operations."""
import hmac
import os
from pathlib import Path


def validate(reviewer, token):
    if os.getenv("ENABLE_APPROVAL_EXECUTION", "false").lower() not in {"true", "1", "yes"}:
        raise ValueError("Approved Terraform execution is disabled on this runner")
    expected = os.getenv("APPROVAL_TOKEN", "")
    if not expected:
        for path in (Path("/run/secrets/cloudpilot-approval-token"), Path.home() / ".cloudpilot-approval-token"):
            if path.is_file():
                expected = path.read_text(encoding="utf-8").strip()
                break
    if not str(reviewer or "").strip() or not expected or not hmac.compare_digest(expected.encode(), str(token or "").encode()):
        raise ValueError("Reviewer name and valid approval token are required")
