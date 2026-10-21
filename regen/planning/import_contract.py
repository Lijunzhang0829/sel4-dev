"""Explicit import contracts for new plans; legacy schemas remain readable."""
from copy import deepcopy


def required_imports(projection):
    if projection.get('schema_version', 0) >= 5:
        if 'suggested_predecessors' in projection:
            raise ValueError(f"Schema {projection['schema_version']} uses required_imports, not suggested_predecessors")
        return projection['required_imports']
    return projection.get('suggested_predecessors', [])


def legacy_proposal(proposal):
    """Reuse the established validator without exposing legacy names to the agent."""
    if not isinstance(proposal, dict) or proposal.get('schema_version') != 5:
        return proposal
    result = deepcopy(proposal)
    result['schema_version'] = 4
    theories = result.get('theories')
    if isinstance(theories, list):
        for theory in theories:
            if isinstance(theory, dict):
                if 'suggested_predecessors' in theory:
                    raise ValueError('Schema 5 requires required_imports; legacy suggested_predecessors is not accepted')
                if 'required_imports' in theory:
                    theory['suggested_predecessors'] = theory.pop('required_imports')
    return result


def import_edges(plan):
    return plan.get('required_import_edges' if plan.get('schema_version', 0) >= 5
                    else 'suggested_theory_edges', [])


def build_bundle_v5(payload, proposal):
    from regen.planning.theory_plan import build_bundle_v4
    from regen.preparation.refine_campaign import sha256_json
    artifacts = build_bundle_v4(payload, legacy_proposal(proposal))
    plan = artifacts['repository-plan.json']
    plan['schema_version'] = 5
    plan['required_import_edges'] = plan.pop('suggested_theory_edges')
    for theory in plan['theories']:
        theory['required_imports'] = theory.pop('suggested_predecessors')
    plan['semantics'].pop('suggested_edges_are_proof_dependencies')
    semantics = {'imports_required_after_plan_acceptance': True,
                 'providers_must_be_sealed': True,
                 'imports_frozen_at_theory_start': True,
                 'individual_fact_citations_required': False,
                 'ready_priority_creates_imports': False}
    plan['semantics'].update(semantics)
    listing = artifacts['file-generation-list.json']
    listing['schema_version'] = 6
    listing['required_import_edges'] = listing.pop('suggested_theory_edges')
    for entry in listing['files']:
        projection = artifacts[entry['projection']]
        projection['schema_version'] = 5
        projection['required_imports'] = projection.pop('suggested_predecessors')
        projection['required_providers'] = projection.pop('suggested_providers')
        projection['import_semantics'] = semantics.copy()
        entry['projection_sha256'] = sha256_json(projection)
    return artifacts


def prompt_v5(payload, feedback):
    from regen.planning.theory_plan import prompt_v4
    return (prompt_v4(payload, feedback)
            .replace('"schema_version": 4', '"schema_version": 5')
            .replace('suggested_predecessors', 'required_imports'))


PROPOSAL_V6_FIELDS = {
    'schema_version', 'ordering_rationale', 'contract_relations', 'theories',
    'unresolved_retrieval_questions', 'collaboration_groups', 'reuse_suggestions',
}


def proposal_v5(proposal):
    """Project schema 6 to its hard-import core for the established validators.

    This is not a complete schema-6 validation: callers must also normalize_v6.
    No advisory relationship is ever converted into an import.
    """
    if not isinstance(proposal, dict) or proposal.get('schema_version') != 6:
        return proposal
    if set(proposal) != PROPOSAL_V6_FIELDS:
        raise ValueError('expected repository proposal schema 6 fields')
    result = deepcopy(proposal)
    result['schema_version'] = 5
    result.pop('collaboration_groups')
    result.pop('reuse_suggestions')
    return result


