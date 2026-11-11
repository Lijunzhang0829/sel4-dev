"""Frozen interpretation of results and separately bound environment evidence.

Evidence about a parameter instance is not a proof about the original fixed
constants. Neither this metadata nor a negative certificate supplies premises.
Legacy runs omit result_scope and keep their original strict negative meaning.
"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re

from regen.execution.proof_commits import digest

OWNED_CONTEXT_POLICY = 'owned-context-outcomes-v1'
NEGATIVE_STATUSES = frozenset({'refuted', 'context_refuted'})
ENVIRONMENT_SCHEMA = 'contract-environment-evidence-v1'


def resolve_result_scope(run):
    scope = run.get('result_scope')
    if scope not in (None, OWNED_CONTEXT_POLICY):
        raise ValueError('unsupported frozen result scope')
    if scope is not None and run.get('contract_outcome_policy') != 'contract-dispositions-v1':
        raise ValueError('owned-context results require individual contract dispositions')
    return scope


def result_scope_binding(run):
    scope = resolve_result_scope(run)
    if scope is None:
        return {}
    bundle = run.get('environment_evidence')
    if not isinstance(bundle, dict) or not bundle.get('bundle_sha256'):
        raise ValueError('owned-context policy lacks frozen environment evidence')
    return {'result_scope': scope, 'environment_evidence_sha256':bundle['bundle_sha256']}


def expected_negative_status(run):
    return 'context_refuted' if resolve_result_scope(run) else 'refuted'


def validate_negative_scope(run, certificate, contract=None):
    scope = resolve_result_scope(run)
    if not isinstance(certificate, dict) or certificate.get('status') != expected_negative_status(run):
        raise ValueError('negative certificate does not match frozen result scope')
    if scope is not None and certificate.get('result_scope') != scope:
        raise ValueError('negative certificate scope binding mismatch')
    if scope is not None and contract is not None:
        view = contract_environment_view(run, contract)
        prepared = view['native_obligations']
        observed = certificate.get('scope_evidence', {})
        for key in ('environment', 'local_target', 'positive_obligation', 'negative_obligation'):
            key += '_term_sha256'
            if not prepared.get(key) or prepared[key] != observed.get(key):
                raise ValueError('negative certificate differs from prepared environment/target: '+key)


def environment_bundle(contracts, scan, checker_context_id, *, parameter_instance_evidence=None):
    """Trusted preparation only: freeze a successful native scope export."""
    from regen.execution.contract_refutation import contract_binding
    if not scan.get('passed') or set(scan.get('records', {})) != {c['contract_id'] for c in contracts}:
        raise ValueError('environment preparation requires a complete successful native export')
    records = {}
    if parameter_instance_evidence is not None:
        from regen.preparation import calg_environment_evidence
        evidence=parameter_instance_evidence
        native_check=evidence.get('native_check', {})
        if (evidence.get('passed') is not True or
                evidence.get('evidence_sha256')!=digest({k:v for k,v in evidence.items() if k!='evidence_sha256'})
                or evidence.get('verifier_sha256')!=hashlib.sha256(Path(calg_environment_evidence.__file__).read_bytes()).hexdigest()
                or native_check.get('verdict')!='green' or native_check.get('returncode')!=0):
            raise ValueError('parameter-instance evidence did not pass its native audit')
    for contract in contracts:
        native = deepcopy(scan['records'][contract['contract_id']])
        instance = None
        if parameter_instance_evidence is not None:
            observed=parameter_instance_evidence['records'][native['environment_term_sha256']]
            expected={'source_term':native['environment_term'], 'skip_proof':'false',
                      'oracles':'0','hypotheses':'0','changed_axioms':'false',
                      'original_constants_established':'false'}
            if (any(observed.get(k)!=v for k,v in expected.items())
                    or not observed.get('rewrite_proposition')
                    or observed.get('instance_term')!=observed.get('proved_term')):
                raise ValueError('parameter-instance evidence does not match the exported environment')
            instance={'status':'checked_parameter_instance',
                'environment_term_sha256':native['environment_term_sha256'],
                'instance_term_sha256':hashlib.sha256(bytes.fromhex(observed['instance_term'])).hexdigest(),
                'evidence_sha256':parameter_instance_evidence['evidence_sha256'],
                'establishes_original_constants':False}
        binding = {'checker_context_id':checker_context_id,
                   'owned_context':contract_binding(contract)['context'],
                   'environment_term_sha256':native['environment_term_sha256']}
        records[contract['contract_id']] = {
            'contract_sha256':digest(contract_binding(contract)),
            'environment_binding':binding, 'environment_id':'environment:'+digest(binding),
            # Even a context-free theorem may use fixed global assumptions
            # embedded in definitions. Only an explicit native verifier could
            # upgrade this to an established concrete deployment.
            'fixed_environment_status':'not_established',
            'native_obligations':native, 'obligations_sha256':digest(native),
            'parameter_instance_evidence':instance,
        }
    value = {'schema':ENVIRONMENT_SCHEMA, 'result_scope':OWNED_CONTEXT_POLICY,
             'checker_context_id':checker_context_id, 'contracts':records,
             'native_export':{k:scan.get(k) for k in ('source_sha256','log_sha256','support_sha256')}}
    value['bundle_sha256'] = digest(value)
    return validate_environment_bundle(value, contracts, checker_context_id=checker_context_id)


def validate_environment_bundle(bundle, contracts, *, checker_context_id=None):
    from regen.execution.contract_refutation import contract_binding, OWNED_CONTEXT_SUPPORT
    if (not isinstance(bundle, dict) or bundle.get('schema') != ENVIRONMENT_SCHEMA
            or bundle.get('result_scope') != OWNED_CONTEXT_POLICY):
        raise ValueError('invalid contract environment evidence bundle')
    if checker_context_id is not None and bundle.get('checker_context_id') != checker_context_id:
        raise ValueError('environment evidence checker identity changed')
    if bundle.get('native_export', {}).get('support_sha256') != hashlib.sha256(OWNED_CONTEXT_SUPPORT.read_bytes()).hexdigest():
        raise ValueError('environment export implementation changed')
    records = bundle.get('contracts')
    expected = {c['contract_id']: c for c in contracts}
    if not isinstance(records, dict) or set(records) != set(expected):
        raise ValueError('environment evidence must cover exactly the fixed contracts')
    for key, record in records.items():
        if not isinstance(record, dict):
            raise ValueError('invalid environment record')
        if record.get('contract_sha256') != digest(contract_binding(expected[key])):
            raise ValueError('environment evidence fixed declaration changed: ' + key)
        if record.get('fixed_environment_status') != 'not_established':
            # Establishing a concrete deployment needs a separate theorem
            # verifier. No arbitrary JSON can claim that proof here.
            raise ValueError('unsupported fixed-environment evidence claim')
        if not isinstance(record.get('native_obligations'), dict):
            raise ValueError('environment evidence lacks native obligation export')
        if record.get('obligations_sha256') != digest(record['native_obligations']):
            raise ValueError('environment obligation export changed')
        native = record['native_obligations']
        if (native.get('result_scope') != OWNED_CONTEXT_POLICY or
                native.get('scope_evidence_sha256') != digest({k:v for k,v in native.items() if k != 'scope_evidence_sha256'})):
            raise ValueError('invalid native scope evidence')
        for name in ('environment','statement_assumptions','local_target','positive_obligation','negative_obligation'):
            try:
                raw = bytes.fromhex(native[name+'_term'])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError('invalid native obligation term') from exc
            if not raw or hashlib.sha256(raw).hexdigest() != native.get(name+'_term_sha256'):
                raise ValueError('native obligation term hash mismatch')
        binding = record.get('environment_binding')
        expected_binding = {'checker_context_id':bundle.get('checker_context_id'),
                            'owned_context':contract_binding(expected[key])['context'],
                            'environment_term_sha256':native['environment_term_sha256']}
        if binding != expected_binding:
            raise ValueError('environment binding differs from native export')
        instance=record.get('parameter_instance_evidence')
        if instance is not None and (instance.get('status')!='checked_parameter_instance'
                or instance.get('environment_term_sha256')!=native['environment_term_sha256']
                or instance.get('establishes_original_constants') is not False
                or set(instance)!={'status','environment_term_sha256','instance_term_sha256',
                                   'evidence_sha256','establishes_original_constants'}
                or any(not isinstance(instance.get(k),str) or not re.fullmatch('[0-9a-f]{64}',instance[k])
                       for k in ('environment_term_sha256','instance_term_sha256','evidence_sha256'))):
            raise ValueError('parameter instance is not evidence of the original constants')
        if record.get('environment_id') != 'environment:' + digest(binding):
            raise ValueError('environment identity mismatch')
        if record['environment_binding'].get('checker_context_id') != bundle.get('checker_context_id'):
            raise ValueError('contract environment does not belong to its checker')
    if bundle.get('bundle_sha256') != digest({k: v for k, v in bundle.items() if k != 'bundle_sha256'}):
        raise ValueError('environment evidence bundle changed')
    return bundle


def dataset_result_binding(dataset, contracts, checker_context_id):
    path = Path(dataset)/'result-scope.json'
    context_path = Path(dataset)/'checker-context.json'
    context = json.loads(context_path.read_text()) if context_path.exists() else {}
    if not path.exists():
        if context.get('result_scope') is not None:
            raise ValueError('dataset environment preparation is incomplete')
        return {}
    bundle = validate_environment_bundle(json.loads(path.read_text()), contracts,
                                         checker_context_id=checker_context_id)
    if context_path.exists() and context.get('result_scope') != bundle['result_scope']:
        raise ValueError('dataset result scope differs from frozen checker context')
    # Keep exact native terms in the immutable prepared artifact, not in every
    # run/task/journal snapshot. The run binds their content identity and path.
    return {'result_scope': OWNED_CONTEXT_POLICY, 'environment_evidence': {
        'bundle_sha256':bundle['bundle_sha256'], 'path':str(path.resolve()),
        'checker_context_id':checker_context_id}}


@lru_cache(maxsize=4)
def _prepared_bundle(path, stamp, expected_digest):
    value=json.loads(Path(path).read_text())
    stat=Path(path).stat()
    if (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)!=stamp:
        raise ValueError('environment evidence changed during read')
    if (value.get('bundle_sha256')!=expected_digest or
            digest({k:v for k,v in value.items() if k!='bundle_sha256'})!=expected_digest):
        raise ValueError('frozen environment evidence changed')
    return value


def _run_bundle(run):
    record=run.get('environment_evidence')
    if not isinstance(record,dict):
        raise ValueError('owned-context run lacks prepared environment evidence')
    if not run.get('checker_context_id') or record.get('checker_context_id')!=run['checker_context_id']:
        raise ValueError('environment evidence does not belong to the current run checker')
    if 'path' not in record:
        raise ValueError('owned-context run requires a frozen prepared artifact reference')
    path=Path(record['path']);stat=path.stat()
    stamp=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    value=_prepared_bundle(str(path),stamp,record['bundle_sha256'])
    if value.get('checker_context_id')!=record.get('checker_context_id'):
        raise ValueError('environment reference checker identity changed')
    return value


def contract_environment_view(run, contract):
    """No prover call; present already prepared exact obligations on demand."""
    from regen.execution.contract_refutation import contract_binding
    scope = resolve_result_scope(run)
    if scope is None:
        return {'result_scope': 'legacy-strict-global-negation',
                'positive_scope': 'fixed-owned-context',
                'negative_scope': 'negation-of-the-exported-closed-positive-target',
                'fixed_environment_status': 'not_recorded'}
    bundle = _run_bundle(run)
    record = bundle.get('contracts', {}).get(contract['contract_id'])
    if not isinstance(record, dict) or record.get('contract_sha256') != digest(contract_binding(contract)):
        raise ValueError('owned-context entry lacks matching environment evidence')
    visible = deepcopy(record)
    visible['native_obligations'] = {k:v for k,v in visible['native_obligations'].items()
                                     if not k.endswith('_term')}
    return {'result_scope': scope, **visible,
            'positive_scope': 'E implies universally closed contract',
            'negative_scope': 'E implies negation of universally closed contract',
            'strict_global_refutation': False,
            'notice': 'The same owned environment E conditions both results. A parameter-instance witness does not establish E for the original fixed constants. This record provides no usable fact or import.'}
