#!/usr/bin/env python3
"""Disclosed counterexample replay through controller, executor, and native checker.

The proof fixture comes from the archived model counterexample, never from this
validation's fresh generation. No model is called. A second Python process checks
that the persisted stop is recoverable without another native check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def read(path):
    return json.loads(Path(path).read_text())


def task_hashes(proof):
    return {str(path.relative_to(proof)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((proof / 'check-tasks').glob('*/*.json'))}


def controller_for(proof):
    from regen.execution.check_executor import CheckExecutor, NativeResources
    from regen.planning.parallel_file_planner import TheoryController
    resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
        pids_limit=1024, provider_build_timeout=900, build_ml_heap_mb=2048)
    executor = CheckExecutor(proof / 'check-tasks', workers=1)
    controller = TheoryController(proof, 'Tree_Execution', resources=resources,
        executor=executor, document_mode=True, generation_strategy_name='sketch-reviewed-v1',
        model='gpt-6-astra', call_timeout=900, check_timeout=900, candidate_timeout=300)
    return controller, executor, resources


def close_all(controller, executor, resources):
    try:
        controller.close()
    finally:
        try:
            executor.close()
        finally:
            resources.close()


def recover(root):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.planning.theory_outcomes import current_outcome
    proof = root / 'proof-refutation-e2e'
    out = root / 'native-refutation-e2e'
    before = task_hashes(proof)
    lock = acquire_run_mutation_lock(proof, operation='stability-negative-recovery')
    controller, executor, resources = controller_for(proof)
    try:
        outcome = current_outcome(controller)
        _, state = controller._state(controller.theory)
        if not outcome or outcome['status'] != 'refuted' or state['proofs'] or state['status'] == 'sealed':
            raise ValueError('fresh process did not recover a negative-only outcome')
        tasks = [executor.get(key) for key in executor.records]
        if len(tasks) != 1 or tasks[0]['status'] != 'completed' or tasks[0]['kind'] != 'refutation':
            raise ValueError('unexpected recovered executor tasks')
    finally:
        try:
            close_all(controller, executor, resources)
        finally:
            release_run_mutation_lock(lock)
    if task_hashes(proof) != before:
        raise ValueError('recovery rewrote completed task evidence')
    result = {'status': 'pass', 'pid': os.getpid(), 'outcome': outcome['status'],
        'certificate_id': outcome['details']['certificate']['certificate_id'],
        'receipt': outcome['receipt'], 'completed_tasks': len(tasks),
        'native_checks_added': 0, 'positive_proofs': 0, 'theory_sealed': False,
        'task_evidence_unchanged': True}
    save(out / 'recovery.json', result)
    return result


def replay(root, fixture):
    from regen.execution.proof_session_runner import init_run, acquire_run_mutation_lock, release_run_mutation_lock
    from regen.planning.theory_outcomes import current_outcome
    from regen.tools.application_release import verify
    verify(root / 'application', root / 'application-manifest.json')
    started = time.time()
    proof = root / 'proof-refutation-e2e'
    out = root / 'native-refutation-e2e'
    out.mkdir(exist_ok=False)
    body = fixture.read_text()
    provenance = read(fixture.parent / 'provenance.json')
    expected_hash = provenance['replayed_proof_sha256']
    if hashlib.sha256(body.encode()).hexdigest() != expected_hash:
        raise ValueError('replay fixture differs from disclosed original extraction')
    save(out / 'fixture-provenance.json', {'fixture': str(fixture),
        'fixture_sha256': expected_hash, 'original_extraction': provenance,
        'fresh_model_generation': False, 'model_calls': 0})
    init_run(proof, root / 'dataset', run_id=root.name + '-refutation-e2e',
        schedule_path=root / 'planning-reused/file-generation-list.json',
        log_storage='regen-merkle-json-v1')
    lock = acquire_run_mutation_lock(proof, operation='stability-negative-replay')
    controller, executor, resources = controller_for(proof)
    try:
        package, before = controller._state(controller.theory)
        contract, = [item for item in package['contracts'] if item['name'] == 'tree_remove_preserves_wf']
        proposal = {'action': 'check_refutation', 'entry_id': contract['contract_id'], 'proof_body': body}
        metadata = {'thread_id': 'validation-replayed-counterexample', 'turn_id': 'replay-1',
            'call_id': 'check-refutation-1', 'validation_fixture': True, 'model_generated_this_run': False}
        result = controller.handle('proof_action', {'theory': controller.theory, 'proposal': proposal}, metadata)
        save(out / 'tool-result.json', result)
        outcome = current_outcome(controller)
        _, after = controller._state(controller.theory)
        if not outcome or outcome['status'] != 'refuted' or not result.get('theory_stop'):
            raise ValueError('negative tool did not publish a durable stop')
        if after['proofs'] != before['proofs'] or after['proofs'] or after['status'] == 'sealed':
            raise ValueError('refutation changed positive proof or seal state')
        row = executor.get(result['task_id'])
        if row['status'] != 'completed' or row['kind'] != 'refutation':
            raise ValueError('negative check did not complete in CheckExecutor')
        checks_before = len(executor.records)
        blocked = controller.handle('proof_action', {'theory': controller.theory, 'proposal': proposal},
                                    {**metadata, 'call_id': 'must-not-recheck'})
        if not blocked.get('theory_stop') or len(executor.records) != checks_before:
            raise ValueError('stopped controller dispatched another mathematical check')
        certificate = outcome['details']['certificate']
        save(out / 'certificate.json', certificate)
        save(out / 'outcome.json', outcome)
        save(out / 'task-summary.json', {key: row.get(key) for key in
             ['task_id', 'kind', 'theory', 'status', 'binding', 'identity_hash', 'result_sha256']})
    finally:
        try:
            close_all(controller, executor, resources)
        finally:
            release_run_mutation_lock(lock)
    command = [sys.executable, str(Path(__file__).resolve()), '--root', str(root), '--phase', 'recover']
    recovered = subprocess.run(command, text=True, capture_output=True, timeout=60)
    (out / 'recovery-process.log').write_text(recovered.stdout + recovered.stderr)
    if recovered.returncode:
        raise RuntimeError('fresh-process recovery failed; inspect recovery-process.log')
    recovery = read(out / 'recovery.json')
    if recovery['pid'] == os.getpid() or recovery['certificate_id'] != certificate['certificate_id']:
        raise ValueError('recovery identity mismatch')
    result = {'status': 'pass', 'started_unix': started, 'finished_unix': time.time(),
        'pid': os.getpid(), 'recovery_pid': recovery['pid'], 'contract_id': contract['contract_id'],
        'outcome': 'refuted', 'certificate_id': certificate['certificate_id'],
        'native_receipt': certificate['native_receipt'],
        'transport': 'TheoryController.handle -> CheckExecutor -> production IsabelleChecker Docker -> outcome journal',
        'restart_recovery': 'new Python process, same frozen run, no extra native checks',
        'subsequent_mathematical_action_blocked': True, 'model_calls': 0,
        'fresh_generation_credit': 0, 'positive_proofs_added': 0, 'theory_sealed': False,
        'scope': 'isolated validation fixture; not published to fresh generation stages'}
    save(out / 'validation.json', result)
    return result


def continuation(root):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.execution.repository_execution import fork_workspace_continuation
    from regen.infrastructure.incremental_log import read_artifact, logged_copy_batch
    from regen.infrastructure.progress_events import observe_events
    from regen.planning.theory_outcomes import current_outcome
    source = root / 'proof-refutation-e2e'
    destination = root / 'proof-refutation-e2e-continuation'
    out = root / 'native-refutation-e2e-continuation'
    out.mkdir(exist_ok=False)
    expected = read(root / 'native-refutation-e2e/outcome.json')
    before = task_hashes(source)
    native_events = []
    def observe(name, fields):
        if name == 'isabelle_started':
            native_events.append(dict(fields))
    lock = acquire_run_mutation_lock(source, operation='stability-negative-continuation')
    try:
        with observe_events(observe), logged_copy_batch():
            run = fork_workspace_continuation(source, destination)
    finally:
        release_run_mutation_lock(lock)
    controller, executor, resources = controller_for(destination)
    try:
        outcome = current_outcome(controller)
        _, state = controller._state(controller.theory)
        if not outcome or outcome['status'] != 'refuted' or state['proofs'] or state['status'] == 'sealed':
            raise ValueError('continued outcome did not preserve a negative-only stop')
        if outcome['details']['certificate'] != expected['details']['certificate']:
            raise ValueError('continued negative certificate changed')
        if outcome['binding']['run_id'] != run['run_id'] or run['run_id'] == expected['binding']['run_id']:
            raise ValueError('continued outcome has not been rebound to a new execution identity')
        if executor.records or native_events or before != task_hashes(source):
            raise ValueError('continuation executed native work or changed source task evidence')
        save(out / 'outcome.json', outcome)
        migration = read_artifact(destination / 'theory-outcomes-continuation.json')
        save(out / 'migration.json', migration)
    finally:
        close_all(controller, executor, resources)
    result = {'status': 'pass', 'source': str(source), 'destination': str(destination),
        'source_run_id': expected['binding']['run_id'], 'new_run_id': run['run_id'],
        'outcome': 'refuted', 'certificate_id': outcome['details']['certificate']['certificate_id'],
        'certificate_unchanged': True, 'native_checks_added': len(native_events),
        'positive_proofs_added': 0, 'theory_sealed': False, 'source_task_evidence_unchanged': True,
        'source_had_positive_commits': False, 'generation_budget_present': False,
        'scope': 'outcome identity migration only; budget continuation tested separately'}
    save(out / 'validation.json', result)
    return result


def cancel_native(root, fixture):
    from regen.execution.proof_session_runner import init_run, acquire_run_mutation_lock, release_run_mutation_lock
    from regen.infrastructure.progress_events import observe_events
    from regen.planning.theory_outcomes import current_outcome
    proof = root / 'proof-refutation-cancel-e2e'
    out = root / 'native-refutation-cancel-e2e'
    out.mkdir(exist_ok=False)
    body = fixture.read_text()
    provenance = read(fixture.parent / 'provenance.json')
    if hashlib.sha256(body.encode()).hexdigest() != provenance['replayed_proof_sha256']:
        raise ValueError('cancel fixture differs from disclosed original extraction')
    save(out / 'fixture-provenance.json', provenance)
    init_run(proof, root / 'dataset', run_id=root.name + '-refutation-cancel-e2e',
        schedule_path=root / 'planning-reused/file-generation-list.json',
        log_storage='regen-merkle-json-v1')
    lock = acquire_run_mutation_lock(proof, operation='stability-negative-native-cancel')
    controller, executor, resources = controller_for(proof)
    started = threading.Event()
    observed = {}
    response = {}
    def observe(name, fields):
        if name == 'isabelle_started':
            observed.update(fields, observed_unix=time.time())
            started.set()
    package, before = controller._state(controller.theory)
    contract, = [item for item in package['contracts'] if item['name'] == 'tree_remove_preserves_wf']
    def execute():
        try:
            with observe_events(observe):
                response['result'] = controller.handle('proof_action', {
                    'theory': controller.theory, 'proposal': {'action': 'check_refutation',
                    'entry_id': contract['contract_id'], 'proof_body': body}},
                    {'thread_id': 'validation-native-cancel', 'turn_id': 'replay-1',
                     'call_id': 'cancel-during-native', 'validation_fixture': True})
        except BaseException as error:
            response.update(error_type=type(error).__name__, diagnostic=str(error))
    thread = threading.Thread(target=execute, name='negative-cancel-validation')
    thread.start()
    try:
        if not started.wait(30):
            raise RuntimeError('negative checker never emitted native start')
        name = observed['container']
        deadline = time.monotonic() + 15
        while True:
            inspected = subprocess.run(['docker', 'inspect', '--format', '{{json .State}}', name],
                text=True, capture_output=True, timeout=5)
            state = json.loads(inspected.stdout) if inspected.returncode == 0 else None
            if state and state.get('Running'):
                break
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('could not observe an actually running native container')
            time.sleep(.05)
        save(out / 'native-running.json', {'event': observed, 'container_state': state,
                                          'observed_unix': time.time()})
        cancel_unix = time.time()
        controller.abort_pending()
        thread.join(45)
        if thread.is_alive():
            raise RuntimeError('negative task did not finish after cancellation')
        finished_unix = time.time()
        tasks = [executor.get(key) for key in executor.records]
        if len(tasks) != 1 or tasks[0]['status'] != 'cancelled' or response.get('error_type') != 'CheckCancelled':
            raise ValueError('native cancellation did not settle as cancelled: ' + repr(response))
        outcome = current_outcome(controller)
        _, after = controller._state(controller.theory)
        if outcome is not None or after['proofs'] != before['proofs'] or after['status'] == 'sealed':
            raise ValueError('cancelled native task published an outcome or positive proof')
        inspected = subprocess.run(['docker', 'inspect', '--format', '{{.State.Status}}', name],
            text=True, capture_output=True, timeout=5)
        missing = inspected.returncode != 0 and ('No such object:' in inspected.stderr or 'No such container:' in inspected.stderr)
        if not missing:
            raise ValueError('cancelled checker container still exists or inspect is inconclusive')
        save(out / 'task.json', tasks[0])
        save(out / 'tool-response.json', response)
        result = {'status': 'pass', 'contract_id': contract['contract_id'],
            'container': name, 'native_running_observed': True, 'native_event': observed,
            'cancel_unix': cancel_unix, 'finished_unix': finished_unix,
            'cancel_latency_seconds': finished_unix - cancel_unix,
            'task_status': tasks[0]['status'], 'tool_error': response['error_type'],
            'outcome': None, 'positive_proofs_added': 0, 'theory_sealed': False,
            'container_removed': True, 'model_calls': 0, 'fresh_generation_credit': 0}
        save(out / 'validation.json', result)
        return result
    finally:
        try:
            if thread.is_alive():
                controller.abort_pending()
                thread.join(45)
            close_all(controller, executor, resources)
        finally:
            release_run_mutation_lock(lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--phase', choices=['replay', 'recover', 'continuation', 'cancel'], default='replay')
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root / 'application'))
    os.chdir(root / 'application')
    os.environ['REGEN_MODEL_CONFIG'] = str(root / 'config/models.json')
    if args.phase in {'replay', 'cancel'} and args.fixture is None:
        parser.error('--fixture required for replay')
    if args.phase == 'recover':
        result = recover(root)
    elif args.phase == 'continuation':
        result = continuation(root)
    elif args.phase == 'cancel':
        result = cancel_native(root, args.fixture.resolve())
    else:
        result = replay(root, args.fixture.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
