"""Small, append-only local POC audit events; no prompts or credentials."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

def event(action: str, **fields):
    allowed = {k: v for k, v in fields.items() if k in {"deployment_id", "provider", "region", "sku", "status", "reviewer", "plan_sha256"}}
    root = Path(os.getenv("DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(), action=action, **allowed)) + "\n"
    fd = os.open(root / "poc_audit.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)
