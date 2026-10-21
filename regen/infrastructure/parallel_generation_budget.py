"""Trusted, theory-scoped events for one global parallel generation budget.

Workers share one union clock. A native exclusion or failed provider affects only
its own lane; it must never stop charging another theory that is still working.
"""
from contextlib import contextmanager
from pathlib import Path
import threading

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.progress_events import observe_events
from regen.infrastructure.theory_activity import EXCLUDED_PHASES


class ParallelGenerationBudgetBridge:
    def __init__(self, stage):
        self.stage = stage
        self.lock = threading.RLock()
        self.phases = {}

    @contextmanager
    def acceptance(self, record):
        state = record['state']
        theory = state['graph']['theory']
        with self.stage.lock:
            if theory not in self.stage.theories:
                raise ValueError('proof publication outside the frozen budget plan')
            fixed = self.stage.theories[theory]['contract_ids']
            new = (fixed & set(state['proofs'])) - self.stage.theories[theory]['green_contract_ids']
            self.stage.check_publication(theory, new)
            yield
            for uid in sorted(new):
                self.stage.observe(uid, {'status': 'green', 'source_commit': record['commit_id']},
                                   kind='validation', theory=theory)
            if state['status'] == 'sealed':
                self.stage.mark_sealed(theory)

    @contextmanager
    def worker(self, theory):
        # emit_event observers receive raw fields, not the event_scope identity.
        # Bind here before copy_context propagates into native/transport threads.
        def scoped(event, fields):
            declared = fields.get('theory')
            if declared is not None and declared != theory and event.startswith((
                    'generation_', 'theory_generation_', 'model_work_')):
                self.stage.cancel('effective_clock_theory_mismatch')
                raise ValueError('generation clock event escaped its assigned theory')
            self.handle(event, {**fields, 'theory': theory})
        try:
            with observe_events(scoped, publication_guard=self.acceptance):
                yield
        finally:
            with self.lock:
                dangling = [identity for identity, (owner, _) in self.phases.items()
                            if owner == theory]
                if dangling:
                    self.stage.ledger.append('generation_clock_unclosed_phases',
                        theory=theory, phase_ids=sorted(dangling),
                        disposition='pause_no_silent_recovery')
                    self.stage.cancel('unclosed_harness_phase')
                    for identity in dangling:
                        _, handle = self.phases.pop(identity)
                        self.stage.end_exclusion(handle)
                self.stage.deactivate(theory)

    @contextmanager
    def integration(self):
        # All theory workers have already joined. Native preparation here belongs
        # to final repository certification, not another generation lane.
        handle = self.stage.begin_exclusion('integration', worker_id=None)
        def scoped(event, fields):
            self.handle(event, {**fields, '_budget_global_integration': True})
        try:
            with observe_events(scoped, publication_guard=self.acceptance):
                yield
        finally:
            self.stage.end_exclusion(handle)

    def handle(self, event, fields):
        relevant = {'provider_transport_progress', 'generation_network_pause_requested', 'generation_provider_http_failure', 'generation_items_interrupted',
                    'generation_runtime_integrity_failure',
                    'generation_clock_untrusted', 'generation_exclusion_started',
                    'generation_exclusion_finished', 'theory_generation_started',
                    'theory_generation_stopped', 'model_work_observed',
                    'model_work_observation_finished'}
        if event not in relevant:
            return
        with self.lock:
            theory = fields.get('theory')
            global_native = (fields.get('_budget_global_integration') is True
                and theory is None and event in {
                    'generation_exclusion_started', 'generation_exclusion_finished'})
            if theory not in self.stage.theories and not global_native:
                self.stage.cancel('effective_clock_missing_theory')
                raise ValueError('parallel generation clock requires a registered theory lane')
            if event == 'provider_transport_progress':
                self.stage.ledger.append(event, theory=theory,
                    generation_id=fields.get('generation_id'))
            elif event == 'generation_runtime_integrity_failure':
                # A broken trusted executable prevents meaningful proof actions.
                # Stop charging at trusted receipt, not after model retry loops.
                self.stage.deactivate(theory)
                self.stage.ledger.append(event, theory=theory,
                    evidence={key: fields[key] for key in (
                        'generation_id', 'error_type', 'reason') if key in fields},
                    disposition='pause_preserve_accepted_state', refund_seconds=0)
                self.stage.cancel('runtime_integrity_failure')
            elif event == 'generation_provider_http_failure':
                # This lane cannot generate after a confirmed provider failure.
                # Do not refund earlier reasoning or hide a healthy peer's work.
                self.stage.deactivate(theory)
                self.stage.ledger.append(event, theory=theory, evidence=dict(fields),
                    disposition='close_owner_then_coordinator_retry_decision', refund_seconds=0)
            elif event == 'generation_network_pause_requested':
                self.stage.ledger.append(event, theory=theory, evidence=dict(fields),
                    disposition='pause_resume_latest_accepted_with_separate_draft',
                    refund_seconds=0)
                self.stage.cancel('provider_network_failure')
            elif event == 'generation_items_interrupted':
                self.stage.ledger.append(event, theory=theory, evidence=dict(fields),
                    disposition='interrupted_not_completed', refund_seconds=0)
            elif event == 'generation_clock_untrusted':
                self.stage.ledger.append('generation_clock_untrusted', theory=theory,
                    evidence={key: fields[key] for key in (
                        'reason', 'generation_id', 'thread_id', 'turn_id',
                        'item_ids', 'model_lifecycle_policy') if key in fields},
                    disposition='pause_measurement_invalid', evidence_trusted=False)
                self.stage.cancel('effective_clock_evidence_error')
            elif event == 'generation_exclusion_started':
                reason, identity = fields['category'], fields['phase_id']
                if reason not in EXCLUDED_PHASES or identity in self.phases:
                    self.stage.cancel('effective_clock_phase_error')
                    raise ValueError('invalid or duplicate effective clock phase')
                self.phases[identity] = (theory,
                    self.stage.begin_exclusion(reason, worker_id=theory))
            elif event == 'generation_exclusion_finished':
                identity = fields['phase_id']
                if identity not in self.phases or self.phases[identity][0] != theory:
                    self.stage.cancel('effective_clock_phase_error')
                    raise ValueError('unmatched effective clock phase completion')
                _, handle = self.phases.pop(identity)
                self.stage.end_exclusion(handle)
            elif event == 'theory_generation_started':
                self.stage.select(theory, worker_id=theory)
            elif event == 'theory_generation_stopped':
                self.stage.deactivate(theory)
            else:
                self.stage.set_model_overlap(event == 'model_work_observed', worker_id=theory)

    def close(self):
        with self.lock:
            if self.phases:
                self.stage.ledger.append('generation_clock_unclosed_phases',
                    phase_ids=sorted(self.phases), disposition='pause_no_silent_recovery')
                self.stage.cancel('unclosed_harness_phase')
                for _, handle in self.phases.values():
                    self.stage.end_exclusion(handle)
                self.phases.clear()


