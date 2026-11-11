"""Controlled Terraform plan/apply service for the IIPP AWS POC stack.

New deployments require an S3 backend with native lockfiles. The S3 backend uses
the controller identity; the Terraform AWS provider may assume a distinct target
account execution role. Saved plans require an explicit reviewer approval.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from provider_connections import get_aws_session, get_aws_terraform_runtime
from policy_engine import evaluate_aws_provision_request

APP_ROOT = Path(__file__).resolve().parent
STACK_TEMPLATE = APP_ROOT / "terraform" / "stacks" / "aws_demo"
INSTANCE_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*$")
INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")
AMI_RE = re.compile(r"^ami-[0-9a-f]{8,17}$")
SUBNET_RE = re.compile(r"^subnet-[0-9a-f]{8,17}$")
SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
REGION_RE = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d+$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,61}$")
TF_STATE_BUCKET = os.getenv("TF_STATE_BUCKET", "").strip()
TF_STATE_REGION = os.getenv("TF_STATE_REGION", os.getenv("AWS_REGION", "ap-south-1")).strip()
TF_STATE_PREFIX = os.getenv("TF_STATE_PREFIX", "iipp/deployments").strip("/")
TF_PLUGIN_CACHE_DIR = os.getenv("TF_PLUGIN_CACHE_DIR", "/terraform-plugin-cache")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_dir() -> Path:
    path = Path(os.getenv("DATA_DIR", "/data")) / "terraform_deployments"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def is_enabled() -> tuple[bool, str]:
    if os.getenv("ENABLE_TERRAFORM_WORKFLOW", "false").lower() not in {"1", "true", "yes"}:
        return False, "Terraform deployment workflow is disabled. Set ENABLE_TERRAFORM_WORKFLOW=true only after reviewing IAM and dashboard access."
    if shutil.which("terraform") is None:
        return False, "Terraform CLI is not installed in the application container. Rebuild with the provided Dockerfile or install a pinned Terraform CLI."
    if not TF_STATE_BUCKET:
        return False, "Terraform is disabled until TF_STATE_BUCKET is configured and the controller IAM role has access to the dedicated S3 state bucket. New states will not be written locally."
    if not STACK_TEMPLATE.is_dir():
        return False, "Terraform AWS demo stack is missing from the application package."
    return True, f"Terraform CLI and S3 backend are configured ({TF_STATE_BUCKET}/{TF_STATE_PREFIX}/); apply still requires explicit reviewer approval."


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    fields = {key: str(payload.get(key, "") or "").strip() for key in (
        "os", "region", "ami_id", "instance_type", "subnet_id", "security_group_id", "instance_name", "owner", "instance_profile_name", "aws_connection_id", "environment"
    )}
    fields["os"] = fields["os"] or "Linux"
    if fields["os"] not in {"Linux", "Windows"}:
        raise ValueError("Only Linux and Windows Server are supported")
    try:
        ttl_days = int(payload.get("ttl_days", 2))
    except (TypeError, ValueError) as exc:
        raise ValueError("TTL days must be an integer between 1 and 30.") from exc
    try:
        min_vcpus = int(payload.get("min_vcpus", 1))
        min_memory_gib = float(payload.get("min_memory_gib", 0.5))
    except (TypeError, ValueError) as exc:
        raise ValueError("Minimum vCPU and RAM requirements must be numeric.") from exc
    if min_vcpus < 1 or min_vcpus > 128:
        raise ValueError("Minimum vCPU requirement must be between 1 and 128.")
    if min_memory_gib < 0.5 or min_memory_gib > 4096:
        raise ValueError("Minimum RAM requirement must be between 0.5 and 4096 GiB.")
    if not REGION_RE.fullmatch(fields["region"]): raise ValueError("Provide a valid AWS region identifier, for example ap-south-1.")
    if not AMI_RE.fullmatch(fields["ami_id"]): raise ValueError("AMI ID must look like ami-0123456789abcdef0.")
    if not INSTANCE_TYPE_RE.fullmatch(fields["instance_type"]): raise ValueError("Provide a valid EC2 instance type identifier, for example m7i.large.")
    if not SUBNET_RE.fullmatch(fields["subnet_id"]): raise ValueError("Provide an existing subnet ID (subnet-...).")
    if not SG_RE.fullmatch(fields["security_group_id"]): raise ValueError("Provide an existing security group ID (sg-...).")
    if not NAME_RE.fullmatch(fields["instance_name"]): raise ValueError("Instance name must be 2-63 letters, digits, dot, underscore or hyphen.")
    if not fields["owner"]: fields["owner"] = "Innominds-IIPP"
    fields["environment"] = (fields["environment"] or "poc").lower()
    if fields["environment"] not in {"poc", "test"}:
        raise ValueError("Environment must be POC or TEST.")
    if len(fields["owner"]) > 64 or any(ord(ch) < 32 for ch in fields["owner"]): raise ValueError("Owner tag must be at most 64 printable characters.")
    if fields["instance_profile_name"] and not re.fullmatch(r"[A-Za-z0-9+=,.@_-]{1,128}", fields["instance_profile_name"]):
        raise ValueError("Instance profile name contains unsupported characters.")
    if not 1 <= ttl_days <= 30: raise ValueError("TTL days must be between 1 and 30.")
    return {**fields, "aws_connection_id": fields["aws_connection_id"] or None,
            "ttl_days": ttl_days, "min_vcpus": min_vcpus, "min_memory_gib": min_memory_gib}


def _terraform_runtime_env(connection_id: str | None, expected_role_arn: str | None = None) -> dict[str, str]:
    runtime = get_aws_terraform_runtime(connection_id)
    role_arn = str(runtime.get("target_role_arn") or "")
    if expected_role_arn is not None and role_arn != expected_role_arn:
        raise ValueError("The saved AWS Terraform execution role differs from the current connection. Review the connection and prepare a new plan.")
    env = dict(runtime.get("source_env") or {})
    env["AWS_DEFAULT_REGION"] = TF_STATE_REGION
    env["AWS_REGION"] = TF_STATE_REGION
    env["TF_VAR_target_role_arn"] = role_arn
    env["TF_VAR_target_role_external_id"] = str(runtime.get("external_id") or "")
    return env


def _backend_init_args(deployment_id: str) -> list[str]:
    if not TF_STATE_BUCKET:
        raise RuntimeError("TF_STATE_BUCKET is required. Refusing to initialize Terraform with local state for new deployments.")
    key = f"{TF_STATE_PREFIX}/{deployment_id}/terraform.tfstate"
    return ["terraform", "init", "-input=false", "-no-color",
            f"-backend-config=bucket={TF_STATE_BUCKET}",
            f"-backend-config=key={key}",
            f"-backend-config=region={TF_STATE_REGION}",
            "-backend-config=encrypt=true", "-backend-config=use_lockfile=true"]


@contextmanager
def _terraform_init_lock():
    """Serialize init across deployments because Terraform plugin cache is not concurrency-safe."""
    import fcntl
    data_root = Path(os.getenv("DATA_DIR", "/data"))
    data_root.mkdir(parents=True, exist_ok=True)
    lock_file = data_root / ".iipp-terraform-init.lock"
    with lock_file.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _run_init(args: list[str], cwd: Path, timeout: int = 240,
              extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    with _terraform_init_lock():
        return _run(args, cwd, timeout, extra_env=extra_env)


def _run(args: list[str], cwd: Path, timeout: int = 300,
         extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    # Argument arrays only; never invoke a shell with form/user input.
    env = os.environ.copy()
    env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "TF_LOG": "off",
                "TF_PLUGIN_CACHE_DIR": TF_PLUGIN_CACHE_DIR})
    if extra_env:
        env.update(extra_env)
    try:
        Path(TF_PLUGIN_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    result = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False)
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    # Keep only recent lines and avoid dumping complete command environments/credentials.
    return result.returncode, "\n".join(output.splitlines()[-100:])[-20000:]


def _write_record(directory: Path, record: dict[str, Any]) -> None:
    path = directory / "deployment.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, default=str))
    tmp.chmod(0o600)
    tmp.replace(path)


def _transition(record: dict[str, Any], status: str, **fields: Any) -> dict[str, Any]:
    """Record each workflow state transition for UI history/auditability."""
    timestamp = _now()
    record["status"] = status
    record["updated_at"] = timestamp
    record.setdefault("state_history", []).append({"status": status, "timestamp": timestamp})
    record.update(fields)
    return record


def _read_record(directory: Path) -> dict[str, Any]:
    path = directory / "deployment.json"
    if not path.exists(): raise ValueError("Deployment record was not found.")
    return json.loads(path.read_text())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _serialized_deployment_operation(func):
    """Reject concurrent state transitions for the same deployment record."""
    @wraps(func)
    def wrapper(deployment_id: str, *args, **kwargs):
        import fcntl
        # Validate before resolving a path; the lock must not accept traversal.
        if not re.fullmatch(r"cp-[0-9a-f]{16}", str(deployment_id)):
            raise ValueError("Invalid deployment ID.")
        directory = _base_dir() / deployment_id
        if not directory.is_dir():
            raise ValueError("Deployment record was not found.")
        lock_path = directory / ".operation.lock"
        with lock_path.open("a+") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another operation is already running for this deployment. Wait and refresh the status before retrying.") from exc
            try:
                return func(deployment_id, *args, **kwargs)
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return wrapper



def _cleanup_terraform_working_cache(directory: Path, record: dict[str, Any], reason: str) -> None:
    """Remove regenerable Terraform working metadata/provider copies, never state or plans."""
    cache_dir = directory / ".terraform"
    try:
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        record["terraform_working_cache"] = {"status": "REMOVED", "reason": reason, "removed_at": _now()}
    except OSError as exc:
        record["terraform_working_cache"] = {
            "status": "CLEANUP_FAILED", "reason": reason,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


def _reinitialize_for_saved_plan(deployment_id: str, directory: Path, record: dict[str, Any],
                                 runtime_env: dict[str, str]) -> None:
    """Recreate Terraform's working directory without changing a reviewed plan or backend."""
    if record.get("state_backend") == "s3":
        if record.get("state_bucket") != TF_STATE_BUCKET:
            raise ValueError("The configured S3 state bucket differs from the deployment's recorded bucket.")
        args = _backend_init_args(deployment_id) + ["-lockfile=readonly"]
    else:
        args = ["terraform", "init", "-input=false", "-no-color"]
    code, output = _run_init(args, directory, 240, extra_env=runtime_env)
    if code:
        raise RuntimeError("Terraform reinitialization failed; the saved plan was not applied. " + output[-3000:])

