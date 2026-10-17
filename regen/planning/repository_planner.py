"""Contract-driven repository plans over verified preparation data streams.

This module produces proposals and file projections, never certified proof nodes.
"""
from __future__ import annotations
import argparse

from collections import Counter
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import re

from regen.preparation.environment_dataflow import validate_dataflow
from regen.preparation.environment_gate import read_json, resolve_ref, sha256_path
from regen.preparation.refine_campaign import sha256_json, verify_manifest, write_immutable_json
from regen.planning.planner_io import parse_model_json, select_completed_transcript
from regen.legacy.repository_planning_compat import (
    audit_path as compatible_audit_path,
    context_budget,
    policy_artifact_supported,
    transcript_stage_supported,
)
from regen.infrastructure.llm_transcript import (
    allocate_artifact_prefix, artifact_paths, call_codex, validate_call_artifact,
)

NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
TOKENS = re.compile(r'[A-Za-z][A-Za-z0-9_\'.]*')
GENERIC = {'lemma', 'theorem', 'corres', 'True', 'False', 'shows', 'assumes', 'fixes'}
REPO = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path('regen/config/repository-planning-policy.json')
OUTPUT_DIRECTORY = 'Repository-Planning'
AUDIT_FILENAME = 'repository-planning-audit.json'
PLANNER_STAGE = 'Repository-Planning'
POLICY_ARTIFACT = 'repository-planning-policy'
BUDGET_KEY = 'repository_planner_context_chars'


def read_stream(root: Path, reference: dict) -> list[dict]:
    return [json.loads(line) for line in resolve_ref(root, reference).read_text().splitlines()]


def validate_policy(policy: dict):
    if policy.get('pilot') is not None:
        from regen.planning.plan_pilot import settings
        settings(policy['pilot'])
        if policy.get('interaction') != 'agent-workspace':
            raise ValueError('Plan pilot requires agent-workspace planning')
    if policy.get('schema_version') not in {2, 3} or policy.get('mode') != 'repository-proof-planning':
        raise ValueError('repository planning requires policy schema 2 or 3')
    if not policy_artifact_supported(policy.get('artifact_type'), POLICY_ARTIFACT) or policy.get('stage') != 'Refine':
        raise ValueError('invalid repository planning policy')
    if not isinstance(policy.get('model', {}).get('model'), str):
        raise ValueError('planning policy must specify a model')
    if policy.get('interaction') in {'agent-iterative', 'agent-workspace'}:
        if 'max_attempts' in policy['model']:
            raise ValueError('iterative planning has no proposal-attempt limit')
    else:
        attempts = policy['model'].get('max_attempts')
        if not isinstance(attempts, int) or attempts <= 0:
            raise ValueError('legacy planning policy must specify positive max_attempts')
    visibility = policy.get('contract_visibility', 'owner-blind-v1')
    if visibility not in {'owner-blind-v1','source-theory-visible-v1'}:
        raise ValueError('invalid contract visibility')
    if visibility == 'source-theory-visible-v1' and policy.get('interaction') != 'agent-workspace':
        raise ValueError('source-theory-visible mode requires agent-workspace planning')
    proposal_version = policy.get('proposal_schema_version')
    if proposal_version is not None and (type(proposal_version) is not int
            or proposal_version not in {5, 6} or policy.get('interaction') != 'agent-workspace'):
        raise ValueError('proposal_schema_version 5 or 6 requires agent-workspace planning')
    if policy['model'].get('reasoning_effort', 'medium') not in {'low','medium','high','xhigh','max'}:
        raise ValueError('invalid planning reasoning effort')


def prompt(payload: dict, feedback: list[str] | None = None) -> str:
    if payload['policy']['schema_version'] == 3:
        from regen.planning.theory_plan import prompt as theory_prompt
        return theory_prompt(payload, feedback)
    schema = {
        'schema_version': 2,
        'theories': [{'name': 'Generated_Operations', 'purpose': 'route towards the fixed contracts',
            'nodes': [
                {'id': 'operation_contract', 'contract_id': 'one exact input contract ID',
                 'suggested_dependencies': ['bridge'], 'premise_ids': []},
                {'id': 'bridge', 'intent': 'describe the auxiliary proposition to establish',
                 'for_contracts': ['one exact input contract ID'],
                 'suggested_dependencies': [], 'premise_ids': ['one visible legal premise ID']},
            ]}],
    }
    return (
        'Plan a repository of Isabelle proofs for the fixed seL4 Refine contracts. '
        'Use only the legal input below. Do not consult or reconstruct deleted human proofs.\n'
        'Choose generated theory names and distribute ALL contracts exactly once. Original owner '
        'groups identify input provenance; they do not constrain your generated files. You may '
        'introduce auxiliary-only theories and split auxiliaries into other ordinary nodes.\n'
        'For a contract provide only its ID, never rewrite its statement/context/attributes. '
        'For an auxiliary provide an intent and the contract IDs it serves. Precise auxiliary '
        'statements will be elaborated by the file proof stage. Do not output proofs, tactics, '
        'imports, assumptions, or GREEN/sealed claims.\n'
        'suggested_dependencies are revisable ordering hints, NOT facts, mandatory premise use, '
        'or cross-file commitments. Use node IDs, reject cycles; keep the proposed theory order '
        'acyclic as a whole. Auxiliary nodes need not be leaves. Failed auxiliary nodes may be '
        'removed later; contracts may not be removed or weakened. Actual premise use is decided '
        'during proof in the legal checked context. Only same-theory proved lemmas, sealed '
        'generated upstream facts, and legal base premises may then be used.\n'
        'premise_ids may be empty or cite only visible legal premise records. Prefer explicit '
        'premise references; simp/wp are allowed later without exhaustive tactic dependency tracing. '
        'The premise selection is lexical, not a semantic symbol-resolution certificate. '
        'Missing retrieval evidence does not establish that a legal fact does not exist.\n'
        'Use unique simple Isabelle identifiers for theory names and node IDs. Return JSON only:\n'
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
        + '\nPREVIOUS VALIDATION ERRORS:\n' + json.dumps(feedback or [], ensure_ascii=False)
        + '\nLEGAL INPUT:\n' + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )


