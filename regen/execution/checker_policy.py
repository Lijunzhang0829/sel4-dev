"""Versioned final-theory rendering; missing policy preserves legacy sources.

This policy does not change statement shells, proof batches or hot prefixes.
Changing it requires a new run binding, never an in-place resume override.
"""
ISOLATED_CONTEXT = 'isolated-v1'
ARCH_REUSE_CONTEXT = 'arch-reuse-v1'
COMPLETE_CONTEXT_POLICIES = (ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT)
CONTRACT_OUTCOME_POLICY = 'contract-dispositions-v1'
NATIVE_ATTEMPTS_POLICY = 'native-attempts-v1'
# An omitted option may follow a new-run default; explicit None always means
# the legacy policy. Never use this sentinel when replaying a frozen run.
DEFAULT_OUTCOME_POLICY = object()


def validate_complete_context_policy(value):
    if not isinstance(value, str) or value not in COMPLETE_CONTEXT_POLICIES:
        raise ValueError('unsupported complete_context_policy: '+repr(value))
    return value


def resolve_complete_context_policy(run):
    return validate_complete_context_policy(run.get('complete_context_policy', ISOLATED_CONTEXT))


def policy_binding(run):
    policy = resolve_complete_context_policy(run)
    return {} if policy == ISOLATED_CONTEXT else {'complete_context_policy': policy}


def contract_policy_binding(run):
    """Bind the recorded policy, without applying any new-run defaults."""
    from regen.planning.theory_outcomes import contract_dispositions_enabled
    binding = ({'contract_outcome_policy': run['contract_outcome_policy']}
               if contract_dispositions_enabled(run) else {})
    evidence = validate_unresolved_evidence_policy(run)
    if evidence is not None:
        binding['unresolved_evidence_policy'] = evidence
    return binding


def validate_unresolved_evidence_policy(run):
    """Missing evidence policy retains legacy reports, including old contract runs."""
    value = run.get('unresolved_evidence_policy')
    if value not in (None, NATIVE_ATTEMPTS_POLICY):
        raise ValueError('unsupported unresolved evidence policy: '+repr(value))
    if value is not None and run.get('contract_outcome_policy') != CONTRACT_OUTCOME_POLICY:
        raise ValueError('native-attempts-v1 requires contract-dispositions-v1')
    return value


def new_outcome_policy_binding(contract_outcome_policy=CONTRACT_OUTCOME_POLICY,
                               unresolved_evidence_policy=DEFAULT_OUTCOME_POLICY):
    """Resolve only fresh repository defaults; explicit None selects legacy."""
    if contract_outcome_policy is DEFAULT_OUTCOME_POLICY:
        contract_outcome_policy = CONTRACT_OUTCOME_POLICY
    if unresolved_evidence_policy is DEFAULT_OUTCOME_POLICY:
        unresolved_evidence_policy = (NATIVE_ATTEMPTS_POLICY
                                     if contract_outcome_policy == CONTRACT_OUTCOME_POLICY else None)
    return contract_policy_binding({'contract_outcome_policy':contract_outcome_policy,
                                    'unresolved_evidence_policy':unresolved_evidence_policy})


def continuation_outcome_arguments(source):
    """Explicit missing values prevent a continuation from adopting new defaults."""
    contract_policy_binding(source)
    return {key:source.get(key) for key in ('contract_outcome_policy','unresolved_evidence_policy')}


def validate_outcome_upgrade_readiness(run, root):
    """A partially prepared policy fork must never start with a fresh budget."""
    upgrade = run.get('outcome_policy_upgrade')
    if upgrade is None:
        return
    from pathlib import Path
    from regen.execution.proof_commits import digest
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.experiment_ledger import Ledger
    from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
    root = Path(root)
    path = root/'outcome-policy-upgrade.json'
    if not path.exists():
        raise ValueError('outcome policy upgrade is incomplete; finish preparation before resuming')
    receipt = read_artifact(path)
    if (receipt.get('state') != 'prepared' or receipt.get('run_sha256') != digest(run)
            or receipt.get('upgrade') != upgrade or receipt.get('historical_costs_reset') is not False):
        raise ValueError('invalid outcome policy upgrade receipt')
    source_dir = Path(upgrade['source_run'])
    source = read_artifact(source_dir/'run.json')
    if digest(source) != upgrade['source_run_sha256']:
        raise ValueError('outcome policy upgrade source run changed')
    source_rows = Ledger(source_dir/'effective-generation-budget', source['run_id']).rows
    tail = next((i for i,r in enumerate(source_rows)
                 if r['event_id'] == upgrade['source_tail_event_id']), None)
    if tail is None:
        raise ValueError('outcome policy upgrade source budget tail is missing')
    source_prefix = source_rows[:tail+1]
    source_state = next((r['data']['state'] for r in reversed(source_prefix)
                         if r['kind'] == CHECKPOINT_KIND), None)
    rows = Ledger(root/'effective-generation-budget', run['run_id']).rows
    complete = [r for r in rows if r['kind'] == 'parallel_budget_continuation_completed']
    bindings = [r for r in rows if r['kind'] == 'parallel_budget_binding']
    if (len(complete) != 1 or len(bindings) != 1
            or bindings[0]['data'].get('run_sha256') != digest(run)
            or complete[0]['event_id'] != receipt.get('budget_completed_event_id')
            or complete[0]['data'] != receipt.get('budget')):
        raise ValueError('outcome policy upgrade budget transfer is incomplete or changed')
    budget = complete[0]['data']
    if (source_state is None or budget.get('source_tail_event_id') != upgrade['source_tail_event_id']
            or budget.get('source_run') != str(source_dir)
            or budget.get('authorization') != upgrade['authorization']
            or budget.get('refund_seconds') != 0
            or budget.get('carried_effective_seconds') != source_state['effective_seconds']
            or budget.get('carried_worker_seconds') != source_state['worker_seconds']
            or budget.get('total_limit_seconds') != source_state['seconds']):
        raise ValueError('outcome policy upgrade changed charged work or budget limit')
    target_state = next((r['data']['state'] for r in reversed(rows[:complete[0]['sequence']-1])
                         if r['kind'] == CHECKPOINT_KIND), None)
    if target_state != source_state:
        raise ValueError('outcome policy upgrade failed to preserve the source checkpoint')


def exports_available(status):
    return status in {'sealed', 'exported_partial'}


def theory_finished(status):
    return status in {'sealed', 'exported_partial', 'retired'}