def _restrict_sensitive_files(directory: Path) -> None:
    """Terraform plans/state may contain sensitive values even when CLI output masks them."""
    for name in ("tfplan", "tfplan-destroy", "terraform.tfstate", "terraform.tfstate.backup", "terraform.tfvars.json"):
        path = directory / name
        if path.exists() and path.is_file():
            try:
                path.chmod(0o600)
            except OSError:
                pass


def list_deployments() -> list[dict[str, Any]]:
    records = []
    for directory in _base_dir().iterdir():
        if not directory.is_dir(): continue
        try:
            records.append(_read_record(directory))
        except Exception:
            continue
    return sorted(records, key=lambda x: x.get("created_at", ""), reverse=True)


def _validate_aws_references(payload: dict[str, Any]) -> None:
    """Check that supplied resources exist in the selected region and are compatible."""
    if payload.get("aws_connection_id"):
        session = get_aws_session(payload.get("aws_connection_id"), purpose="inventory")
        ec2 = session.client("ec2", region_name=payload["region"], config=Config(connect_timeout=5, read_timeout=12, retries={"max_attempts": 2}))
    else:
        ec2 = boto3.client("ec2", region_name=payload["region"], config=Config(connect_timeout=5, read_timeout=12, retries={"max_attempts": 2}))
    try:
        images = ec2.describe_images(ImageIds=[payload["ami_id"]]).get("Images", [])
        if not images:
            raise ValueError(f"AMI {payload['ami_id']} was not returned in {payload['region']}.")
        image = images[0]
        if image.get("State", "available") != "available":
            raise ValueError(f"AMI {payload['ami_id']} is not available in {payload['region']}.")
        if image.get("Architecture", "x86_64") not in {"x86_64"}:
            raise ValueError("This reviewed demo stack currently supports x86_64 Linux AMIs. Select an x86_64 EC2 type discovered from the live AWS API.")
        platform = str(image.get("PlatformDetails", "Linux/UNIX")).lower()
        expected_os = payload.get("os", "Linux")
        if ("windows" in platform) != (expected_os == "Windows") or (expected_os == "Linux" and "linux" not in platform and "unix" not in platform):
            raise ValueError("The selected AMI does not match the requested Linux/Windows OS.")
        if image.get("ProductCodes"):
            raise ValueError("Marketplace images are outside the POC")
        subnets = ec2.describe_subnets(SubnetIds=[payload["subnet_id"]]).get("Subnets", [])
        groups = ec2.describe_security_groups(GroupIds=[payload["security_group_id"]]).get("SecurityGroups", [])
        if not subnets: raise ValueError(f"Subnet {payload['subnet_id']} was not found in {payload['region']}.")
        if not groups: raise ValueError(f"Security group {payload['security_group_id']} was not found in {payload['region']}.")
        subnet_vpc = subnets[0].get("VpcId")
        subnet_az = subnets[0].get("AvailabilityZone")
        if not subnet_az:
            raise ValueError(f"EC2 did not return the Availability Zone for subnet {payload['subnet_id']}.")
        if groups[0].get("VpcId") != subnet_vpc:
            raise ValueError("The selected subnet and security group are in different VPCs.")
        for permission in groups[0].get("IpPermissions", []):
            for source in permission.get("IpRanges", []):
                if source.get("CidrIp") == "0.0.0.0/0":
                    raise ValueError("Policy denied the selected security group because it allows IPv4 ingress from 0.0.0.0/0.")
            for source in permission.get("Ipv6Ranges", []):
                if source.get("CidrIpv6") == "::/0":
                    raise ValueError("Policy denied the selected security group because it allows IPv6 ingress from ::/0.")
        # Validate the type in the subnet's actual AZ, not just the broader region.
        # This catches region-valid types that cannot launch into the chosen subnet.
        offerings = ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [payload["instance_type"]]},
                     {"Name": "location", "Values": [subnet_az]}],
        ).get("InstanceTypeOfferings", [])
        if not any(x.get("InstanceType") == payload["instance_type"] for x in offerings):
            raise ValueError(f"EC2 does not list {payload['instance_type']} as offered in subnet Availability Zone {subnet_az}.")
        type_rows = ec2.describe_instance_types(InstanceTypes=[payload["instance_type"]]).get("InstanceTypes", [])
        if not type_rows:
            raise ValueError(f"EC2 returned no live specification for {payload['instance_type']}.")
        type_spec = type_rows[0]
        architectures = type_spec.get("ProcessorInfo", {}).get("SupportedArchitectures") or []
        root_types = type_spec.get("SupportedRootDeviceTypes") or []
        virtualization_types = type_spec.get("SupportedVirtualizationTypes") or []
        usage_classes = type_spec.get("SupportedUsageClasses") or []
        if "x86_64" not in architectures:
            raise ValueError(f"EC2 type {payload['instance_type']} does not support x86_64, which is required by the selected AMI.")
        if "ebs" not in root_types:
            raise ValueError(f"EC2 type {payload['instance_type']} does not support an EBS root device.")
        if "hvm" not in virtualization_types:
            raise ValueError(f"EC2 type {payload['instance_type']} does not support HVM virtualization.")
        if usage_classes and "on-demand" not in usage_classes:
            raise ValueError(f"EC2 type {payload['instance_type']} does not support On-Demand usage.")
        if type_spec.get("BareMetal") is True:
            raise ValueError(f"EC2 type {payload['instance_type']} is bare metal; this POC supports virtualized EC2 types only.")
        actual_vcpus = int(type_spec.get("VCpuInfo", {}).get("DefaultVCpus", 0))
        actual_memory_gib = float(type_spec.get("MemoryInfo", {}).get("SizeInMiB", 0)) / 1024
        if actual_vcpus < int(payload.get("min_vcpus", 1)) or actual_memory_gib < float(payload.get("min_memory_gib", 0.5)):
            raise ValueError(
                f"Selected {payload['instance_type']} provides {actual_vcpus} vCPU / {actual_memory_gib:g} GiB RAM, "
                f"below the requested minimum {payload.get('min_vcpus', 1)} vCPU / {float(payload.get('min_memory_gib', 0.5)):g} GiB."
            )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not validate AMI/subnet/security group/instance type using live EC2 APIs: {type(exc).__name__}: {exc}. Check the IIPP controller IAM role's read permissions.") from exc


