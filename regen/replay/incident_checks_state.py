"""Receipt, budget and storage replays from CRefine operator incidents."""
from copy import deepcopy
import errno
import hashlib
import io
import json
import math
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def evidence(case, root):
    return json.loads((root / case['_replay']['parameters']['evidence']).read_text())


def unit_regressions(case, root, scratch):
    names = case['_replay']['parameters']['tests']
    if not names or any(not name.startswith('regen.tests.current.test_') for name in names):
        raise ValueError('only reviewed repository unit tests are supported')
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
    assert result.testsRun >= len(names) and result.wasSuccessful(), output.getvalue()
    assert not result.skipped, 'a skipped test is not a successful replay'
    return {'tests_run': result.testsRun, 'native_checker': 'stubbed or not invoked',
            'production_regressions': names}


def prefix_step(case, root, scratch):
    from regen.execution.vero_checker_adapter import VeroCheckerAdapter
    from regen.infrastructure.vero_experiment_observer import VeroObserver
    data = evidence(case, root)
    adapter = object.__new__(VeroCheckerAdapter)
    adapter.context = {'checker_context_id': 'offline-receipt-projection-only'}
    adapter.frozen_plan = None
    uids = {r['data']['target_id'] for t in data['transitions'] for r in t['revocations']}
    adapter.observer = SimpleNamespace(bench={'contracts': [
        {'id': uid, 'module': 'Vero_Thread_Core'} for uid in sorted(uids)]})
    results = []
    for transition in data['transitions']:
        before = (root / transition['before']).read_bytes()
        after = (root / transition['after']).read_bytes()
        a = adapter._contract_receipts('Vero_Thread_Core', {'Vero_Thread_Core.thy': before},
                                       {'Vero_Thread_Core': ()})
        b = adapter._contract_receipts('Vero_Thread_Core', {'Vero_Thread_Core.thy': after},
                                       {'Vero_Thread_Core': ()})
        for record in transition['revocations']:
            uid = record['data']['target_id']
            old, new = record['data']['old_receipt'], record['data']['current_receipt']
            assert a[uid]['proof_body_sha256'] == b[uid]['proof_body_sha256'] == old['proof_body_sha256'] == new['proof_body_sha256']
            assert a[uid]['pre_proof_sha256'] != b[uid]['pre_proof_sha256']
            assert not VeroObserver._same_accepted_proof(old, new)
            results.append({'sequence': record['sequence'], 'target': uid,
                'body_unchanged': True, 'prefix_changed': True,
                'requires_independent_native_revalidation': True})
    audit = data['native_dependency_audit']
    assert audit['changed_helpers_used_by_affected_proofs'] == []
    assert audit['native_replay']['original_returncode'] == 0
    assert audit['native_replay']['all_changed_helpers_removed_returncode'] == 0
    assert audit['native_replay']['affected_contract_proof_bodies_unchanged']
    return {'historical_revocations_reproduced': results,
            'historical_native_dependency_audit_verified': True,
            'new_native_execution': False, 'new_green_published': False}


def budget_union(case, root, scratch):
    data = evidence(case, root)
    framework = data['framework']['data']['correction']
    branch = data['branch']['data']['correction']
    intervals = framework['intervals']
    start, end = branch['branch_start_unix'], branch['branch_end_unix']
    overlap = sum(i['seconds'] for i in intervals
                  if start <= i['start_unix'] and i['end_unix'] <= end)
    branch_cost = branch['branch_charged_time']['effective_seconds']
    framework_cost = sum(i['seconds'] for i in intervals)
    union = branch_cost + framework_cost - overlap
    expected = case['_replay']['parameters']['expected']
    for actual, key in ((overlap, 'overlap'), (union, 'union'),
                        (branch_cost - overlap, 'additional_credit')):
        assert math.isclose(actual, expected[key], abs_tol=1e-6), (actual, key)
    assert math.isclose(union, branch['total_union_credit_seconds'], abs_tol=1e-6)
    before, after = branch['original_state'], branch['corrected_state']
    assert math.isclose(before['effective_seconds'] - after['effective_seconds'],
                        branch_cost - overlap, abs_tol=1e-6)
    for state in (before, after):
        assert math.isclose(state['effective_seconds'], state['worker_seconds'] + state['planning_seconds'], abs_tol=1e-6)
        assert math.isclose(state['worker_seconds'], sum(state['theory_seconds'].values()), abs_tol=1e-6)
        assert state['seconds'] == state['base_seconds'] == 172800
    assert before['planning_seconds'] == after['planning_seconds']
    assert branch['preserve_tokens_proofs_frozen_inputs_and_original_events']
    assert not math.isclose(branch_cost + framework_cost, union, abs_tol=1e-6)
    return {'union_credit_seconds': union, 'overlap_credited_once_seconds': overlap,
            'naive_double_count_negative_control_detected': True,
            'additional_credit_seconds': branch_cost - overlap,
            'scope': 'historical arithmetic oracle; no live budget adjustment'}


