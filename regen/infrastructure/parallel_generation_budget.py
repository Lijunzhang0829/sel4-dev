"""Trusted, theory-scoped events for one global parallel generation budget.

Workers share one union clock. A native exclusion or failed provider affects only
its own lane; it must never stop charging another theory that is still working.
"""
from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import threading

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.progress_events import observe_events
from regen.infrastructure.theory_activity import EXCLUDED_PHASES


PLANNING_IMPORT_STARTED = 'planning_budget_import_started'
PLANNING_IMPORT_COMPLETED = 'planning_budget_import_completed'


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_planning_models(receipt, run, binding):
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.audited_llm import model_settings
    model, effort = receipt.get('model'), receipt.get('reasoning_effort')
    settings = model_settings()
    frozen_settings = read_artifact(Path(run['model_config_path']))
    if (not isinstance(model, str) or not model or not isinstance(effort, str) or not effort
            or not run.get('execution_models')
            or set(run['execution_models'].values()) != {model}
            or binding.get('model') != model
            or frozen_settings.get('reasoning_effort') != effort
            or settings.get('reasoning_effort') != effort
            or set(settings.get('role_models', {}).values()) != {model}):
        raise ValueError('planning budget model or reasoning effort differs from proof generation')


def _planning_budget_source(directory, overview, theories, binding, seconds):
    """Verify a closed planning clock against the exact downstream experiment."""
    from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND, POLICY
    from regen.infrastructure.incremental_log import read_artifact
    directory = Path(directory).resolve()
    receipt_path = directory/'budget-receipt.json'
    receipt = read_artifact(receipt_path)
    if (receipt.get('schema_version') != 1 or receipt.get('state') != 'complete'
            or receipt.get('policy') != POLICY or receipt.get('include_planning') is not True
            or receipt.get('ledger_run_id') != directory.name
            or receipt.get('seconds') != seconds):
        raise ValueError('planning budget receipt policy or allowance mismatch')
    source = Ledger(directory/'budget', receipt['ledger_run_id'])
    if not source.rows or source.rows[-1]['event_id'] != receipt.get('ledger_tail_event_id'):
        raise ValueError('planning budget receipt does not bind the ledger tail')
    last = source.rows[-1]
    state = last['data'].get('state', {})
    forbidden = {'time_budget_registered', 'time_budget_green', 'time_budget_sealed',
        'time_budget_finished', 'time_budget_extended', 'proof_commit',
        PLANNING_IMPORT_STARTED, PLANNING_IMPORT_COMPLETED, 'parallel_budget_binding'}
    if (last['kind'] != CHECKPOINT_KIND or last['data'].get('boundary') != 'close'
            or state.get('policy') != POLICY or state.get('include_planning') is not True
            or state.get('base_seconds') != seconds or state.get('seconds') != seconds
            or state.get('planning_active') is not False or state.get('gate_open') is not False
            or state.get('finished') is not False or state.get('paused') is not False
            or state.get('reason') is not None or state.get('unclean_recoveries') != 0
            or state.get('worker_seconds') != 0 or state.get('theory_seconds') != {}
            or any(row['kind'] in forbidden for row in source.rows)
            or not any(row['kind'] == CHECKPOINT_KIND
                and row['data'].get('boundary') == 'planning_finished' for row in source.rows)):
        raise ValueError('planning budget requires a clean, unallocated completed planning clock')
    spent = state.get('effective_seconds')
    planning = state.get('planning_seconds')
    if (type(spent) not in (int, float) or not math.isfinite(spent) or spent < 0
            or type(planning) not in (int, float) or not math.isfinite(planning)
            or not math.isclose(spent, planning, rel_tol=0, abs_tol=1e-9)
            or spent >= seconds):
        raise ValueError('planning budget accounting is invalid or allowance exhausted')

    frozen_path = directory/'frozen-plan.json'
    frozen = read_artifact(frozen_path)
    if (frozen.get('state') != 'frozen'
            or _file_sha256(frozen_path) != receipt.get('frozen_plan_sha256')):
        raise ValueError('planning budget frozen-plan receipt mismatch')
    def artifact(key):
        path = Path(frozen[key])
        path = (path if path.is_absolute() else directory/path).resolve()
        if not path.is_relative_to(directory):
            raise ValueError('planning budget plan artifacts escape their planning directory')
        return path
    plan_path, list_path = artifact('plan'), artifact('file_generation_list')
    if (_file_sha256(plan_path) != receipt.get('repository_plan_sha256')
            or frozen.get('plan_sha256') != receipt.get('repository_plan_sha256')
            or _file_sha256(list_path) != receipt.get('file_generation_list_sha256')):
        raise ValueError('planning budget plan or file-list hash mismatch')
    plan, listing = read_artifact(plan_path), read_artifact(list_path)
    run = overview.run
    schedule = run.get('session_schedule', {})
    if (Path(schedule.get('source', '')).resolve() != list_path
            or schedule.get('file_list_sha256') != digest(listing)
            or schedule.get('plan_id') != plan.get('plan_id')
            or schedule.get('plan_revision') != plan.get('revision')
            or listing.get('plan_id') != plan.get('plan_id')
            or listing.get('plan_revision') != plan.get('revision')):
        raise ValueError('planning budget source differs from the run-frozen plan')
    planned = {row['name']: row['contract_ids'] for row in plan['theories']}
    listed = {row['theory']: row['contract_ids'] for row in listing['files']}
    if (len(planned) != len(plan['theories']) or len(listed) != len(listing['files'])
            or planned != theories or listed != theories):
        raise ValueError('planning budget contract allocation differs from the proof run')
    if run.get('execution_input_kind') != 'refine-dataset':
        raise ValueError('planning budget requires an independent dataset proof run')
    source_input = Path(run['checker_context_root'])/'repository-planning-input.json'
    if _file_sha256(source_input) != receipt.get('input_sha256'):
        raise ValueError('planning budget input differs from the proof run dataset')
    contracts = read_artifact(source_input)['contracts']
    allocated = [uid for ids in theories.values() for uid in ids]
    if (len(allocated) != len(set(allocated))
            or sorted(allocated) != sorted(row['contract_id'] for row in contracts)):
        raise ValueError('planning budget does not cover the exact fixed input contracts')
    _verify_planning_models(receipt, run, binding)
    identity = dict(source_planning_dir=str(directory),
        receipt_sha256=_file_sha256(receipt_path),
        source_ledger_run_id=source.run_id, source_tail_event_id=source.rows[-1]['event_id'],
        source_rows_sha256=digest(source.rows), source_rows=len(source.rows),
        input_sha256=receipt['input_sha256'], frozen_plan_sha256=receipt['frozen_plan_sha256'],
        repository_plan_sha256=receipt['repository_plan_sha256'],
        file_generation_list_sha256=receipt['file_generation_list_sha256'],
        carried_effective_seconds=spent, carried_planning_seconds=planning,
        total_limit_seconds=seconds, include_planning=True,
        model=receipt['model'], reasoning_effort=receipt['reasoning_effort'])
    return source, identity


