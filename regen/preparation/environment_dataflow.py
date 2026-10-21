"""Deterministic, public data streams over an already validated environment gate."""
from __future__ import annotations

import json
from pathlib import Path

from regen.preparation.environment_gate import (
    artifact_ref, payload_sha256, read_json, resolve_ref, validate_environment_gate,
)
from regen.preparation.refine_campaign import write_immutable_json

CONTRACT_FIELDS = (
    'contract_id', 'name', 'kind', 'exact_statement', 'normalized_statement_sha256',
    'owner_theory', 'owner_file_slot', 'contract_owned_attributes_raw',
    'contract_owned_attributes', 'contract_owned_context', 'contract_context_sha256',
    'contract_semantic_sha256',
)
PREMISE_FIELDS = (
    'record_id', 'canonical_name', 'declaration_name', 'fact_index', 'exact_statement',
    'normalized_statement_sha256', 'owner_theory', 'owner_session', 'source_class',
    'attributes', 'definition_equations', 'publication_state',
)


def streams(root: Path, gate: dict) -> tuple[list[dict], list[dict], list[dict]]:
    contracts, owners = [], []
    for path in sorted((root / gate['contract_packages']['path']).glob('*.json')):
        package = read_json(path)
        if package.get('campaign_id') != gate['campaign_id']:
            raise ValueError('public contract package belongs to another campaign')
        selected = [{key: item[key] for key in CONTRACT_FIELDS if key in item}
                    for item in package['contracts']]
        contracts.extend({**item, 'proof_status': 'unproved', 'usable_as_premise': False}
                         for item in selected)
        if selected:
            owners.append({'owner_theory': package['theory'], 'file_slot': package['file_slot'],
                           'contract_ids': sorted(item['contract_id'] for item in selected)})
    ids = [item['contract_id'] for item in contracts]
    if len(ids) != len(set(ids)) or len(ids) != gate['contract_packages']['contract_count']:
        raise ValueError('public contract stream has duplicate or missing targets')
    snapshot = read_json(resolve_ref(root, gate['premise_catalog']['snapshot']))
    premises = [{**{key: item[key] for key in PREMISE_FIELDS if key in item},
                 'usable_as_premise': True,
                 'environment_gate_id': gate['gate_id']}
                for item in snapshot['records']]
    return sorted(contracts, key=lambda x: x['contract_id']), sorted(premises, key=lambda x: x['record_id']), owners


def write_jsonl(path: Path, records: list[dict]):
    text = ''.join(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n' for item in records)
    if path.exists() and path.read_text() != text:
        raise FileExistsError(f'refusing to replace data stream: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def publish_dataflow(root: Path, gate_path: Path, output: Path) -> dict:
    root = root.resolve()
    gate = validate_environment_gate(root, gate_path)
    contracts, premises, owners = streams(root, gate)
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / 'contracts.jsonl', contracts)
    write_jsonl(output / 'premises.jsonl', premises)
    write_immutable_json(output / 'owners.json', {
        'groups': owners, 'meaning': 'original target grouping; not a generated theory constraint',
    })
    body = {
        'schema_version': 1, 'artifact_type': 'environment-dataflow',
        'campaign_id': gate['campaign_id'], 'environment_gate_id': gate['gate_id'],
        'status': 'ready', 'model_calls_performed': 0,
        'streams': {name: artifact_ref(root, output / filename) for name, filename in (
            ('contracts', 'contracts.jsonl'), ('premises', 'premises.jsonl'), ('owners', 'owners.json'))},
        'counts': {'contracts': len(contracts), 'premises': len(premises), 'owners': len(owners)},
        'policy': {'contract_statements_are_premises': False, 'generated_facts_included': False,
                   'owner_groups_constrain_generated_theories': False,
                   'symbol_resolution_performed': False, 'private_inventory_included': False},
    }
    index = {**body, 'dataflow_id': 'dataflow:' + payload_sha256(body)[:20]}
    write_immutable_json(output / 'environment-index.json', index)
    return index


def validate_dataflow(root: Path, gate_path: Path, index_path: Path) -> dict:
    root = root.resolve()
    gate = validate_environment_gate(root, gate_path)
    index = read_json(index_path)
    body = {key: value for key, value in index.items() if key != 'dataflow_id'}
    if index.get('dataflow_id') != 'dataflow:' + payload_sha256(body)[:20]:
        raise ValueError('dataflow index identity mismatch')
    if index.get('environment_gate_id') != gate['gate_id'] or index.get('campaign_id') != gate['campaign_id']:
        raise ValueError('dataflow binds another environment')
    if index.get('artifact_type') != 'environment-dataflow' or index.get('schema_version') != 1 or index.get('status') != 'ready':
        raise ValueError('unsupported or incomplete dataflow index')
    expected_policy = {
        'contract_statements_are_premises': False, 'generated_facts_included': False,
        'owner_groups_constrain_generated_theories': False,
        'symbol_resolution_performed': False, 'private_inventory_included': False,
    }
    if index.get('policy') != expected_policy:
        raise ValueError('dataflow policy mismatch')
    expected_contracts, expected_premises, expected_owners = streams(root, gate)
    for name, expected in [('contracts', expected_contracts), ('premises', expected_premises)]:
        path = resolve_ref(root, index['streams'][name])
        actual = [json.loads(line) for line in path.read_text().splitlines()]
        if actual != expected or index['counts'][name] != len(expected):
            raise ValueError(f'dataflow {name} differs from legal environment')
    owners = read_json(resolve_ref(root, index['streams']['owners']))
    if owners['groups'] != expected_owners or index['counts']['owners'] != len(expected_owners):
        raise ValueError('dataflow owner coverage mismatch')
    return index
