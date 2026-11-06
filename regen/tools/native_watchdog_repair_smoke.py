"""Model-free native watchdog recovery through the production document tools.

The only fault injection is a two-second delay in a disposable synthetic
worker, with a shorter host response deadline. The candidate text and every
acceptance check remain unchanged. No benchmark proof is supplied or credited.
"""
from contextlib import ExitStack
from copy import deepcopy
import argparse
import json
from pathlib import Path
import time
from unittest.mock import patch

from regen.execution.batch_checker import BatchWorker
from regen.execution.check_executor import CheckExecutor, NativeResources
from regen.execution.proof_commits import durable
from regen.execution.repository_execution import journal
from regen.planning.parallel_file_planner import TheoryController
from regen.preparation.checker_context import load_context
from regen.tools.dynamic_imports_native_smoke import (
    CONSUMER, MODEL, ScriptedProvenance, create_fixture, require,
)


def run(dataset, output):
    dataset, root = Path(dataset).resolve(), Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'pass': False, 'model_calls': 0, 'benchmark_contracts_proved': 0,
        'scope': 'synthetic native worker watchdog, retained prefix, rebuild and seal',
        'fault_injection': {'worker_delay_seconds': 2, 'host_response_timeout_seconds': .25},
        'substitutions': ['synthetic HOL contracts', 'synthetic model-origin eligibility',
                          'temporary delay before native candidate execution'], 'cases': []}
    resources = executor = controller = None

    def passed(name, **values):
        report['cases'].append({'name': name, 'pass': True, **values})
        durable(root/'report.json', report)
        print(json.dumps({'case': name, 'pass': True}), flush=True)

    try:
        context = load_context(dataset)
        run_config, packages = create_fixture(root, dataset, context)
        resources = NativeResources(1, 2, 1, cpus=4, memory_mb=8192,
            pids_limit=1024, provider_build_timeout=600, build_ml_heap_mb=2048)
        executor = CheckExecutor(root/'check-tasks', workers=1)
        provenance = ScriptedProvenance(root)
        with ExitStack() as stack:
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',
                                      side_effect=provenance.record))
            stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact',
                                      side_effect=provenance.validate))
            stack.enter_context(patch('regen.infrastructure.workspace_transcript.audit_workspace_session',
                return_value={'synthetic': True, 'real_model': False, 'model_calls': 0}))
            stack.enter_context(patch('regen.planning.batch_file_planner.call_llm',
                                      side_effect=AssertionError('model invocation forbidden')))
            controller = TheoryController(root, CONSUMER, resources=resources,
                executor=executor, document_mode=True, model=MODEL,
                check_timeout=180, candidate_timeout=60)
            serial = 0

            def action(proposal):
                nonlocal serial
                serial += 1
                meta = {'thread_id': 'synthetic', 'turn_id': 'synthetic',
                    'call_id': str(serial), 'synthetic': True,
                    'transcript_path': str(root/'synthetic-transcript.json'), 'transcript_offset': 1}
                durable(root/'tool-calls'/f'{serial:03d}-request.json', proposal, immutable=True)
                value = controller.handle('proof_action',
                    {'theory': CONSUMER, 'proposal': proposal}, meta)
                durable(root/'tool-calls'/f'{serial:03d}-result.json', value, immutable=True)
                return value

            def read():
                return controller.handle('read_theory', {'theory': CONSUMER}, {})['document']

            def state():
                return journal(root, run_config, packages[CONSUMER]).replay(repair=False)

            def prove(entry):
                draft = read()
                return action({'action': 'edit_proof', 'entry_id': entry,
                    'expected_hash': draft['source_sha256'], 'proof_body': 'by simp'})

            prove('stable_truth')
            action({'action': 'check_theory', 'source_sha256': read()['source_sha256']})
            before = deepcopy(state())
            require(set(before['proofs']) == {'stable_truth'}, 'synthetic prefix not certified')
            passed('real_native_prefix_accepted')
            prove('consumer_truth')
            draft = read()
            checker = controller._runtime()[4]
            old_worker = getattr(checker, '_batch_worker', None)
            if old_worker is not None:
                old_worker.close()
                checker._batch_worker = None
            from regen.execution.native_runtime import worker_source
            original_wait = BatchWorker._wait
            observed_workers = []

            def delayed_source(checker, source):
                text = worker_source(checker, source)
                anchor = 'val delta = read (stem ^ ".request");'
                require(text.count(anchor) == 1, 'native fault-injection anchor changed')
                return text.replace(anchor, anchor +
                    '\n          val _ = OS.Process.sleep (Time.fromSeconds 2);', 1)

            def bounded_wait(worker, path, *, timeout=None):
                if path.name.endswith('.response') and (worker.root/(path.stem+'.action')).read_text() == 'check':
                    observed_workers.append(worker)
                    return original_wait(worker, path, timeout=.25)
                return original_wait(worker, path, timeout=timeout)

            with patch('regen.execution.native_runtime.worker_source', side_effect=delayed_source), \
                    patch.object(BatchWorker, '_wait', bounded_wait):
                result = action({'action': 'check_theory', 'source_sha256': draft['source_sha256']})
            feedback = result['feedback']
            check = feedback['check']
            require(check['verdict'] == 'infrastructure_error', 'watchdog became a mathematical verdict')
            require(feedback['retryable'] and not feedback['framework_blocked'], 'failure not returned as recoverable feedback')
            require(feedback['new_green'] == [], 'failed worker published a proof')
            require(state()['proofs'] == before['proofs'], 'watchdog changed accepted prefix')
            require(read()['source_sha256'] == draft['source_sha256'], 'watchdog lost saved draft')
            require(checker._batch_worker is None, 'failed worker retained for reuse')
            require(len(observed_workers) == 1 and observed_workers[0].closed,
                    'owned native worker was not discarded')
            require(observed_workers[0].process.poll() is not None, 'native worker still running')
            passed('watchdog_returns_feedback_preserving_prefix_and_draft',
                   worker=observed_workers[0].name, native_recovery=check['native_recovery'])

            action({'action': 'check_theory', 'source_sha256': draft['source_sha256']})
            require(set(state()['proofs']) == {'stable_truth', 'consumer_truth'},
                    'rebuild did not certify pending candidate')
            replacement = checker._batch_worker
            require(replacement is not None and replacement.name != observed_workers[0].name,
                    'retry did not use a fresh worker')
            passed('fresh_native_worker_rechecks_exact_saved_draft', worker=replacement.name)
            action({'action': 'finish', 'source_sha256': read()['source_sha256']})
            require(state()['status'] == 'sealed', 'recovered synthetic theory did not seal')
            passed('recovered_synthetic_theory_seals')
            report['pass'] = True
    finally:
        if controller is not None:
            controller.close()
        if executor is not None:
            executor.close()
        if resources is not None:
            resources.close()
        report['wall_seconds'] = time.monotonic()-started
        durable(root/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.dataset, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