def normalize_v6(payload, proposal):
    """Validate hard imports separately from unproved collaboration hypotheses."""
    from regen.planning.repository_planner import NAME, id_list
    from regen.planning.theory_plan import normalize_v4
    if not isinstance(proposal, dict) or proposal.get('schema_version') != 6:
        raise ValueError('expected repository proposal schema 6')
    result = normalize_v4(payload, legacy_proposal(proposal_v5(proposal)))
    result['required_import_edges'] = result.pop('suggested_theory_edges')
    owners = {}
    for theory in result['theories']:
        theory['required_imports'] = theory.pop('suggested_predecessors')
        names = [contract['name'] for contract in theory['contracts']]
        if len(set(names)) != len(names):
            raise ValueError('contract declaration name collision')
        if theory['name'] == 'Planner_Preflight_All':
            raise ValueError('Theory name is reserved for the native preflight entry point')
        owners.update((cid, theory['name']) for cid in theory['contract_ids'])
    if len(owners) != len(payload['contracts']):
        raise ValueError('input contract IDs must be unique')
    theory_names = {theory['name'] for theory in result['theories']}

    def prose(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(label + ' requires nonempty text')

    groups = proposal['collaboration_groups']
    if not isinstance(groups, list):
        raise ValueError('collaboration_groups must be a list')
    group_ids, normalized_groups = set(), []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'id', 'contract_ids', 'reason'}:
            raise ValueError('collaboration group fields must be id, contract_ids, reason')
        key = group['id']
        if not isinstance(key, str) or not NAME.fullmatch(key) or key in group_ids:
            raise ValueError('invalid/duplicate collaboration group ID')
        group_ids.add(key)
        ids = id_list(group['contract_ids'], key + ' contracts')
        if len(ids) < 2 or not set(ids) <= owners.keys():
            raise ValueError('collaboration group must identify at least two known contracts')
        prose(group['reason'], 'collaboration group reason')
        normalized_groups.append({**deepcopy(group),
            'member_theories': sorted({owners[cid] for cid in ids})})

    suggestions = proposal['reuse_suggestions']
    if not isinstance(suggestions, list):
        raise ValueError('reuse_suggestions must be a list')
    pairs, normalized_suggestions = set(), []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict) or set(suggestion) != {'provider', 'consumer', 'reason'}:
            raise ValueError('reuse suggestion fields must be provider, consumer, reason')
        provider, consumer = suggestion['provider'], suggestion['consumer']
        if (not isinstance(provider, str) or not isinstance(consumer, str)
                or provider not in theory_names or consumer not in theory_names):
            raise ValueError('reuse suggestion references unknown theory')
        pair = (provider, consumer)
        if provider == consumer or pair in pairs:
            raise ValueError('self/duplicate reuse suggestion')
        pairs.add(pair)
        prose(suggestion['reason'], 'reuse suggestion reason')
        normalized_suggestions.append(deepcopy(suggestion))
    result.update(collaboration_groups=normalized_groups,
                  reuse_suggestions=normalized_suggestions)
    return deepcopy(result)


def _bind_bundle_identity(artifacts):
    """Bind fresh in-memory artifacts; never retain a previous plan/run identity."""
    from regen.preparation.refine_campaign import sha256_json
    plan = artifacts['repository-plan.json']
    plan.pop('plan_id', None)
    plan['plan_id'] = 'repository-plan:' + sha256_json(plan)[:20]
    listing = artifacts['file-generation-list.json']
    listing['plan_id'] = plan['plan_id']
    for entry in listing['files']:
        projection = artifacts[entry['projection']]
        projection['plan_id'] = plan['plan_id']
        entry['plan_id'] = plan['plan_id']
        entry['projection_sha256'] = sha256_json(projection)
    return artifacts


