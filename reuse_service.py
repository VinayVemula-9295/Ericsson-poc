"""Fresh planning from retained source; never invokes the recommendation agent."""
import copy
import json
from reuse_artifacts import load


def prepare_reuse(source, candidate, request, inputs):
    directory, original, checksum = load(*source)
    old = original.get('candidate') or original.get('requested_configuration') or {}
    if candidate['provider'] != 'AWS' or candidate['sku'] != (old.get('sku') or old.get('instance_type')):
        raise ValueError('Reuse preserves the saved provider and instance size')
    if candidate['os'] != old.get('os', 'Linux'):
        raise ValueError('Reuse preserves the saved operating system')
    saved_request = original.get('requirements', {})
    if request['kind'] != saved_request.get('kind', 'VM'):
        raise ValueError('Reuse preserves the saved infrastructure type')
    if candidate.get('cluster') != old.get('cluster'):
        raise ValueError('Reuse preserves saved worker sizing')
    saved_inputs = json.loads((directory / 'terraform.tfvars.json').read_text(encoding='utf-8'))
    old_name = saved_inputs.get('name') or saved_inputs.get('instance_name')
    if not inputs.get('name') or inputs['name'] == old_name:
        raise ValueError('Enter a new resource name for this separate deployment')
    # Validate current availability before the existing planner checks policy/runtime.
    if source[0] == 'poc_plans':
        from provider_inputs import validate_live_inputs
        validate_live_inputs(candidate, request, inputs)
    candidate = copy.deepcopy(candidate)
    candidate.update(available=True, offered_in_target_location=True)
    # Evidence about another target is not proof for this target.
    if request.get('require_quota'):
        from poc_catalog import aws_quota
        candidate.update(aws_quota(candidate['connection_id'], candidate['region']))
    if source[0] == 'poc_plans':
        from poc_planner import prepare
        return prepare(candidate, dict(request, source_deployment=source[1]), inputs,
                       request.get('latency_evidence', []), reuse_from=source)
    from terraform_service import prepare_plan
    cfg = copy.deepcopy(original['requested_configuration'])
    cfg.update(region=candidate['region'], aws_connection_id=candidate['connection_id'],
               instance_name=inputs['name'], ami_id=inputs['image_id'], subnet_id=inputs['subnet_id'],
               security_group_id=inputs['security_group_id'], initiated_by=request.get('initiated_by'),
               environment=request.get('environment', cfg.get('environment', 'poc')),
               ttl_days=request.get('ttl_days', cfg.get('ttl_days', 2)))
    # This workflow rechecks live hardware minimums, regional/AZ offerings, images
    # and network references in prepare_plan; old records stored minimums, not specs.
    return prepare_plan(cfg, reuse_from=source)