def initialize_parallel_budget(root, overview, binding, *, seconds=172800,
                               resume_authorization=None, extension_seconds=None, rows=None):
    """Bind the clock before launch; never give a legacy continuation zero cost."""
    from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY, CHECKPOINT_KIND
    root = Path(root)
    ledger = Ledger(root/'effective-generation-budget', overview.run['run_id'])
    diagnostic = [row for row in ledger.rows if row['kind']=='checkpoint_diagnostic_window_authorized']
    prepared = [row for row in ledger.rows if row['kind']=='checkpoint_diagnostic_window_prepared']
    if diagnostic or prepared:
        if (len(diagnostic)!=1 or len(prepared)!=1
                or prepared[0]['data'].get('authorization_event_id')!=diagnostic[0]['event_id']
                or prepared[0]['data'].get('authorization_sha256')!=digest(diagnostic[0]['data'])):
            raise ValueError('incomplete checkpoint diagnostic clock initialization; do not start or reset')
    imports = [r for r in ledger.rows if r['kind'] == 'parallel_budget_continuation_started']
    if imports and not any(r['kind'] == 'parallel_budget_continuation_completed' for r in ledger.rows):
        raise ValueError('incomplete effective budget continuation import; do not start')
    rows = overview.list_workspace()['theories'] if rows is None else rows
    theories = {row['theory']: [c['id'] if isinstance(c, dict) else c
                               for c in row['contracts']] for row in rows}
    baseline = set()
    has_proofs = any(row.get('green_count', 0) for row in rows)
    has_history = (has_proofs or (root/'workspace-continuation.json').exists()
        or any((root/'parallel-sessions').glob('**/session-transcript.jsonl'))
        or any((root/'theories').glob('*/batch-requests/*'))
        or any((root/'theories').glob('*/document/edits/*')))
    prior_clock = any(row['kind'] == CHECKPOINT_KIND for row in ledger.rows)
    if has_history and not prior_clock:
        raise ValueError('legacy experiment has generated work without the effective budget clock; '
                         'an audited accounting migration is required, never resume from zero')
    identity = {'policy': POLICY, 'run_sha256': digest(overview.run),
                'contracts_sha256': digest(theories), 'configuration_sha256': digest(binding)}
    prior = [row for row in ledger.rows if row['kind'] == 'parallel_budget_binding']
    if prior:
        if len(prior) != 1 or prior[0]['data'] != identity:
            raise ValueError('parallel effective budget source/configuration binding changed')
    elif ledger.rows:
        raise ValueError('parallel effective budget ledger is missing its source binding')
    else:
        ledger.append('parallel_budget_binding', **identity)
    if has_proofs:
        for row in rows:
            if row.get('green_count', 0):
                _, state = overview._state(row['theory'])
                baseline.update(set(theories[row['theory']]) & set(state['proofs']))
    stage = EffectiveTimeBudget(ledger, seconds=seconds,
        resume_authorization=resume_authorization, extension_seconds=extension_seconds)
    try:
        stage.register(theories, baseline_green=baseline)
    except BaseException:
        stage.close()
        raise
    return stage, ParallelGenerationBudgetBridge(stage)


