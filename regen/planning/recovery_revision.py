"""Explicit recovery inputs and hard preservation bounds for incremental planning.

Generated execution observations are not removed human proofs, certified premises,
or a license to read arbitrary historical artifacts. Only their audited summaries
enter the planner/reviewer; source files are hash checked by the trusted harness.
"""
from copy import deepcopy
import hashlib
from pathlib import Path


CONSTRAINT_FIELDS = {'schema_version', 'immutable_theories',
                     'preserve_theory_contract_sets', 'fixed_contract_owners'}
EVIDENCE_KINDS = {'native_proof_failure', 'provider_unavailable',
                  'infrastructure_failure', 'accepted_progress',
                  'model_failure_assessment', 'operator_pause'}


def validate_recovery_source(source, tail):
    """Reject stale or live predecessor clocks before spending on a new planner."""
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.experiment_ledger import Ledger
    from regen.infrastructure.replanning_budget import _closed_state
    root=Path(source)
    if not root.is_absolute():
        raise ValueError('Recovery source must be an absolute proof run path')
    run=read_artifact(root/'run.json')
    ledger=Ledger(root/'effective-generation-budget',run['run_id'])
    if not ledger.rows or ledger.rows[-1]['event_id'] != tail:
        raise ValueError('Recovery source budget tail changed')
    return _closed_state(ledger)


def validate_revision_constraints(before, after, constraints):
    if constraints is None:
        return
    if (not isinstance(constraints, dict) or set(constraints)-CONSTRAINT_FIELDS
            or constraints.get('schema_version') != 1):
        raise ValueError('Invalid recovery revision constraints')
    old = {t['name']: t for t in before['theories']}
    new = {t['name']: t for t in after['theories']}
    for key in ('immutable_theories', 'preserve_theory_contract_sets'):
        names = constraints.get(key, [])
        if (not isinstance(names, list) or any(not isinstance(n, str) for n in names)
                or len(names) != len(set(names)) or not set(names) <= old.keys()):
            raise ValueError('Invalid recovery constraint: '+key)
        for name in names:
            if name not in new:
                raise ValueError('Recovery requires preserved theory: '+name)
            if key == 'immutable_theories' and old[name] != new[name]:
                raise ValueError('Recovery immutable theory changed: '+name)
            if (key == 'preserve_theory_contract_sets'
                    and old[name]['contract_ids'] != new[name]['contract_ids']):
                raise ValueError('Recovery theory contract order/membership changed: '+name)
    owners = {c: t['name'] for t in before['theories'] for c in t['contract_ids']}
    proposed = {c: t['name'] for t in after['theories'] for c in t['contract_ids']}
    fixed = constraints.get('fixed_contract_owners', [])
    if not isinstance(fixed, list):
        raise ValueError('Invalid fixed contract owners')
    seen = set()
    for row in fixed:
        if (not isinstance(row, dict) or set(row) != {'contract_id', 'theory'}
                or not isinstance(row['contract_id'], str) or not isinstance(row['theory'], str)
                or row['contract_id'] in seen
                or owners.get(row['contract_id']) != row['theory']):
            raise ValueError('Invalid fixed contract owner')
        seen.add(row['contract_id'])
        if proposed.get(row['contract_id']) != row['theory']:
            raise ValueError('Recovery fixed contract owner changed: '+row['contract_id'])


def recovery_evidence(value, plan, *, verify_sources=True):
    """Validate an explicitly authorized generated-evidence packet and redact paths."""
    fields = {'schema_version', 'authorization', 'observations', 'notice',
              'recovery_source_run', 'recovery_source_tail_event_id'}
    if (not isinstance(value, dict) or set(value)-fields or value.get('schema_version') != 1
            or not isinstance(value.get('authorization'), str) or not value['authorization'].strip()
            or not isinstance(value.get('observations'), list)):
        raise ValueError('Invalid authorized recovery evidence')
    if 'notice' in value and (not isinstance(value['notice'], str) or not value['notice'].strip()):
        raise ValueError('Invalid recovery notice')
    source = value.get('recovery_source_run')
    tail = value.get('recovery_source_tail_event_id')
    if (source is None) != (tail is None) or (source is not None and (
            not isinstance(source, str) or not source.strip() or not isinstance(tail, str)
            or len(tail) != 64 or any(c not in '0123456789abcdef' for c in tail))):
        raise ValueError('Recovery source and budget tail must be supplied together')
    if verify_sources and source is not None:
        validate_recovery_source(source,tail)
    theories = {t['name'] for t in plan['theories']}
    contracts = {c for t in plan['theories'] for c in t['contract_ids']}
    packet = {'schema_version': 1, 'authorization': value['authorization'], 'observations': [],
              'notice': 'Authorized observations from this generated-proof experiment only. '
                        'They are fallible evidence about failed routes and provider readiness, '
                        'not proof necessity or certified premises. No removed human proofs or '
                        'historical proof dependencies are supplied or authorized.'}
    if 'notice' in value:
        packet['operator_notice'] = value['notice']
    for row in value['observations']:
        if (not isinstance(row, dict)
                or set(row) != {'theory', 'contract_ids', 'kind', 'summary', 'evidence'}
                or not isinstance(row['theory'], str) or row['theory'] not in theories
                or not isinstance(row['contract_ids'], list)
                or any(not isinstance(c, str) or c not in contracts for c in row['contract_ids'])
                or not isinstance(row['kind'], str) or row['kind'] not in EVIDENCE_KINDS
                or not isinstance(row['summary'], str) or not row['summary'].strip()
                or not isinstance(row['evidence'], list) or not row['evidence']):
            raise ValueError('Invalid generated recovery observation')
        refs = []
        for ref in row['evidence']:
            if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
                    or not isinstance(ref['path'], str) or not ref['path']
                    or not isinstance(ref['sha256'], str) or len(ref['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in ref['sha256'])):
                raise ValueError('Invalid recovery evidence reference')
            if verify_sources and hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() != ref['sha256']:
                raise ValueError('Recovery evidence source changed')
            refs.append({'sha256': ref['sha256']})
        packet['observations'].append({**deepcopy(row), 'evidence': refs})
    return packet
