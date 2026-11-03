"""Append a separately measured recovery-planning cost to a paused experiment.

The planning clock is bounded by the original remainder. Its checkpoints are
evidence, never replacement checkpoints for the cumulative proof clock.
"""
from copy import deepcopy
import fcntl
import math
from pathlib import Path
from types import SimpleNamespace

from regen.execution.proof_commits import digest, durable
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.provider_request_accounting import ProviderRequestAccounting


STARTED = 'replanning_budget_import_started'
EVIDENCE = 'replanning_budget_source_event'
COMPLETED = 'replanning_budget_import_completed'
BOUNDARY = 'replanning_budget_import'


def _closed_state(ledger):
    rows = [r for r in ledger.rows if r['kind'] == CHECKPOINT_KIND]
    if not rows:
        raise ValueError('replanning requires an existing budget checkpoint')
    state = rows[-1]['data']['state']
    if (state.get('policy') != POLICY or not state.get('include_planning')
            or not state.get('paused') or state.get('finished')
            or state.get('gate_open') or state.get('planning_active')
            or state.get('unclean_recoveries')
            or any(r['kind'] == 'time_budget_finished' for r in ledger.rows)):
        raise ValueError('replanning requires a clean paused cumulative clock')
    return deepcopy(state)


def _accounting(state):
    value = state.get('provider_request_accounting')
    if value is None:
        return ProviderRequestAccounting(state['effective_seconds'], state['worker_seconds'],
                                         state.get('theory_seconds', {}))
    return ProviderRequestAccounting.restore(value,
        effective_seconds=state['effective_seconds'], worker_seconds=state['worker_seconds'],
        theory_seconds=state.get('theory_seconds', {}))


def _merged_state(before, planning):
    after = deepcopy(before)
    delta = planning['effective_seconds']
    if delta < 0 or not math.isfinite(delta) or delta >= before['seconds']-before['effective_seconds']:
        raise ValueError('replanning consumed the original remaining allowance')
    extra = _accounting(planning)
    # Reviewed planning is serial and uses its dedicated request accounting;
    # no proof-worker request IDs or pending segments may enter this merge.
    if (extra.requests or extra.active or extra.segments or extra.credits
            or extra.raw_worker_seconds or extra.raw_theory_seconds
            or extra.excluded_effective_seconds or extra.excluded_worker_seconds
            or extra.excluded_theory_seconds or extra.other_excluded_worker_seconds
            or extra.other_excluded_theory_seconds):
        raise ValueError('replanning ledger contains proof-worker accounting')
    accounting = _accounting(before)
    accounting.raw_effective_seconds += extra.raw_effective_seconds
    accounting.other_excluded_effective_seconds += extra.other_excluded_effective_seconds
    after['effective_seconds'] += delta
    after['planning_seconds'] += planning['planning_seconds']
    accounting.validate(after['effective_seconds'], after['worker_seconds'], after.get('theory_seconds', {}))
    after.update(provider_request_accounting=accounting.state(), **accounting.summary())
    return after