def carry_parallel_budget(source_dir, destination, *, authorization, clock_review,
                          target_strategy=None):
    """Carry a stopped full-workspace continuation's ledger, never reset costs.

    Caller owns the source mutation lock and must establish stopped liveness.
    Checkpoint rollback is intentionally unsupported: it needs separate audited
    replay/duplicate-work accounting, not a refund of the original history.
    """
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY
    source_dir, destination = Path(source_dir).resolve(), Path(destination).resolve()
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('explicit continuation authorization required')
    origin = read_artifact(destination/'workspace-continuation.json')
    if Path(origin['source_run']).resolve() != source_dir or (destination/'checkpoint.json').exists():
        raise ValueError('only full latest workspace continuation may carry this budget')
    source_run, target_run = [read_artifact(p/'run.json') for p in (source_dir, destination)]
    if source_run['run_id'] == target_run['run_id']:
        raise ValueError('continuation requires a new execution identity')
    ledger = Ledger(source_dir/'effective-generation-budget', source_run['run_id'])
    bindings = [r for r in ledger.rows if r['kind'] == 'parallel_budget_binding']
    if (len(bindings) != 1 or bindings[0]['data']['run_sha256'] != digest(source_run)
            or bindings[0]['data']['policy'] != POLICY):
        raise ValueError('invalid source budget binding')
    checkpoints = [r for r in ledger.rows if r['kind'] == CHECKPOINT_KIND]
    if not checkpoints:
        raise ValueError('source lacks effective-time checkpoint')
    state = checkpoints[-1]['data']['state']
    if (not state['paused'] or state['gate_open'] or state['finished']
            or state.get('unclean_recoveries', 0)
            or any(r['kind']=='time_budget_finished' for r in ledger.rows)):
        raise ValueError('source requires a clean paused clock checkpoint')
    source_policy = read_artifact(source_dir/'parallel-workspace-policy.json')
    if digest(source_policy) != bindings[0]['data']['configuration_sha256']:
        raise ValueError('source execution policy hash mismatch')
    target_policy = dict(source_policy)
    if target_strategy is not None:
        from regen.planning.parallel_file_planner import generation_strategy
        if (not isinstance(target_strategy, dict)
                or target_strategy.get('name') != source_policy.get('generation_strategy', {}).get('name')
                or target_strategy != generation_strategy(target_strategy['name'])):
            raise ValueError('strategy update must match the new frozen implementation of the same strategy')
        target_policy['generation_strategy'] = target_strategy
    uncertain = [r for r in ledger.rows if r['kind'] == 'generation_clock_untrusted']
    if (clock_review.get('source_tail_event_id') != ledger.rows[-1]['event_id']
            or clock_review.get('resolved_event_ids') != [r['event_id'] for r in uncertain]
            or clock_review.get('disposition') != 'retain_all_charged_time_no_step_refund'):
        raise ValueError('clock review does not bind exact source history')
    if any(r['data'].get('evidence', {}).get('reason') not in {
            'tool_callback_finished_with_open_model_item',
            'tool_callback_started_with_open_model_item'} for r in uncertain):
        raise ValueError('unsupported uncertainty requires separate accounting review')
    target = Ledger(destination/'effective-generation-budget', target_run['run_id'])
    if target.rows:
        raise ValueError('target budget already exists; never overwrite or replay import')
    # Preserve original wall timestamps and data. New envelopes bind the new run;
    # source event IDs and the tail remain auditable through the migration receipt.
    target.append('parallel_budget_binding', **{**bindings[0]['data'], 'run_sha256':digest(target_run),
        'configuration_sha256':digest(target_policy)})
    target.append('parallel_budget_continuation_started', source_run=str(source_dir),
        source_tail_event_id=ledger.rows[-1]['event_id'], authorization=authorization,
        clock_review=clock_review, source_run_sha256=digest(source_run))
    if target_strategy is not None:
        target.append('parallel_budget_strategy_update', authorization=authorization,
            previous_strategy=source_policy['generation_strategy'], target_strategy=target_strategy,
            previous_configuration_sha256=digest(source_policy),
            target_configuration_sha256=digest(target_policy),
            effective_seconds_unchanged=state['effective_seconds'], seconds_unchanged=state['seconds'])
    import time
    try:
        for row in ledger.rows:
            if row['kind'] == 'parallel_budget_binding':
                continue
            # Never duplicate earlier import-control markers into this import.
            if row['kind'].startswith('parallel_budget_continuation_'):
                continue
            target.clock = lambda row=row: row['time_unix']
            target.append(row['kind'], **row['data'])
    finally:
        target.clock = time.time
    receipt = dict(source_run=str(source_dir), source_tail_event_id=ledger.rows[-1]['event_id'],
        source_rows=len(ledger.rows), authorization=authorization, clock_review=clock_review,
        carried_effective_seconds=state['effective_seconds'],
        carried_worker_seconds=state['worker_seconds'], total_limit_seconds=state['seconds'],
        historical_uncertainty_retained=True, refund_seconds=0)
    target.append('parallel_budget_continuation_completed', **receipt)
    return receipt
