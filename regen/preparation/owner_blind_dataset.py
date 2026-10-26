"""Prepare anonymous canonical contracts for every generation stage.

Planner-only aliases are insufficient: canonical IDs otherwise reappear in
consumer review, file projections and proof tools. The original map is a private
preparation artifact, never part of the derived dataset or any model workspace.
"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from regen.planning.owner_blind import OwnerBlindView
from regen.preparation.refine_campaign import sha256_json, write_immutable_json


def anonymous_payload(source):
    view = OwnerBlindView(source, visibility='owner-blind-v1')
    # Use a distinct canonical namespace so a subsequent planner alias cannot
    # equal its own private source ID (the planner correctly rejects that).
    aliases = {key: 'goal:' + key.removeprefix('contract:') for key in view.to_original}
    def replace(value):
        if isinstance(value, str): return aliases.get(value, value)
        if isinstance(value, list): return [replace(item) for item in value]
        if isinstance(value, dict): return {key: replace(item) for key, item in value.items()}
        return value
    payload = replace(deepcopy(view.payload))
    target = payload['benchmark']['target_session']
    # Compatibility metadata for trusted projection code: one synthetic owner,
    # independent of every original allocation. It is not an original theory.
    synthetic = target + '.Fixed_Targets'
    for contract in payload['contracts']:
        contract['owner_theory'] = synthetic
        contract['owner_file_slot'] = 'Fixed_Targets.thy'
    payload['owner_groups'] = {synthetic: [c['contract_id'] for c in payload['contracts']]}
    payload['theory_import_graph'] = {}
    payload['session_graph'] = {}
    payload['upstream_session_closure'] = deepcopy(source.get('upstream_session_closure', []))
    payload.pop('planning_input_id', None)
    payload['planning_input_id'] = 'owner-blind-input:' + sha256_json(payload)[:20]
    view.assert_no_owner_disclosure(json.dumps(payload, ensure_ascii=False))
    return payload, {aliases[key]: original for key, original in view.to_original.items()}


def vero_benchmark(payload, *, checker_context_id, base_import):
    """Expose anonymous canonical IDs, semantic order and one neutral module.

    The module is a rendering placeholder, never an original allocation or a
    prescribed plan. VERO's repository planner chooses its own final modules.
    """
    target = payload['benchmark']['target_session']
    if (payload.get('contract_visibility') != 'owner-blind-v1'
            or payload.get('theory_import_graph') or payload.get('session_graph')
            or any(c.get('owner_theory') != target + '.Fixed_Targets'
                   or not c['contract_id'].startswith('goal:')
                   for c in payload['contracts'])):
        raise ValueError('VERO export requires the anonymous canonical dataset')
    contracts = [
        {'id': f'c{i:04d}', 'source_id': item['contract_id'],
         'name': item['name'], 'statement': item['exact_statement'],
         'context': deepcopy(item.get('contract_owned_context', [])),
         'module': 'Vero_Fixed_Targets'}
        for i, item in enumerate(payload['contracts'], 1)
    ]
    if len({c['source_id'] for c in contracts}) != len(contracts):
        raise ValueError('anonymous VERO contract coverage mismatch')
    body = {
        'schema_version': 2, 'backend': 'isabelle', 'mode': 'proof',
        'base_import': base_import, 'contracts': contracts,
        'provenance': {'contract_visibility': 'owner-blind-v1',
                       'anonymous_input_id': payload['planning_input_id'],
                       'checker_context_id': checker_context_id},
        'policy': {'unfilled': 'oops', 'reference_proofs_visible': False,
                   'cross_module_proof_imports': True, 'auxiliary_slots': True,
                   'editable_generated_imports': True},
    }
    # VERO's public digest uses the default separators, unlike sha256_json.
    return {**body, 'benchmark_id': hashlib.sha256(json.dumps(
        body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}


def prepare(source, output, private_map):
    from regen.preparation.checker_context import load_context
    source, output, private_map = map(lambda p: Path(p).resolve(), (source, output, private_map))
    if private_map.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('derived dataset and private mapping must be separate from source and each other')
    if output.exists() or private_map.exists():
        raise ValueError('fresh owner-blind output and private map required')
    context = load_context(source)
    raw = (source/'repository-planning-input.json').read_bytes()
    original = json.loads(raw)
    if original['benchmark']['target_session'] != context['target_session']:
        raise ValueError('contract target differs from checker context')
    payload, mapping = anonymous_payload(original)
    if len(mapping) != len(original['contracts']):
        raise ValueError('anonymous contract coverage mismatch')
    output.mkdir(parents=True)
    for filename in ('parent-premises.sqlite', 'parent-premises-manifest.json', 'premise-audit-context.json'):
        path = (source/filename).resolve()
        if not path.is_file():
            raise ValueError('missing source dataset asset: '+filename)
        (output/filename).symlink_to(path)
    (output/'checker-assets').mkdir()
    for name in context['paths']:
        (output/context['paths'][name]).symlink_to(context[name], target_is_directory=True)
    # Relative asset layout is identical; preserve the accepted checker identity.
    (output/'checker-context.json').write_bytes((source/'checker-context.json').read_bytes())
    write_immutable_json(output/'repository-planning-input.json', payload)
    write_immutable_json(private_map, {
        'source_input_sha256': hashlib.sha256(raw).hexdigest(),
        'anonymous_to_original': mapping,
        'model_visible': False,
    })
    receipt = {
        'schema_version': 1, 'policy': 'canonical-owner-blind-v1',
        'contract_count': len(mapping), 'target_session': context['target_session'],
        'source_input_sha256': hashlib.sha256(raw).hexdigest(),
        'anonymous_input_sha256': hashlib.sha256((output/'repository-planning-input.json').read_bytes()).hexdigest(),
        'checker_context_id': context['checker_context_id'],
        'original_groups_and_imports_removed': True,
        'synthetic_owner_is_original_mapping': False,
        'statement_context_attributes_preserved': True,
    }
    load_context(output)
    write_immutable_json(output/'owner-blind-receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'output', 'private-map'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.private_map), indent=2))


if __name__ == '__main__':
    main()