def restore_clock(case, root, scratch):
    from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, budget_journal_summary
    from regen.infrastructure.experiment_ledger import Ledger
    data = evidence(case, root)
    state = data['branch']['data']['correction']['corrected_state']
    tick = [100.0]
    ledger = Ledger(scratch / 'ledger', 'offline-incident-clock')
    ledger.append('time_budget_checkpoint', boundary='close', state=deepcopy(state))
    old_event = deepcopy(ledger.rows[0])
    clock = EffectiveTimeBudget(ledger, seconds=172800, include_planning=True,
        resume_authorization='offline fixture only', monotonic=lambda: tick[0])
    try:
        tick[0] += 86400
        snapshot = clock.snapshot()
        assert snapshot['effective_seconds'] == state['effective_seconds']
        assert snapshot['planning_seconds'] == state['planning_seconds']
        assert not snapshot['gate_open']
        assert budget_journal_summary(ledger)['unobserved_tail_seconds'] is None
        assert ledger.rows[0] == old_event
    finally:
        clock.close()
    return {'effective_seconds_preserved': state['effective_seconds'],
            'offline_seconds_charged': 0, 'unknown_crash_tail_preserved': True,
            'historical_event_unchanged': True}


def storage_atomicity(case, root, scratch):
    from regen.execution.proof_commits import durable
    data = evidence(case, root)
    previous = scratch / 'last-checkpoint.json'
    durable(previous, {'sequence': 27725, 'effective_seconds': 147106.26784896082})
    before = previous.read_bytes()
    destination = scratch / 'response.json'
    with patch('regen.execution.proof_commits.os.fsync', side_effect=OSError(errno.ENOSPC, 'injected full disk')):
        try:
            durable(destination, {'new_response': True}, immutable=True)
        except OSError as error:
            assert error.errno == errno.ENOSPC
        else:
            raise AssertionError('injected ENOSPC was swallowed')
    assert not destination.exists()
    assert previous.read_bytes() == before
    assert not list(scratch.glob('.pending-*'))
    receipts = data['archive_receipts']
    assert len(receipts) == 31
    released = sum(row['original_bytes'] - row['archive_bytes'] for row in receipts)
    assert released == 21773021413
    assert all(len(row['uncompressed_sha256']) == 64 for row in receipts)
    return {'injected_errno': errno.ENOSPC, 'partial_response_not_published': True,
            'prior_checkpoint_preserved': True, 'historical_archives': len(receipts),
            'historical_released_bytes': released, 'actual_disk_filled': False}


def owner_visibility(case, root, scratch):
    from regen.preparation.owner_blind_dataset import anonymous_payload, vero_benchmark
    data = evidence(case, root)
    source = data['synthetic_source']
    changed = deepcopy(source)
    old_id = source['contracts'][0]['contract_id']
    new_id = 'contract:Refine.Other_R:example:secret'
    changed['contracts'][0].update(contract_id=new_id, owner_theory='Refine.Other_R',
                                   owner_file_slot='Other_R.thy')
    changed['contract_cards'][0].update(contract_id=new_id, owner_theory='Refine.Other_R')
    changed['owner_groups'] = {'Refine.Other_R': [new_id]}
    changed['theory_import_graph'] = {'Refine.Other_R': ['Refine.Hidden_R']}
    public, _ = anonymous_payload(source)
    other, _ = anonymous_payload(changed)
    assert public == other
    bench = vero_benchmark(public, checker_context_id='test', base_import='Semantic_Root')
    assert old_id not in json.dumps(bench) and 'Demo_R' not in json.dumps(bench)
    assert bench['contracts'][0]['statement'] == source['contracts'][0]['exact_statement']
    historical = data['historical_audit']
    assert historical['contract_count'] == 309
    assert historical['original_owner_and_id_disclosures'] == 0
    assert historical['semantics_context_attributes_preserved']
    return {'owner_reassignment_invariant': True, 'statement_preserved': True,
            'historical_contract_count': 309, 'runtime_fixture_contract_count': 1,
            'native_execution': False}


def artifact_reuse(case, root, scratch):
    data = evidence(case, root)
    comparison = data['artifact_comparison']
    expected = case['_replay']['parameters']['expected']
    assert comparison['iter004_sha256'] == expected['previous_sha256']
    assert comparison['current_workspace_sha256'] == expected['current_sha256']
    assert comparison['iter004_sha256'] != comparison['current_workspace_sha256']
    assert comparison['equal'] is False
    assert [s['slot'] for s in comparison['changed_slots']] == ['c0236:prove']
    assert data['journal']['iter004_proved_specs'] == 14
    assert data['journal']['iter005_eval_report_exists'] is False
    return {'previous_artifact_sha256': comparison['iter004_sha256'],
            'current_artifact_sha256': comparison['current_workspace_sha256'],
            'historical_changed_slot': 'c0236:prove',
            'old_report_can_certify_changed_artifact': False,
            'scope': 'historical identity evidence; no production runner or native replay'}


HANDLERS = {'state.unit_regressions': unit_regressions, 'state.prefix_step': prefix_step,
    'state.budget_union': budget_union, 'state.restore_clock': restore_clock,
    'state.storage_atomicity': storage_atomicity, 'state.owner_visibility': owner_visibility,
    'state.artifact_reuse': artifact_reuse}