def make_input(root: Path, gate_path: Path, index_path: Path, policy: dict,
               policy_hash: str, context_budget: int) -> dict:
    validate_policy(policy)
    index = validate_dataflow(root, gate_path, index_path)
    contracts = read_stream(root, index['streams']['contracts'])
    premises = read_stream(root, index['streams']['premises'])
    if not contracts:
        raise ValueError('repository planning requires at least one fixed contract')
    query = Counter(token for item in contracts for token in TOKENS.findall(
        item['exact_statement'] + ' ' + '\n'.join(item.get('contract_owned_context', [])))
        if token not in GENERIC and len(token) > 1)
    def rank(item):
        tokens = set(TOKENS.findall(item['canonical_name'] + ' ' + item['exact_statement']))
        score = sum(query[token] for token in tokens)
        return (-score, item['record_id'])
    ranked = sorted(premises, key=rank)
    payload = {
        'schema_version': 2, 'artifact_type': 'repository-planner-input',
        'campaign_id': index['campaign_id'], 'environment_gate_id': index['environment_gate_id'],
        'environment_dataflow_id': index['dataflow_id'], 'policy_sha256': policy_hash,
        'contracts': contracts,
        'original_owner_groups': read_json(resolve_ref(root, index['streams']['owners']))['groups'],
        'legal_premises': [],
        'premise_selection': {'method': 'contract-token-overlap-then-stable-id',
                              'catalog_count': len(premises), 'included_count': 0,
                              'omitted_count': len(premises), 'complete': not premises,
                              'semantic_resolution_performed': False},
        'policy': policy,
    }
    # Reserve bounded space for later validation feedback. Never truncate contracts.
    reserve = min(8000, max(0, context_budget // 10))
    limit = context_budget - reserve
    if len(prompt(payload)) > limit:
        raise ValueError('all fixed contracts exceed frozen planning context budget; targets were not truncated')
    remaining = limit - len(prompt(payload)) - 128  # room for updated count metadata
    for item in ranked:
        cost = len(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        cost += bool(payload['legal_premises'])
        if cost <= remaining:
            payload['legal_premises'].append(item)
            remaining -= cost
    count = len(payload['legal_premises'])
    payload['premise_selection'].update(included_count=count, omitted_count=len(premises)-count,
                                       complete=count == len(premises))
    payload['legal_premises'].sort(key=lambda item: item['record_id'])
    # Verify after counts and presentation order are final as well.
    if len(prompt(payload)) > limit:
        raise ValueError('planning input metadata exceeds frozen context budget')
    return payload


def topological(nodes: list[str], edges: list[tuple[str, str]], priority=None) -> tuple[list[str], dict[str, int]]:
    remaining = {name: set() for name in nodes}
    for provider, consumer in edges:
        remaining[consumer].add(provider)
    order, depths = [], {}
    while remaining:
        ready = sorted((name for name, providers in remaining.items() if not providers), key=priority)
        if not ready:
            raise ValueError('cyclic suggested dependencies; revise node/theory grouping')
        name = ready[0]
        depths[name] = max((depths[a] + 1 for a, b in edges if b == name), default=0)
        order.append(name)
        del remaining[name]
        for providers in remaining.values():
            providers.discard(name)
    return order, depths


def id_list(value, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f'{label} must be a string list')
    if len(value) != len(set(value)):
        raise ValueError(f'{label} contains duplicates')
    return value


def normalize_proposal(payload: dict, proposal: dict) -> dict:
    if payload['policy']['schema_version'] == 3:
        from regen.planning.theory_plan import normalize
        return normalize(payload, proposal)
    if set(proposal) != {'schema_version', 'theories'} or proposal['schema_version'] != 2:
        raise ValueError('expected repository proposal schema 2 with theories only')
    theories = proposal['theories']
    if not isinstance(theories, list) or not theories:
        raise ValueError('proposal must contain generated theories')
    contracts = {item['contract_id']: item for item in payload['contracts']}
    premises = {item['record_id'] for item in payload['legal_premises']}
    names, all_nodes, targets, normalized = set(), {}, [], []
    for theory in theories:
        if not isinstance(theory, dict) or set(theory) != {'name', 'purpose', 'nodes'}:
            raise ValueError('theory must contain only name, purpose and nodes')
        name = theory['name']
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError(f'invalid or duplicate generated theory name: {name}')
        names.add(name)
        if not isinstance(theory['purpose'], str) or not theory['purpose'].strip():
            raise ValueError(f'theory {name} has no purpose')
        if not isinstance(theory['nodes'], list) or not theory['nodes']:
            raise ValueError(f'generated theory {name} must have planned nodes')
        declarations = set()
        nodes = []
        for raw in theory['nodes']:
            if not isinstance(raw, dict):
                raise ValueError('node must be an object')
            fixed = 'contract_id' in raw
            keys = {'id', 'contract_id', 'suggested_dependencies', 'premise_ids'} if fixed else {
                'id', 'intent', 'for_contracts', 'suggested_dependencies', 'premise_ids'}
            if set(raw) != keys:
                raise ValueError('node fields must match contract/auxiliary schema; no proof or statement overrides')
            node_id = raw['id']
            if not isinstance(node_id, str) or not NAME.fullmatch(node_id) or node_id in all_nodes:
                raise ValueError(f'invalid or duplicate node ID: {node_id}')
            dependencies = id_list(raw['suggested_dependencies'], f'{node_id} dependencies')
            cited = id_list(raw['premise_ids'], f'{node_id} premise_ids')
            if not set(cited) <= premises:
                raise ValueError(f'{node_id} cites premises absent from the legal planner input')
            node = {'id': node_id, 'theory': name, 'role': 'contract' if fixed else 'auxiliary',
                    'suggested_dependencies': sorted(dependencies), 'premise_ids': sorted(cited),
                    'proof_status': 'unproved', 'usable_as_premise': False}
            if fixed:
                contract_id = raw['contract_id']
                if not isinstance(contract_id, str) or contract_id not in contracts:
                    raise ValueError(f'unknown fixed contract: {contract_id}')
                contract = contracts[contract_id]
                if contract['name'] in declarations:
                    raise ValueError(f'contract declaration name collision in {name}: {contract["name"]}')
                declarations.add(contract['name'])
                targets.append(contract_id)
                node.update(contract_id=contract_id, contract=contract)
            else:
                served = id_list(raw['for_contracts'], f'{node_id} for_contracts')
                if not served or not set(served) <= contracts.keys():
                    raise ValueError(f'auxiliary {node_id} must serve known contracts')
                if not isinstance(raw['intent'], str) or not raw['intent'].strip():
                    raise ValueError(f'auxiliary {node_id} has no intent')
                node.update(intent=raw['intent'], for_contracts=sorted(served))
            all_nodes[node_id] = node
            nodes.append(node)
        normalized.append({'name': name, 'file_slot': name + '.thy', 'purpose': theory['purpose'],
                           'node_ids': sorted(item['id'] for item in nodes)})
    if sorted(targets) != sorted(contracts):
        raise ValueError('every fixed contract must occur exactly once')
    edges = []
    for node in all_nodes.values():
        for dep in node['suggested_dependencies']:
            if dep not in all_nodes:
                raise ValueError(f'dangling suggested dependency {dep} on {node["id"]}')
            edges.append((dep, node['id']))
    node_order, _ = topological(list(all_nodes), edges,
                                priority=lambda name: (all_nodes[name]['role'] == 'contract', name))
    theory_edges = sorted({(all_nodes[a]['theory'], all_nodes[b]['theory']) for a, b in edges
                           if all_nodes[a]['theory'] != all_nodes[b]['theory']})
    theory_order, depths = topological(sorted(names), theory_edges)
    return {'theories': sorted(normalized, key=lambda x: theory_order.index(x['name'])),
            'nodes': [all_nodes[name] for name in node_order], 'node_order': node_order,
            'theory_order': theory_order, 'theory_depths': depths,
            'suggested_theory_edges': [{'provider': a, 'consumer': b} for a, b in theory_edges]}


def build_bundle(payload: dict, proposal: dict) -> dict[str, dict]:
    if payload['policy']['schema_version'] == 3:
        from regen.planning.theory_plan import build_bundle as theory_bundle
        return theory_bundle(payload, proposal)
    normalized = normalize_proposal(payload, proposal)
    body = {'schema_version': 2, 'artifact_type': 'repository-proof-plan',
            'campaign_id': payload['campaign_id'], 'environment_gate_id': payload['environment_gate_id'],
            'environment_dataflow_id': payload['environment_dataflow_id'],
            'planner_input_sha256': sha256_json(payload), 'proposal_sha256': sha256_json(proposal),
            'policy_sha256': payload['policy_sha256'], 'status': 'proposed', **normalized,
            'semantics': {'suggested_edges_are_proof_dependencies': False,
                          'auxiliaries_must_be_leaves': False, 'node_statements_certified': False,
                          'generated_facts_published': False}}
    plan = {**body, 'plan_id': 'repository-plan:' + sha256_json(body)[:20]}
    artifacts = {'repository-plan.json': plan}
    by_id = {node['id']: node for node in plan['nodes']}
    premises = {item['record_id']: item for item in payload['legal_premises']}
    files = []
    for theory in plan['theories']:
        nodes = [by_id[key] for key in plan['node_order'] if key in theory['node_ids']]
        projection = {'schema_version': 2, 'artifact_type': 'repository-file-projection',
                      'plan_id': plan['plan_id'], 'environment_gate_id': payload['environment_gate_id'],
                      'environment_dataflow_id': payload['environment_dataflow_id'],
                      'theory': theory['name'], 'file_slot': theory['file_slot'], 'purpose': theory['purpose'],
                      'contracts': [node['contract'] for node in nodes if node['role'] == 'contract'],
                      'nodes': nodes,
                      'suggested_node_order': [node['id'] for node in nodes],
                      'legal_premises': [premises[key] for key in sorted({key for node in nodes for key in node['premise_ids']})],
                      'status': 'requires-file-planning-and-Isabelle-checks'}
        filename = f"files/{theory['name']}.json"
        artifacts[filename] = projection
        files.append({'theory': theory['name'], 'file_slot': theory['file_slot'],
                      'contract_ids': [node['contract_id'] for node in nodes if node['role'] == 'contract'],
                      'node_ids': [node['id'] for node in nodes], 'projection': filename,
                      'projection_sha256': sha256_json(projection)})
    artifacts['file-generation-list.json'] = {
        'schema_version': 3, 'artifact_type': 'repository-file-generation-list',
        'plan_id': plan['plan_id'], 'environment_gate_id': payload['environment_gate_id'],
        'environment_dataflow_id': payload['environment_dataflow_id'],
        'files': files, 'serial_fallback': plan['theory_order'],
        'contract_count': len(payload['contracts']), 'file_count': len(files),
        'next_module': 'file-planning', 'execution_ready': False,
    }
    return artifacts


@contextmanager
def output_lock(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.planning.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('another repository planner holds this output lock') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def verify_bundle(output: Path, payload: dict, *, restore_missing: bool = False) -> dict:
    audit = read_json(compatible_audit_path(output, AUDIT_FILENAME))
    core = {key: value for key, value in audit.items() if key != 'audit_sha256'}
    if audit.get('audit_sha256') != sha256_json(core):
        raise ValueError('planning audit identity mismatch')
    prefix = output / audit['transcript_prefix']
    try:
        prefix.resolve().relative_to(output.resolve())
    except ValueError as error:
        raise ValueError('transcript reference escapes planning output') from error
    meta = validate_call_artifact(prefix, require_real=True)
    paths = artifact_paths(prefix)
    proposal = parse_model_json(paths['final'].read_text())
    if paths['prompt'].read_text() != prompt(payload, audit['feedback']):
        raise ValueError('transcript differs from legal planning prompt')
    validate_meta(meta, payload)
    expected = build_bundle(payload, proposal)
    if (audit.get('plan_id') != expected['repository-plan.json']['plan_id'] or
        audit.get('planner_input_sha256') != sha256_json(payload) or
        audit.get('prompt_sha256') != sha256_path(paths['prompt']) or
        audit.get('final_sha256') != sha256_path(paths['final']) or audit.get('verdict') != 'pass'):
        raise ValueError('planning audit binding mismatch')
    expected = {'planner-input.json': payload, 'planner-proposal.json': proposal, **expected}
    # Check all existing artifacts before restoring any missing projection.
    for name, artifact in expected.items():
        path = output / name
        if path.is_file():
            if read_json(path) != artifact:
                raise ValueError(f'planning artifact does not reconstruct: {name}')
        elif not restore_missing:
            raise ValueError(f'missing planning artifact: {name}')
    if restore_missing:
        for name, artifact in expected.items():
            write_immutable_json(output / name, artifact)
    return expected['repository-plan.json']


def validate_meta(meta: dict, payload: dict):
    if (meta.get('model') != payload['policy']['model']['model'] or
        not transcript_stage_supported(meta.get('stage'), payload['policy'].get('artifact_type'), PLANNER_STAGE) or not meta.get('sealable_generation') or
        meta.get('provider') not in {'codex-exec-audited', 'claude-print-audited'}):
        raise ValueError('repository planner transcript is not sealable for this policy')


def verify_frozen_policy_snapshot(root: Path, manifest: dict, policy: dict, policy_hash: str):
    frozen_policies = [root / 'Environment-Freeze/frozen-inputs' / item['path']
                       for item in manifest['frozen_inputs'] if item['sha256'] == policy_hash]
    if not any(path.is_file() and sha256_path(path) == policy_hash and read_json(path) == policy
               for path in frozen_policies):
        raise ValueError('repository planning policy does not match a frozen input snapshot')


def load_file_projection(root: Path, output: Path, theory: str, *,
                         gate_path: Path | None = None, index_path: Path | None = None) -> dict:
    """Trusted downstream handoff: validate preparation and plan before returning one file.

    This reads no raw source and performs no new model or prover calls.
    """
    root, output = root.resolve(), output.resolve()
    manifest = read_json(root / 'Environment-Freeze/campaign-manifest.json')
    verify_manifest(manifest)
    stored = read_json(output / 'planner-input.json')
    independent = (output / 'Planning-Freeze/manifest.json').is_file()
    if not independent:
        verify_frozen_policy_snapshot(root, manifest, stored['policy'], stored['policy_sha256'])
    payload = make_input(root, gate_path or root / 'Environment-Preparation/environment-gate.json',
                         index_path or root / 'Environment-Dataflow/environment-index.json',
                         stored['policy'], stored['policy_sha256'],
                         context_budget(manifest, BUDGET_KEY))
    if independent:
        from regen.planning.planning_freeze import bind
        payload = bind(output, payload, manifest)
    if payload['campaign_id'] != manifest['campaign_id']:
        raise ValueError('projection environment differs from frozen campaign')
    plan = verify_bundle(output, payload)
    if theory not in plan['theory_order']:
        raise ValueError(f'unknown generated theory: {theory}')
    return read_json(output / 'files' / f'{theory}.json')


def run(root: Path, gate_path: Path, index_path: Path, policy: dict, policy_hash: str,
        output: Path, *, allow_external_call: bool = False, verify_only: bool = False,
        freeze_planning: bool = False, prepare_only: bool = False) -> dict:
    if policy.get('interaction') == 'agent-iterative':
        raise ValueError('iterative repository planning requires --planning-input')
    root, output = root.resolve(), output.resolve()
    manifest = read_json(root / 'Environment-Freeze/campaign-manifest.json')
    verify_manifest(manifest)
    independent = freeze_planning or (output / 'Planning-Freeze/manifest.json').is_file()
    if independent and policy.get('schema_version') != 3:
        raise ValueError('independent planning freeze requires policy v3')
    if not independent:
        verify_frozen_policy_snapshot(root, manifest, policy, policy_hash)
    budget = context_budget(manifest, BUDGET_KEY)
    timeout = int(manifest['budgets']['model_call_timeout_seconds'])
    payload = make_input(root, gate_path, index_path, policy, policy_hash, budget)
    if payload['campaign_id'] != manifest['campaign_id']:
        raise ValueError('environment dataflow differs from frozen campaign')
    with output_lock(output):
        if independent:
            from regen.planning.planning_freeze import bind
            payload = bind(output, payload, manifest, create=freeze_planning and not verify_only,
                           check_live=not verify_only)
        if compatible_audit_path(output, AUDIT_FILENAME).is_file():
            plan = verify_bundle(output, payload, restore_missing=not verify_only)
            return summary(plan, 'verified')
        if verify_only:
            raise ValueError('no completed repository planning bundle to verify')
        write_immutable_json(output / 'planner-input.json', payload)
        write_immutable_json(output / 'planner-policy.json', policy)
        if prepare_only:
            return {'verdict': 'prepared', 'contracts': len(payload['contracts']),
                    'legal_premises': len(payload['legal_premises']),
                    'planning_freeze_id': payload.get('planning_freeze_id'),
                    'external_call_performed': False, 'execution_ready': False}
        feedback = []
        for attempt in range(1, policy['model']['max_attempts'] + 1):
            text = prompt(payload, feedback)
            if len(text) > budget:
                raise ValueError('planning prompt including feedback exceeds frozen context budget')
            preferred = output / 'transcript' / f'repository-planner-{attempt:03d}'
            completed = select_completed_transcript(preferred, prompt=text, model=policy['model']['model'])
            if completed:
                prefix, meta = completed
                final = artifact_paths(prefix)['final'].read_text()
            else:
                if not allow_external_call:
                    raise ValueError('fresh repository planning requires --allow-external-call')
                prefix = allocate_artifact_prefix(preferred)
                final = call_codex(text, prefix, model=policy['model']['model'], timeout=timeout,
                                   theory='Refine', stage=PLANNER_STAGE, clean_runtime=True)
                meta = validate_call_artifact(prefix, require_real=True)
            validate_meta(meta, payload)
            paths = artifact_paths(prefix)
            if paths['prompt'].read_text() != text or paths['final'].read_text() != final:
                raise ValueError('planner call output differs from audited transcript')
            try:
                proposal = parse_model_json(final)
                artifacts = build_bundle(payload, proposal)
            except (ValueError, KeyError, TypeError) as error:
                diagnostic = str(error)[:1000]
                write_immutable_json(output / f'attempt-{attempt:03d}-rejection.json', {
                    'attempt': attempt, 'diagnostic': diagnostic,
                    'transcript_prefix': str(prefix.relative_to(output)),
                })
                # Keep feedback bounded, preserving full rejected artifacts on disk.
                feedback = [diagnostic[:max(0, min(1000, budget // 20))]]
                continue
            write_immutable_json(output / 'planner-proposal.json', proposal)
            for filename, artifact in artifacts.items():
                write_immutable_json(output / filename, artifact)
            core = {'schema_version': 2, 'artifact_type': 'repository-planning-audit', 'verdict': 'pass',
                    'plan_id': artifacts['repository-plan.json']['plan_id'],
                    'planner_input_sha256': sha256_json(payload),
                    'transcript_prefix': str(prefix.relative_to(output)), 'feedback': feedback,
                    'prompt_sha256': sha256_path(paths['prompt']), 'final_sha256': sha256_path(paths['final']),
                    'checks': ['exact-once-contracts', 'legal-visible-premise-ids', ('theory-dag-and-explicit-priority' if policy['schema_version'] == 3 else 'node-and-theory-dag'),
                               'preserved-contract-payloads', 'deterministic-file-projections'],
                    'statement_elaboration_performed': False}
            # Publish audit last: an interrupted partial bundle is not complete.
            write_immutable_json(output / AUDIT_FILENAME, {**core, 'audit_sha256': sha256_json(core)})
            plan = verify_bundle(output, payload)
            return summary(plan, 'created-or-recovered')
        raise ValueError('repository planning validation attempts exhausted; see rejection diagnostics')


def summary(plan: dict, disposition: str) -> dict:
    if plan['schema_version'] < 3:
        contract_count = sum(node['role'] == 'contract' for node in plan['nodes'])
        auxiliary_field = 'auxiliary_nodes'
        auxiliary_count = sum(node['role'] == 'auxiliary' for node in plan['nodes'])
    else:
        contract_count = sum(len(theory['contracts']) for theory in plan['theories'])
        auxiliary_field = 'auxiliary_intents'
        if plan['schema_version'] == 3:
            auxiliary_count = sum(
                len(theory.get('auxiliary_intents', []))
                for theory in plan['theories']
            )
        else:
            auxiliary_count = sum(
                decision['kind'] == 'propose_auxiliary_intent'
                for theory in plan['theories']
                for decision in theory.get('helper_decisions', [])
            )
    return {
        'verdict': 'pass',
        'disposition': disposition,
        'plan_id': plan['plan_id'],
        'files': len(plan['theories']),
        'contracts': contract_count,
        auxiliary_field: auxiliary_count,
        'next_module': 'file-planning',
        'execution_ready': False,
    }


def resolve_policy(environment_root: Path, repo: Path, policy_argument: Path | None,
                   output: Path, freeze_planning: bool) -> tuple[dict, str]:
    policy_path = policy_argument or DEFAULT_POLICY
    policy_path = policy_path if policy_path.is_absolute() else repo / policy_path
    freeze_manifest = output / 'Planning-Freeze/manifest.json'
    if freeze_planning or freeze_manifest.is_file():
        if policy_argument is None and freeze_manifest.is_file():
            frozen = read_json(freeze_manifest)
            return frozen['policy'], frozen['policy_sha256']
        policy = read_json(policy_path)
        return policy, sha256_path(policy_path)
    manifest = read_json(environment_root / 'Environment-Freeze/campaign-manifest.json')
    verify_manifest(manifest)
    policy = read_json(policy_path)
    digest = sha256_path(policy_path)
    verify_frozen_policy_snapshot(environment_root, manifest, policy, digest)
    return policy, digest


def inspection_prompt(payload: dict) -> str:
    schema = {
        'schema_version': 1,
        'contract_batches': [['contract ID']],
        'premise_searches': [{
            'query': 'existing lemma names, definitions, or relevant concepts',
            'contract_ids': ['contract ID'],
            'context_profile_ids': ['allowed context profile ID'],
            'attributes': [],
            'limit': 20,
        }],
    }
    from regen.planning.theory_plan import compact_input
    compact = compact_input(payload)
    compact['planner_interfaces'] = payload['planner_interfaces']
    return (
        'Select bounded evidence for a seL4 Refine repository plan. This is an '
        'independent Refine experiment; never request CRefine data. Inspect every '
        'contract exactly once in batches no larger than the advertised limit. '
        'Request at most 20 premise searches over native-verified frozen parents and '
        'retained semantic exports. Repository planning has no proof-state query. '
        'Search by names, definitions and shared concepts. Source registry '
        'checks exclude unresolved origins and dynamic collections. Candidates are '
        'not applicability certificates. No tools, filesystem, shell, network, or '
        'external agents may be used; the harness executes this JSON request. '
        'Do not propose a plan, helper '
        'statement, proof, tactic, command, path, or unrestricted read. Return JSON '
        'only matching:\n' + json.dumps(schema, sort_keys=True)
        + '\nCOMPACT INPUT:\n'
        + json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )


def execute_inspection(payload: dict, request: dict, planning_input_path: Path) -> dict:
    from regen.preparation.planning_input import PlanningInputQueries
    if not isinstance(request, dict) or set(request) != {
        'schema_version', 'contract_batches', 'premise_searches',
    } or request['schema_version'] != 1:
        raise ValueError('invalid repository-planner inspection request')
    queries = PlanningInputQueries(planning_input_path)
    batches = request['contract_batches']
    if not isinstance(batches, list):
        raise ValueError('contract_batches must be a list')
    inspected_contracts = []
    requested_ids = []
    for batch in batches:
        if not isinstance(batch, list):
            raise ValueError('contract batch must be a list')
        requested_ids.extend(batch)
        inspected_contracts.extend(queries.inspect_contracts(batch))
    expected = sorted(item['contract_id'] for item in payload['contracts'])
    if sorted(requested_ids) != expected or len(requested_ids) != len(set(requested_ids)):
        raise ValueError('inspection must cover every contract exactly once')
    searches = request['premise_searches']
    if not isinstance(searches, list) or len(searches) > 20:
        raise ValueError('invalid premise search request count')
    search_results = []
    premise_ids = []
    contract_ids = set(expected)
    for search in searches:
        required = {'query', 'contract_ids', 'context_profile_ids', 'attributes', 'limit'}
        if (not isinstance(search, dict) or not required <= set(search)
                or set(search) - required - {'mode', 'use_vectors'}):
            raise ValueError('invalid premise search request')
        if not search['contract_ids'] or not set(search['contract_ids']) <= contract_ids:
            raise ValueError('premise search references unknown contracts')
        if search.get('mode', 'search') != 'search' or search.get('use_vectors', False):
            raise ValueError('repository planning uses lemma search, not proof-state queries')
        hits = queries.search_parent_premises(
            search['query'], context_profile_ids=search['context_profile_ids'],
            attributes=search['attributes'], limit=search['limit'])
        premise_ids.extend(item['record_id'] for item in hits)
        search_results.append({'request': search, 'results': hits})
    unique_ids = sorted(set(premise_ids))
    batch_size = payload['planner_interfaces']['inspect_parent_premises']['max_ids']
    inspected_premises = []
    for offset in range(0, len(unique_ids), batch_size):
        inspected_premises.extend(
            queries.inspect_parent_premises(unique_ids[offset:offset + batch_size])
        )
    return {
        'inspected_contracts': inspected_contracts,
        'premise_searches': search_results,
        'inspected_parent_premises': inspected_premises,
    }


def _direct_meta(meta: dict, model: str, stage: str) -> None:
    if (
        meta.get('model') != model or meta.get('stage') != stage
        or not meta.get('sealable_generation')
        or meta.get('provider') not in {'codex-exec-audited', 'claude-print-audited'}
    ):
        raise ValueError(f'unsealable direct repository-planner transcript: {stage}')


def _direct_call(
    text: str, prefix: Path, *, model: str, timeout: int,
    allow_external_call: bool, stage: str, reasoning_effort: str | None = None,
) -> tuple[str, dict, Path]:
    completed = select_completed_transcript(prefix, prompt=text, model=model)
    if completed:
        actual_prefix, meta = completed
        final = artifact_paths(actual_prefix)['final'].read_text()
    else:
        if not allow_external_call:
            raise ValueError('fresh repository planning requires --allow-external-call')
        from regen.tools.repository_planning_retry import call_with_retry
        final, actual_prefix = call_with_retry(
            call_codex, text, prefix, model=model, timeout=timeout,
            theory='Refine', stage=stage, clean_runtime=True, reasoning_effort=reasoning_effort,
        )
        meta = validate_call_artifact(actual_prefix, require_real=True)
    _direct_meta(meta, model, stage)
    return final, meta, actual_prefix


def _bind_direct_artifacts(
    artifacts: dict[str, dict], current_plan: dict, output: Path,
) -> dict[str, dict]:
    result = dict(artifacts)
    result['repository-plan.json'] = current_plan
    files = []
    for entry in artifacts['file-generation-list.json']['files']:
        projection = {
            **artifacts[entry['projection']],
            'plan_id': current_plan['plan_id'],
            'plan_revision': current_plan['revision'],
        }
        result[entry['projection']] = projection
        files.append({
            **entry,
            'plan_id': current_plan['plan_id'],
            'plan_revision': current_plan['revision'],
            'projection_sha256': sha256_json(projection),
        })
    result['file-generation-list.json'] = {
        **artifacts['file-generation-list.json'],
        'plan_id': current_plan['plan_id'],
        'plan_revision': current_plan['revision'],
        'files': files,
    }
    return result


def run_prepared(
    planning_input_path: Path, policy: dict, output: Path, *,
    allow_external_call: bool = False, prepare_only: bool = False,
    log_storage: str = 'inline-v1',
) -> dict:
    """Run Repository Planner directly over the prepared Refine dataset."""
    from regen.planning.plan_revisions import append_revision, start_plan
    from regen.planning.theory_plan import build_bundle as build_theory_bundle
    from regen.planning.theory_plan import prompt as theory_prompt
    planning_input_path = planning_input_path.resolve()
    payload = read_json(planning_input_path)
    if (
        payload.get('artifact_type') != 'repository-planning-input'
        or payload.get('benchmark', {}).get('target_session') != 'Refine'
        or payload.get('benchmark', {}).get('cross_experiment_inputs') != []
    ):
        raise ValueError('direct planning requires an independent Refine input')
    validate_policy(policy)
    output = output.resolve()
    with output_lock(output):
        from regen.infrastructure.incremental_log import enable, policy_arguments
        logging = policy_arguments({'log_storage':log_storage})
        if logging:
            enable(output)
            policy = {**policy, **logging}
        write_immutable_json(output / 'planner-input.json', payload)
        write_immutable_json(output / 'planner-policy.json', policy)
        if prepare_only:
            return {
                'verdict': 'prepared', 'contracts': len(payload['contracts']),
                'external_call_performed': False, 'execution_ready': False,
            }
        model = policy['model']['model']
        timeout = int(policy['model'].get('timeout_seconds', 1800))
        if policy.get('interaction') == 'agent-workspace':
            from regen.planning.workspace_planner import run_agent
            result = run_agent(payload, planning_input_path,
                {**policy, '_allow_external_call': allow_external_call}, output)
            proposal, artifacts = result['proposal'], result['artifacts']
            plan_prefix = result['plan_prefix']
            inspection_prefix = None
        elif policy.get('interaction') == 'agent-iterative':
            from regen.planning.iterative_planner import run_agent
            result = run_agent(payload, planning_input_path,
                {**policy, '_allow_external_call': allow_external_call}, output, _direct_call)
            if result['status'] == 'stopped':
                return {'verdict': 'stopped', 'reason': result['reason'],
                        'turns': result['turns'], 'execution_ready': False}
            proposal, artifacts = result['proposal'], result['artifacts']
            plan_prefix = result['plan_prefix']
            inspection_prefix = None
        else:
            inspection_text = inspection_prompt(payload)
            final, _meta, inspection_prefix = _direct_call(
                inspection_text, output / 'transcript/repository-inspection-001',
                model=model, timeout=timeout, allow_external_call=allow_external_call,
                stage='Repository-Planning-Inspection', reasoning_effort=policy['model'].get('reasoning_effort'),
            )
            request = parse_model_json(final)
            evidence = execute_inspection(payload, request, planning_input_path)
            enriched = {**payload, 'planner_evidence': evidence}
            write_immutable_json(output / 'planner-inspection.json', request)
            write_immutable_json(output / 'planner-evidence.json', evidence)
            feedback = []
            proposal = None
            artifacts = None
            plan_prefix = None
            for attempt in range(1, policy['model']['max_attempts'] + 1):
                text = theory_prompt(enriched, feedback)
                final, _meta, plan_prefix = _direct_call(
                    text, output / 'transcript' / f'repository-plan-{attempt:03d}',
                    model=model, timeout=timeout,
                    allow_external_call=allow_external_call,
                    stage='Repository-Planning', reasoning_effort=policy['model'].get('reasoning_effort'),
                )
                try:
                    proposal = parse_model_json(final)
                    artifacts = build_theory_bundle(enriched, proposal)
                    break
                except (ValueError, KeyError, TypeError) as error:
                    diagnostic = str(error)[:1000]
                    write_immutable_json(
                        output / f'attempt-{attempt:03d}-rejection.json',
                        {'attempt': attempt, 'diagnostic': diagnostic},
                    )
                    feedback = [diagnostic]
            if proposal is None or artifacts is None or plan_prefix is None:
                raise ValueError('repository planning validation attempts exhausted')
        write_immutable_json(output / 'planner-proposal.json', proposal)
        identity = start_plan(
            output / 'planning', payload['benchmark']['id'],
        )
        current = append_revision(
            output / 'planning',
            {**artifacts['repository-plan.json'], 'plan_id': identity['plan_id']},
        )
        artifacts = _bind_direct_artifacts(artifacts, current, output)
        for filename, artifact in artifacts.items():
            if filename == 'repository-plan.json':
                continue
            write_immutable_json(output / filename, artifact)
        audit_core = {
            'schema_version': 3,
            'artifact_type': 'repository-planning-audit',
            'verdict': 'pass',
            'plan_id': current['plan_id'],
            'plan_revision': current['revision'],
            'planning_input_id': payload['planning_input_id'],
            'interaction': policy.get('interaction'),
            'native_declaration_preflight': policy.get('interaction') == 'agent-workspace',
            'inspection_transcript_prefix': str(inspection_prefix.relative_to(output)) if inspection_prefix else None,
            'planning_transcript_prefix': str(plan_prefix.relative_to(output)),
            'checks': [
                'independent-refine-input', 'exact-once-contracts',
                'inspected-parent-premise-reuse', 'context-profile-ids',
                'theory-dag-and-explicit-priority', 'stable-plan-revision',
            ],
        }
        if policy.get('interaction') == 'agent-workspace':
            submitted=read_json(output/'control/submitted.json')
            preflight=output/'preflight'/submitted['preflight_sha256']/'result.json'
            audit_core['native_preflight_evidence']={
                'path':str(preflight.relative_to(output)),
                'sha256':sha256_path(preflight),
                'statements_only':True,
                'proved_contracts':0,
            }
            audit_core['checks'].extend(['native-declaration-import-preflight','workspace-call-integrity'])
        write_immutable_json(
            output / AUDIT_FILENAME,
            {**audit_core, 'audit_sha256': sha256_json(audit_core)},
        )
        return summary(current, 'created')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--environment-root', type=Path)
    parser.add_argument('--planning-input', type=Path)
    parser.add_argument('--environment-gate', type=Path)
    parser.add_argument('--dataflow', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-external-call', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--freeze-planning', action='store_true')
    from regen.infrastructure.incremental_log import FORMAT
    parser.add_argument('--log-storage', choices=['inline-v1', FORMAT], default='inline-v1')
    args = parser.parse_args()
    if args.log_storage != 'inline-v1' and not args.planning_input:
        parser.error('incremental planning logs require --planning-input')
    if args.prepare_only and (args.verify_only or args.allow_external_call):
        parser.error('--prepare-only cannot be combined with --verify-only or --allow-external-call')

    repo = args.repo.resolve()
    if args.planning_input:
        if args.environment_root or args.environment_gate or args.dataflow or args.freeze_planning:
            parser.error('--planning-input cannot be combined with legacy environment arguments')
        if args.verify_only:
            parser.error('direct planning verification is not yet exposed through this CLI')
        policy_path = args.policy or DEFAULT_POLICY
        policy_path = policy_path if policy_path.is_absolute() else repo / policy_path
        policy = read_json(policy_path)
        output = (
            args.output or args.planning_input.resolve().parent / OUTPUT_DIRECTORY
        ).resolve()
        result = run_prepared(
            args.planning_input, policy, output,
            allow_external_call=args.allow_external_call,
            prepare_only=args.prepare_only,
            log_storage=args.log_storage,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.environment_root is None:
        parser.error('provide --planning-input or --environment-root')
    environment_root = args.environment_root.resolve()
    gate_path = args.environment_gate or environment_root / 'Environment-Preparation/environment-gate.json'
    gate = read_json(gate_path)
    if not gate.get('proof_generation_allowed'):
        raise ValueError('environment gate does not allow proof generation')
    output = (args.output or environment_root / OUTPUT_DIRECTORY).resolve()
    policy, policy_hash = resolve_policy(
        environment_root, repo, args.policy, output, args.freeze_planning,
    )
    validate_policy(policy)
    result = run(
        environment_root, gate_path,
        args.dataflow or environment_root / 'Environment-Dataflow/environment-index.json',
        policy, policy_hash, output,
        allow_external_call=args.allow_external_call,
        verify_only=args.verify_only,
        freeze_planning=args.freeze_planning,
        prepare_only=args.prepare_only,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
