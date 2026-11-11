"""Saved Terraform plans and token-protected approval of the exact saved artifact."""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from placement import evaluate
from poc_terraform import configuration
from provider_connections import get_connection, get_saved_secret, get_aws_terraform_runtime
from poc_audit import event


def execution_timeout(kind):
    """Allow EKS creation/deletion to finish without cutting off provider waiters."""
    name = "IIPP_KUBERNETES_TIMEOUT_SECONDS" if kind == "Kubernetes" else "IIPP_SERVER_TIMEOUT_SECONDS"
    default = 3600 if kind == "Kubernetes" else 900
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(name + " must be an integer") from None
    if not default <= value <= 14400:
        raise ValueError(name + f" must be between {default} and 14400")
    return value


def _write(directory, record):
    timestamp = datetime.now(timezone.utc).isoformat()
    history = record.setdefault("state_history", [])
    if not history or history[-1].get("status") != record.get("status"):
        history.append({"status": record.get("status"), "timestamp": timestamp})
    record["updated_at"] = timestamp
    temporary = directory / "record.tmp"
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(directory / "record.json")


def list_saved_plans():
    """Read managed resource records without refreshing or changing a cloud."""
    root = Path(os.getenv("DATA_DIR", "/data")) / "poc_plans"
    records = []
    for path in root.glob("poc-*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") in {"SUCCEEDED", "APPLYING"} or (record.get("status") == "FAILED" and record.get("execution_enabled")):
                records.append(record)
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda record: record.get("created_at", ""), reverse=True)


