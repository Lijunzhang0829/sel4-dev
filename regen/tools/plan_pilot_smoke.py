"""Prepare a three-contract, proof-free subset for the actual workspace pilot.

This is a pipeline smoke test, not a benchmark of architecture quality.
Run the standard repository_planner CLI on the emitted dataset and policy.
"""
import argparse
import hashlib
import json
from pathlib import Path
from regen.preparation.checker_context import load_context, create_context
from regen.preparation.refine_campaign import sha256_json
from regen.planning.plan_pilot import write


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    dataset = output/'dataset'; dataset.mkdir()
    path = source/'repository-planning-input.json'
    original = json.loads(path.read_text())
    selected_names = {'getCurThread_corres', 'getIdleThread_corres', 'setCurThread_corres'}
    selected = [c for c in original['contracts'] if c['name'] in selected_names]
    if len(selected) != 3:
        raise ValueError('Expected three unique frozen contracts')
    ids = {c['contract_id'] for c in selected}
    payload = {k: v for k, v in original.items() if k != 'planning_input_id'}
    payload['contracts'] = selected
    payload['contract_cards'] = [c for c in original['contract_cards'] if c['contract_id'] in ids]
    payload['owner_groups'] = {k: [c for c in v if c in ids] for k, v in original['owner_groups'].items() if any(c in ids for c in v)}
    payload['contract_relation_candidates'] = [r for r in original['contract_relation_candidates'] if r['left_contract_id'] in ids and r['right_contract_id'] in ids]
    payload['planning_input_id'] = 'refine-planning-input:' + sha256_json(payload)[:20]
    write(dataset/'repository-planning-input.json', payload)
    for name in ('parent-premises.sqlite', 'parent-premises-manifest.json'):
        (dataset/name).symlink_to(source/name)
    ctx = load_context(source)
    create_context(dataset, **{k: ctx[k] for k in ('l4v', 'semantic_workspace', 'heaps', 'isabelle', 'image',
        'base_session', 'base_import', 'base_catalog', 'architecture')})
    policy = json.loads((Path(__file__).resolve().parents[1]/'config/repository-planning-policy-pilot.json').read_text())
    write(output/'policy.json', policy)
    write(output/'test-binding.json', {'scope': 'three-contract workspace planning/pilot smoke',
        'source_input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'contracts': sorted(ids),
        'historical_proofs_imported': False, 'model': policy['model'], 'pilot': policy['pilot'],
        'architecture_quality_experiment': False})
    return {'dataset': str(dataset), 'policy': str(output/'policy.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.dataset, args.output), indent=2))
