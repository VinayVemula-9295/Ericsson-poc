"""Portable, secret-free Terraform source generation and guarded value editing."""
import copy
import json
from poc_terraform import configuration


def generate(candidate, request, inputs):
    safe = {k: v for k, v in inputs.items() if k not in {'external_id', 'admin_password'}}
    return {'configuration': configuration(candidate, request['kind']), 'inputs': safe}


def validate(text, candidate, request, original_inputs):
    try:
        draft = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid JSON. Correct the configuration before continuing.') from exc
    expected = generate(candidate, request, original_inputs)
    if not isinstance(draft, dict) or set(draft) != set(expected) or draft['configuration'] != expected['configuration']:
        raise ValueError('Terraform resource structure, provider and backend are protected. Change supported input values only.')
    values = draft['inputs']
    if not isinstance(values, dict) or set(values) != set(expected['inputs']):
        raise ValueError('Input fields cannot be added or removed.')
    protected = {'region', 'execution_role_arn', 'subscription_id', 'project_id'}
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Complete every input with a nonempty string value.')
        if key in protected and value != expected['inputs'][key]:
            raise ValueError('Account, execution role and region must be changed through the configuration workflow.')
    return copy.deepcopy(draft)
