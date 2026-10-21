"""Read-only historical replay plus isolated regressions; no model/native calls.

Writes a new report outside the protected run. Character reductions are response
projections, not measured token/latency/GREEN improvements of a live model.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest

from regen.execution.proof_commits import digest
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.infrastructure.information_progress import information_payload
from regen.infrastructure.workspace_transcript import approved_registry
from regen.planning.attempt_views import attempt_view
from regen.planning.batch_file_planner import compact_feedback, planner_input
from regen.planning.document_interface import public_view
from regen.planning.document_recall import document_observations
from regen.planning.planner_workspace import read_observation
from regen.tools.harness_acceptance_baseline import protected_snapshot, RecordedResult


MODULES = (
    'test_history_recall_repair', 'test_shared_proof_workspace', 'test_planner_workspace',
    'test_attempt_memory', 'test_batch_query_memory', 'test_codex_workspace_session',
    'test_document_concurrency', 'test_document_acceptance_validation',
    'test_parallel_provider_cooldown', 'test_generation_strategy', 'test_parallel_file_planner',
    'test_planner_context', 'test_batch_malformed_memory', 'test_document_interface',
    'test_workspace_transcript', 'test_batch_memory_recovery', 'test_batch_recovery_memory',
    'test_async_observation_memory', 'test_observation_context', 'test_theory_document',
    'test_proof_workspace_agent', 'test_workspace_argument_recovery_audit',
    'test_workspace_sleep_policy_audit', 'test_workspace_protocol_cancellation',
    'test_effective_time_budget', 'test_parallel_effective_budget',
    'test_generic_registry', 'test_generic_tool_contract',
)
CHANGED = (
    'planning/attempt_views.py', 'planning/document_recall.py',
    'infrastructure/information_progress.py', 'execution/shared_proof_workspace.py',
    'planning/proof_workspace_agent.py', 'planning/planner_workspace.py',
    'planning/batch_file_planner.py', 'planning/parallel_file_planner.py',
    'planning/document_interface.py', 'infrastructure/codex_workspace_session.py',
    'infrastructure/workspace_transcript.py', 'tests/current/test_history_recall_repair.py',
    'tools/validate_history_recall.py', 'docs/implementation/HARNESS-APPLICATION-FRAMEWORK.md',
)


def size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def code_hashes():
    repo = Path(__file__).resolve().parents[2]
    return {'regen/'+name: hashlib.sha256((repo/'regen'/name).read_bytes()).hexdigest()
            for name in CHANGED}


def calls(path):
    requests, replies, registries = {}, {}, []
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            message = event.get('message', {})
            message = message.get('parsed', message)
            if message.get('method') == 'thread/start':
                registries.append(message['params']['dynamicTools'])
            if message.get('method') == 'item/tool/call':
                requests[message['id']] = message['params']
            if event.get('direction') == 'out' and 'result' in message:
                result = message['result']
                if isinstance(result, dict) and 'success' in result:
                    text = ''.join(v.get('text', '') for v in result.get('contentItems', []))
                    try:
                        value = json.loads(text)
                    except ValueError:
                        continue
                    replies[message['id']] = (result['success'], value)
    assert registries and all(approved_registry(r) for r in registries), 'frozen registry compatibility'
    return requests, replies


def pair(requests, replies, first, second):
    a, b = requests[first], requests[second]
    va, vb = replies[first][1], replies[second][1]
    old_a, old_b = [a['tool'], a['arguments'], va], [b['tool'], b['arguments'], vb]
    new_a = information_payload(a['tool'], a['arguments'], va)
    new_b = information_payload(b['tool'], b['arguments'], vb)
    assert old_a != old_b and new_a == new_b, 'metadata-only progress regression'
    return {'rpc_ids': [first, second], 'old_equal': False, 'new_equal': True}


def replay(root):
    workspace = SharedProofWorkspace(root)
    theory = 'KernelStatePrimitives'
    start = time.monotonic()
    page = workspace.list_attempts(theory, limit=2)
    cold = time.monotonic() - start
    snapshot = workspace._snapshot(theory)
    state, directory = snapshot['state'], Path(snapshot['directory'])
    package = workspace._package(theory)
    sessions = sorted(root.glob('parallel-sessions/*/KernelStatePrimitives/session-transcript.jsonl'))
    assert len(sessions) == 1, 'expected one initial KSP session'
    transcripts = [sessions[0], sessions[0].parent/'retry-001/session-transcript.jsonl']
    result = {'cold_directory_seconds': cold, 'failed_directory_total': page['total'],
              'source_commit_id': snapshot['commit_id'], 'sessions': []}
    cache = {}
    def bound_full(number):
        if number not in cache:
            full = read_observation(directory, state, {
                'action': 'read_attempts', 'request_numbers': [number], 'view': 'full'})['attempts'][0]
            assert digest(full['candidate']) == full['bindings']['proposal_sha256']
            assert digest(full['feedback']) == full['bindings']['feedback_sha256']
            assert public_view(compact_feedback(full)) == full
            cache[number] = full
        return cache[number]

    def summary_of(number):
        full = bound_full(number)
        return attempt_view(number, full['candidate'], full['feedback'], **{
            k: v for k, v in full['bindings'].items() if k != 'request_number'})

    for index, path in enumerate(transcripts):
        requests, replies = calls(path)
        old_chars = new_chars = count = 0
        old_selected_chars = new_selected_chars = document_reads = 0
        for rpc, request in requests.items():
            if rpc not in replies or not replies[rpc][0]:
                continue
            if request['tool'] == 'read_theory':
                selected = replies[rpc][1].get('observations', {}).get('requested_attempts', [])
                projected = [public_view(summary_of(item['request_number'])) for item in selected]
                old_selected_chars += size(selected)
                new_selected_chars += size(projected)
                document_reads += 1
            if request['tool'] != 'read_attempt':
                continue
            number = request['arguments']['request_number']
            projected = summary_of(number)
            # Reproduce the shared API envelope, retaining its audit receipt.
            old = replies[rpc][1]
            projected.update(status='candidate_reference', theory=theory,
                source_commit_id=old.get('source_commit_id'), read_tool={
                    'tool': 'read_attempt', 'arguments': {'theory': theory,
                        'request_number': number, 'view': 'full'}})
            old_chars += size(old)
            new_chars += size(public_view(projected))
            count += 1
        result['sessions'].append({'path': str(path.relative_to(root)),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'successful_read_attempt_calls': count, 'old_response_chars': old_chars,
            'summary_response_chars': new_chars,
            'response_reduction_fraction': 1-new_chars/old_chars if old_chars else None,
            'read_theory_selected_history_only': {'calls': document_reads,
                'old_chars': old_selected_chars, 'new_chars': new_selected_chars,
                'scope': 'Selected-history component only, not the complete document/navigation response.'},
            'metadata_only_pair': pair(requests, replies, *( (34,144) if index == 0 else (26,28) )),
            'frozen_registry_approved': True})
    result['bound_full_artifact_pairs_verified'] = len(cache)
    # Exercise actual public single/plural routes and exact full round-trips.
    result['public_route_checks'] = []
    for number in (94, 105, 108):
        start = time.monotonic()
        summary = workspace.read_attempt(theory, number)
        full = workspace.read_attempt(theory, number, view='full')
        plural = read_observation(directory, state, {
            'action': 'read_attempts', 'request_numbers': [number], 'view': 'full'})['attempts'][0]
        assert all(full[k] == v for k, v in plural.items())
        assert summary['content_id'] == full['content_id']
        assert public_view(compact_feedback(full)) == full
        result['public_route_checks'].append({'request_number': number,
            'summary_chars': size(summary), 'full_chars': size(full),
            'combined_seconds': time.monotonic()-start, 'exact_full_roundtrip': True})
    context = planner_input(package, state)
    before = digest(state)
    # Check the baseline against the run's actual frozen controller, not a
    # recalled list of fields or today's evolving implementation.
    baseline = root.parent/'application/regen/planning/parallel_file_planner.py'
    baseline_source = baseline.read_text()
    old_fields = ('query_index', 'query_memory', 'requested_attempts',
                  'next_proof_observation', 'requested_proof_state', 'resolved_symbols')
    tuples = [tuple(item.value for item in node.elts) for node in ast.walk(ast.parse(baseline_source))
              if isinstance(node, ast.Tuple) and all(isinstance(item, ast.Constant) for item in node.elts)]
    assert old_fields in tuples, 'frozen observation schema differs from the comparison baseline'
    old_observations = {k: compact_feedback(context[k]) for k in old_fields if k in context}
    observations = document_observations(context, state)
    assert digest(state) == before
    assert observations == document_observations(planner_input(package, state), state)
    assert all('candidate' not in a for a in observations['requested_attempts'])
    result['checkpoint_observations'] = {'old_chars': size(public_view(old_observations)),
        'new_chars': size(public_view(observations)), 'new_fields': sorted(observations),
        'baseline_fields': old_fields, 'baseline_file': str(baseline),
        'baseline_file_sha256': hashlib.sha256(baseline_source.encode()).hexdigest(),
        'new_field_chars': {k: size(public_view(v)) for k, v in observations.items()},
        'state_unchanged': True, 'repeat_read_equal': True}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.run.resolve(), args.output.resolve()
    if output.is_relative_to(root) or output.exists():
        parser.error('output must be a new file outside the protected run')
    before = protected_snapshot(root)
    report = {'version': 'history-recall-repair-v1', 'time_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Offline real-journal replay and isolated regressions; no provider/native request.',
        'limitations': ['Character counts are not tokens or live model effectiveness.',
            'No claim that GREEN yield or model choice of reads is improved.',
            'Read-only exploratory new facts still count as information; unchanged thresholds.'],
        'protected_before': before, 'candidate_file_sha256': code_hashes()}
    try:
        report['replay'] = replay(root)
        suite = unittest.defaultTestLoader.loadTestsFromNames(
            ['regen.tests.current.'+name for name in MODULES])
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            result = unittest.TextTestRunner(stream=captured, verbosity=0,
                resultclass=RecordedResult).run(suite)
        report['regressions'] = {'run': result.testsRun, 'failures': len(result.failures),
            'errors': len(result.errors), 'skipped': len(result.skipped), 'rows': result.rows,
            'success': result.wasSuccessful()}
        if not result.wasSuccessful():
            report['regressions']['diagnostics'] = captured.getvalue()
    except Exception as error:
        import traceback
        report['error'] = {'type': type(error).__name__, 'message': str(error),
                           'traceback': traceback.format_exc()}
    report['protected_after'] = protected_snapshot(root)
    report['protected_unchanged'] = report['protected_after'] == before
    report['candidate_unchanged'] = report['candidate_file_sha256'] == code_hashes()
    report['success'] = ('error' not in report and report.get('regressions', {}).get('success', False)
                         and report['protected_unchanged'] and report['candidate_unchanged'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'output': str(output), 'success': report['success'],
        'error': report.get('error'), 'protected_unchanged': report['protected_unchanged']}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
