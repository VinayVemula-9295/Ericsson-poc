"""Parse exported Terraform configurations without credentials or cloud calls."""
import json
import subprocess
import sys
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from poc_terraform import configuration
for provider, sku in (("AWS", "t3.large"), ("Azure", "Standard_D2s_v5"), ("GCP", "n2-standard-2")):
    for os_name, kind in (("Linux", "VM"), ("Windows", "VM"), ("Linux", "Kubernetes")):
        folder = ROOT / ".cache" / "poc-template-check" / f"{provider}-{os_name}-{kind}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.tf.json").write_text(json.dumps(configuration(dict(provider=provider, sku=sku, region="test", os=os_name, cluster={"node_count": 3}), kind), indent=2), encoding="utf-8")
        result = subprocess.run(["terraform", "fmt", "-check", "-no-color", str(folder)], capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(result.stdout + result.stderr)
        if "--schemas" in sys.argv:
            cache = ROOT / ".cache" / "poc-provider-cache"
            cache.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, TF_PLUGIN_CACHE_DIR=str(cache), TF_IN_AUTOMATION="1")
            for command in (["terraform", "init", "-backend=false", "-input=false", "-no-color"], ["terraform", "validate", "-no-color"]):
                result = subprocess.run(command, cwd=folder, env=env, capture_output=True, text=True, timeout=300)
                if result.returncode:
                    raise SystemExit(result.stdout + result.stderr)
            print(f"Validated {provider} {os_name} {kind}", flush=True)
print("9 configurations parsed; provider schemas/live planning not validated")