def append_replanning_budget(source_run, destination, planning_directory, *, authorization):
    """Call under the continuation's run lock, before its first worker starts."""
    from regen.infrastructure.parallel_generation_budget import _planning_budget_source, _file_sha256
    source_run, destination, planning_directory = map(
        lambda p: Path(p).resolve(), (source_run, destination, planning_directory))
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('explicit replanning authorization required')
    old_run, new_run = [read_artifact(p/'run.json') for p in (source_run, destination)]
    old = Ledger(source_run/'effective-generation-budget', old_run['run_id'])
    target = Ledger(destination/'effective-generation-budget', new_run['run_id'])
    validate_replanning_imports(source_run, ledger=old)
    validate_replanning_imports(destination, ledger=target)
    before = _closed_state(target)
    old_state = _closed_state(old)
    for key in ('base_seconds', 'seconds', 'effective_seconds', 'planning_seconds',
                'worker_seconds', 'theory_seconds', 'provider_request_accounting'):
        if before.get(key) != old_state.get(key):
            raise ValueError('replanning destination must retain all source costs')
    tails = [r for r in target.rows if r['kind'] == 'parallel_budget_continuation_completed']
    if (len(tails) != 1 or Path(tails[0]['data']['source_run']).resolve() != source_run
            or tails[0]['data']['source_tail_event_id'] != old.rows[-1]['event_id']):
        raise ValueError('replanning destination must bind the exact source continuation')
    remaining = before['seconds']-before['effective_seconds']
    policy = read_artifact(destination/'parallel-workspace-policy.json') if (
        destination/'parallel-workspace-policy.json').exists() else read_artifact(source_run/'parallel-workspace-policy.json')
    frozen = read_artifact(planning_directory/'frozen-plan.json')
    plan_path = Path(frozen['plan'])
    plan_path = plan_path if plan_path.is_absolute() else planning_directory/plan_path
    if (not plan_path.resolve().is_relative_to(planning_directory)
            or _file_sha256(plan_path) != frozen.get('plan_sha256')):
        raise ValueError('replanning plan hash or location changed')
    plan = read_artifact(plan_path)
    theories = {row['name']: row['contract_ids'] for row in plan['theories']}
    planning, identity = _planning_budget_source(planning_directory,
        SimpleNamespace(run=new_run), theories, policy, remaining)
    binding = [r for r in planning.rows if r['kind'] == 'reviewed_planning_budget_binding']
    if (len(binding) != 1
            or Path(binding[0]['data'].get('recovery_source_run', '')).resolve() != source_run
            or binding[0]['data'].get('recovery_source_tail_event_id') != old.rows[-1]['event_id']):
        raise ValueError('replanning clock does not bind the paused source history')
    after = _merged_state(before, planning.rows[-1]['data']['state'])
    claim = dict(policy='replanning-budget-addition-v1', authorization=authorization,
        source_run=str(source_run), source_run_sha256=digest(old_run),
        source_tail_event_id=old.rows[-1]['event_id'], target_run=str(destination),
        target_run_sha256=digest(new_run), target_run_id=new_run['run_id'],
        prior_target_tail_event_id=target.rows[-1]['event_id'], planning=identity,
        before_state=before, after_state_sha256=digest(after),
        previous_effective_seconds=before['effective_seconds'],
        added_effective_seconds=identity['carried_effective_seconds'],
        added_planning_seconds=identity['carried_planning_seconds'],
        cumulative_effective_seconds=after['effective_seconds'],
        total_limit_seconds=before['seconds'], refund_seconds=0)
    with (planning_directory/'budget-transfer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = planning_directory/'budget-transfer.json'
        if path.exists():
            raise ValueError('replanning budget already claimed; never replay a partial import')
        durable(path, claim, immutable=True)
        start = target.append(STARTED, **claim)
        for row in planning.rows:
            target.append(EVIDENCE, import_event_id=start['event_id'], source_event=row)
        checkpoint = target.append(CHECKPOINT_KIND, boundary=BOUNDARY, state=after,
            import_event_id=start['event_id'])
        complete = target.append(COMPLETED, import_event_id=start['event_id'],
            claim_sha256=digest(claim), checkpoint_event_id=checkpoint['event_id'])
    validate_replanning_imports(destination, ledger=target)
    return {**claim, 'completion_event_id':complete['event_id']}


def validate_replanning_imports(root, *, ledger=None):
    """Recheck inherited additions too; incomplete imports never start workers."""
    root = Path(root).resolve()
    if ledger is None:
        run = read_artifact(root/'run.json')
        ledger = Ledger(root/'effective-generation-budget', run['run_id'])
    starts = [r for r in ledger.rows if r['kind'] == STARTED]
    completions = [r for r in ledger.rows if r['kind'] == COMPLETED]
    evidence = [r for r in ledger.rows if r['kind'] == EVIDENCE]
    checkpoints = [r for r in ledger.rows if r['kind'] == CHECKPOINT_KIND
                   and r['data'].get('boundary') == BOUNDARY]
    if len(starts) != len(completions) or len(starts) != len(checkpoints):
        raise ValueError('incomplete replanning budget import; do not start or reset')
    # Event IDs change when a cumulative ledger is carried to another run.
    # The original start identity is retained in each following receipt.
    referenced = set()
    directories = set()
    for start in starts:
        claim = start['data']
        matches = [r for r in completions if r['data'].get('claim_sha256') == digest(claim)]
        if len(matches) != 1:
            raise ValueError('duplicate or changed replanning import receipt')
        complete = matches[0]
        identity = complete['data']['import_event_id']
        if identity in referenced:
            raise ValueError('duplicate replanning import identity')
        referenced.add(identity)
        rows = [r['data']['source_event'] for r in evidence
                if r['data'].get('import_event_id') == identity]
        states = [r for r in checkpoints if r['data'].get('import_event_id') == identity]
        if (len(states) != 1 or not start['sequence'] < states[0]['sequence'] < complete['sequence']):
            raise ValueError('replanning checkpoint order changed')
        planning_dir = Path(claim['planning']['source_planning_dir']).resolve()
        if planning_dir in directories:
            raise ValueError('planning cost imported more than once')
        directories.add(planning_dir)
        if read_artifact(planning_dir/'budget-transfer.json') != claim:
            raise ValueError('replanning transfer claim changed')
        source = Ledger(planning_dir/'budget', claim['planning']['source_ledger_run_id'])
        if (rows != source.rows or digest(rows) != claim['planning']['source_rows_sha256']
                or source.rows[-1]['event_id'] != claim['planning']['source_tail_event_id']):
            raise ValueError('replanning source accounting evidence changed')
        from regen.infrastructure.parallel_generation_budget import _file_sha256
        receipt = read_artifact(planning_dir/'budget-receipt.json')
        frozen = read_artifact(planning_dir/'frozen-plan.json')
        def artifact(key):
            path = Path(frozen[key])
            path = (path if path.is_absolute() else planning_dir/path).resolve()
            if not path.is_relative_to(planning_dir):
                raise ValueError('replanning artifact escapes its planning directory')
            return path
        expected = claim['planning']
        if (_file_sha256(planning_dir/'budget-receipt.json') != expected['receipt_sha256']
                or _file_sha256(planning_dir/'frozen-plan.json') != expected['frozen_plan_sha256']
                or _file_sha256(artifact('plan')) != expected['repository_plan_sha256']
                or _file_sha256(artifact('file_generation_list')) != expected['file_generation_list_sha256']
                or receipt['ledger_tail_event_id'] != source.rows[-1]['event_id']):
            raise ValueError('replanning plan or receipt evidence changed')
        target_run = read_artifact(Path(claim['target_run'])/'run.json')
        if digest(target_run) != claim['target_run_sha256']:
            raise ValueError('replanning destination identity changed')
        old_path = Path(claim['source_run'])
        old_run = read_artifact(old_path/'run.json')
        old_ledger = Ledger(old_path/'effective-generation-budget', old_run['run_id'])
        if (digest(old_run) != claim['source_run_sha256']
                or old_ledger.rows[-1]['event_id'] != claim['source_tail_event_id']):
            raise ValueError('paused replanning source changed')
        after = _merged_state(claim['before_state'], source.rows[-1]['data']['state'])
        if (digest(after) != claim['after_state_sha256'] or states[0]['data']['state'] != after
                or claim['total_limit_seconds'] != after['seconds'] or claim['refund_seconds'] != 0):
            raise ValueError('replanning cumulative accounting changed')
    if any(r['data'].get('import_event_id') not in referenced for r in evidence+checkpoints):
        raise ValueError('unowned replanning accounting evidence')
    return len(starts)