def prepare_plan(payload: dict[str, Any], *, reuse_from=None) -> dict[str, Any]:
    from control_plane import authorize_connection
    actor = authorize_connection(payload.get('aws_connection_id'))
    if actor:
        payload = dict(payload, initiated_by=actor['username'])
    enabled, message = is_enabled()
    if not enabled: raise RuntimeError(message)
    safe = _validate_payload(payload)
    if not safe.get("aws_connection_id"):
        raise ValueError("Select a verified AWS-POC or AWS-TEST connection. Provisioning directly into the IIPP controller account is disabled in this release.")
    runtime = get_aws_terraform_runtime(safe.get("aws_connection_id"))
    policy_decision = evaluate_aws_provision_request(safe, runtime)
    _validate_aws_references(safe)
    deployment_id = "cp-" + uuid.uuid4().hex[:16]
    directory = _base_dir() / deployment_id
    directory.mkdir(mode=0o700)
    bundle = None
    if reuse_from:
        from reuse_artifacts import load, copy_sources
        bundle = load(*reuse_from)
        if reuse_from[0] != 'terraform_deployments':
            raise ValueError('Saved source uses another Terraform workflow')
        original = bundle[1].get('requested_configuration', {})
        if safe['instance_type'] != original.get('instance_type') or safe['os'] != original.get('os', 'Linux'):
            raise ValueError('Reuse preserves the saved infrastructure configuration')
        copy_sources(bundle[0], directory)
    for source in (() if bundle else STACK_TEMPLATE.iterdir()):
        if source.is_file() and source.suffix in {".tf", ".md"}:
            shutil.copyfile(source, directory / source.name)
    tfvars = {key: safe[key] for key in ("os", "region", "ami_id", "instance_type", "subnet_id", "security_group_id", "instance_name", "owner", "ttl_days", "instance_profile_name", "environment")}
    (directory / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))
    (directory / "terraform.tfvars.json").chmod(0o600)
    created_at = _now()
    record: dict[str, Any] = {
        "deployment_id": deployment_id, "status": "DRAFT", "created_at": created_at,
        "updated_at": created_at, "state_history": [{"status": "DRAFT", "timestamp": created_at}],
        "provider": "AWS", "region": safe["region"],
        "initiated_by": payload.get("initiated_by"),
        "aws_connection_id": safe.get("aws_connection_id"),
        "requested_configuration": {k: v for k, v in safe.items() if k not in {"instance_profile_name"}},
        "execution_mode": "Terraform S3 remote-state saved-plan approval workflow",
        "state_backend": "s3", "state_bucket": TF_STATE_BUCKET,
        "state_key": f"{TF_STATE_PREFIX}/{deployment_id}/terraform.tfstate",
        "state_locking": "S3 native lockfile",
        "policy_decision": policy_decision,
        "target_account_name": runtime.get("connection_name"),
        "warnings": ["This creates a new EC2 instance; it does not resize QuickCart.",
                     "Public IP assignment is disabled; the supplied subnet and security group must be suitable.",
                     "Terraform state is stored in the configured S3 bucket with native S3 locking. Provider binaries use the shared plugin cache.",
                     "AWS discovery/validation uses the selected connection's inventory role; Terraform resource operations use its execution role."],
    }
    if bundle:
        record.update(source_deployment=reuse_from[1], source_artifact_sha256=bundle[2])
    _write_record(directory, record)
    try:
        _transition(record, "VALIDATED")
        _write_record(directory, record)
        runtime = get_aws_terraform_runtime(safe.get("aws_connection_id"))
        record["target_account_id"] = runtime.get("account_id")
        record["target_execution_role_arn"] = runtime.get("target_role_arn")
        _write_record(directory, record)
        runtime_env = _terraform_runtime_env(safe.get("aws_connection_id"))
        code, output = _run_init(_backend_init_args(deployment_id) + (['-lockfile=readonly'] if bundle else []), directory, 240, extra_env=runtime_env)
        if code:
            _transition(record, "FAILED", error="terraform init failed", log=output)
            _cleanup_terraform_working_cache(directory, record, "init failed before a saved plan existed")
            _write_record(directory, record); return record
        code, output = _run(["terraform", "validate", "-no-color"], directory, 120, extra_env=runtime_env)
        if code:
            _transition(record, "FAILED", error="terraform validate failed", log=output)
            _cleanup_terraform_working_cache(directory, record, "validate failed before a saved plan existed")
            _write_record(directory, record); return record
        _transition(record, "PLAN_READY")
        _write_record(directory, record)
        (directory / "terraform.tfvars.json").chmod(0o600)
        code, plan_output = _run(["terraform", "plan", "-input=false", "-no-color", "-out=tfplan", "-var-file=terraform.tfvars.json"], directory, 300, extra_env=runtime_env)
        if code:
            _transition(record, "FAILED", error="terraform plan failed", log=plan_output)
            _cleanup_terraform_working_cache(directory, record, "plan failed; backend and state remain recoverable")
            _write_record(directory, record); _restrict_sensitive_files(directory); return record
        plan_file = directory / "tfplan"
        plan_file.chmod(0o600)
        _restrict_sensitive_files(directory)
        show_code, summary = _run(["terraform", "show", "-no-color", "tfplan"], directory, 120, extra_env=runtime_env)
        from reuse_artifacts import snapshot
        snapshot(directory, record)
        _transition(record, "AWAITING_APPROVAL", plan_sha256=_sha256(plan_file),
                    plan_file="tfplan", plan_summary=summary if show_code == 0 else "Saved plan created; human-readable summary unavailable.",
                    plan_created_at=_now(), plan_changes="NOT APPLIED")
        _cleanup_terraform_working_cache(directory, record, "saved plan is retained; working directory is recreated at approval time")
        _write_record(directory, record)
        return record
    except subprocess.TimeoutExpired:
        _transition(record, "FAILED", error="Terraform command exceeded its time limit.")
        _cleanup_terraform_working_cache(directory, record, "Terraform command timed out before approval")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record
    except Exception as exc:
        _transition(record, "FAILED", error=f"Terraform workflow error: {type(exc).__name__}: {exc}")
        if record.get("status") != "AWAITING_APPROVAL":
            _cleanup_terraform_working_cache(directory, record, "workflow failed before approval")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record