def build_bundle_v6(payload, proposal):
    """Pure construction of a fresh plan with advisory collaboration metadata.

    Semantic declarations/contexts come only from payload, never the proposal.
    This checks structural compatibility, not Isabelle elaboration or a proof.
    """
    from regen.preparation.refine_campaign import sha256_json
    normalized = normalize_v6(payload, proposal)
    artifacts = deepcopy(build_bundle_v5(payload, proposal_v5(proposal)))
    plan = artifacts['repository-plan.json']
    plan.update(schema_version=6, proposal_sha256=sha256_json(proposal),
                collaboration_groups=normalized['collaboration_groups'],
                reuse_suggestions=normalized['reuse_suggestions'])
    semantics = {
        **plan['semantics'],
        'collaboration_groups_are_advisory': True,
        'reuse_suggestions_create_imports': False,
        'reuse_suggestions_create_readiness_dependencies': False,
        'collaboration_grants_premise_visibility': False,
        'precise_helpers_and_proof_routes_deferred': True,
    }
    plan['semantics'] = semantics
    listing = artifacts['file-generation-list.json']
    listing.update(schema_version=7,
                   collaboration_groups=deepcopy(plan['collaboration_groups']),
                   reuse_suggestions=deepcopy(plan['reuse_suggestions']))
    profiles = {item['context_profile_id']: item for item in payload['context_profiles']}
    for entry in listing['files']:
        projection = artifacts[entry['projection']]
        name = projection['theory']
        projection.update(schema_version=6, import_semantics=deepcopy(semantics),
            context_profiles=[deepcopy(profiles[key]) for key in projection['context_profile_ids']],
            collaboration_groups=[deepcopy(group) for group in plan['collaboration_groups']
                                  if name in group['member_theories']],
            reuse_suggestions=[deepcopy(item) for item in plan['reuse_suggestions']
                               if name in (item['provider'], item['consumer'])])
    return _bind_bundle_identity(artifacts)


def safe_repartition(payload, previous_plan, replacement_proposal):
    """Build a NEW plan from an explicit replacement, without migrating proofs.

    Changing hard imports is allowed in the replacement: its entire hard DAG is
    validated again. The caller must start a fresh run with zero proof state.
    Supply the old canonical validation payload including its planner_evidence.
    The old plan, frozen run, bindings, GREEN nodes and proof history are untouched.
    No disk access, model invocation, or native checking takes place here.
    """
    from regen.preparation.refine_campaign import sha256_json
    if (previous_plan.get('artifact_type') != 'repository-proof-plan'
            or previous_plan.get('schema_version') not in {4, 5, 6}):
        raise ValueError('repartition requires a direct repository plan schema 4, 5 or 6')
    if (previous_plan.get('planning_input_id') != payload.get('planning_input_id')
            or previous_plan.get('benchmark') != payload.get('benchmark')):
        raise ValueError('repartition cannot change the frozen semantic planning input')
    previous = [contract for theory in previous_plan['theories'] for contract in theory['contracts']]
    current = payload['contracts']
    old_by_id = {contract['contract_id']: contract for contract in previous}
    new_by_id = {contract['contract_id']: contract for contract in current}
    if (len(old_by_id) != len(previous) or len(new_by_id) != len(current)
            or old_by_id != new_by_id):
        raise ValueError('repartition requires unchanged exact-once contracts and semantic contexts')
    if previous_plan.get('planner_input_sha256') != sha256_json(payload):
        raise ValueError('repartition requires the exact previously validated planning input, including evidence and frozen semantic parents')
    artifacts = build_bundle_v6(payload, replacement_proposal)
    artifacts['repository-plan.json']['repartition_source'] = {
        'plan_sha256': sha256_json(previous_plan),
        'proof_state_migrated': False,
        'requires_fresh_run': True,
    }
    return _bind_bundle_identity(artifacts)


