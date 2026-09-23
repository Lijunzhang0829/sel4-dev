"""Explicit import contracts for new plans; legacy schemas remain readable."""
from copy import deepcopy


def required_imports(projection):
    if projection.get('schema_version', 0) >= 5:
        if 'suggested_predecessors' in projection:
            raise ValueError('Schema 5 uses required_imports, not suggested_predecessors')
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
