"""Bounded orchestration controls, not real Isabelle acceptance evidence.

Real derived copies, normal document validators/journals, and a cold Python
journal verifier surround a synthetic checker. No model or native worker runs.
"""
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import context_graph, journal, package_for
from regen.planning.proof_graph import build_graph
from regen.planning.theory_document import block, candidate, header, source_hash
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import check
from regen.tools import controlled_native_replay as replay
from regen.tools import prepared_checkpoint as checkpoint


class SyntheticWorker:
    pending = None

    def __init__(self):
        self.closed = False

    def align(self, source):
        if self.closed:
            raise AssertionError('closed worker reused')

    def check_batch(self, source, names):
        self.align(source)
        return {'verdict': 'red', 'diagnostic': 'Undefined fact: rejected_helper'}

    def close(self):
        self.closed = True


class SyntheticChecker(Checker):
    def __init__(self):
        self.calls = 0
        self.closed = 0
        self.workers = []
        self._batch_worker = None
        self.checked_sources = []

    def _check(self, source, label, *, timeout=None):
        with CURRENT_RESOURCES.get().lease(self):
            if self._batch_worker is None:
                self._batch_worker = SyntheticWorker()
                self.workers.append(self._batch_worker)
            self.checked_sources.append(source)
            self.verdict = 'red' if 'lemma rejected_helper:' in source else 'green'
            return super()._check(source, label, timeout=timeout)

    def close_prefix(self):
        self.closed += 1
        if self._batch_worker is not None:
            self._batch_worker.close()
            self._batch_worker = None


class ControlledNativeReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.fixture = self.root/'fixture'
        self.fixture_run = self.fixture/'run'
        self.checkers, self.pools, self.setup_arms = [], [], []
        self.package = {'theory': replay.THEORY, 'file_slot': replay.THEORY+'.thy',
            'repository_projection': {'nodes': []}, 'contracts': [
                {'contract_id': 'contract:target', 'name': 'target',
                 'exact_statement': 'lemma target: "True"', 'contract_owned_context': []}]}
        proposal = {'generated_nodes': [{'id': 'helper', 'name': 'helper', 'role': 'helper',
            'statement': 'lemma helper: "True & True"', 'context': [], 'dependencies': []}],
            'contract_dependencies': {}}
        graph = context_graph(build_graph(self.package, proposal, run_id='test-lineage', revision=1), [])
        self.old = {'graph': graph, 'proofs': {}, 'accepted_order': [], 'rounds': {'helper': 7},
            'revisions': {'helper': 3}, 'batch_requests': 44, 'status': 'planned', 'seal_id': None}
        self.package['package_payload_sha256'] = digest(self.package)
        self.imports = SyntheticChecker().imports()
        helpers = [next(n for n in graph['nodes'] if n['role'] == 'helper')] + [
            {'id': f'helper:{i}', 'name': f'fresh_{i}', 'role': 'helper', 'context': [],
             'statement': f'lemma fresh_{i}: "({i}::nat) = {i}"'} for i in range(1, 5)]
        helpers.append({'id': 'helper:bad', 'name': 'rejected_helper', 'role': 'helper',
                        'context': [], 'statement': 'lemma rejected_helper: "False"'})
        contract = next(n for n in graph['nodes'] if n['role'] == 'contract')
        self.draft = header(replay.THEORY, self.imports)+''.join(block(n, 'by simp')
            for n in helpers)+block(contract)+'end\n'
        proposed = candidate(self.package, self.old, self.draft, self.imports)
        self.candidates = [{'request': 45, 'source': self.draft, 'state': proposed,
                            'source_sha256': source_hash(self.draft)}]
        config = {'run_id': 'test-lineage', 'implementation': {}, 'session_schedule': {},
            'contract_package_dir': str(self.fixture_run/'packages'),
            'session_progress_ledger': str(self.fixture_run/'session-progress-ledger.json')}
        durable(self.fixture_run/'run.json', config)
        durable(self.fixture_run/'execution-input.json', {'package_hashes': {
            self.package['file_slot']: self.package['package_payload_sha256']}})
        durable(self.fixture_run/'packages'/(self.package['file_slot']+'.json'), self.package)
        journal(self.fixture_run, config, self.package).append('initial', self.old, {'check': check()})
        self.app = {'scope': 'synthetic-orchestration-only'}
        durable(self.fixture/'manifest.json', {'schema': checkpoint.SCHEMA, 'application': self.app,
            'external_context': {'files': {}, 'source_trees': {}}, 'lineage_run_id': config['run_id'],
            'prepared_root': str(self.fixture_run), 'files': checkpoint.inventory(self.fixture_run),
            'run_binding': {k: v for k, v in config.items() if k not in checkpoint.RELOCATABLE}})
        self.pin = checkpoint.sha(self.fixture/'manifest.json')
        durable(self.source/'theories'/replay.THEORY/'batch-requests/000045/check.json',
                {'sealable_generation': True, 'transcript_prefix': 'synthetic-test-only'})
        self.patch('regen.tools.prepared_checkpoint.application_binding', return_value=self.app)
        self.patch('regen.infrastructure.progress_events.emit_event')
        self.patch_object(replay, 'SOURCE', self.source)
        self.patch_object(replay, 'load_fixture', return_value=(self.old, self.package,
            self.candidates, self.imports, {'scope': 'synthetic-test-only'}))
        self.setup_mock = self.patch_object(replay, 'setup', side_effect=self.setup_arm)
        self.patch_object(replay, 'NativeResources', side_effect=self.make_pool)

    def patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def patch_object(self, *args, **kwargs):
        patcher = patch.object(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_pool(self, *args, **kwargs):
        result = NativeResources(*args, **kwargs)
        self.pools.append(result)
        return result

    def setup_arm(self, arm, theory, timeout):
        self.assertEqual((theory, timeout), (replay.THEORY, 900))
        config = replay.read_artifact(arm/'run.json')
        package = package_for(config, self.package['file_slot'])
        commits = journal(arm, config, package)
        self.assertEqual(commits.replay()['batch_requests'], 44)
        checker = SyntheticChecker()
        self.checkers.append(checker)
        self.setup_arms.append(arm)
        return config, package, arm/'theories'/theory, commits, checker, []

    def run_replay(self, **kwargs):
        return replay.run(self.fixture, kwargs.pop('pin', self.pin), self.root/'output',
                          pairs=kwargs.pop('pairs', 1), wall_limit=kwargs.pop('wall_limit', 30), **kwargs)

    def assert_cleanup(self):
        self.assertTrue(self.pools)
        self.assertTrue(all(p.closed and not p.residents for p in self.pools))
        self.assertTrue(all(c.closed for c in self.checkers))
        self.assertTrue(all(w.closed for c in self.checkers for w in c.workers))
        self.assertIsNone(CURRENT_RESOURCES.get())

    def test_real_copy_journal_cold_recovery_and_no_recheck_of_green(self):
        original = checkpoint.inventory(self.fixture)
        native_calls = []
        actual_subprocess = subprocess.run
        def cold(*args, **kwargs):
            native_calls.append(args[0])
            self.assertEqual(kwargs['cwd'], Path(replay.__file__).resolve().parents[2])
            self.assertTrue(self.checkers[-1].closed)
            self.assertIsNone(self.checkers[-1]._batch_worker)
            return actual_subprocess(*args, **kwargs)
        with patch.object(replay.subprocess, 'run', side_effect=cold), patch.object(replay, '__name__', '__main__'):
            report = self.run_replay()
        self.assertTrue(report['pass'])
        self.assertEqual(report['order'], ['A', 'B'])
        self.assertEqual(report['arms'][0]['new_ids'], [])
        self.assertEqual(len(report['arms'][1]['new_ids']), 5)
        self.assertTrue(report['arms'][1]['cold_recovery_verified'])
        self.assertEqual(native_calls[0][2], 'regen.tools.controlled_native_replay')
        self.assertEqual(self.checkers[1].calls, 6)  # Five GREEN, one RED; first GREEN only once.
        self.assertEqual(len(self.checkers[1].workers), 2)
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['target_contracts_proved'], 0)
        for row in report['arms']:
            restored = replay.verify_journal(Path(row['run_dir']))
            self.assertEqual(restored['batch_requests'], 44)
            self.assertTrue(row['original_costs_preserved'])
            self.assertTrue(row['requests'][0]['draft_preserved'])
        self.assertEqual(checkpoint.inventory(self.fixture), original)
        self.assert_cleanup()
        # Summaries retain evidence links and timings, not Isabelle logs/proof bodies.
        text = json.dumps(report)
        for forbidden in ('source_content', 'log_content', 'document_source', 'by simp'):
            self.assertNotIn(forbidden, text)

    def test_interrupted_check_receipts_preserved_in_projection(self):
        report = self.run_replay()
        arm = report['arms'][1]
        row, interrupted = arm['requests'][0], arm['interrupted_checks']
        self.assertEqual(set(row['new_green']) | {r['entry_id'] for r in interrupted}, set(arm['new_ids']))
        self.assertEqual(len(row['checks'])+len(interrupted), self.checkers[1].calls)
        self.assertEqual(len(interrupted), 1)
        self.assertTrue(interrupted[0]['accepted_before_interruption'])
        self.assertTrue(Path(interrupted[0]['evidence_path']).is_file())

    def test_abba_independent_arms_interrupt_exactly_once(self):
        report = self.run_replay(pairs=2)
        self.assertTrue(report['pass'])
        self.assertEqual(report['order'], ['A', 'B', 'B', 'A'])
        self.assertEqual(sum(r['interruption_exercised'] for r in report['arms']), 1)
        instances = [replay.read_artifact(Path(r['run_dir'])/'arm-instance.json') for r in report['arms']]
        self.assertEqual(len({r['instance_id'] for r in instances}), 4)
        self.assert_cleanup()

    def test_wrong_manifest_identity_rejected_before_setup_and_checker(self):
        with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
            self.run_replay(pin='0'*64)
        self.setup_mock.assert_not_called()
        self.assertFalse(self.checkers)
        report = replay.read_artifact(self.root/'output/report.json')
        self.assertFalse(report['pass'])
        self.assertEqual(report['error']['type'], 'ValueError')
        self.assert_cleanup()

    def test_context_mismatch_rejects_before_candidate_check(self):
        actual = self.setup_arm
        def wrong(*args):
            result = actual(*args)
            result[4].imports = lambda: ['Different.Import']
            return result
        self.setup_mock.side_effect = wrong
        with self.assertRaisesRegex(ValueError, 'frozen candidate context'):
            self.run_replay()
        self.assertEqual(self.checkers[0].calls, 0)
        self.assert_cleanup()

    def test_cold_recovery_mismatch_refuses_resume_and_cleans(self):
        with patch.object(replay.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({'state_sha256': 'wrong'}), '')):
            with self.assertRaisesRegex(AssertionError, 'different proof state'):
                self.run_replay()
        self.assertEqual(self.checkers[1].calls, 1)
        restored = replay.verify_journal(self.setup_arms[1])
        self.assertEqual(restored['accepted_order'], ['helper'])
        self.assertEqual(restored['batch_requests'], 44)
        self.assert_cleanup()

    def test_cold_process_failure_keeps_committed_proof(self):
        with patch.object(replay.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 1, '', 'synthetic cold validator failure')):
            with self.assertRaisesRegex(RuntimeError, 'cold journal recovery failed'):
                self.run_replay()
        self.assertEqual(replay.verify_journal(self.setup_arms[1])['accepted_order'], ['helper'])
        self.assert_cleanup()

    def test_native_check_exception_cleans_and_records_failure(self):
        with patch.object(replay, 'check_document', side_effect=RuntimeError('synthetic checker failed')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic checker failed'):
                self.run_replay()
        self.assertFalse(replay.read_artifact(self.root/'output/report.json')['pass'])
        self.assert_cleanup()

    def test_hard_deadline_covers_setup_and_cleans_registered_resources(self):
        previous = signal.getsignal(signal.SIGALRM)
        def slow(*args):
            result = self.setup_arm(*args)
            checker = result[4]
            with CURRENT_RESOURCES.get().lease(checker):
                time.sleep(5)
            return result
        self.setup_mock.side_effect = slow
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'hard wall limit'):
            self.run_replay(wall_limit=.1)
        self.assertLess(time.monotonic()-started, 2)
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        report = replay.read_artifact(self.root/'output/report.json')
        self.assertEqual(report['error']['type'], 'TimeoutError')
        self.assert_cleanup()

    def test_existing_deadline_is_not_replaced(self):
        signal.setitimer(signal.ITIMER_REAL, 30)
        try:
            with self.assertRaisesRegex(RuntimeError, 'existing process deadline'):
                self.run_replay()
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 20)
            self.setup_mock.assert_not_called()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def test_invalid_limits_and_source_output_rejected_without_loading_fixture(self):
        for wall in (0, -1, float('nan'), float('inf')):
            with self.subTest(wall=wall), self.assertRaises(ValueError):
                self.run_replay(wall_limit=wall)
        for pairs in (0, 3):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                self.run_replay(pairs=pairs)
        with self.assertRaisesRegex(ValueError, 'isolated output'):
            replay.run(self.fixture, self.pin, self.source/'inside', pairs=1, wall_limit=30)
        self.setup_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