@_serialized_deployment_operation
def apply_saved_plan(deployment_id: str, reviewer: str) -> dict[str, Any]:
    """Apply only the exact, checksummed, unexpired plan after reviewer approval."""
    from control_plane import guard_apply
    guard_apply('terraform_deployments', deployment_id)
    enabled, message = is_enabled()
    if not enabled: raise RuntimeError(message)
    directory = _base_dir() / deployment_id
    record = _read_record(directory)
    if record.get("state_backend") != "s3":
        raise ValueError("This is a legacy local-state deployment. It is preserved but cannot be applied by the new S3-only workflow. Create a fresh S3-backed plan after reviewing the target account.")
    if record.get("state_bucket") != TF_STATE_BUCKET:
        raise ValueError("The configured S3 state bucket differs from the bucket recorded for this deployment. Refusing to apply against a different backend.")
    runtime_env = _terraform_runtime_env(record.get("aws_connection_id"), record.get("target_execution_role_arn", ""))
    if record.get("status") != "AWAITING_APPROVAL":
        raise ValueError(f"Deployment is not awaiting approval (status={record.get('status')}).")
    try:
        created = datetime.fromisoformat(str(record.get("plan_created_at", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Saved plan timestamp is missing or invalid. Generate and review a fresh plan.") from exc
    if created.tzinfo is None: created = created.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    if age < -timedelta(minutes=5):
        raise ValueError("Saved Terraform plan timestamp is unexpectedly in the future. Generate and review a fresh plan.")
    if age > timedelta(hours=4):
        raise ValueError("Saved Terraform plan is older than four hours. Generate and review a fresh plan before applying.")
    if record.get("plan_file", "tfplan") != "tfplan":
        raise ValueError("Unexpected saved-plan path. Generate and review a new plan.")
    if record.get('reuse_manifest'):
        from reuse_artifacts import digest
        for name, checksum in record['reuse_manifest']['files'].items():
            path = directory / name
            if Path(name).name != name or path.is_symlink() or not path.is_file() or digest(path) != checksum:
                raise ValueError('Saved Terraform source changed. Generate a new plan.')
    plan_file = directory / "tfplan"
    if not plan_file.is_file() or _sha256(plan_file) != record.get("plan_sha256"):
        raise ValueError("Saved plan is missing or its checksum changed. Generate and review a new plan.")
    reviewer_name = str(reviewer or "").strip()
    if not reviewer_name: raise ValueError("Reviewer name is required.")
    try:
        _reinitialize_for_saved_plan(deployment_id, directory, record, runtime_env)
    except Exception as exc:
        record["reinitialize_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        _cleanup_terraform_working_cache(directory, record, "approval-time reinitialization failed; retry after fixing the backend/provider issue")
        _write_record(directory, record)
        _restrict_sensitive_files(directory)
        raise
    if not plan_file.is_file() or _sha256(plan_file) != record.get("plan_sha256"):
        raise ValueError("Saved plan changed during Terraform reinitialization. Generate and review a fresh plan.")
    _transition(record, "APPROVED", reviewer=reviewer_name, approved_at=_now())
    _write_record(directory, record)
    _transition(record, "APPLYING")
    _write_record(directory, record)
    try:
        code, output = _run(["terraform", "apply", "-input=false", "-no-color", str(plan_file)], directory, 600, extra_env=runtime_env)
    except subprocess.TimeoutExpired:
        _transition(record, "FAILED", error="Terraform apply timed out; verify AWS resources and Terraform state before retrying.")
        _cleanup_terraform_working_cache(directory, record, "apply timed out; reinitialize before recovery")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record
    except Exception as exc:
        _transition(record, "FAILED", error=f"Terraform apply raised {type(exc).__name__}; verify AWS resources and Terraform state before retrying.")
        _cleanup_terraform_working_cache(directory, record, "apply failed; reinitialize before recovery")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record
    _restrict_sensitive_files(directory)
    if code:
        _transition(record, "FAILED", error="terraform apply failed", log=output)
        _cleanup_terraform_working_cache(directory, record, "apply failed; reinitialize before recovery")
        _write_record(directory, record)
        return record
    outputs: dict[str, Any] = {}
    outputs_error = None
    try:
        out_code, out = _run(["terraform", "output", "-json"], directory, 60, extra_env=runtime_env)
        if out_code == 0:
            try: outputs = json.loads(out)
            except json.JSONDecodeError: outputs_error = "Terraform output was not valid JSON."
        else:
            outputs_error = "Terraform apply succeeded, but output collection failed: " + out[-4000:]
    except Exception as exc:
        outputs_error = f"Terraform apply succeeded, but output collection raised {type(exc).__name__}."
    _cleanup_terraform_working_cache(directory, record, "apply completed; S3 state is authoritative")
    _transition(record, "SUCCEEDED", outputs=outputs, outputs_error=outputs_error,
                apply_log=output, completed_at=_now())
    _write_record(directory, record)
    _restrict_sensitive_files(directory)
    return record


@_serialized_deployment_operation
def prepare_destroy_plan(deployment_id: str) -> dict[str, Any]:
    """Prepare but do not apply a destroy plan for an instance created by this workflow."""
    enabled, message = is_enabled()
    if not enabled: raise RuntimeError(message)
    directory = _base_dir() / deployment_id
    record = _read_record(directory)
    if record.get("state_backend") == "s3":
        if record.get("state_bucket") != TF_STATE_BUCKET:
            raise ValueError("The configured S3 state bucket differs from the deployment's recorded bucket.")
        runtime_env = _terraform_runtime_env(record.get("aws_connection_id"), record.get("target_execution_role_arn", ""))
        init_code, init_output = _run_init(_backend_init_args(deployment_id), directory, 240, extra_env=runtime_env)
        if init_code:
            raise RuntimeError("Could not reinitialize the S3 backend for destroy planning. State was not changed. " + init_output[-2000:])
    else:
        # Legacy deployments retain their local state; reinitialize locally without migrating it.
        runtime_env = _terraform_runtime_env(record.get("aws_connection_id"))
        init_code, init_output = _run_init(["terraform", "init", "-input=false", "-no-color"], directory, 240, extra_env=runtime_env)
        if init_code:
            raise RuntimeError("Could not reinitialize the legacy local backend. State was not changed. " + init_output[-2000:])
    if record.get("status") not in {"SUCCEEDED", "DESTROY_PLAN_FAILED", "DESTROY_FAILED", "DESTROY_AWAITING_APPROVAL"}:
        raise ValueError("Only a successful IIPP-managed deployment, or a previous destroy failure, can be prepared for destroy.")
    try:
        code, output = _run(["terraform", "plan", "-destroy", "-input=false", "-no-color", "-out=tfplan-destroy", "-var-file=terraform.tfvars.json"], directory, 300, extra_env=runtime_env)
        if code:
            _transition(record, "DESTROY_PLAN_FAILED", error="terraform destroy plan failed", log=output)
            _cleanup_terraform_working_cache(directory, record, "destroy plan failed; reinitialize before retry")
            _write_record(directory, record); return record
        path = directory / "tfplan-destroy"
        if not path.is_file():
            _transition(record, "DESTROY_PLAN_FAILED", error="Terraform returned success but no saved destroy plan was found.")
            _cleanup_terraform_working_cache(directory, record, "destroy plan file missing; reinitialize before retry")
            _write_record(directory, record); return record
        path.chmod(0o600)
        show_code, summary = _run(["terraform", "show", "-no-color", "tfplan-destroy"], directory, 120, extra_env=runtime_env)
        _restrict_sensitive_files(directory)
        _transition(record, "DESTROY_AWAITING_APPROVAL", destroy_plan_sha256=_sha256(path),
                    destroy_artifact_hashes={p.name: _sha256(p) for p in directory.iterdir()
                                            if p.name in {"main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.json", ".terraform.lock.hcl"}},
                    destroy_state_region=TF_STATE_REGION, destroy_state_prefix=TF_STATE_PREFIX,
                    destroy_plan_summary=summary if show_code == 0 else "Destroy plan created; summary unavailable.",
                    destroy_plan_created_at=_now())
        _cleanup_terraform_working_cache(directory, record, "destroy plan is retained; working directory is recreated at destroy approval time")
        _write_record(directory, record)
        return record
    except subprocess.TimeoutExpired:
        _transition(record, "DESTROY_PLAN_FAILED", error="Terraform destroy plan generation timed out.")
        _cleanup_terraform_working_cache(directory, record, "destroy plan generation timed out; reinitialize before retry")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record
    except Exception as exc:
        _transition(record, "DESTROY_PLAN_FAILED", error=f"Destroy-plan generation raised {type(exc).__name__}.")
        _cleanup_terraform_working_cache(directory, record, "destroy plan generation failed; reinitialize before retry")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record


@_serialized_deployment_operation
def apply_destroy_plan(deployment_id: str, reviewer: str, token: str = "") -> dict[str, Any]:
    from control_plane import enabled, authorize_connection
    if enabled():
        if not re.fullmatch(r'cp-[0-9a-f]{16}', deployment_id):
            raise ValueError('Invalid deployment ID')
        metadata = _read_record(_base_dir() / deployment_id)
        actor = authorize_connection(metadata.get('aws_connection_id'), ('administrator', 'approver'))
        reviewer = actor['username']
    """Apply the exact reviewed destroy plan for a IIPP-managed deployment only."""
    from approval import validate
    validate(reviewer, token)
    enabled, message = is_enabled()
    if not enabled: raise RuntimeError(message)
    directory = _base_dir() / deployment_id
    record = _read_record(directory)
    if record.get("state_backend") == "s3":
        if record.get("state_bucket") != TF_STATE_BUCKET:
            raise ValueError("The configured S3 state bucket differs from the deployment's recorded bucket.")
        runtime_env = _terraform_runtime_env(record.get("aws_connection_id"), record.get("target_execution_role_arn", ""))
    else:
        runtime_env = _terraform_runtime_env(record.get("aws_connection_id"))
    if record.get("status") != "DESTROY_AWAITING_APPROVAL":
        raise ValueError("Deployment is not awaiting explicit destroy approval.")
    def verify_destroy_inputs():
        hashes = record.get("destroy_artifact_hashes")
        if not hashes or record.get("destroy_state_region") != TF_STATE_REGION or record.get("destroy_state_prefix") != TF_STATE_PREFIX:
            raise ValueError("Destroy inputs or backend changed; prepare a fresh destroy plan.")
        for name, checksum in hashes.items():
            if name not in {"main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.json", ".terraform.lock.hcl"} or not (directory / name).is_file() or _sha256(directory / name) != checksum:
                raise ValueError("Destroy configuration or inputs changed; prepare a fresh plan.")
    verify_destroy_inputs()
    try:
        created = datetime.fromisoformat(str(record.get("destroy_plan_created_at", record.get("updated_at", ""))).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Destroy-plan timestamp is missing or invalid. Prepare and review a new destroy plan.") from exc
    if created.tzinfo is None: created = created.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    if age < -timedelta(minutes=5):
        raise ValueError("Destroy-plan timestamp is unexpectedly in the future. Prepare and review a new destroy plan.")
    if age > timedelta(hours=1):
        raise ValueError("Destroy plan is older than one hour. Generate and review a fresh destroy plan before applying.")
    path = directory / "tfplan-destroy"
    if not path.is_file() or _sha256(path) != record.get("destroy_plan_sha256"):
        raise ValueError("Destroy plan is missing or its checksum changed. Prepare and review a new destroy plan.")
    reviewer_name = str(reviewer or "").strip()
    if not reviewer_name: raise ValueError("Reviewer name is required.")
    try:
        _reinitialize_for_saved_plan(deployment_id, directory, record, runtime_env)
    except Exception as exc:
        record["reinitialize_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        _cleanup_terraform_working_cache(directory, record, "destroy approval-time reinitialization failed; retry after fixing the backend/provider issue")
        _write_record(directory, record)
        _restrict_sensitive_files(directory)
        raise
    if not path.is_file() or _sha256(path) != record.get("destroy_plan_sha256"):
        raise ValueError("Destroy plan changed during Terraform reinitialization. Prepare and review a fresh destroy plan.")
    verify_destroy_inputs()
    _transition(record, "DESTROY_APPROVED", destroy_reviewer=reviewer_name, destroy_approved_at=_now())
    _write_record(directory, record)
    _transition(record, "DESTROYING")
    _write_record(directory, record)
    try:
        code, output = _run(["terraform", "apply", "-input=false", "-no-color", str(path)], directory, 600, extra_env=runtime_env)
    except subprocess.TimeoutExpired:
        _transition(record, "DESTROY_FAILED", error="Terraform destroy apply timed out; verify AWS resources and Terraform state before retrying.")
        _cleanup_terraform_working_cache(directory, record, "destroy apply timed out; reinitialize before recovery")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record
    except Exception as exc:
        _transition(record, "DESTROY_FAILED", error=f"Terraform destroy apply raised {type(exc).__name__}; verify AWS resources before retrying.")
        _cleanup_terraform_working_cache(directory, record, "destroy apply failed; reinitialize before recovery")
        _write_record(directory, record); _restrict_sensitive_files(directory); return record
    _restrict_sensitive_files(directory)
    if code:
        _transition(record, "DESTROY_FAILED", error="terraform destroy apply failed", destroy_log=output)
        _cleanup_terraform_working_cache(directory, record, "destroy failed; reinitialize before recovery")
    else:
        _transition(record, "DESTROYED", destroy_log=output, destroyed_at=_now())
        _cleanup_terraform_working_cache(directory, record, "destroy completed; deployment working directory is no longer needed")
    _write_record(directory, record)
    _restrict_sensitive_files(directory)
    return record