def _import_planning_budget(ledger, source, identity, root, run):
    """Claim one destination and retain every source event without resetting time."""
    import fcntl
    from regen.execution.proof_commits import durable
    from regen.infrastructure.incremental_log import read_artifact
    directory = Path(identity['source_planning_dir'])
    claim = {**identity, 'target_run_dir':str(Path(root).resolve()),
             'target_run_id':run['run_id'], 'target_run_sha256':digest(run)}
    with (directory/'budget-transfer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = directory/'budget-transfer.json'
        if path.exists():
            if read_artifact(path) != claim:
                raise ValueError('planning budget already transferred to another proof run')
            raise ValueError('planning budget transfer already claimed; never replay an incomplete import')
        durable(path, claim, immutable=True)
        ledger.append(PLANNING_IMPORT_STARTED, **claim)
        previous_clock = ledger.clock
        try:
            for row in source.rows:
                ledger.clock = lambda row=row: row['time_unix']
                ledger.append(row['kind'], **row['data'],
                    planning_source_event_id=row['event_id'],
                    planning_source_sequence=row['sequence'])
        finally:
            ledger.clock = previous_clock
        ledger.append(PLANNING_IMPORT_COMPLETED, **claim, refund_seconds=0)


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
        relevant = {'native_request_started', 'native_request_finished', 'provider_transport_progress', 'generation_network_pause_requested', 'generation_provider_http_failure', 'generation_items_interrupted',
                    'generation_provider_request_started', 'generation_provider_request_finished',
                    'generation_provider_request_failed', 'provider_feedback_retry_succeeded',
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
            if event == 'native_request_started':
                self.stage.begin_native_request(theory, fields['request_id'], fields['binding'])
            elif event == 'native_request_finished':
                self.stage.finish_native_request(theory, fields['request_id'], fields['outcome'])
            elif event == 'generation_provider_request_started':
                self.stage.begin_provider_request(theory, fields['request_id'])
            elif event == 'generation_provider_request_finished':
                self.stage.finish_provider_request(theory, fields['request_id'],
                    outcome=fields['outcome'], failure=fields.get('failure'))
            elif event == 'generation_provider_request_failed':
                self.stage.deactivate(theory)
                self.stage.ledger.append(event, theory=theory, evidence=dict(fields),
                    disposition='one_resend_then_credit_only_after_complete_response')
            elif event == 'provider_feedback_retry_succeeded':
                self.stage.credit_provider_request(fields['failed_request_id'],
                    fields['successful_request_id'])
                self.stage.ledger.append(event, theory=theory,
                    failed_request_id=fields['failed_request_id'],
                    successful_request_id=fields['successful_request_id'],
                    generation_id=fields.get('generation_id'))
            elif event == 'provider_transport_progress':
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
                               resume_authorization=None, extension_seconds=None, rows=None,
                               planning_budget=None, wall_seconds=None):
    """Bind the clock before launch; never give a legacy continuation zero cost."""
    from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY, CHECKPOINT_KIND
    root = Path(root)
    if any((root/name).exists() for name in ('native-watchdog-budget-transfer.json',
            'resource-reduction-budget-transfer.json', 'provider-feedback-transfer.json')):
        raise ValueError('source budget already claimed by a continuation; do not resume the old source')
    from regen.execution.checker_policy import validate_outcome_upgrade_readiness
    validate_outcome_upgrade_readiness(overview.run, root)
    ledger = Ledger(root/'effective-generation-budget', overview.run['run_id'])
    from regen.infrastructure.replanning_budget import validate_replanning_imports
    validate_replanning_imports(root, ledger=ledger)
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
    planning_started = [row for row in ledger.rows if row['kind'] == PLANNING_IMPORT_STARTED]
    planning_completed = [row for row in ledger.rows if row['kind'] == PLANNING_IMPORT_COMPLETED]
    if planning_started or planning_completed:
        if (len(planning_started) != 1 or len(planning_completed) != 1
                or planning_started[0]['sequence'] >= planning_completed[0]['sequence']
                or planning_completed[0]['data'] != {**planning_started[0]['data'], 'refund_seconds':0}):
            raise ValueError('incomplete or duplicate planning budget import; do not start or reset')
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
    source = source_identity = None
    if planning_budget is not None:
        source, source_identity = _planning_budget_source(
            planning_budget, overview, theories, binding, seconds)
        if planning_completed:
            prior_import = planning_completed[0]['data']
            if any(prior_import.get(key) != value for key, value in source_identity.items()):
                raise ValueError('planning budget source changed after its one-time import')
        elif ledger.rows or has_history:
            raise ValueError('planning budget may only be imported into a fresh proof run')
        elif extension_seconds is not None:
            raise ValueError('fresh planning handoff cannot extend the experiment allowance')
    elif planning_completed:
        _verify_planning_models(planning_completed[0]['data'], overview.run, binding)
    include_planning = bool(planning_completed or source_identity)
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
    if source is not None and not planning_completed:
        _import_planning_budget(ledger, source, source_identity, root, overview.run)
        # This is a continuous, already-authorized experiment transition, not an
        # operator resume after an interruption. Later resumes still need their
        # ordinary explicit authorization and never import these rows again.
        resume_authorization = resume_authorization or (
            'planning-handoff:'+source_identity['source_tail_event_id'])
    if has_proofs:
        for row in rows:
            if row.get('green_count', 0):
                _, state = overview._state(row['theory'])
                baseline.update(set(theories[row['theory']]) & set(state['proofs']))
    stage = EffectiveTimeBudget(ledger, seconds=seconds,
        resume_authorization=resume_authorization, extension_seconds=extension_seconds,
        include_planning=include_planning)
    stage.bind_wall_limit(wall_seconds)
    try:
        stage.register(theories, baseline_green=baseline)
    except BaseException:
        stage.close()
        raise
    return stage, ParallelGenerationBudgetBridge(stage)


def review_native_watchdog_interruption(source_dir, event):
    """Authenticate a local watchdog abort; retain historical clock uncertainty.

    A missing native response is not a mathematical verdict. This review only
    permits a stopped latest-workspace continuation to carry its existing costs.
    It does not complete the open model items, refund work or certify a deadline.
    """
    import json
    import re
    from regen.infrastructure.incremental_log import read_artifact

    root = Path(source_dir).resolve()
    evidence = event['data'].get('evidence', {})
    theory = event['data'].get('theory')
    owner = evidence.get('thread_id'), evidence.get('turn_id')
    item_ids = evidence.get('item_ids')
    if (evidence.get('reason') != 'session_stopped_with_open_model_item'
            or not isinstance(theory, str) or Path(theory).name != theory
            or not all(isinstance(v, str) and v for v in (*owner, evidence.get('generation_id')))
            or not isinstance(item_ids, list) or not item_ids
            or any(not isinstance(v, str) or not v for v in item_ids)
            or len(set(item_ids)) != len(item_ids)):
        raise ValueError('native watchdog review lacks exact clock/session identity')

    def local(path):
        path = Path(path)
        if not path.is_absolute():
            path = root/path
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError('native watchdog evidence escapes source or is missing')
        return path

    stop_path = local(Path('theories')/theory/'execution-stop.json')
    stop = read_artifact(stop_path)
    diagnostic = stop.get('reason', '')
    response = re.fullmatch(r'TIMEOUT\nincremental batch worker timeout waiting for ([1-9][0-9]*)\.response', diagnostic)
    count = stop.get('request_number')
    if (stop.get('status') != 'infrastructure_error'
            or stop.get('exception_type') != 'NativePreparationError'
            or stop.get('theory') != theory or type(count) is not int or count <= 0
            or response is None):
        raise ValueError('clock fault is not a native candidate response watchdog abort')
    check_path = local(Path('theories')/theory/'batch-requests'/f'{count:06d}'/'check.json')
    saved = read_artifact(check_path)
    check = saved.get('check', {})
    if (check.get('verdict') != 'infrastructure_error'
            or check.get('failure_scope') != 'native_preparation'
            or check.get('native_failure_stage') != 'candidate-response-watchdog'
            or check.get('timeout_scope') != 'candidate-watchdog'
            or check.get('timed_out') is not True or check.get('framework_blocked') is not True
            or check.get('diagnostic') != diagnostic
            or (check_path.parent/'feedback.json').exists()):
        raise ValueError('native watchdog check receipt is not the unreturned infrastructure failure')
    log_path = local(check['log'])
    stem = response.group(1)
    request_path = local(log_path.parent/(stem+'.request'))
    if (log_path.name != stem+'.log'
            or log_path.parent.name != check.get('worker')
            or (log_path.parent/(stem+'.response')).exists()
            or hashlib.sha256(request_path.read_bytes()).hexdigest() != check.get('delta_sha256')):
        raise ValueError('native watchdog request/response identity changed')

    matches = []
    for task_path in sorted((root/'check-tasks').glob('*/task.json')):
        task = read_artifact(task_path)
        meta = task.get('request', {}).get('metadata', {})
        if (task.get('theory') == theory
                and (meta.get('thread_id'), meta.get('turn_id')) == owner
                and task.get('error_type') == 'NativePreparationError'
                and task.get('diagnostic') == diagnostic):
            matches.append((local(task_path), task))
    if len(matches) != 1:
        raise ValueError('native watchdog requires exactly one bound failed check task')
    task_path, task = matches[0]
    meta = task['request']['metadata']
    raw_request = meta.get('raw_request', {})
    arguments = raw_request.get('params', {}).get('arguments', {})
    run = read_artifact(root/'run.json')
    if (task.get('status') != 'error' or task.get('kind') != 'proof_batch'
            or task.get('owner') != theory or task.get('binding', {}).get('run_id') != run['run_id']
            or raw_request.get('method') != 'item/tool/call'
            or raw_request.get('params', {}).get('tool') != 'proof_action'
            or arguments.get('theory') != theory
            or arguments.get('proposal') != task['request'].get('proposal')
            or arguments.get('proposal', {}).get('action') != 'check_theory'
            or read_artifact(check_path.parent/'model-proposal.json') != arguments.get('proposal')
            or not isinstance(saved.get('document_source'), str)
            or hashlib.sha256(saved['document_source'].encode()).hexdigest() != saved.get('document_source_sha256')
            or read_artifact(check_path.parent/'document-source.json').get('source') != saved['document_source']
            or not 0 <= event['time_unix']-task.get('finished_at', float('inf')) <= 10):
        raise ValueError('native watchdog task does not bind the saved document check')
    transcript = local(meta['transcript_path'])
    if transcript.parent.name != theory or transcript.name != 'session-transcript.jsonl':
        raise ValueError('native watchdog transcript owner differs')
    raw = transcript.read_bytes()
    opened, request_seen, response_seen, failed = set(), None, False, None
    for number, line in enumerate(raw.splitlines(), 1):
        row = json.loads(line)
        message = row['message']
        if 'parsed' in message:
            if json.loads(message['raw_json']) != message['parsed']:
                raise ValueError('native watchdog raw transcript differs from parsed message')
            message = message['parsed']
        params = message.get('params', {})
        if row['direction'] == 'in' and (params.get('threadId'), params.get('turnId')) == owner:
            method, item = message.get('method'), params.get('item', {})
            if method in ('item/started', 'item/completed') and item.get('type') in {'reasoning', 'agentMessage'}:
                if method == 'item/started': opened.add(item.get('id'))
                else: opened.discard(item.get('id'))
            elif method in ('item/agentMessage/delta', 'item/reasoning/textDelta', 'item/reasoning/summaryTextDelta'):
                opened.add(params.get('itemId'))
            if message == raw_request:
                if request_seen is not None:
                    raise ValueError('native watchdog tool request repeated')
                request_seen = number
        if (row['direction'] == 'in' and message.get('method') == 'turn/completed'
                and (params.get('threadId'), params.get('turn', {}).get('id')) == owner):
            raise ValueError('native watchdog session already has a terminal turn')
        if (row['direction'] == 'out' and message.get('id') == raw_request.get('id')
                and ('result' in message or 'error' in message)):
            response_seen = True
        if row['direction'] == 'local' and message.get('event') == 'session_failed':
            if (failed is not None or request_seen is None or response_seen
                    or message.get('exception_type') != 'NativePreparationError'
                    or message.get('message') != diagnostic or message.get('nonsealable') is not True
                    or opened != set(item_ids)
                    or not 0 <= row['time_unix']-event['time_unix'] <= 5):
                raise ValueError('native watchdog session abort does not match the clock fault')
            failed = number
    if failed is None or response_seen:
        raise ValueError('native watchdog lacks an unreturned tool and trusted session abort')
    return dict(policy='verified-native-watchdog-interruption-no-refund-v1',
        source_run=str(root), source_run_sha256=digest(run), event_id=event['event_id'],
        theory=theory, thread_id=owner[0], turn_id=owner[1], item_ids=sorted(item_ids),
        check_receipt=str(check_path.relative_to(root)), check_receipt_sha256=digest(saved),
        task=str(task_path.relative_to(root)), task_sha256=digest(task),
        execution_stop_sha256=digest(stop), transcript=str(transcript.relative_to(root)),
        transcript_sha256=hashlib.sha256(raw).hexdigest(), tool_request_line=request_seen,
        session_failed_line=failed, native_request=str(request_path.relative_to(root)),
        native_request_sha256=hashlib.sha256(request_path.read_bytes()).hexdigest(),
        native_log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(),
        historical_uncertainty_retained=True, mathematical_verdict_created=False,
        refund_seconds=0)


def _review_parallel_budget_clock(source_dir, ledger, ancestors=()):
    """Verify inherited shutdown evidence without importing or refunding it again."""
    from regen.infrastructure.incremental_log import read_artifact
    from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
    source_dir = Path(source_dir).resolve()
    from regen.infrastructure.replanning_budget import validate_replanning_imports
    validate_replanning_imports(source_dir, ledger=ledger)
    if source_dir in ancestors:
        raise ValueError('cyclic clock review continuation ancestry')
    uncertain = [row for row in ledger.rows if row['kind'] == 'generation_clock_untrusted']
    review = dict(source_tail_event_id=ledger.rows[-1]['event_id'],
        resolved_event_ids=[row['event_id'] for row in uncertain],
        disposition='retain_all_charged_time_no_step_refund')
    unsupported = [row for row in uncertain if row['data'].get('evidence', {}).get('reason') not in {
        'tool_callback_finished_with_open_model_item', 'tool_callback_started_with_open_model_item'}]
    if not unsupported:
        return dict(clock_review=review, inherited_reviews=[])
    starts = [row for row in ledger.rows if row['kind'] == 'parallel_budget_continuation_started']
    if not starts and all(row['data'].get('evidence', {}).get('reason') ==
                          'session_stopped_with_open_model_item' for row in unsupported):
        from regen.infrastructure.handler_stop_review import review_handler_stop
        witnesses = []
        for row in unsupported:
            try:
                witness = review_handler_stop(source_dir, row)
            except ValueError:
                try:
                    witness = review_native_watchdog_interruption(source_dir, row)
                except (ValueError, KeyError, OSError) as error:
                    raise ValueError('unsupported uncertainty: '+str(error)) from error
            witnesses.append(witness)
        return dict(clock_review=review, inherited_reviews=witnesses)
    completions = [row for row in ledger.rows if row['kind'] == 'parallel_budget_continuation_completed']
    if len(starts) != 1 or len(completions) != 1 or starts[0]['sequence'] >= completions[0]['sequence']:
        raise ValueError('unsupported uncertainty requires separate accounting review')
    start, complete = starts[0], completions[0]
    previous_dir = Path(complete['data']['source_run']).resolve()
    origin = read_artifact(source_dir/'workspace-continuation.json')
    if (Path(origin['source_run']).resolve() != previous_dir
            or Path(start['data']['source_run']).resolve() != previous_dir):
        raise ValueError('inherited clock review source identity changed')
    previous_run = read_artifact(previous_dir/'run.json')
    previous = Ledger(previous_dir/'effective-generation-budget', previous_run['run_id'])
    previous_uncertain = [row for row in previous.rows if row['kind'] == 'generation_clock_untrusted']
    previous_review = dict(source_tail_event_id=previous.rows[-1]['event_id'],
        resolved_event_ids=[row['event_id'] for row in previous_uncertain],
        disposition='retain_all_charged_time_no_step_refund')
    if (start['data'].get('source_run_sha256') != digest(previous_run)
            or start['data'].get('source_tail_event_id') != previous_review['source_tail_event_id']
            or complete['data'].get('source_tail_event_id') != previous_review['source_tail_event_id']
            or complete['data'].get('source_rows') != len(previous.rows)
            or start['data'].get('clock_review') != previous_review
            or complete['data'].get('clock_review') != previous_review
            or not isinstance(complete['data'].get('authorization'), str)
            or not complete['data']['authorization'].strip()
            or start['data'].get('authorization') != complete['data']['authorization']
            or complete['data'].get('historical_uncertainty_retained') is not True
            or complete['data'].get('refund_seconds') != 0):
        raise ValueError('inherited clock review no longer binds the exact source history')
    original = [row for row in previous.rows if row['kind'] != 'parallel_budget_binding'
                and not row['kind'].startswith('parallel_budget_continuation_')]
    region = ledger.rows[start['sequence']:complete['sequence']-1]
    copied = region[-len(original):] if original else []
    metadata = region[:-len(original)] if original else region
    allowed_metadata = {'parallel_budget_strategy_update', 'parallel_budget_feedback_retry_policy_update',
                        'parallel_budget_candidate_timeout_update', 'parallel_budget_clock_review_inherited',
                        'parallel_budget_resource_reduction_update'}
    logical = lambda row: {key:row[key] for key in ('kind', 'time_unix', 'data')}
    if (len(copied) != len(original) or any(row['kind'] not in allowed_metadata for row in metadata)
            or any(logical(a) != logical(b) for a, b in zip(copied, original))):
        raise ValueError('inherited clock review copied history changed')
    mapping = {new['event_id']:old for new, old in zip(copied, original)}
    if any(row['event_id'] not in mapping for row in unsupported):
        raise ValueError('new unsupported uncertainty requires separate accounting review')
    resource_updates = [row for row in metadata if row['kind'] == 'parallel_budget_resource_reduction_update']
    if resource_updates:
        old_policy = read_artifact(previous_dir/'parallel-workspace-policy.json')
        change = resource_updates[0]['data']
        reduced_policy, old_resources = _resource_reduction_policy(old_policy, change.get('target_resources'))
        old_state = next(row['data']['state'] for row in reversed(previous.rows) if row['kind'] == CHECKPOINT_KIND)
        expected_change = dict(policy='authorized-resource-reduction-v1',
            source_run=str(previous_dir), source_tail_event_id=previous.rows[-1]['event_id'],
            authorization=complete['data']['authorization'], previous_resources=old_resources,
            target_resources=change['target_resources'],
            previous_configuration_sha256=digest(old_policy),
            target_configuration_sha256=digest(reduced_policy),
            effective_seconds_unchanged=old_state['effective_seconds'],
            worker_seconds_unchanged=old_state['worker_seconds'],
            planning_seconds_unchanged=old_state.get('planning_seconds', 0.),
            seconds_unchanged=old_state['seconds'], refund_seconds=0)
        bindings = [row for row in ledger.rows if row['kind'] == 'parallel_budget_binding']
        if (len(resource_updates) != 1 or change != expected_change
                or len(bindings) != 1
                or bindings[0]['data']['configuration_sha256'] != digest(reduced_policy)
                or complete['data'].get('resource_reduction_update') != change):
            raise ValueError('inherited resource reduction policy or accounting witness changed')
        claim = read_artifact(previous_dir/'resource-reduction-budget-transfer.json')
        if (claim != complete['data'].get('resource_reduction_transfer')
                or claim.get('target_run') != str(source_dir)
                or claim.get('target_run_id') != ledger.run_id
                or claim.get('target_configuration_sha256') != digest(reduced_policy)
                or claim.get('refund_seconds') != 0):
            raise ValueError('inherited resource reduction transfer claim changed')
    feedback_updates = [row for row in metadata if row['kind'] == 'parallel_budget_feedback_retry_policy_update']
    inherited = []
    legacy_review_id = None
    if feedback_updates:
        from regen.infrastructure.provider_feedback_continuation import plan_legacy_feedback_import, IMPORT_BOUNDARY
        proposal = plan_legacy_feedback_import(previous_dir)
        checkpoint = next(row for row in reversed(copied) if row['kind'] == CHECKPOINT_KIND)
        expected = dict(**proposal, authorization=complete['data']['authorization'],
            target_run=str(source_dir), target_run_id=ledger.run_id,
            original_checkpoint_event_id=checkpoint['event_id'])
        imports = [row for row in ledger.rows if row['kind'] == CHECKPOINT_KIND
                   and row['data'].get('boundary') == IMPORT_BOUNDARY
                   and row['data'].get('review_id') == proposal['review_id']]
        if (len(feedback_updates) != 1 or feedback_updates[0]['data'].get('review_id') != proposal['review_id']
                or read_artifact(source_dir/'provider-feedback-continuation.json') != expected
                or len(imports) != 1 or imports[0]['sequence'] <= complete['sequence']
                or imports[0]['data'].get('authorization') != complete['data']['authorization']
                or imports[0]['data'].get('refund_seconds') != 0
                or digest(checkpoint['data']['state']) != proposal['source_state_sha256']):
            raise ValueError('inherited legacy feedback review receipt changed')
        legacy_review_id = proposal['review_id']
    else:
        inherited = _review_parallel_budget_clock(previous_dir, previous, (*ancestors, source_dir))['inherited_reviews']
        witnesses = [row['data'] for row in metadata if row['kind'] == 'parallel_budget_clock_review_inherited']
        if inherited and (len(witnesses) != 1
                or witnesses[0].get('clock_review') != previous_review
                or witnesses[0].get('reviews') != inherited
                or witnesses[0].get('refund_seconds') != 0
                or witnesses[0].get('authorization') != complete['data']['authorization']):
            raise ValueError('inherited clock transcript witness changed')
    receipt = dict(policy='inherited-verified-clock-review-v1', source_run=str(source_dir),
        source_tail_event_id=ledger.rows[-1]['event_id'],
        continuation_completed_event_id=complete['event_id'], reviewed_source_run=str(previous_dir),
        reviewed_source_tail_event_id=previous.rows[-1]['event_id'],
        reviewed_source_rows_sha256=digest(previous.rows), legacy_provider_feedback_review_id=legacy_review_id,
        event_mappings=[dict(event_id=row['event_id'], source_event_id=mapping[row['event_id']]['event_id'],
            logical_record_sha256=digest(logical(row))) for row in unsupported], refund_seconds=0)
    return dict(clock_review=review, inherited_reviews=[*inherited, receipt])


def review_parallel_budget_clock(source_dir):
    """Read-only review for a full continuation; caller establishes stopped liveness."""
    from regen.infrastructure.incremental_log import read_artifact
    source_dir = Path(source_dir).resolve()
    run = read_artifact(source_dir/'run.json')
    ledger = Ledger(source_dir/'effective-generation-budget', run['run_id'])
    if not ledger.rows:
        raise ValueError('source lacks effective-time history')
    return _review_parallel_budget_clock(source_dir, ledger)


def _resource_reduction_policy(source_policy, target_resources):
    """A typed resource-only migration cannot increase capacity or change policy."""
    fields = {'max_parallel_theories', 'resident_limit', 'native_slots'}
    if not isinstance(target_resources, dict) or set(target_resources) != fields:
        raise ValueError('resource reduction requires exactly worker, resident and native limits')
    previous = {name:source_policy.get(name) for name in fields}
    if any(type(previous[name]) is not int or type(target_resources[name]) is not int
           or not 0 < target_resources[name] <= previous[name] for name in fields):
        raise ValueError('resource reduction requires positive integer nonincreasing limits')
    if target_resources == previous:
        raise ValueError('resource reduction requires at least one lower limit')
    if target_resources['native_slots'] > min(target_resources['max_parallel_theories'],
                                              target_resources['resident_limit']):
        raise ValueError('resource reduction native slots must fit worker and resident limits')
    return {**source_policy, **target_resources}, previous


def carry_parallel_budget(source_dir, destination, *, authorization, clock_review,
                          target_strategy=None, provider_feedback_review=None,
                          target_execution_policy=None, target_candidate_timeout=None,
                          target_native_slots=None, target_resources=None,
                          native_runtime_review=None):
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
    if target_resources is not None and any(value is not None for value in (
            target_strategy, provider_feedback_review, target_execution_policy,
            target_candidate_timeout, target_native_slots)):
        raise ValueError('resource-only reduction cannot combine other policy migrations')
    if provider_feedback_review is not None:
        from regen.infrastructure.provider_feedback_continuation import plan_legacy_feedback_import
        if provider_feedback_review != plan_legacy_feedback_import(source_dir):
            raise ValueError('legacy provider feedback review or source evidence changed')
        expected_policy = {**source_policy['execution_policy'], 'provider_feedback_retries':1}
        if target_execution_policy != expected_policy:
            raise ValueError('legacy feedback migration permits only the explicit one-resend policy change')
        target_policy['execution_policy'] = dict(target_execution_policy)
    elif target_execution_policy is not None:
        raise ValueError('execution policy change requires an audited feedback continuation')
    if target_strategy is not None:
        from regen.planning.parallel_file_planner import generation_strategy
        if (not isinstance(target_strategy, dict)
                or target_strategy.get('name') != source_policy.get('generation_strategy', {}).get('name')
                or target_strategy != generation_strategy(target_strategy['name'])):
            raise ValueError('strategy update must match the new frozen implementation of the same strategy')
        target_policy['generation_strategy'] = target_strategy
    resource_reduction_change = None
    if target_resources is not None:
        if any(value is not None for value in (target_strategy, provider_feedback_review,
                target_execution_policy, target_candidate_timeout, target_native_slots)):
            raise ValueError('resource-only reduction cannot combine other policy migrations')
        if state['effective_seconds'] >= state['seconds']:
            raise ValueError('resource reduction cannot extend an exhausted experiment budget')
        target_policy, previous_resources = _resource_reduction_policy(source_policy, target_resources)
        resource_reduction_change = dict(policy='authorized-resource-reduction-v1',
            source_run=str(source_dir), source_tail_event_id=ledger.rows[-1]['event_id'],
            authorization=authorization, previous_resources=previous_resources,
            target_resources=dict(target_resources),
            previous_configuration_sha256=digest(source_policy),
            target_configuration_sha256=digest(target_policy),
            effective_seconds_unchanged=state['effective_seconds'],
            worker_seconds_unchanged=state['worker_seconds'],
            planning_seconds_unchanged=state.get('planning_seconds', 0.),
            seconds_unchanged=state['seconds'], refund_seconds=0)
    native_slots_change = None
    if target_native_slots is not None:
        previous = source_policy.get('native_slots')
        if (type(target_native_slots) is not int or target_native_slots <= 0
                or type(previous) is not int or target_native_slots <= previous
                or target_native_slots > source_policy.get('max_parallel_theories', 0)
                or target_native_slots > source_policy.get('resident_limit', 0)):
            raise ValueError('native slots increase must fit frozen worker and resident limits')
        if state['effective_seconds'] >= state['seconds']:
            raise ValueError('native slots migration cannot extend an exhausted experiment budget')
        target_policy['native_slots'] = target_native_slots
        native_slots_change = dict(previous_native_slots=previous,
            target_native_slots=target_native_slots, authorization=authorization,
            effective_seconds_unchanged=state['effective_seconds'],
            worker_seconds_unchanged=state['worker_seconds'],
            seconds_unchanged=state['seconds'], refund_seconds=0)
    candidate_timeout_change = None
    if target_candidate_timeout is not None:
        previous = source_policy.get('candidate_timeout')
        if (type(previous) is not int or previous <= 0
                or type(target_candidate_timeout) is not int
                or target_candidate_timeout <= 0):
            raise ValueError('candidate timeout migration requires positive integer seconds')
        if target_candidate_timeout <= previous:
            raise ValueError('candidate timeout migration must increase the frozen candidate timeout')
        if state['effective_seconds'] >= state['seconds']:
            raise ValueError('candidate timeout migration cannot extend an exhausted experiment budget')
        # The typed option changes only this native candidate limit. The full
        # resulting policy hash remains the launch-time authority, so another
        # model, resource setting or total budget cannot ride on this migration.
        target_policy['candidate_timeout'] = target_candidate_timeout
        candidate_timeout_change = dict(
            policy='authorized-candidate-timeout-increase-v1',
            source_run=str(source_dir), source_tail_event_id=ledger.rows[-1]['event_id'],
            authorization=authorization,
            previous_candidate_timeout=previous,
            target_candidate_timeout=target_candidate_timeout,
            previous_configuration_sha256=digest(source_policy),
            target_configuration_sha256=digest(target_policy),
            effective_seconds_unchanged=state['effective_seconds'],
            worker_seconds_unchanged=state['worker_seconds'],
            planning_seconds_unchanged=state.get('planning_seconds', 0.),
            seconds_unchanged=state['seconds'],
            remaining_effective_seconds=state['seconds']-state['effective_seconds'],
            provider_request_accounting_sha256=digest(state.get('provider_request_accounting')),
            refund_seconds=0)
    uncertain = [r for r in ledger.rows if r['kind'] == 'generation_clock_untrusted']
    if (clock_review.get('source_tail_event_id') != ledger.rows[-1]['event_id']
            or clock_review.get('resolved_event_ids') != [r['event_id'] for r in uncertain]
            or clock_review.get('disposition') != 'retain_all_charged_time_no_step_refund'):
        raise ValueError('clock review does not bind exact source history')
    inherited_clock_reviews = []
    if provider_feedback_review is None:
        inherited_clock_reviews = _review_parallel_budget_clock(source_dir, ledger)['inherited_reviews']
    target = Ledger(destination/'effective-generation-budget', target_run['run_id'])
    if target.rows:
        raise ValueError('target budget already exists; never overwrite or replay import')
    # A prior claim is authoritative even when a later caller asks for a plain
    # continuation rather than repeating the resource/watchdog migration mode.
    if any((source_dir/name).exists() for name in (
            'native-watchdog-budget-transfer.json', 'resource-reduction-budget-transfer.json',
            'provider-feedback-transfer.json')):
        raise ValueError('source budget already claimed by a continuation')
    resource_reduction_transfer = None
    if resource_reduction_change is not None:
        import fcntl
        from regen.execution.proof_commits import durable
        with (source_dir/'resource-reduction-budget-transfer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            claim = source_dir/'resource-reduction-budget-transfer.json'
            if claim.exists():
                raise ValueError('resource reduction budget already claimed by a continuation')
            resource_reduction_transfer = dict(source_run=str(source_dir),
                source_tail_event_id=ledger.rows[-1]['event_id'],
                clock_review_sha256=digest(clock_review),
                reviewed_witnesses_sha256=digest(inherited_clock_reviews),
                target_run=str(destination), target_run_id=target_run['run_id'],
                target_configuration_sha256=digest(target_policy),
                authorization=authorization, refund_seconds=0)
            durable(claim, resource_reduction_transfer, immutable=True)
    native_watchdog_transfer = None
    if native_runtime_review is not None:
        if (native_runtime_review.get('policy') != 'native-stage-runtime-repair-v1'
                or native_runtime_review.get('authorization') != authorization
                or native_runtime_review.get('source_run_sha256') != digest(source_run)
                or native_runtime_review.get('target_implementation_sha256') != digest(target_run['implementation'])
                or any(v is not None for v in (target_strategy, provider_feedback_review,
                    target_execution_policy, target_candidate_timeout, target_native_slots, target_resources))):
            raise ValueError('native runtime review does not bind unchanged limits and exact implementations')
    if resource_reduction_change is None and (native_runtime_review is not None or any(
           row.get('policy') == 'verified-native-watchdog-interruption-no-refund-v1'
           for row in inherited_clock_reviews)):
        # One stopped allowance must not fund two descendants. A partial failed
        # import remains claimed and inspectable; retry never grants fresh time.
        import fcntl
        from regen.execution.proof_commits import durable
        with (source_dir/'native-watchdog-budget-transfer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            claim = source_dir/'native-watchdog-budget-transfer.json'
            if claim.exists():
                raise ValueError('native watchdog budget already claimed by a continuation')
            native_watchdog_transfer = dict(source_run=str(source_dir),
                source_tail_event_id=ledger.rows[-1]['event_id'],
                reviewed_witnesses_sha256=digest(inherited_clock_reviews),
                target_run=str(destination), target_run_id=target_run['run_id'],
                authorization=authorization, refund_seconds=0)
            durable(claim, native_watchdog_transfer, immutable=True)
    if provider_feedback_review is not None:
        # Claim once before copying. A failed partial import remains inspectable
        # and cannot create multiple descendants spending the same pending tail.
        import fcntl
        from regen.execution.proof_commits import durable
        with (source_dir/'provider-feedback-transfer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            claim = source_dir/'provider-feedback-transfer.json'
            if claim.exists():
                raise ValueError('legacy provider feedback budget already claimed by a continuation')
            durable(claim, dict(source_tail_event_id=ledger.rows[-1]['event_id'],
                review_id=provider_feedback_review['review_id'], target_run=str(destination),
                target_run_id=target_run['run_id'], authorization=authorization), immutable=True)
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
    if provider_feedback_review is not None:
        target.append('parallel_budget_feedback_retry_policy_update', authorization=authorization,
            review_id=provider_feedback_review['review_id'],
            previous_execution_policy=source_policy['execution_policy'],
            target_execution_policy=target_execution_policy,
            previous_configuration_sha256=digest(source_policy),
            target_configuration_sha256=digest(target_policy),
            effective_seconds_unchanged=state['effective_seconds'], seconds_unchanged=state['seconds'])
    if resource_reduction_change is not None:
        target.append('parallel_budget_resource_reduction_update', **resource_reduction_change)
    if native_slots_change is not None:
        native_slots_change.update(previous_configuration_sha256=digest(source_policy),
                                   target_configuration_sha256=digest(target_policy))
        target.append('parallel_budget_native_slots_update', **native_slots_change)
    if candidate_timeout_change is not None:
        target.append('parallel_budget_candidate_timeout_update', **candidate_timeout_change)
    if native_runtime_review is not None:
        target.append('parallel_budget_native_runtime_update',
            review_sha256=digest(native_runtime_review), authorization=authorization,
            native_timing_policy='native-stages-and-timeout-exclusion-v1',
            historical_costs_unchanged=True, refund_seconds=0,
            effective_seconds_unchanged=state['effective_seconds'], seconds_unchanged=state['seconds'])
    if inherited_clock_reviews:
        target.append('parallel_budget_clock_review_inherited', authorization=authorization,
            clock_review=clock_review, reviews=inherited_clock_reviews, refund_seconds=0)
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
    if resource_reduction_change is not None:
        receipt['resource_reduction_update'] = resource_reduction_change
        receipt['resource_reduction_transfer'] = resource_reduction_transfer
    if native_watchdog_transfer is not None:
        receipt['native_watchdog_transfer'] = native_watchdog_transfer
    if native_slots_change is not None:
        receipt['native_slots_update'] = native_slots_change
    if candidate_timeout_change is not None:
        receipt['candidate_timeout_update'] = candidate_timeout_change
    if inherited_clock_reviews:
        receipt['inherited_clock_reviews'] = inherited_clock_reviews
    target.append('parallel_budget_continuation_completed', **receipt)
    if provider_feedback_review is not None:
        from regen.infrastructure.provider_feedback_continuation import append_legacy_feedback_import
        imported = append_legacy_feedback_import(source_dir, destination,
            authorization=authorization, proposal=provider_feedback_review)
        receipt.update(provider_feedback_review_id=imported['review_id'],
            pending_failed_request_ids=[r['request_id'] for r in imported['requests']],
            pending_retry_count=len(imported['requests']))
    return receipt