@contextmanager
def _lock(directory):
    import fcntl
    with (directory / ".review.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _backend(record, readonly=False):
    args = ["terraform", "init", "-input=false", "-no-color",
            "-backend-config=bucket=" + record["state_bucket"],
            "-backend-config=region=" + record["state_region"],
            "-backend-config=key=" + record["state_key"],
            "-backend-config=use_lockfile=true", "-backend-config=encrypt=true"]
    if readonly:
        args.append("-lockfile=readonly")
    return args


def _runtime(candidate, request, inputs, conn, tf):
    """Rebuild ephemeral credentials and recheck policy at plan and apply time."""
    if conn.get("provider") != candidate["provider"] or conn.get("connection_status") != "CONNECTED":
        raise ValueError("A verified connection matching the target provider is required")
    if evaluate(dict(candidate), request, request.get("latency_evidence", []))["status"] != "pass":
        raise ValueError("Mandatory policy evidence failed or is unavailable")
    if not candidate.get("available") or candidate.get("vcpus", 0) < request["min_vcpus"] or candidate.get("memory_gib", 0) < request["min_memory_gib"]:
        raise ValueError("Selected configuration does not meet the hardware requirements")
    if request.get("worker_count") and candidate.get("cluster", {}).get("node_count") != request["worker_count"]:
        raise ValueError("Worker count differs from the requested configuration")
    budget = request.get("max_monthly_budget_usd")
    if budget and (candidate.get("monthly_compute_estimate") is None or candidate["monthly_compute_estimate"] > budget):
        raise ValueError("Selected configuration exceeds the compute budget")
    if request.get("residency_regions") and tf.TF_STATE_REGION not in request["residency_regions"]:
        raise ValueError("Central Terraform state region violates residency policy")
    metadata = conn.get("metadata", {})
    provider = candidate["provider"]
    env = {}
    if provider == "AWS":
        from policy_engine import evaluate_aws_provision_request
        runtime = get_aws_terraform_runtime(candidate["connection_id"])
        if inputs.get("execution_role_arn") != runtime["target_role_arn"]:
            raise ValueError("Execution role differs from verified target")
        if candidate.get("offered_in_target_location") is not True:
            raise ValueError("AWS regional offering is not verified")
        payload = dict(region=candidate["region"], instance_type=candidate["sku"],
                       environment=request.get("environment", "poc"), ttl_days=request.get("ttl_days", 2))
        evaluate_aws_provision_request(payload, runtime)
        import re
        if not re.fullmatch(r"[\w+=,.@:/-]{2,1224}", runtime.get("external_id", "")):
            raise ValueError("The target execution role requires a valid External ID")
        if request["kind"] == "Kubernetes":
            import re
            if not re.fullmatch(r"iipp-[A-Za-z0-9][A-Za-z0-9_-]{0,94}", inputs.get("name", "")):
                raise ValueError("EKS cluster names must start with iipp- to match the execution policy")
            for field in ("cluster_role_arn", "node_role_arn"):
                if not re.fullmatch(r"arn:aws:iam::" + runtime["account_id"] + r":role/[A-Za-z0-9+=,.@_/-]+", inputs.get(field, "")):
                    raise ValueError("Kubernetes IAM roles must belong to the selected target account")
            if not re.fullmatch(r"arn:aws:kms:" + re.escape(candidate["region"]) + ":" + runtime["account_id"] + r":key/[a-f0-9-]+", inputs.get("kms_key_arn", "")):
                raise ValueError("KMS key must belong to the selected account and region")
            if inputs.get("subnet_id") == inputs.get("second_subnet_id"):
                raise ValueError("EKS requires two distinct subnets in different availability zones")
        env.update(runtime.get("source_env", {}))
        env["TF_VAR_external_id"] = runtime["external_id"]
    else:
        secret = get_saved_secret(candidate["connection_id"])
        if provider == "Azure":
            if inputs.get("subscription_id") != metadata.get("subscription_id"):
                raise ValueError("Subscription differs from verified target")
            env.update(ARM_CLIENT_ID=metadata["client_id"], ARM_TENANT_ID=metadata["tenant_id"],
                       ARM_CLIENT_SECRET=secret["client_secret"], ARM_SUBSCRIPTION_ID=metadata["subscription_id"])
            if candidate["os"] == "Windows" and request["kind"] == "VM":
                password = os.getenv("IIPP_AZURE_WINDOWS_ADMIN_PASSWORD", "")
                if not password:
                    raise ValueError("Configure the protected Windows administrator password on the runner")
                env["TF_VAR_admin_password"] = password
        elif provider == "GCP":
            if inputs.get("project_id") != metadata.get("project_id"):
                raise ValueError("Project differs from verified target")
            info = secret["service_account_json"]
            env["GOOGLE_CREDENTIALS"] = info if isinstance(info, str) else json.dumps(info)
        else:
            raise ValueError("Unsupported provider")
    return env

def prepare(candidate: dict, request: dict, inputs: dict, latency: list[dict] = (), *, reuse_from=None) -> dict:
    from control_plane import authorize_connection
    actor = authorize_connection(candidate.get('connection_id'))
    if actor:
        request = dict(request, initiated_by=actor['username'])
    # Lazy import: execution requires Linux flock; configuration generation is portable.
    import terraform_service as tf
    enabled, message = tf.is_enabled()
    if not enabled:
        raise ValueError(message)
    if evaluate(dict(candidate), request, latency)["status"] != "pass":
        raise ValueError("Mandatory policy evidence failed or is unavailable")
    processing = request.get("residency_regions", [])
    if processing and tf.TF_STATE_REGION not in processing:
        raise ValueError("Central Terraform state region violates residency policy")
    connection_id = candidate.get("connection_id")
    conn = get_connection(connection_id)
    if not conn or conn.get("connection_status") != "CONNECTED" or conn.get("provider") != candidate["provider"]:
        raise ValueError("A verified connection matching the target provider is required")
    bundle = None
    if reuse_from:
        from reuse_artifacts import load
        bundle = load(*reuse_from)
        if reuse_from[0] != 'poc_plans':
            raise ValueError('Saved source uses another Terraform workflow')
        original = bundle[1].get('candidate', {})
        if any(candidate.get(k) != original.get(k) for k in ('provider', 'sku', 'os', 'cluster')):
            raise ValueError('Reuse preserves the saved infrastructure configuration')
        config = json.loads((bundle[0] / 'main.tf.json').read_text(encoding='utf-8'))
    else:
        config = configuration(candidate, request["kind"])
    names = set(config["variable"])
    if {"admin_password", "external_id"}.intersection(inputs):
        raise ValueError("Sensitive values must come from protected runtime secrets, not JSON inputs")
    if set(inputs) - names:
        raise ValueError("Unknown Terraform inputs")
    missing = [name for name, spec in config["variable"].items() if "default" not in spec and not inputs.get(name) and name not in {"external_id", "admin_password"}]
    if missing:
        raise ValueError("Required inputs: " + ", ".join(missing))
    if inputs.get("region", candidate["region"]) != candidate["region"]:
        raise ValueError("Region differs from approved candidate")
    if not all(isinstance(value, str) and value.strip() for value in inputs.values()):
        raise ValueError("Terraform inputs must be nonempty strings")
    if not 1 <= int(request.get("ttl_days", 2)) <= 30 or request.get("environment", "poc") not in {"poc", "test"}:
        raise ValueError("Choose a POC/TEST environment and a TTL between 1 and 30 days")
    meta = conn.get("metadata", {})
    provider = candidate["provider"]
    execution_timeout(request["kind"])
    env = _runtime(candidate, request, inputs, conn, tf)
    from provider_inputs import validate_live_inputs
    live_evidence = validate_live_inputs(candidate, request, inputs)
    image_evidence = None
    if request["kind"] == "VM":
        from poc_image_validation import verify
        image_evidence = verify(candidate, conn, inputs["image_id"])
    if provider == "AWS":
        if not bundle:
            config["provider"]["aws"]["default_tags"] = [{"tags": {
                "ManagedBy": "Innominds-IIPP-Terraform", "Environment": request.get("environment", "poc"),
                "TTL_Days": str(request.get("ttl_days", 2)), "Owner": "Innominds-IIPP", "Project": "Innominds-IIPP"}}]
        if request["kind"] == "VM":
            safe = tf._validate_payload(dict(region=candidate["region"], instance_type=candidate["sku"],
                os=candidate["os"], ami_id=inputs["image_id"], subnet_id=inputs["subnet_id"],
                security_group_id=inputs["security_group_id"], instance_name=inputs["name"],
                aws_connection_id=connection_id, environment=request.get("environment", "poc"),
                ttl_days=request.get("ttl_days", 2), min_vcpus=request["min_vcpus"], min_memory_gib=request["min_memory_gib"]))
            tf._validate_aws_references(safe)
    deployment_id = "poc-" + uuid.uuid4().hex
    directory = Path(os.getenv("DATA_DIR", "/data")) / "poc_plans" / deployment_id
    directory.mkdir(parents=True, mode=0o700)
    env["TF_DATA_DIR"] = str(directory / ".terraform")
    values = {k: v for k, v in inputs.items() if k not in {"external_id", "admin_password"}}
    content = json.dumps(config, indent=2)
    if bundle:
        from reuse_artifacts import copy_sources
        copy_sources(bundle[0], directory)
    (directory / "main.tf.json").write_text(content, encoding="utf-8")
    (directory / "terraform.tfvars.json").write_text(json.dumps(values), encoding="utf-8")
    for path in directory.iterdir():
        path.chmod(0o600)
    record = {"record_version": 1, "deployment_id": deployment_id, "status": "PREPARING",
        "created_at": datetime.now(timezone.utc).isoformat(), "connection_id": connection_id,
        "initiated_by": request.get("initiated_by"), "source_deployment": request.get("source_deployment"),
        "subscription_id": conn.get("subscription_id"), "project_id": conn.get("project_id"),
        "resource_name": values.get("name", deployment_id),
        "target_identity": conn.get("account_id") or meta.get("subscription_id") or meta.get("project_id"),
        "candidate": candidate, "requirements": request, "module_version": "poc-v1",
        "configuration_sha256": tf._sha256(directory / "main.tf.json"), "state_backend": "s3",
        "state_bucket": tf.TF_STATE_BUCKET, "state_region": tf.TF_STATE_REGION,
        "state_key": "iipp/poc/" + deployment_id + "/terraform.tfstate",
        "inputs_sha256": tf._sha256(directory / "terraform.tfvars.json"),
        "execution_enabled": False, "image_evidence": image_evidence, "live_input_evidence": live_evidence}
    if bundle:
        record.update(source_deployment=reuse_from[1], source_artifact_sha256=bundle[2])
    _write(directory, record)
    try:
        event("PLAN_STARTED", deployment_id=deployment_id, provider=provider, region=candidate["region"], sku=candidate["sku"])
        backend = _backend(record, readonly=bool(bundle))
        for args in (backend, ["terraform", "validate", "-no-color"], ["terraform", "plan", "-input=false", "-no-color", "-out=tfplan"]):
            code, _ = (tf._run_init if args[1] == "init" else tf._run)(args, directory, 300, extra_env=env)
            if code:
                raise ValueError("Terraform " + args[1] + " failed; inspect protected runner diagnostics")
        code, summary = tf._run(["terraform", "show", "-no-color", "tfplan"], directory, 120, extra_env=env)
        if code:
            raise ValueError("Terraform show failed")
        (directory / "tfplan").chmod(0o600)
        record.update(status="AWAITING_APPROVAL", plan_sha256=tf._sha256(directory / "tfplan"),
                      lock_sha256=tf._sha256(directory / ".terraform.lock.hcl"), plan_summary=summary)
    except Exception:
        record.update(status="FAILED", error="Planning failed; no apply was attempted",
                      completed_at=datetime.now(timezone.utc).isoformat())
        raise
    finally:
        from reuse_artifacts import snapshot
        snapshot(directory, record)
        _write(directory, record)
        event("PLAN_FINISHED", deployment_id=deployment_id, status=record["status"])
    return record


def apply_saved_plan(deployment_id: str, reviewer: str, token: str) -> dict:
    """Authorize, lock, verify, then apply the same binary plan exactly once."""
    from control_plane import guard_apply
    guard_apply('poc_plans', deployment_id)
    import hmac
    import re
    import terraform_service as tf
    from placement import timestamp
    enabled, message = tf.is_enabled()
    if not enabled:
        raise ValueError(message)
    if os.getenv("ENABLE_APPROVAL_EXECUTION", "false").lower() not in {"1", "true", "yes"}:
        raise ValueError("Approved Terraform execution is disabled on this runner")
    if not re.fullmatch(r"poc-[0-9a-f]{32}", deployment_id):
        raise ValueError("Invalid deployment ID")
    expected = os.getenv("APPROVAL_TOKEN", "")
    if not expected:
        for location in (Path("/run/secrets/cloudpilot-approval-token"), Path.home() / ".cloudpilot-approval-token"):
            if location.is_file():
                expected = location.read_text(encoding="utf-8").strip()
                break
    if not expected or not hmac.compare_digest(expected.encode(), token.encode()) or not reviewer.strip():
        event("APPROVAL_REJECTED", deployment_id=deployment_id, status="INVALID_APPROVAL")
        raise ValueError("Approver name and valid approval token are required")
    directory = Path(os.getenv("DATA_DIR", "/data")) / "poc_plans" / deployment_id
    with _lock(directory):
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        if record["status"] != "AWAITING_APPROVAL":
            raise ValueError("Plan is not awaiting approval; generate and review a new plan")
        age = (datetime.now(timezone.utc) - timestamp(record["created_at"])).total_seconds()
        if not 0 <= age <= 3600:
            raise ValueError("Plan expired; generate and review a new plan")
        if (record.get("state_bucket") != tf.TF_STATE_BUCKET or record.get("state_region") != tf.TF_STATE_REGION or
            record.get("state_backend") != "s3" or record.get("state_key") != "iipp/poc/" + deployment_id + "/terraform.tfstate"):
            raise ValueError("Terraform backend changed; generate a new plan")
        def verify_artifacts():
            for name, key in (("tfplan", "plan_sha256"), ("main.tf.json", "configuration_sha256"),
                              ("terraform.tfvars.json", "inputs_sha256"), (".terraform.lock.hcl", "lock_sha256")):
                if not record.get(key) or not (directory / name).is_file() or tf._sha256(directory / name) != record[key]:
                    raise ValueError("Plan, inputs, configuration or provider lock changed; generate a new plan")
        verify_artifacts()
        candidate, request = record["candidate"], record["requirements"]
        conn = get_connection(record["connection_id"])
        if not conn:
            raise ValueError("Connection no longer verified")
        metadata = conn.get("metadata", {})
        identity = conn.get("account_id") or metadata.get("subscription_id") or metadata.get("project_id")
        if identity != record["target_identity"]:
            raise ValueError("Target identity changed; generate a new plan")
        inputs = json.loads((directory / "terraform.tfvars.json").read_text(encoding="utf-8"))
        env = _runtime(candidate, request, inputs, conn, tf)
        env["TF_DATA_DIR"] = str(directory / ".terraform")
        code, _ = tf._run_init(_backend(record, readonly=True), directory, 300, extra_env=env)
        if code:
            raise ValueError("Terraform backend initialization failed; no apply was attempted")
        verify_artifacts()
        record.update(status="APPLYING", reviewer=reviewer.strip(), approved_at=datetime.now(timezone.utc).isoformat(), execution_enabled=True)
        _write(directory, record)
        event("PLAN_APPROVED", deployment_id=deployment_id, reviewer=reviewer.strip(), plan_sha256=record["plan_sha256"])
        try:
            code, _ = tf._run(["terraform", "apply", "-input=false", "-no-color", "tfplan"], directory, execution_timeout(record["requirements"]["kind"]), extra_env=env)
            record.update(status="SUCCEEDED" if code == 0 else "FAILED", completed_at=datetime.now(timezone.utc).isoformat())
            if code:
                record["error"] = "Terraform apply failed; inspect protected runner diagnostics and Terraform state before retrying"
        except Exception:
            record.update(status="FAILED", error="Apply did not complete; inspect protected runner diagnostics and Terraform state before retrying",
                          completed_at=datetime.now(timezone.utc).isoformat())
        if record["status"] == "SUCCEEDED":
            try:
                code, output = tf._run(["terraform", "output", "-json"], directory, 60, extra_env=env)
                if code:
                    raise ValueError("Output retrieval failed")
                record["resource_details"] = safe_outputs(json.loads(output))
            except Exception:
                record["outputs_unavailable"] = True
        _write(directory, record)
        event("APPLY_FINISHED", deployment_id=deployment_id, status=record["status"], reviewer=reviewer.strip(), plan_sha256=record["plan_sha256"])
        return record


def safe_outputs(outputs):
    allowed = {"resource_name", "resource_id", "instance_id", "private_ip", "public_ip",
               "cluster_arn", "kubernetes_version", "node_group_name", "worker_count"}
    return {key: item["value"] for key, item in outputs.items()
            if key in allowed and isinstance(item, dict) and not item.get("sensitive", True)
            and isinstance(item.get("value"), (str, int, float)) and not isinstance(item.get("value"), bool)}

def review(deployment_id: str, reviewer: str, token: str, approve: bool) -> dict:
    """Record a checksum-bound POC review; cannot apply an unvalidated target."""
    import fcntl
    import hmac
    import re
    import terraform_service as tf
    from placement import timestamp
    if not re.fullmatch(r"poc-[0-9a-f]{32}", deployment_id):
        raise ValueError("Invalid deployment ID")
    expected = os.getenv("APPROVAL_TOKEN", "")
    if not expected:
        for location in (Path("/run/secrets/cloudpilot-approval-token"), Path.home() / ".cloudpilot-approval-token"):
            if location.is_file():
                expected = location.read_text().strip()
                break
    if not expected or not hmac.compare_digest(expected, token) or not reviewer.strip():
        raise ValueError("POC reviewer name and valid approval token required")
    directory = Path(os.getenv("DATA_DIR", "/data")) / "poc_plans" / deployment_id
    with (directory / ".review.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        if record["status"] != "PLAN_READY_EXECUTION_UNVALIDATED":
            raise ValueError("Plan is not awaiting POC review")
        age = (datetime.now(timezone.utc) - timestamp(record["created_at"])).total_seconds()
        if not 0 <= age <= 3600:
            raise ValueError("Plan expired; generate a new plan")
        if tf._sha256(directory / "tfplan") != record["plan_sha256"] or tf._sha256(directory / "main.tf.json") != record["configuration_sha256"]:
            raise ValueError("Plan or configuration checksum changed")
        candidate, request = record["candidate"], record["requirements"]
        if evaluate(dict(candidate), request, request.get("latency_evidence", []))["status"] != "pass":
            raise ValueError("Policy evidence no longer passes")
        conn = get_connection(record["connection_id"])
        if not conn or conn.get("connection_status") != "CONNECTED":
            raise ValueError("Connection no longer verified")
        metadata = conn.get("metadata", {})
        identity = conn.get("account_id") or metadata.get("subscription_id") or metadata.get("project_id")
        if identity != record["target_identity"] or record["state_bucket"] != tf.TF_STATE_BUCKET:
            raise ValueError("Target identity or backend changed; generate a new plan")
        record.update(status="REVIEWED_EXECUTION_DISABLED" if approve else "REJECTED", reviewer=reviewer.strip(), reviewed_at=datetime.now(timezone.utc).isoformat())
        _write(directory, record)
        event("PLAN_REVIEWED", deployment_id=deployment_id, reviewer=reviewer.strip(), status=record["status"], plan_sha256=record["plan_sha256"])
        return record


def _destroy_context(deployment_id):
    import re
    import terraform_service as tf
    if not re.fullmatch(r"poc-[0-9a-f]{32}", deployment_id):
        raise ValueError("Invalid deployment ID")
    enabled, message = tf.is_enabled()
    if not enabled:
        raise ValueError(message)
    return tf, Path(os.getenv("DATA_DIR", "/data")) / "poc_plans" / deployment_id


def _destroy_runtime(tf, directory, record):
    if record["candidate"]["provider"] != "AWS":
        raise ValueError("Destroy execution currently supports AWS only")
    if (record.get("state_backend") != "s3" or record.get("state_bucket") != tf.TF_STATE_BUCKET
            or record.get("state_region") != tf.TF_STATE_REGION
            or record.get("state_key") != "iipp/poc/" + record["deployment_id"] + "/terraform.tfstate"):
        raise ValueError("Recorded Terraform backend differs from runner configuration")
    for name, key in (("main.tf.json", "configuration_sha256"), ("terraform.tfvars.json", "inputs_sha256"),
                      (".terraform.lock.hcl", "lock_sha256")):
        if not record.get(key) or tf._sha256(directory / name) != record[key]:
            raise ValueError("Configuration, inputs or provider lock changed")
    conn = get_connection(record["connection_id"])
    if not conn or conn.get("account_id") != record["target_identity"] or conn.get("connection_status") != "CONNECTED":
        raise ValueError("Target connection changed or is no longer verified")
    inputs = json.loads((directory / "terraform.tfvars.json").read_text(encoding="utf-8"))
    runtime = get_aws_terraform_runtime(record["connection_id"])
    if runtime["account_id"] != record["target_identity"] or runtime["target_role_arn"] != inputs["execution_role_arn"]:
        raise ValueError("Target execution role changed")
    # Cleanup must remain possible when price, hardware or TTL evidence has expired.
    if not runtime.get("external_id"):
        raise ValueError("Execution role External ID is required")
    env = dict(runtime.get("source_env", {}), TF_VAR_external_id=runtime["external_id"], TF_DATA_DIR=str(directory / ".terraform"))
    code, _ = tf._run_init(_backend(record, readonly=True), directory, 300, extra_env=env)
    if code:
        raise ValueError("Terraform destroy backend initialization failed; no deletion attempted")
    for name, key in (("main.tf.json", "configuration_sha256"), ("terraform.tfvars.json", "inputs_sha256"),
                      (".terraform.lock.hcl", "lock_sha256")):
        if tf._sha256(directory / name) != record[key]:
            raise ValueError("Configuration, inputs or provider lock changed during initialization")
    return env


def _resource_ids(tf, directory, env):
    code, output = tf._run(["terraform", "show", "-json"], directory, 120, extra_env=env)
    if code:
        raise ValueError("Could not read managed resource identifiers")
    values = json.loads(output).get("values", {}).get("root_module", {})
    return [{"type": r["type"], "name": r.get("values", {}).get("tags", {}).get("Name") or r.get("values", {}).get("name") or r["name"],
             "id": r["values"]["id"]} for r in values.get("resources", [])
            if r.get("mode") == "managed" and r.get("values", {}).get("id")]


def prepare_destroy_plan(deployment_id):
    """Save a destroy plan without executing it."""
    tf, directory = _destroy_context(deployment_id)
    with _lock(directory):
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        if record["status"] not in {"SUCCEEDED", "DESTROY_FAILED", "DESTROY_PLAN_FAILED", "DESTROY_AWAITING_APPROVAL"}:
            raise ValueError("Deployment is not eligible for destroy planning")
        try:
            env = _destroy_runtime(tf, directory, record)
            record["resource_identifiers"] = _resource_ids(tf, directory, env)
            code, _ = tf._run(["terraform", "plan", "-destroy", "-input=false", "-no-color", "-out=tfplan-destroy"], directory, 900, extra_env=env)
            if code:
                raise ValueError("Terraform destroy planning failed; inspect protected runner diagnostics")
            code, summary = tf._run(["terraform", "show", "-no-color", "tfplan-destroy"], directory, 120, extra_env=env)
            if code:
                raise ValueError("Destroy plan summary unavailable; no deletion attempted")
            (directory / "tfplan-destroy").chmod(0o600)
            record.update(status="DESTROY_AWAITING_APPROVAL", destroy_plan_summary=summary,
                          destroy_plan_sha256=tf._sha256(directory / "tfplan-destroy"),
                          destroy_plan_created_at=datetime.now(timezone.utc).isoformat())
        except Exception:
            record.update(status="DESTROY_PLAN_FAILED", error="Destroy planning failed; no deletion attempted")
            raise
        finally:
            _write(directory, record)
            event("DESTROY_PLAN_FINISHED", deployment_id=deployment_id, status=record["status"])
        return record


def apply_destroy_plan(deployment_id, reviewer, token):
    from control_plane import enabled, authorize_connection
    if enabled():
        import re
        if not re.fullmatch(r'poc-[0-9a-f]{32}', deployment_id):
            raise ValueError('Invalid deployment ID')
        directory = Path(os.getenv('DATA_DIR', '/data')) / 'poc_plans' / deployment_id
        metadata = json.loads((directory / 'record.json').read_text(encoding='utf-8'))
        actor = authorize_connection(metadata.get('connection_id'), ('administrator', 'approver'))
        reviewer = actor['username']
    from approval import validate
    from placement import timestamp
    validate(reviewer, token)
    tf, directory = _destroy_context(deployment_id)
    with _lock(directory):
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        if record["status"] != "DESTROY_AWAITING_APPROVAL":
            raise ValueError("Deployment is not awaiting destroy approval")
        age = (datetime.now(timezone.utc) - timestamp(record["destroy_plan_created_at"])).total_seconds()
        if not 0 <= age <= 3600:
            raise ValueError("Destroy plan expired; prepare a new plan")
        path = directory / "tfplan-destroy"
        if not path.is_file() or tf._sha256(path) != record["destroy_plan_sha256"]:
            raise ValueError("Destroy plan checksum changed")
        env = _destroy_runtime(tf, directory, record)
        if tf._sha256(path) != record["destroy_plan_sha256"]:
            raise ValueError("Destroy plan changed during initialization")
        record.update(status="DESTROYING", destroy_reviewer=reviewer.strip())
        _write(directory, record)
        event("DESTROY_APPROVED", deployment_id=deployment_id, reviewer=reviewer.strip(), plan_sha256=record["destroy_plan_sha256"])
        try:
            code, _ = tf._run(["terraform", "apply", "-input=false", "-no-color", "tfplan-destroy"], directory, max(1800, execution_timeout(record["requirements"]["kind"])), extra_env=env)
            record.update(status="DESTROYED" if code == 0 else "DESTROY_FAILED")
        except Exception:
            record["status"] = "DESTROY_FAILED"
        record["destroy_completed_at"] = datetime.now(timezone.utc).isoformat()
        _write(directory, record)
        event("DESTROY_FINISHED", deployment_id=deployment_id, status=record["status"])
        return record