def prompt_v6(payload, feedback):
    """Owner-blind semantics-first planning, without prescribing a proof route."""
    import json
    from regen.planning.theory_plan import compact_input, compact_evidence
    schema = {
        'schema_version': 6,
        'ordering_rationale': 'Explain semantic boundaries, alternatives, hard imports, ready priority and uncertainties.',
        'contract_relations': [{'left_contract_id': 'contract ID', 'right_contract_id': 'contract ID',
            'relation': 'shared-operation|shared-invariant|semantic-chain|common-premise|other',
            'evidence_ids': ['returned relation or inspected parent-premise ID']}],
        'collaboration_groups': [{'id': 'RelatedOperations', 'contract_ids': ['contract ID', 'another contract ID'],
            'reason': 'Shared semantic operations or state relations that warrant joint exploration; not a proof dependency.'}],
        'reuse_suggestions': [{'provider': 'Generated_Foundation', 'consumer': 'Generated_Operations',
            'reason': 'Potential established results to investigate; not a required import.'}],
        'theories': [{'name': 'Generated_Operations', 'purpose': 'Semantic responsibility',
            'grouping_reason': 'Why these operations belong together; explain alternatives and required contexts.',
            'contract_ids': ['contract ID'], 'context_profile_ids': ['allowed context profile ID'],
            'helper_decisions': [{'id': 'SharedResult', 'kind': 'reuse_existing_fact|propose_auxiliary_intent',
                'intent': 'Optional reasoning gap to investigate; no exact statement or prescribed proof route.',
                'for_contracts': ['contract ID'], 'premise_ids': ['inspected premise ID; reuse only']}],
            'required_imports': [{'theory': 'Generated_Foundation',
                'reason': 'Concrete semantic or context requirement committing this consumer to this provider.'}]}],
        'unresolved_retrieval_questions': ['Unresolved semantic or reuse question'],
    }
    compact = compact_input(payload)
    compact['planner_evidence'] = compact_evidence(payload.get('planner_evidence', {}))
    return (
        'Design the complete repository architecture at THEORY level. Allocate every fixed contract exactly once '
        'among coherent generated theories. Inspect exact statements, owned contexts and relevant legal definitions. '
        'Choose boundaries from shared semantic operations, state access/update behavior and related invariants. '
        'A common correspondence predicate or context alone is not evidence for grouping. Consider joint exploration '
        'of related primitive operations, including restricted and general cases, before separating consumers '
        'into large protocol theories. Choose names, number and boundaries yourself; do not reconstruct original '
        'source ownership. A multi-contract repository normally needs multiple coherent theories, but do not '
        'invent arbitrary splits or empty placeholders. Explain alternatives and manageable proof-planning scope. '
        'Do not prescribe exact helper statements, a local proof DAG, tactics or a fixed proof route. '
        'Optional helper_decisions describe open shared-result intentions or inspected existing facts. '
        'File Planner discovers, tests, revises and removes non-contract helpers using proof feedback. '
        'The physical theories and the collaboration scope are separate: collaboration_groups identify related '
        'contracts worth exploring together, possibly across multiple theories. They are advisory, can overlap, '
        'and neither assign duplicate contracts nor grant visibility to unproved facts. '
        'reuse_suggestions record possible directed cross-theory reuse. Reciprocal suggestions are allowed: '
        'they express alternatives or opportunities, not a cyclic proof/import dependency. '
        'required_imports alone create the hard theory DAG. Use them for a concrete semantic/context requirement '
        'you commit to retaining, not lexical similarity, operation calls, uncertain helper reuse or priority. '
        'Accepted providers must seal before their consumer starts; imports then freeze. An import does not '
        'require explicit citation of every provider fact. Where exploration suggests circular assistance, '
        'keep it advisory, reconsider grouping, or propose a common lower layer without assuming its precise proof. '
        'Only actual required_imports must be acyclic. The theories ARRAY ORDER gives the ready-theory priority. '
        'Review exact-once coverage, semantic contexts, boundaries, evidence, hard imports, collaboration and '
        'remaining uncertainties before submitting. Structural validation proves consistency, not architectural '
        'quality, mathematical independence, context elaboration or successful proofs. '
        'Use only the supplied legal input and any explicit harness operations offered by the enclosing interaction. '
        'Do not read files, history, removed proofs, or external sources. '
        'Original contract theory attribution is absent in owner-blind mode. Refine and CRefine are separate '
        'experiments; do not import cross-experiment information. Fixed statements/context/attributes are immutable '
        'and copied by the harness from their IDs. Pending contracts or auxiliary intentions are never premises. '
        'Search helpers by names, definitions or concepts and inspect full legal facts before declaring reuse; '
        'there is no proof-state retrieval at repository planning time. Similarity does not certify applicability. '
        'Zero helper intentions, collaboration groups, reuse suggestions or hard imports are allowed when justified. '
        'Return JSON only matching this schema (all fields required):\n'
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
        + '\nPREVIOUS VALIDATION ERRORS:\n' + json.dumps(feedback or [], ensure_ascii=False)
        + '\nCOMPACT LEGAL INPUT:\n' + json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )
