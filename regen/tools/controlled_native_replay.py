"""Real-size, zero-model candidate replay through ordinary acceptance journals.

Historical generated candidates are diagnostic inputs only, never online hints.
Independent arms use the same validated fixture and AB/BA ordering. Recovery is
reported separately from candidate execution, and never claimed as model speed.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import signal
from pathlib import Path
import subprocess
import sys
import time

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.proof_batches import _binding
from regen.execution.proof_commits import durable, digest
from regen.execution.repository_execution import journal, package_for, setup
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.progress_events import event_scope
from regen.planning.bounded_prefix import run_prefix
from regen.planning.theory_document import DocumentStore, candidate, check_document, validate_document_commit
from regen.tools.harness_acceptance_baseline import protected_snapshot
from regen.tools.prefix_batch_optimization_validation import load_fixture, SOURCE, THEORY


def verify_journal(root):
    run = read_artifact(root/'run.json')
    package = package_for(run, THEORY+'.thy')
    state = journal(root, run, package).replay(repair=False)
    return {'state_sha256': digest(state), 'accepted_order': state['accepted_order'],
            'batch_requests': state.get('batch_requests')}


@contextmanager
def hard_deadline(seconds):
    """Bound work even inside setup; signal handlers still run normal cleanup."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('positive finite wall limit required')
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise RuntimeError('cannot replace an existing process deadline')
    def expired(signum, frame):
        raise TimeoutError('native replay hard wall limit reached')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run(fixture, manifest_sha256, output, *, pairs=2, wall_limit=7200):
    with hard_deadline(wall_limit):
        return _run(fixture, manifest_sha256, output, pairs=pairs, wall_limit=wall_limit)


