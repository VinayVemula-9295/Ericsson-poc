"""Integrity checked, credential-free source snapshots for fresh deployments."""
import hashlib
import json
import os
import re
from pathlib import Path

STORES = {'poc_plans': ('poc-[0-9a-f]{32}', 'record.json'),
          'terraform_deployments': ('cp-[0-9a-f]{16}', 'deployment.json')}
TERMINAL = {'SUCCEEDED'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(directory):
    return sorted(p for p in directory.iterdir() if p.name.endswith(('.tf', '.tf.json'))
                  or p.name in {'terraform.tfvars.json', '.terraform.lock.hcl'})


def snapshot(directory, record):
    files = source_files(directory)
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError('Saved Terraform files must be regular files')
    record['reuse_manifest'] = {'version': 1, 'files': {p.name: digest(p) for p in files}}


def load(source, deployment_id):
    if source not in STORES or not re.fullmatch(STORES[source][0], deployment_id):
        raise ValueError('Invalid deployment source or ID')
    root = Path(os.getenv('DATA_DIR', '/data')) / source
    directory = root / deployment_id
    if directory.is_symlink() or directory.resolve().parent != root.resolve():
        raise ValueError('Invalid deployment directory')
    record = json.loads((directory / STORES[source][1]).read_text(encoding='utf-8'))
    if record.get('status') not in TERMINAL:
        raise ValueError('Only completed successful deployments can be reused')
    from control_plane import visible_connection
    config = record.get('requested_configuration') or {}
    connection_id = record.get('connection_id') or record.get('aws_connection_id') or config.get('aws_connection_id')
    if not visible_connection(connection_id):
        raise ValueError('Customer access denied')
    manifest = record.get('reuse_manifest')
    if not manifest:
        # Earlier Agent records already have checksums for the entire supported bundle.
        keys = {'main.tf.json': 'configuration_sha256', 'terraform.tfvars.json': 'inputs_sha256',
                '.terraform.lock.hcl': 'lock_sha256'}
        if source != 'poc_plans' or not all(record.get(k) for k in keys.values()):
            raise ValueError('This older record has no verified source bundle. Create new infrastructure.')
        manifest = {'version': 1, 'files': {n: record[k] for n, k in keys.items()}}
    hashes = manifest.get('files', {})
    if manifest.get('version') != 1 or not hashes or 'terraform.tfvars.json' not in hashes or '.terraform.lock.hcl' not in hashes:
        raise ValueError('Saved source bundle is incomplete')
    if not any(name.endswith(('.tf', '.tf.json')) for name in hashes):
        raise ValueError('Saved Terraform source is missing')
    actual = {p.name for p in source_files(directory)}
    if actual != set(hashes):
        raise ValueError('Saved Terraform source files changed or are missing')
    for name, checksum in hashes.items():
        path = directory / name
        if Path(name).name != name or path.is_symlink() or not path.is_file() or digest(path) != checksum:
            raise ValueError('Saved Terraform integrity verification failed')
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return directory, record, fingerprint


def copy_sources(source, destination):
    # Never copy state, binary plans, approvals, credentials or working caches.
    for path in source_files(source):
        if path.name != 'terraform.tfvars.json':
            target = destination / path.name
            content = path.read_bytes()
            target.write_bytes(content)
            target.chmod(0o600)


def availability(row):
    try:
        load(row['source'], row['deployment_id'])
        return None
    except ValueError as exc:
        return str(exc)
    except (OSError, TypeError, KeyError):
        return 'Verified saved Terraform is unavailable or incomplete.'
