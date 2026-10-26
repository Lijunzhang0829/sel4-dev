"""Offline Codex incident boundaries; never launch a provider or native worker.

Extracted steps retain real public checker/clock evidence. Explicitly synthetic
probes exercise small production storage/clock paths, not proof acceptance.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET


def _parameters(case):
    return case['_replay']['parameters']


def _load(root, name):
    return json.loads((root / name).read_text())


def _reject(call, error=ValueError):
    try:
        call()
    except error:
        return
    raise AssertionError('invalid input was accepted')


def locale_audit(case, fixture_root: Path, scratch: Path):
    from regen.execution.batch_audit import validate_native_audit
    from regen.execution.batch_checker import body
    p = _parameters(case)
    actual = _load(fixture_root, p['failure_evidence'])['data']
    assert actual['diagnostic'] == 'RED\nUndefined fact: "get_tsType_ccorres"'
    assert actual['returncode'] == 1 and not actual['timed_out']
    value = _load(fixture_root, p['evidence'])
    xml = ET.fromstring(value['audit_content'])
    prefix, delta = xml.findtext('prefix'), xml.findtext('delta')
    assert body(value['source_content']) == prefix + delta
    for field, text in [('prefix', prefix), ('delta', delta), ('source', value['source_content'])]:
        assert hashlib.sha256(text.encode()).hexdigest() == value[field + '_sha256']
    args = dict(prefix=prefix, delta=delta, names=value['audited_facts'],
                imports=[x.text for x in xml.findall('./imports/import')], label=xml.get('theory'))
    validate_native_audit(value, **args)
    for field in ('prefix', 'delta', 'names', 'imports', 'label'):
        changed = dict(args)
        changed[field] = ['different'] if isinstance(changed[field], list) else 'different'
        _reject(lambda changed=changed: validate_native_audit(value, **changed))
    for field, different in [('worker', 'wrong-worker'), ('request_id', 999), ('audit_sha256', '0'*64)]:
        _reject(lambda field=field, different=different:
                validate_native_audit({**value, field: different}, **args))
    return {'recorded_failed_batch': 39, 'replayed_native_regression_request': value['request_id'],
            'binding_mutations_rejected': 8, 'native_invocations': 0}


def _store(scratch, name):
    from regen.infrastructure import incremental_log as logs
    root = scratch / name
    logs.enable(root)
    return root


def merkle_copy(case, fixture_root: Path, scratch: Path):
    from regen.infrastructure import incremental_log as logs
    source, target = _store(scratch, 'source'), _store(scratch, 'target')
    original = source / 'batch-requests/000001/check.json'
    value = {'proof': 'synthetic shared payload ' * 200,
             'feedback': {'source': 'synthetic diagnostic ' * 200}}
    logs.write_artifact(original, value, immutable=True)
    manifest = original.read_bytes()
    key = json.loads(manifest)['root']
    copies = _parameters(case)['copies']
    with patch.object(logs.ObjectStore, 'decode', side_effect=AssertionError('expanded copy')), \
         patch.object(logs.ObjectStore, 'encode', side_effect=AssertionError('reencoded copy')), \
         logs.logged_copy_batch():
        for i in range(copies):
            logs.copy_logged_file(original, target / f'batch-requests/{i:06d}/check.json')
    assert all(p.read_bytes() == manifest for p in target.glob('batch-requests/*/check.json'))
    shutil.rmtree(source)
    logs.clear_cache()
    assert logs.read_artifact(target / 'batch-requests/000000/check.json') == value
    # A copied graph must be independent and every later operation revalidates it.
    corrupt_target = _store(scratch, 'corrupt-target')
    copied = target / 'batch-requests/000000/check.json'
    logs.copy_logged_file(copied, corrupt_target / 'batch-requests/000000/check.json')
    object_path = logs.ObjectStore(corrupt_target).path(key)
    object_path.write_bytes(b'corrupt')
    dest = corrupt_target / 'batch-requests/000001/check.json'
    _reject(lambda: logs.copy_logged_file(copied, dest))
    assert not dest.exists()
    source_object = logs.ObjectStore(target).path(key)
    source_object.write_bytes(b'corrupt')
    other = _store(scratch, 'another-target') / 'batch-requests/000000/check.json'
    _reject(lambda: logs.copy_logged_file(copied, other))
    assert not other.exists()
    # Reconstruct a clean source to test durability failure independently.
    fresh = _store(scratch, 'fresh')
    fp = fresh / 'batch-requests/000000/check.json'
    logs.write_artifact(fp, value, immutable=True)
    with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['sync', '-f'])):
        _reject(lambda: logs.copy_logged_file(fp, other), subprocess.CalledProcessError)
    return {'copied_manifests': copies, 'copy_decode_calls': 0, 'copy_encode_calls': 0,
            'independent_after_source_removed': True, 'corrupt_stores_rejected': 2,
            'failed_sync_reported': True}


def resume_evidence(case, fixture_root: Path, scratch: Path):
    v = _load(fixture_root, _parameters(case)['evidence'])
    assert v['source_acceptance_sequence'] == 991 and v['source_batch_request'] == 628
    assert v['source_valid_contracts'] == v['destination_valid_contracts'] == 11
    assert len(set(v['destination_accepted_order'])) == 11
    assert v['source_draft_sha256'] == v['destination_first_observed_draft_sha256']
    assert v['draft_inheritance_exact']
    assert v['workspace_continuation']['theories'][0]['source_commit'] == v['source_acceptance_commit']
    initial, opened = v['initial_resume_clock']['state'], v['first_gate_reopened_clock']['state']
    assert initial['effective_seconds'] == opened['effective_seconds'] == v['inherited_effective_seconds']
    assert not initial['gate_open'] and opened['gate_open']
    assert initial['seconds'] - initial['effective_seconds'] == v['remaining_seconds']
    assert not v['pending_tool_status']['provider_tool_outcome_recorded']
    assert not v['continuation_origin']['pending_tool_replayed']
    assert not v['continuation_origin']['old_thread_restored']
    assert v['new_provider_tool_round_trip']['outcome'] == 'returned'
    return {'source_step': 991, 'source_batch': 628, 'preserved_contract_count': 11,
            'charged_seconds': initial['effective_seconds'], 'remaining_seconds': v['remaining_seconds'],
            'pending_call_replayed': False, 'evidence_only': True}


def resume_clock(case, fixture_root: Path, scratch: Path):
    from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
    from regen.infrastructure.experiment_ledger import Ledger
    p = _parameters(case)
    v = _load(fixture_root, p['evidence'])
    state = deepcopy(v['initial_resume_clock']['state'])
    now = [0.0]
    ledger = Ledger(scratch / 'clock', 'synthetic-clock-envelope', clock=lambda: now[0])
    ledger.append('time_budget_checkpoint', boundary='close', state=state)
    clock = EffectiveTimeBudget(ledger, state['base_seconds'], include_planning=True,
                                resume_authorization='offline-incident-replay', monotonic=lambda: now[0])
    try:
        before = clock.snapshot()
        now[0] += p['offline_seconds']
        after = clock.snapshot()
        assert before['effective_seconds'] == after['effective_seconds'] == state['effective_seconds']
        assert not after['gate_open']
        assert after['planning_seconds'] == state['planning_seconds']
        assert after['worker_seconds'] == state['worker_seconds']
    finally:
        clock.close()
    return {'checkpoint_effective_seconds': state['effective_seconds'],
            'offline_seconds_simulated': p['offline_seconds'], 'offline_seconds_charged': 0,
            'synthetic_ledger_envelope': True}


def native_failure(case, fixture_root: Path, scratch: Path):
    from regen.execution.batch_checker import BatchWorker
    v = _load(fixture_root, _parameters(case)['evidence'])['data']
    assert v['native_proof_state']['command'] == 'done'
    assert int(v['native_proof_state']['line']) == 2963
    assert len(v['native_proof_state_full']['goals']) == 5
    worker = object.__new__(BatchWorker)
    worker.root = scratch / 'native-response-boundary'
    worker.root.mkdir()
    worker.base, worker.pending, worker.index = '', None, 628
    worker.name, worker.label = 'offline-recorded-red', 'Recorded_Batch_628'
    worker.checker = SimpleNamespace(batch_candidate_timeout=v['timeout_seconds'])
    worker.imports, worker.checkpoints = [], {}
    worker._write_checkpoints = lambda *args: {}
    worker.transaction = lambda *args, **kwargs: (v['diagnostic'], '')
    snapshot = v['native_proof_state_full']
    xml = ET.Element('proof_state', {k: str(snapshot[k]) for k in ('origin', 'command', 'line')})
    goals = ET.SubElement(xml, 'goals')
    for text in snapshot['goals']:
        ET.SubElement(goals, 'goal').text = text
    (worker.root / '628.proof-state.xml').write_bytes(ET.tostring(xml))
    result = worker.check_batch(v['source_content'], v['audited_facts'])
    assert result['verdict'] == 'red' and result['returncode'] == 1
    assert result['timed_out'] is False and result['timeout_scope'] is None
    assert result['native_proof_state']['goal_count'] == 5
    assert worker.pending is None
    return {'recorded_batch': 628, 'returncode': result['returncode'],
            'timed_out': result['timed_out'], 'goal_count': 5,
            'native_invocations': 0, 'response_boundary_only': True}


def copy_operation_counts(case, fixture_root: Path, scratch: Path):
    from regen.infrastructure import incremental_log as logs
    p = _parameters(case)
    history = _load(fixture_root, p['evidence'])
    assert history['acceptance_input_size_audit']['logical_canonical_json_bytes_sum'] == 6073136932
    source = _store(scratch, 'source')
    original = source / 'batch-requests/000000/check.json'
    shared = {'source': 'synthetic shared source ' * 200, 'prior': [1, 2, 3]}
    logs.write_artifact(original, {'shared': [shared] * 8}, immutable=True)
    references = logs.ObjectStore.references
    measurements = {}
    for batched in (False, True):
        target = _store(scratch, 'batched' if batched else 'unbatched')
        counts = {}
        def count(store, key):
            identity = (str(store.root), key)
            counts[identity] = counts.get(identity, 0) + 1
            return references(store, key)
        with patch.object(logs.ObjectStore, 'references', count), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0)) as sync:
            if batched:
                with logs.logged_copy_batch():
                    for i in range(p['copies']):
                        logs.copy_logged_file(original, target / f'batch-requests/{i:06d}/check.json')
            else:
                for i in range(p['copies']):
                    logs.copy_logged_file(original, target / f'batch-requests/{i:06d}/check.json')
            sync_calls = len([c for c in sync.call_args_list if c.args and c.args[0][:2] == ['sync', '-f']])
        assert all(logs.read_artifact(f) == {'shared': [shared] * 8}
                   for f in target.glob('batch-requests/*/check.json'))
        measurements['batched' if batched else 'unbatched'] = {
            'filesystem_sync_calls': sync_calls, 'object_reference_calls': sum(counts.values()),
            'distinct_store_objects': len(counts), 'max_visits_per_store_object': max(counts.values())}
        if batched:
            assert sync_calls == 1 and all(n == 1 for n in counts.values())
    return {'copies': p['copies'], 'operation_counts': measurements,
            'historical_logical_json_bytes': 6073136932,
            'historical_copied_files': history['copied_batch_files'] + history['copied_document_files'],
            'wall_clock_threshold': None, 'production_performance_issue_status': 'open_observation',
            'sync_execution_mocked_for_counting': True}


HANDLERS = {
    'codex.locale_audit': locale_audit,
    'codex.merkle_copy': merkle_copy,
    'codex.resume_evidence': resume_evidence,
    'codex.resume_clock': resume_clock,
    'codex.native_failure': native_failure,
    'codex.copy_operation_counts': copy_operation_counts,
}