def _run(fixture, manifest_sha256, output, *, pairs=2, wall_limit=7200):
    from regen.tools.prepared_checkpoint import derive
    if pairs not in (1, 2) or not math.isfinite(wall_limit) or wall_limit <= 0:
        raise ValueError('bounded replay requires one or two pairs and a positive wall limit')
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(SOURCE.resolve()):
        raise ValueError('new isolated output required')
    old, package, candidates, imports, origin = load_fixture()
    output.mkdir(parents=True, exist_ok=False)
    before = protected_snapshot(SOURCE)
    started = time.monotonic()
    report = {'schema': 'controlled-native-replay-v1', 'pass': False,
        'model_calls': 0, 'production_acceptance_writes': 0,
        'target_contracts_proved': 0, 'fixture_manifest_sha256': manifest_sha256,
        'source': origin, 'initial_prefix_facts': len(old['proofs']), 'arms': [],
        'scope': 'fixed historical generated candidates, not online proof discovery',
        'order': ['A', 'B'] if pairs == 1 else ['A', 'B', 'B', 'A'],
        'wall_limit_seconds': wall_limit, 'recovery_injected_once': True}
    durable(output/'intent.json', report, immutable=True)
    resources = NativeResources(1, 4, 1, cpus=4, memory_mb=16384)
    token = CURRENT_RESOURCES.set(resources)

    def budget():
        if time.monotonic()-started >= wall_limit:
            raise TimeoutError('diagnostic wall limit reached; no automatic retry')

    try:
        for index, mode in enumerate(report['order']):
            budget()
            arm = output/'arms'/f'{index+1:02d}-{mode}'
            t = time.monotonic()
            derive(Path(fixture), arm, expected_manifest_sha256=manifest_sha256)
            row = {'arm': mode, 'run_dir': str(arm), 'derive_seconds': time.monotonic()-t,
                   'requests': [], 'recovery_seconds': 0., 'interruption_exercised': False}
            row['interrupted_checks'] = []
            report['arms'].append(row)
            checker = None
            try:
                with event_scope(arm, run_id='controlled-native-replay', theory=THEORY):
                    t = time.monotonic()
                    run_config, actual_package, directory, commits, checker, _ = setup(arm, THEORY, 900)
                    checker.batch_candidate_timeout = 120
                    row['setup_seconds'] = time.monotonic()-t
                    state = commits.replay()
                    if _binding(state) != _binding(old) or checker.imports() != imports:
                        raise ValueError('native arm differs from frozen candidate context')
                    row['initial_binding'] = _binding(state)
                    original_costs = {k: deepcopy(state.get(k)) for k in ('batch_requests', 'rounds', 'revisions')}
                    document = DocumentStore(directory/'document', actual_package, state, imports)
                    interrupt = mode == 'B' and not any(a['interruption_exercised'] for a in report['arms'])
                    for item in candidates:
                        budget()
                        number, source = item['request'], item['source']
                        evidence_path = SOURCE/'theories'/THEORY/'batch-requests'/f'{number:06d}'/'check.json'
                        historic = read_artifact(evidence_path)
                        provenance = {k: historic[k] for k in ('sealable_generation', 'transcript_prefix')}
                        provenance['diagnostic_replay'] = {'original_request': number,
                            'candidate_sha256': item['source_sha256'], 'new_model_calls': 0,
                            'not_an_online_generated_result': True}
                        document.edit({'action': 'edit_theory',
                            'base_sha256': document.read()['source_sha256'], 'source': source},
                            {'diagnostic_historical_candidate': number})
                        t = time.monotonic()
                        if mode == 'A':
                            proposed = candidate(actual_package, state, source, imports)
                            evidence = check_document(checker, state, proposed, source)
                            evidence.update(provenance)
                            durable(arm/f'replay-{number}/check.json', evidence, immutable=True)
                            if evidence['check']['verdict'] == 'green':
                                validate_document_commit(actual_package, state, proposed, evidence)
                                state = commits.append('document', proposed, evidence)
                            else:
                                try:
                                    validate_document_commit(actual_package, state, proposed, evidence)
                                except ValueError:
                                    pass
                                else:
                                    raise AssertionError('RED candidate accepted')
                            result = {'status': evidence['check']['verdict'], 'checks': 1}
                        else:
                            def publish(new, evidence):
                                nonlocal state, interrupt, commits
                                budget()
                                state = commits.append('document', new, evidence)
                                if interrupt:
                                    interrupt = False
                                    row['interruption_exercised'] = True
                                    segment = evidence['bounded_prefix']
                                    row['interrupted_checks'].append({
                                        'request': number, 'entry_id': segment['entry_id'],
                                        'verdict': evidence['check']['verdict'],
                                        'check_wall_seconds': segment['check_wall_seconds'],
                                        'evidence_path': str(arm/f'replay-{number}'/
                                            f"{segment['segment']:06d}"/'check.json'),
                                        'accepted_before_interruption': True})
                                    raise InterruptedError('controlled interruption after durable GREEN')
                                return state

                            try:
                                result = run_prefix(actual_package, state, source, checker,
                                    arm/f'replay-{number}', commit=publish, evidence_origin=provenance)
                            except InterruptedError:
                                recovery_start = time.monotonic()
                                checker.close_prefix()
                                # A fresh process verifies the journal without shared in-memory caches.
                                completed = subprocess.run([sys.executable, '-m', 'regen.tools.controlled_native_replay',
                                    'verify-journal', '--run-dir', str(arm)], capture_output=True,
                                    text=True, cwd=Path(__file__).resolve().parents[2],
                                    timeout=min(1800, max(1, wall_limit-(recovery_start-started))))
                                if completed.returncode:
                                    raise RuntimeError('cold journal recovery failed: '+completed.stderr[-2000:])
                                restored = json.loads(completed.stdout)
                                if restored['state_sha256'] != digest(state):
                                    raise AssertionError('cold process recovered a different proof state')
                                if document.read()['source'] != source:
                                    raise AssertionError('interruption lost the complete candidate draft')
                                row['recovery_seconds'] += time.monotonic()-recovery_start
                                row['cold_recovery_verified'] = True
                                row['native_worker_closed_before_resume'] = True
                                prior_ids = set(state['proofs'])
                                result = run_prefix(actual_package, state, source, checker,
                                    arm/f'replay-{number}-resumed', commit=publish, evidence_origin=provenance)
                                if any(c['entry_id'] in prior_ids for c in result['bounded_prefix']['checks']):
                                    raise AssertionError('resumption checked an already committed segment')
                            if result is None:
                                raise AssertionError('bounded strategy silently fell back')
                        bounded = result.get('bounded_prefix', {})
                        check_rows = bounded.get('checks', [])
                        if mode == 'A':
                            check_rows = [{'verdict': result['status'],
                                'evidence_path': str(arm/f'replay-{number}/check.json')}]
                        # Detailed evidence already lives in immutable check.json;
                        # projections must not repeat native logs or proof bodies.
                        row['requests'].append({'number': number, 'wall_seconds': time.monotonic()-t,
                            'status': result['status'], 'new_green': result.get('new_green', []),
                            'stop_reason': bounded.get('stop_reason'), 'checks': check_rows,
                            'draft_preserved': document.read()['source'] == source})
                        durable(output/'progress.json', report)
                    final = commits.replay()
                    if original_costs != {k: final.get(k) for k in original_costs}:
                        raise AssertionError('historical costs changed in offline replay')
                    row.update(new_ids=sorted(set(final['proofs'])-set(old['proofs'])),
                        original_costs_preserved=True, journal_replay_verified=True,
                        final_state_sha256=digest(final))
                    if any(k.startswith('contract:') for k in row['new_ids']):
                        raise AssertionError('fixture unexpectedly produced a target contract')
                    if mode == 'B':
                        from regen.execution.batch_checker import body
                        from regen.execution.proof_batches import _render
                        missing = [k for k in candidates[-1]['state']['accepted_order']
                                   if k not in final['proofs']]
                        if not missing:
                            raise AssertionError('fixture lacks the intended rejected helper')
                        names = {n['id']: n['name'] for n in candidates[-1]['state']['graph']['nodes']}
                        label = 'Batch_Check_'+digest(imports)[:16]
                        accepted_source = _render(final['graph'], final['proofs'], imports, label)
                        worker = checker._batch_worker
                        with resources.lease(checker):
                            worker.align(body(accepted_source))
                            probe = accepted_source.rsplit('\nend\n', 1)[0]+(
                                '\nlemma controlled_isolation_probe: "True"\nusing '+
                                names[missing[0]]+' by simp\nend\n')
                            isolation = worker.check_batch(probe, ['controlled_isolation_probe'])
                        diagnostic = isolation.get('log_content', '')+'\n'+isolation.get('diagnostic', '')
                        row['rejected_fact_isolated'] = (isolation['verdict'] == 'red'
                            and 'Undefined fact' in diagnostic and worker.pending is None)
                        durable(arm/'rejected-fact-isolation.json', isolation, immutable=True)
                        if not row['rejected_fact_isolated']:
                            raise AssertionError('rejected fact leaked into the accepted context')
                    row['pass'] = (not row['new_ids'] if mode == 'A' else len(row['new_ids']) >= 5)
                    row['pass'] &= all(r['draft_preserved'] for r in row['requests'])
                    receipts = [*row['interrupted_checks'],
                        *(c for r in row['requests'] for c in r['checks'])]
                    row['checker_timings'] = []
                    for receipt in receipts:
                        checked = read_artifact(receipt['evidence_path'])['check']
                        row['checker_timings'].append({
                            'evidence_path': receipt['evidence_path'],
                            'prefix_prepare_ms': checked.get('prefix_prepare_ms'),
                            'candidate_execution_and_audit_ms': checked.get('wall_ms'),
                            'worker': checked.get('worker'), 'verdict': checked['verdict']})
                    row['timing_scope'] = ('request wall includes publication and injected recovery; '
                        'journal recovery, initial setup and checker prefix/candidate timings are separate; '
                        'do not sum overlapping phases')
            finally:
                if checker is not None:
                    checker.close_prefix()
            durable(output/'progress.json', report)
        report['pass'] = all(r['pass'] for r in report['arms']) and any(
            r.get('cold_recovery_verified') for r in report['arms'])
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        try:
            resources.close()
            report['native_cleanup_verified'] = not resources.residents
        finally:
            CURRENT_RESOURCES.reset(token)
            report['source_unchanged'] = before == protected_snapshot(SOURCE)
            report['pass'] &= report['source_unchanged'] and report.get('native_cleanup_verified', False)
            report['wall_seconds'] = time.monotonic()-started
            report['native_metrics'] = resources.metrics
            durable(output/'report.json', report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    verify = sub.add_parser('verify-journal')
    verify.add_argument('--run-dir', type=Path, required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--fixture', type=Path, required=True)
    execute.add_argument('--manifest-sha256', required=True)
    execute.add_argument('--output', type=Path, required=True)
    execute.add_argument('--pairs', type=int, choices=(1, 2), default=2)
    execute.add_argument('--wall-limit', type=float, default=7200)
    args = vars(parser.parse_args())
    if args.pop('operation') == 'verify-journal':
        print(json.dumps(verify_journal(args['run_dir'])))
    else:
        from regen.tools.green_contract_ab import termination_signals
        with termination_signals():
            result = run(**args)
        print(json.dumps({'pass': result['pass'], 'wall_seconds': result['wall_seconds']}))
        raise SystemExit(0 if result['pass'] else 1)


if __name__ == '__main__':
    main()
