"""Independent native evidence protects GREEN from unrelated helper edits."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from threading import RLock
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tests.current.test_vero_contract_receipt_retention import Stop


def source(bodies, helper='lemma unused_helper: "True" by simp\n'):
    return ('theory A\nimports Vero_Support\n(* VERO START A imports *)\n'
        '(* VERO END A imports *)\nbegin\n') + helper + ''.join(
        f'vero_prove "{uid}" {uid}_prove: "True"\n'
        f'(* VERO START {uid} prove *)\n{body}\n(* VERO END {uid} prove *)\n'
        f'vero_disprove "{uid}" {uid}_disprove: "False"\n'
        f'(* VERO START {uid} disprove *)\noops\n(* VERO END {uid} disprove *)\n'
        for uid, body in bodies.items()) + 'end\n'


class VeroPrefixRevalidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'Vero_Support.thy').write_text('theory Vero_Support imports Main begin end\n')
        self.ledger = Ledger(self.root / 'ledger', 'revalidate')
        self.stop = Stop()
        self.stop.check = lambda: None
        self.bench = {'schema_version': 2, 'benchmark_id': 'b' * 64,
            'contracts': [{'id': uid, 'module': 'A', 'context': []}
                          for uid in ['c0001', 'c0002', 'c0003']]}
        self.observer = VeroObserver(self.ledger, self.stop, self.workspace, self.bench)
        self.adapter = object.__new__(VeroCheckerAdapter)
        self.adapter.root = self.root / 'adapter'
        self.adapter.context = dict(image='test', l4v=self.root, semantic_workspace=self.root,
            isabelle=self.root, base_session='HOL', base_import='Main', base_catalog='/base',
            checker_context_id='ctx')
        self.adapter.timeout = 10
        self.adapter.resources = Mock()
        self.adapter.lock = RLock()
        self.adapter.closed = False
        self.adapter.frozen_plan = None
        self.adapter.set_frozen_plan({'imports': {'A': []},
            'plan_sha256': 'a' * 64, 'allocation_sha256': 'b' * 64, 'imports_sha256': 'c' * 64})
        self.adapter.observer = self.observer
        checker = ModuleType('vero.isabelle.checker')
        checker.strip_comments = lambda text: text
        modules = patch.dict(sys.modules, {'vero.isabelle.checker': checker})
        modules.start()
        self.addCleanup(modules.stop)
        self.calls = []
        self.revalidation_rc = 0
        self.main_rc = 1
        self.omit_audit = False
        self.import_failure = False
        owner = self

        class Native:
            def __init__(self, run, work, **kwargs):
                self.work = work

            def _check(self, text, label, **kwargs):
                self.work.mkdir(parents=True, exist_ok=True)
                (self.work / (label + '.thy')).write_text(text)
                isolated = 'prefix-revalidations' in str(self.work)
                owner.calls.append((isolated, text))
                rc = owner.revalidation_rc if isolated else owner.main_rc
                if 'by (rule used_helper)' in text and 'lemma used_helper:' not in text:
                    rc = 1
                audited = [uid for uid in ['c0001', 'c0002', 'c0003']
                    if f'(* VERO END {uid} prove *)' in text]
                log = self.work / 'native.log'
                text_log = '' if owner.omit_audit else ''.join(
                    f'VERO_RESULT {uid} prove clean\n' for uid in audited)
                if owner.import_failure:
                    rc = 1
                    text_log += '*** At command "done" (line 87 of "/candidate/Vero_P.thy")\n'
                log.write_text(text_log)
                return {'returncode': rc, 'wall_ms': 20, 'log': str(log),
                        'source': str(self.work / (label + '.thy'))}

            def close_prefix(self):
                pass

        for module in ['regen.execution.vero_prefix_revalidation',
                       'regen.execution.vero_checker_adapter']:
            replacement = patch(module + '.IsabelleChecker', Native)
            replacement.start()
            self.addCleanup(replacement.stop)

    def accept(self, text, ids):
        (self.workspace / 'A.thy').write_text(text)
        receipts = self.adapter._contract_receipts('A',
            {p.name: p.read_bytes() for p in self.workspace.glob('*.thy')}, {'A': ()})
        self.observer.checked('A', {'status': 'ok', 'contract_status': 'ok',
            'audited': [uid + ':prove' for uid in ids], 'contract_receipts': receipts,
            'native_evidence': {'returncode': 0}, 'elapsed_seconds': 1}, text)

    def check(self, text):
        (self.workspace / 'A.thy').write_text(text)
        return self.adapter.check(self.workspace, 'A')

    def test_helper_after_checked_prefix_never_receives_green(self):
        self.main_rc=0
        text=source({'c0001':'by simp','c0002':'oops','c0003':'oops'},
                    helper='lemma vero_aux_checked: "True" by simp\n')
        text=text.rsplit('end\n',1)[0]+'lemma vero_aux_unchecked: "False" oops\nend\n'
        result=self.check(text)
        self.assertEqual(result['returncode'],0)
        self.assertNotIn('vero_aux_unchecked',self.calls[-1][1])
        helpers={row['uid']:row['status'] for row in self.observer.nodes['A'] if row['kind']=='helper'}
        self.assertEqual(helpers,{'helper:A:vero_aux_checked':'green'})
        self.assertEqual(set(self.observer.accepted_receipts),{'c0001'})

    def test_oops_or_sorry_helper_inside_checked_prefix_is_not_green(self):
        self.main_rc=0
        text=source({'c0001':'by simp','c0002':'oops','c0003':'oops'},helper=(
            'lemma vero_aux_abandoned: "False" apply simp oops\n'
            'lemma vero_aux_admitted: "False" proof - show ?thesis sorry qed\n'
            'lemma vero_aux_checked: "True" by simp\n'))
        result=self.check(text)
        self.assertEqual(result['returncode'],0)
        self.assertIn('vero_aux_abandoned',self.calls[-1][1])
        helpers={row['uid']:row['status'] for row in self.observer.nodes['A'] if row['kind']=='helper'}
        self.assertEqual(helpers,{'helper:A:vero_aux_abandoned':'open',
            'helper:A:vero_aux_admitted':'open','helper:A:vero_aux_checked':'green'})

    def test_helper_accounting_ignores_quoted_and_nested_cartouche_commands(self):
        from regen.infrastructure.vero_experiment_observer import _helper_proofs_in_checked_source
        text=('text ‹lemma vero_aux_fake: \\<open>by simp\\<close>›\n'
              'lemma vero_aux_real: "oops lemma vero_aux_quoted" by simp\n'
              'vero_prove "c0001" c0001_prove: "False" oops\n')
        self.assertEqual(_helper_proofs_in_checked_source(text),{'vero_aux_real':True})

    def test_unused_helper_removed_and_later_red_keeps_green(self):
        old = source({'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'})
        self.accept(old, ['c0001'])
        result = self.check(source({'c0001': 'by simp', 'c0002': 'BAD_DRAFT', 'c0003': 'oops'}, helper=''))
        self.assertEqual(result['returncode'], 1)
        self.assertEqual(set(self.observer.accepted_receipts), {'c0001'})
        self.assertNotIn('BAD_DRAFT', self.calls[0][1])
        self.assertTrue(self.calls[0][0])
        self.assertFalse(any(e['kind'] == 'contract_green_invalidated' for e in self.ledger.rows))
        self.assertIn('accepted_source_version', self.observer.nodes['A'][0])
        self.assertEqual(experiment_summary(self.ledger)['current_valid_contract_ids'], ['c0001'])

    def test_used_helper_removed_really_invalidates_proof(self):
        bodies = {'c0001': 'by (rule used_helper)', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies, helper='lemma used_helper: "True" by simp\n'), ['c0001'])
        self.check(source(bodies, helper=''))
        self.assertFalse(self.observer.accepted_receipts)
        self.assertTrue(any(e['kind'] == 'prefix_revalidation_failed' for e in self.ledger.rows))

    def test_historical_invalidated_version_recovers_only_after_native_success(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        changed = source(bodies, helper='')
        receipts = self.adapter._contract_receipts('A',
            {'A.thy': changed.encode(), 'Vero_Support.thy':
                (self.workspace / 'Vero_Support.thy').read_bytes()}, {'A': ()})
        self.observer.reconcile_contracts('A', {'A': receipts})
        self.assertFalse(self.observer.accepted_receipts)
        self.observer = VeroObserver(self.ledger, self.stop, self.workspace, self.bench)
        self.adapter.observer = self.observer
        self.assertIn('c0001', self.observer.certified_receipts)
        self.check(changed)
        self.assertEqual(set(self.observer.accepted_receipts), {'c0001'})

    def test_original_open_draft_is_masked_during_revalidation(self):
        self.accept(source({'c0001': 'oops', 'c0002': 'by simp', 'c0003': 'oops'}), ['c0002'])
        self.check(source({'c0001': 'BAD_DRAFT', 'c0002': 'by simp', 'c0003': 'oops'}, helper=''))
        self.assertNotIn('BAD_DRAFT', self.calls[0][1])
        self.assertIn('BAD_DRAFT', self.calls[-1][1])
        self.assertEqual(set(self.observer.accepted_receipts), {'c0002'})

    def test_failed_later_revalidation_still_checks_earlier_boundary(self):
        bodies = {'c0001': 'by simp', 'c0002': 'by (rule used_helper)', 'c0003': 'oops'}
        self.accept(source(bodies, helper='lemma used_helper: "True" by simp\n'), ['c0001', 'c0002'])
        self.check(source(bodies, helper=''))
        self.assertEqual(set(self.observer.accepted_receipts), {'c0001'})
        self.assertEqual([isolated for isolated, _ in self.calls], [True, True, False])

    def test_timeout_clean_markers_cannot_renew_certificate(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        self.revalidation_rc = 124
        self.check(source(bodies, helper=''))
        self.assertFalse(self.observer.accepted_receipts)

    def test_zero_exit_without_audit_cannot_renew_certificate(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        self.omit_audit = True
        self.check(source(bodies, helper=''))
        self.assertFalse(self.observer.accepted_receipts)

    def test_changed_accepted_body_is_rejected_without_revalidation(self):
        self.accept(source({'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}), ['c0001'])
        with self.assertRaisesRegex(ValueError, 'proof body changed'):
            self.check(source({'c0001': 'by auto', 'c0002': 'oops', 'c0003': 'oops'}, helper=''))
        self.assertFalse(self.calls)

    def test_source_race_prevents_publication(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        origin = self.observer.accepted_origins['c0001']
        with patch.object(self.adapter, '_assert_workspace_current_or_reconcile',
                side_effect=[None, None, ValueError('raw workspace changed')]):
            with self.assertRaisesRegex(ValueError, 'raw workspace changed'):
                self.check(source(bodies, helper=''))
        self.assertNotIn('c0001', self.observer.accepted_receipts)
        self.assertNotEqual(self.observer.certified_origins['c0001'], None)

    def test_revalidated_version_survives_replay_and_later_red(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        self.check(source(bodies, helper=''))
        origin = self.observer.accepted_origins['c0001']
        version = deepcopy(self.observer.nodes['A'][0]['accepted_source_version'])
        self.observer = VeroObserver(self.ledger, self.stop, self.workspace, self.bench)
        self.adapter.observer = self.observer
        self.check(source({**bodies, 'c0002': 'BAD_DRAFT'}, helper=''))
        self.assertEqual(self.observer.accepted_origins['c0001'], origin)
        self.assertEqual(self.observer.nodes['A'][0]['accepted_source_version'], version)

    def test_progress_canonical_audit_revalidates_before_reconciliation(self):
        from regen.tools.vero_serial_application import canonical_contract_receipts
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        (self.workspace / 'A.thy').write_text(source(bodies, helper=''))
        raw = self.root / 'raw-snapshot'
        raw.mkdir()
        for p in self.workspace.glob('*.thy'):
            (raw / p.name).write_bytes(p.read_bytes())
        benchmark = ModuleType('vero.isabelle.benchmark')

        def render(bench, destination, slots):
            destination.mkdir()
            for p in raw.glob('*.thy'):
                (destination / p.name).write_bytes(p.read_bytes())

        benchmark.render = render
        with patch.dict(sys.modules, {'vero.isabelle.benchmark': benchmark}):
            _, _, receipts = canonical_contract_receipts(self.bench, {'slots': {}},
                self.adapter, {'A': ()}, self.root / 'canonical', raw_snapshot=raw)
        self.observer.reconcile_contracts(None, receipts)
        self.assertEqual(set(self.observer.accepted_receipts), {'c0001'})
        self.assertTrue(self.calls[0][0])
        self.assertFalse(any(e['kind'] == 'contract_green_invalidated' for e in self.ledger.rows))

    def test_changed_external_context_requires_fresh_successful_evidence(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        old_context = self.observer.accepted_receipts['c0001']['context_identity']
        support = self.workspace / 'Vero_Support.thy'
        support.write_text(support.read_text() + '(* changed legal support source *)\n')
        self.check(source(bodies))
        self.assertNotEqual(self.observer.accepted_receipts['c0001']['context_identity'], old_context)
        self.assertTrue(self.calls[0][0])

    def test_changed_external_context_failure_invalidates_old_certificate(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        support = self.workspace / 'Vero_Support.thy'
        support.write_text(support.read_text() + '(* changed legal support source *)\n')
        self.revalidation_rc = 1
        self.check(source(bodies))
        self.assertFalse(self.observer.accepted_receipts)

    def test_cancelled_revalidation_does_not_leave_stale_current_green(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        with patch.object(self.stop, 'check', side_effect=[None, RuntimeError('budget stopped')]):
            with self.assertRaisesRegex(RuntimeError, 'budget stopped'):
                self.check(source(bodies, helper=''))
        self.assertNotIn('c0001', self.observer.accepted_receipts)
        self.assertIn('c0001', self.observer.certified_receipts)

    def test_source_race_after_result_preparation_prevents_publication(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        with patch.object(self.adapter, '_assert_workspace_current_or_reconcile',
                side_effect=[None, None, None, ValueError('prepublication source race')]):
            with self.assertRaisesRegex(ValueError, 'prepublication source race'):
                self.check(source(bodies, helper=''))
        self.assertNotIn('c0001', self.observer.accepted_receipts)

    def test_one_failed_import_is_not_rechecked_for_every_contract(self):
        self.adapter.frozen_plan['imports'] = {'A': ('Vero_P',), 'Vero_P': ()}
        parent = (b'theory Vero_P\nimports Vero_Support\n'
            b'(* VERO START Vero_P imports *)\n(* VERO END Vero_P imports *)\nbegin\nend\n')
        (self.workspace / 'Vero_P.thy').write_bytes(parent)
        bodies = {'c0001': 'by simp', 'c0002': 'by simp', 'c0003': 'oops'}
        initial = source(bodies).replace('imports Vero_Support\n', 'imports Vero_Support Vero_P\n', 1)
        self.accept(initial, ['c0001', 'c0002'])
        self.import_failure = True
        changed = source(bodies, helper='').replace(
            'imports Vero_Support\n', 'imports Vero_Support Vero_P\n', 1)
        result = self.check(changed)
        self.assertTrue(result['reused_failed_import_check'])
        self.assertEqual([isolated for isolated, _ in self.calls], [True])
        self.assertFalse(self.observer.accepted_receipts)
        self.calls.clear()
        self.check(changed)
        self.assertFalse(self.calls)

    def test_changed_import_source_invalidates_the_cached_rejection(self):
        self.test_one_failed_import_is_not_rechecked_for_every_contract()
        self.calls.clear()
        parent = self.workspace / 'Vero_P.thy'
        parent.write_text(parent.read_text() + '(* repaired import source *)\n')
        self.import_failure = False
        self.check((self.workspace / 'A.thy').read_text())
        self.assertEqual([isolated for isolated, _ in self.calls], [True, False])
        self.assertEqual(set(self.observer.accepted_receipts), {'c0001', 'c0002'})

    def test_corrupt_cached_rejection_is_not_reused(self):
        self.test_one_failed_import_is_not_rechecked_for_every_contract()
        self.calls.clear()
        record = next(iter(self.adapter._failed_import_evidence.values()))
        Path(record['result']).write_text('{}')
        self.check((self.workspace / 'A.thy').read_text())
        self.assertEqual([isolated for isolated, _ in self.calls], [True])

    def test_partial_revalidation_cannot_authorize_a_full_theory_seal(self):
        bodies = {'c0001': 'by simp', 'c0002': 'oops', 'c0003': 'oops'}
        self.accept(source(bodies), ['c0001'])
        self.check(source(bodies, helper=''))
        commits = [e for e in self.ledger.rows if e['kind'] == 'proof_commit'
            and e['data'].get('authority') == 'native-independent-prefix-revalidation']
        snapshot = {p.name: p.read_bytes() for p in self.workspace.glob('*.thy')}
        self.assertNotEqual(commits[-1]['data']['source_identity'],
            self.adapter._seal_source_identity('A', snapshot, {'A': ()}))


if __name__ == '__main__':
    unittest.main()
