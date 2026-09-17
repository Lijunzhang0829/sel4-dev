"""Immutable planning revision over an already verified semantic environment."""
from pathlib import Path
from regen.preparation.environment_gate import read_json, sha256_path
from regen.preparation.refine_campaign import sha256_json, write_immutable_json

MANIFEST = 'Planning-Freeze/manifest.json'


def code_files():
    repo = Path(__file__).resolve().parents[2]
    return {str(p.relative_to(repo)): p for folder in ('planning', 'infrastructure', 'preparation')
            for p in sorted((repo / 'regen' / folder).glob('*.py'))}


def bind(output, payload, environment_manifest, *, create=False, check_live=False):
    """Validate frozen bytes on every handoff; live code only when producing a plan."""
    path = output / MANIFEST
    sources = code_files()
    if create and not path.exists():
        if (output / 'planner-input.json').exists():
            raise ValueError('cannot add a planning freeze to an existing plan')
        files = {}
        for relative, source in sources.items():
            target = output / 'Planning-Freeze/code' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            if target.exists() and target.read_bytes() != content:
                raise ValueError('partial planning freeze code drift')
            if not target.exists():
                with target.open('xb') as stream:
                    stream.write(content)
            files[relative] = sha256_path(target)
        body = {'schema_version': 1, 'artifact_type': 'repository-planning-freeze',
                'environment_manifest_sha256': sha256_json(environment_manifest),
                'campaign_id': payload['campaign_id'], 'environment_gate_id': payload['environment_gate_id'],
                'environment_dataflow_id': payload['environment_dataflow_id'],
                'policy': payload['policy'], 'policy_sha256': payload['policy_sha256'],
                'budgets': environment_manifest['budgets'], 'code': files}
        write_immutable_json(path, {**body, 'freeze_id': 'planning-freeze:' + sha256_json(body)})
    frozen = read_json(path)
    body = {k: v for k, v in frozen.items() if k != 'freeze_id'}
    if frozen['freeze_id'] != 'planning-freeze:' + sha256_json(body):
        raise ValueError('planning freeze identity mismatch')
    expected = {'schema_version': 1, 'artifact_type': 'repository-planning-freeze',
                'environment_manifest_sha256': sha256_json(environment_manifest),
                'campaign_id': payload['campaign_id'], 'environment_gate_id': payload['environment_gate_id'],
                'environment_dataflow_id': payload['environment_dataflow_id'],
                'policy': payload['policy'], 'policy_sha256': payload['policy_sha256'],
                'budgets': environment_manifest['budgets']}
    if any(frozen.get(k) != value for k, value in expected.items()):
        raise ValueError('planning freeze environment/policy/budget mismatch')
    if not isinstance(frozen.get('code'), dict) or not frozen['code']:
        raise ValueError('planning freeze missing code')
    for relative, digest in frozen['code'].items():
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('invalid planning freeze code path')
        if sha256_path(output / 'Planning-Freeze/code' / rel) != digest:
            raise ValueError('planning freeze code snapshot drift')
    if check_live and frozen['code'] != {name: sha256_path(p) for name, p in sources.items()}:
        raise ValueError('planning implementation changed; create a new planning output')
    return {**payload, 'planning_freeze_id': frozen['freeze_id']}
