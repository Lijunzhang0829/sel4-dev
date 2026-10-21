"""Actionable, aggregated diagnostics for editable repository plans.

The authoritative bundle validator still runs after these checks. These messages
explain data and context-availability errors; they do not certify architecture.
"""
from collections import Counter
import re

NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
PREFLIGHT_THEORY = 'Planner_Preflight_All'


def diagnostics(payload, proposal, evidence=None):
    from regen.planning.import_contract import legacy_proposal
    try:
        errors = _diagnostics_v4(payload, legacy_proposal(proposal), evidence)
    except ValueError as error:
        return [{'path': '', 'message': str(error)}]
    if isinstance(proposal, dict) and proposal.get('schema_version') == 5:
        import json
        errors = json.loads(json.dumps(errors).replace('suggested_predecessors', 'required_imports'))
    return errors


def _diagnostics_v4(payload, proposal, evidence=None):
    errors = []

    def error(path, message, **details):
        errors.append({'path': path, 'message': message, **details})

    def fields(value, path, expected):
        if not isinstance(value, dict):
            error(path, 'Expected an object.')
            return False
        missing = sorted(expected - value.keys())
        extra = sorted(value.keys() - expected)
        if missing or extra:
            error(path, 'Fields must match the proposal schema.', missing=missing, unknown=extra)
        return True

    def strings(value, path, *, nonempty=False):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            error(path, 'Expected an array of strings.')
            return []
        if nonempty and not value:
            error(path, 'At least one item is required.')
        duplicates = sorted(k for k, n in Counter(value).items() if n > 1)
        if duplicates:
            error(path, 'Duplicate entries.', values=duplicates)
        return value

    def prose(value, path):
        if not isinstance(value, str) or not value.strip():
            error(path, 'Expected nonempty explanatory text.')

    def identifier(value, path):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            error(path, 'Use [A-Za-z][A-Za-z0-9_]*; no hyphens or spaces.', value=value)
            return False
        return True

    if not fields(proposal, '', {'schema_version', 'ordering_rationale', 'contract_relations',
                                 'theories', 'unresolved_retrieval_questions'}):
        return errors
    if proposal.get('schema_version') != 4:
        error('/schema_version', 'Expected schema version 4.')
    prose(proposal.get('ordering_rationale'), '/ordering_rationale')
    theories = proposal.get('theories')
    if not isinstance(theories, list) or not theories:
        error('/theories', 'Expected a nonempty theories array.')
        return errors
    expected = {c['contract_id'] for c in payload['contracts']}
    profiles = {p['context_profile_id'] for p in payload['context_profiles']}
    inspected = {p['record_id'] for p in (evidence or {}).get('inspected_parent_premises', [])}
    names = {t['name'] for t in theories if isinstance(t, dict) and isinstance(t.get('name'), str)}
    assigned, name_paths, helper_paths, graph = [], {}, {}, {}
    for i, t in enumerate(theories):
        path = f'/theories/{i}'
        if not fields(t, path, {'name', 'purpose', 'grouping_reason', 'contract_ids',
                               'context_profile_ids', 'helper_decisions', 'suggested_predecessors'}):
            continue
        name = t.get('name')
        if identifier(name, path + '/name'):
            if name == PREFLIGHT_THEORY:
                error(path + '/name', 'This name is reserved for the native checker entry theory.', value=name)
            if name in name_paths:
                error(path + '/name', 'Duplicate theory name.', first_path=name_paths[name], value=name)
            name_paths[name] = path + '/name'
            graph[name] = []
        for field in ('purpose', 'grouping_reason'):
            prose(t.get(field), path + '/' + field)
        ids = strings(t.get('contract_ids'), path + '/contract_ids')
        assigned.extend(ids)
        for cid in sorted(set(ids) - expected):
            error(path + '/contract_ids', 'Unknown public contract ID.', value=cid)
        selected = strings(t.get('context_profile_ids'), path + '/context_profile_ids', nonempty=True)
        if set(selected) - profiles:
            error(path + '/context_profile_ids', 'Unknown context profiles.', unknown=sorted(set(selected)-profiles), allowed=sorted(profiles))
        helpers = t.get('helper_decisions')
        if not isinstance(helpers, list):
            error(path + '/helper_decisions', 'Expected an array.')
            helpers = []
        if not ids and not helpers:
            error(path, 'A theory must contain fixed contracts or auxiliary intentions.')
        for j, d in enumerate(helpers):
            hp = path + f'/helper_decisions/{j}'
            if not fields(d, hp, {'id', 'kind', 'intent', 'for_contracts', 'premise_ids'}):
                continue
            key = d.get('id')
            if identifier(key, hp + '/id'):
                if key in helper_paths:
                    error(hp + '/id', 'Helper IDs must be unique across the plan.', first_path=helper_paths[key], value=key)
                helper_paths[key] = hp + '/id'
            prose(d.get('intent'), hp + '/intent')
            targets = strings(d.get('for_contracts'), hp + '/for_contracts', nonempty=True)
            if set(targets) - expected:
                error(hp + '/for_contracts', 'Unknown consumer contracts.', unknown=sorted(set(targets)-expected))
            premises = strings(d.get('premise_ids'), hp + '/premise_ids')
            if d.get('kind') == 'reuse_existing_fact':
                if not premises or set(premises) - inspected:
                    error(hp + '/premise_ids', 'Inspect complete trusted premise records before reuse.', uninspected=sorted(set(premises)-inspected))
            elif d.get('kind') == 'propose_auxiliary_intent':
                if premises:
                    error(hp + '/premise_ids', 'An unproved auxiliary intention cannot claim premise IDs.')
            else:
                error(hp + '/kind', 'Use reuse_existing_fact or propose_auxiliary_intent.')
        providers = t.get('suggested_predecessors')
        if not isinstance(providers, list):
            error(path + '/suggested_predecessors', 'Expected an array of planned provider theories.')
            continue
        seen = set()
        for j, pred in enumerate(providers):
            pp = path + f'/suggested_predecessors/{j}'
            if not fields(pred, pp, {'theory', 'reason'}):
                continue
            provider = pred.get('theory')
            prose(pred.get('reason'), pp + '/reason')
            if not isinstance(provider, str) or provider not in names:
                error(pp + '/theory', 'Unknown provider; use a theory name from this plan.', value=provider)
                continue
            if provider == name or provider in seen:
                error(pp + '/theory', 'Self or duplicate provider edge.', value=provider)
            seen.add(provider)
            if isinstance(name, str) and name in graph:
                graph[name].append(provider)
    missing, duplicate = sorted(expected-set(assigned)), sorted(k for k,n in Counter(assigned).items() if n>1)
    if missing or duplicate:
        error('/theories', 'Each fixed contract must occur exactly once.', missing=missing, duplicates=duplicate)
    # Report an actual cycle, not just a generic topological-sort failure.
    active, done = [], set()
    def visit(name):
        if name in active:
            error('/theories', 'Provider cycle: revise the boundaries or provider edges; changing array priority cannot resolve it.', cycle=active[active.index(name):]+[name])
            return
        if name in done:
            return
        active.append(name)
        for provider in graph.get(name, []):
            visit(provider)
        active.pop()
        done.add(name)
    for name in graph:
        visit(name)
    relations = proposal.get('contract_relations')
    if isinstance(relations, list):
        candidates = {r['relation_id']: r for r in payload['contract_relation_candidates']}
        for i, r in enumerate(relations):
            if not isinstance(r, dict):
                continue
            left, right = r.get('left_contract_id'), r.get('right_contract_id')
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            ids = r.get('evidence_ids', [])
            if not isinstance(ids, list):
                continue
            for rid in ids:
                candidate = candidates.get(rid) if isinstance(rid, str) else None
                if candidate and {left, right} != {candidate['left_contract_id'], candidate['right_contract_id']}:
                    error(f'/contract_relations/{i}/evidence_ids', 'Relation evidence belongs to different contract endpoints.', value=rid)
    return errors
