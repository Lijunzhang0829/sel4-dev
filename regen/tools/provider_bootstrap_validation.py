"""Isolated real provider bootstrap and unchanged failed document replay.

Only the outer repository setup and DocumentStore are fixtures. Real
TheoryController._document/_runtime, resource leasing, Docker, Isabelle build
and cold source checking are exercised. No model or acceptance journal writes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
from unittest.mock import patch

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.proof_commits import durable
from regen.execution.repository_execution import checker_for
from regen.execution.isabelle_node_checker import native_run as real_native_run
from regen.infrastructure.incremental_log import read_artifact
from regen.infrastructure.progress_events import event_scope
from regen.infrastructure.resource_budget import clone_heaps
from regen.planning.parallel_file_planner import TheoryController


STEPS = {'Untyped_Object_Lifecycle': '000041', 'Scheduler_Dispatch': '000047'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def journal_fingerprints(source):
    return {str(p.relative_to(source)): sha(p)
            for p in sorted(Path(source).glob('theories/*/acceptance/**/*.json'))}


def proof_failure_category(diagnostic):
    for category in ('Failed to finish proof', 'Failed to apply proof method',
                     'Undefined fact', 'Undefined fact name'):
        if category in diagnostic:
            return category
    return None


def candidate_header(text, theory):
    if not re.match(r'\s*theory\s+'+re.escape(theory)+r'\s+imports\b', text):
        raise ValueError('candidate theory header differs from replay label')


def goal_signature(diagnostic):
    text = diagnostic.replace('*** ', '')
    match = re.search(r'goal \(.*?subgoals?\):\s*(.*)', text, re.S)
    return re.sub(r'\s+', '', match[1])[:160] if match else None


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source in output.parents:
        raise ValueError('validation output must be outside original run')
    output.mkdir(parents=True, exist_ok=False)
    run = json.loads((source/'run.json').read_text())
    manifest = {'source_run': str(source), 'source_run_sha256': sha(source/'run.json'),
                'model_calls': 0, 'acceptance_writes': 0, 'theories': {}}
    for theory, step in STEPS.items():
        origin = source/'theories'/theory
        destination = output/theory
        destination.mkdir()
        heaps = destination/'heaps'
        # Shared run heaps contain frozen upstream sessions only. Refuse an
        # accidentally warm provider cache rather than deleting source files.
        if any(p.name.startswith(('Generated_Refine', 'Sealed_Refine'))
               for p in (source/'heaps').rglob('*')):
            raise ValueError('source base heaps unexpectedly include generated session')
        copy = clone_heaps(source/'heaps', heaps)
        generated = destination/'generated'
        generated.mkdir()
        sources = {}
        for path in sorted((origin/'generated').glob('*.thy')):
            target = generated/path.name
            shutil.copy2(path, target)
            sources[str(path)] = sha(path)
        ancestors = json.loads((origin/'imports.json').read_text())
        if not ancestors or not sources:
            raise ValueError('missing frozen provider inputs')
        request = origin/'batch-requests'/step
        prior = read_artifact(request/'check.json')
        if prior['check']['verdict'] != 'red':
            raise ValueError('selected step is not a failed candidate')
        text = read_artifact(request/'document-source.json')['source']
        candidate_header(text, theory)
        candidate = destination/(theory+'.thy')
        candidate.write_text(text)
        manifest['theories'][theory] = {
            'step': step, 'imports': ancestors, 'provider_sources': sources,
            'source_request': str(request), 'candidate_sha256': sha(candidate),
            'heap_copy': copy, 'prior_check_verdict': prior['check']['verdict'],
            'prior_diagnostic': prior['check'].get('diagnostic', ''),
        }
    durable(output/'inputs.json', manifest, immutable=True)
    return run, manifest


def bridge(theory, directory, resources):
    """Use actual production methods without constructing a writable run."""
    controller = object.__new__(TheoryController)
    controller.root, controller.theory = directory, theory
    controller.options = {'check_timeout': 900}
    controller.resources, controller.document_mode = resources, True
    controller._runtime_lock = threading.RLock()
    controller.runtime_cache = {}
    controller._provider_preparation_error = None
    controller._state = lambda _: ({}, {})
    return controller


def conditions(report):
    rows = report['theories'].values()
    return (report['peak_native_active'] == 1
            and all(r['resource_context_present'] and r['build']['verdict'] == 'green'
                    and r['replay']['verdict'] == 'red'
                    and not r['replay'].get('timed_out', False)
                    and r['replay'].get('returncode') not in (124, 137)
                    and r.get('same_proof_failure', False)
                    and r['inputs_unchanged'] for r in rows)
            and len(report['theories']) == 2)


def run(source, output, *, prepared=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if prepared:
        manifest = json.loads((output/'inputs.json').read_text())
        if manifest['source_run'] != str(source) or manifest['source_run_sha256'] != sha(source/'run.json'):
            raise ValueError('prepared run identity changed')
        run_config = json.loads((source/'run.json').read_text())
    else:
        run_config, manifest = prepare(source, output)
    resources = NativeResources(1, 4, 2, cpus=4, memory_mb=16384,
                                provider_build_timeout=1800)
    report = {'pass': False, 'model_calls': 0, 'acceptance_writes': 0,
              'fixture_scope': 'outer setup/DocumentStore mocked; production runtime and native execution real',
              'replay_scope': 'unchanged failed source, cold complete check; not original incremental protocol',
              'policy': resources.policy, 'theories': {}, 'started_unix': time.time()}
    report['journal_before'] = journal_fingerprints(source)
    implementation = Path(__file__).resolve().parents[2]
    report['implementation_sha256'] = {str(p.relative_to(implementation)): sha(p)
        for p in sorted((implementation/'regen').rglob('*.py')) if '__pycache__' not in p.parts}
    checkers = {}
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    native_commands = []

    def recorded_native_run(command, **kwargs):
        row = {'command': command, 'timeout_seconds': kwargs.get('timeout'),
               'started_unix': time.time()}
        with lock:
            index = len(native_commands)
            native_commands.append(row)
        durable(output/('native-command-%02d.json' % index), row, immutable=True)
        try:
            return real_native_run(command, **kwargs)
        finally:
            row['finished_unix'] = time.time()

    def setup_fixture(directory, theory, timeout):
        info = manifest['theories'][theory]
        checker = checker_for(directory, run_config, directory/'isabelle', timeout,
                              info['imports'], heaps_dir=directory/'heaps')
        checkers[theory] = checker
        present = CURRENT_RESOURCES.get() is resources
        started = time.time()
        build = checker.build_generated_session(info['imports'])
        with lock:
            report['theories'][theory] = {'resource_context_present': present,
                'build_started_unix': started, 'build_finished_unix': time.time(),
                'build': build}
        durable(directory/'build.json', build, immutable=True)
        if build['verdict'] != 'green':
            raise RuntimeError('provider build failed for '+theory)
        return run_config, {}, directory, None, checker, info['imports']

    def execute(theory):
        directory = output/theory
        with event_scope(directory, run_id='provider-bootstrap-validation', theory=theory):
            controller = bridge(theory, directory, resources)
            barrier.wait(timeout=60)
            controller._document()
            # The synchronous runtime scope must have restored the context.
            if CURRENT_RESOURCES.get() is not None:
                raise RuntimeError('resource context leaked from runtime')
            marker = CURRENT_RESOURCES.set(resources)
            try:
                text = (directory/(theory+'.thy')).read_text()
                candidate_header(text, theory)
                replay = checkers[theory]._check(text, theory, timeout=900)
            finally:
                CURRENT_RESOURCES.reset(marker)
            durable(directory/'replay.json', replay, immutable=True)
            info = manifest['theories'][theory]
            unchanged = (sha(directory/(theory+'.thy')) == info['candidate_sha256']
                         and all(sha(path) == value and sha(directory/'generated'/Path(path).name) == value
                                 for path, value in info['provider_sources'].items()))
            original = read_artifact(Path(info['source_request'])/'check.json')['check']['diagnostic']
            old_category = proof_failure_category(original)
            new_category = proof_failure_category(replay['diagnostic'])
            old_goal, new_goal = goal_signature(original), goal_signature(replay['diagnostic'])
            same_failure = (old_category is not None and old_category == new_category
                            and old_goal is not None and old_goal == new_goal)
            with lock:
                report['theories'][theory].update(replay=replay, inputs_unchanged=unchanged,
                    same_proof_failure=same_failure, prior_failure_category=old_category,
                    replay_failure_category=new_category, prior_goal_signature=old_goal,
                    replay_goal_signature=new_goal)

    try:
        with patch('regen.execution.repository_execution.setup', setup_fixture), \
             patch('regen.planning.theory_document.DocumentStore', lambda *a: None), \
             patch('regen.execution.isabelle_node_checker.native_run', recorded_native_run), \
             ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(execute, theory) for theory in STEPS]
            errors = []
            for future in futures:
                try:
                    future.result()
                except Exception as error:
                    errors.append(type(error).__name__+': '+str(error))
            report['errors'] = errors
        report.update(peak_native_active=resources.peak_active,
                      native_operations=resources.metrics, native_commands=native_commands)
        builds = [r for r in native_commands if 'build' in r['command']]
        report['build_commands_match_policy'] = len(builds) == 2 and all(
            r['timeout_seconds'] == 1800 and 'threads=4' in r['command']
            and r['command'][r['command'].index('--memory')+1] == '16384m'
            and r['command'][r['command'].index('--cpus')+1] == '4'
            for r in builds)
        report['pass'] = not errors and conditions(report) and report['build_commands_match_policy']
    finally:
        resources.close()
        report['journal_after'] = journal_fingerprints(source)
        report['original_acceptance_unchanged'] = report['journal_before'] == report['journal_after']
        report['pass'] &= report['original_acceptance_unchanged']
        report['finished_unix'] = time.time()
        durable(output/'report.json', report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--prepared', action='store_true')
    args = parser.parse_args()
    if args.prepare_only:
        prepare(args.source_run, args.output)
        return 0
    result = run(args.source_run, args.output, prepared=args.prepared)
    print(json.dumps({'pass': result['pass'], 'errors': result.get('errors', [])}))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
