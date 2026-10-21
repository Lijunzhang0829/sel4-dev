"""A seal-time raw edit cannot publish stale native evidence or erase unrelated GREEN."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from regen.execution.vero_checker_adapter import durable as actual_durable
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tests.current.test_vero_dynamic_imports import CHILD, Native, PARENT, Stop


class PostNativeWorkspaceRaceTests(unittest.TestCase):
    def prepared(self, root):
        from regen.tests.current.test_vero_dynamic_imports import DynamicImportsTests
        adapter, workspace, ledger, stop=DynamicImportsTests().setup_adapter(root)
        from vero.isabelle.benchmark import render
        render(adapter.observer.bench,workspace,{'c0001:prove':'by simp'})
        frozen=root/'immutable-raw';frozen.mkdir()
        for path in workspace.glob('*.thy'):
            shutil.copy2(path,frozen/path.name)
        adapter.observer.audit_workspace_snapshot=frozen
        return adapter,workspace,ledger,stop

    @staticmethod
    def insert_later_helper(workspace):
        path=workspace/(PARENT+'.thy')
        start='(* VERO START c0001 disprove_aux *)'
        source=path.read_text()
        assert start in source
        path.write_text(source.replace(start,
            start+'\nlemma vero_aux_later: True by simp\n',1))

    def test_later_helper_edit_during_seal_retains_prior_contract_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)

            class EditDuringSeal(Native):
                def build_generated_session(self,theories):
                    self_test.insert_later_helper(workspace)
                    return super().build_generated_session(theories)

            self_test=self
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',EditDuringSeal):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])
            self.assertNotIn(PARENT,stop.sealed)
            self.assertEqual([row for row in ledger.rows
                if row['kind']=='contract_green_invalidated'],[])
            self.assertEqual(len([row for row in ledger.rows
                if row['kind']=='proof_commit' and row['data']['theory']==PARENT]),1)
            self.assertEqual([row for row in ledger.rows
                if row['kind']=='seal_result' and row['data']['theory']==PARENT],[])

    def test_green_body_edit_during_seal_is_rejected_and_current_receipt_revoked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)

            class EditDuringSeal(Native):
                def build_generated_session(self,theories):
                    path=workspace/(PARENT+'.thy')
                    source=path.read_text()
                    path.write_text(source.replace('by simp','by blast',1))
                    return super().build_generated_session(theories)

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',EditDuringSeal):
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(adapter.observer.accepted_receipts,{})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],[])
            self.assertEqual(experiment_summary(ledger)['historical_first_green_contract_ids'],
                ['c0001'])
            self.assertTrue(any(row['kind']=='green_proof_edit_rejected'
                for row in ledger.rows))
            self.assertTrue(any(row['kind']=='contract_green_invalidated' and
                row['data']['reason']=='accepted_proof_body_changed_outside_seal'
                for row in ledger.rows))
            self.assertNotIn(PARENT,stop.sealed)

    def test_pre_proof_helper_edit_during_seal_revokes_current_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)

            class EditDuringSeal(Native):
                def build_generated_session(self,theories):
                    path=workspace/(PARENT+'.thy')
                    marker='(* VERO START c0001 prove_aux *)'
                    source=path.read_text()
                    path.write_text(source.replace(marker,
                        marker+'\nlemma vero_aux_prior: True by simp\n',1))
                    return super().build_generated_session(theories)

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',EditDuringSeal):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(adapter.observer.accepted_receipts,{})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],[])
            invalid=[row['data'] for row in ledger.rows
                if row['kind']=='contract_green_invalidated']
            self.assertEqual([(row['target_id'],row['reason']) for row in invalid],
                [('c0001','checked_prefix_before_proof_changed')])
            self.assertEqual(experiment_summary(ledger)['historical_first_green_contract_ids'],
                ['c0001'])
            self.assertNotIn(PARENT,stop.sealed)

    def test_missing_theory_does_not_hide_another_changed_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            from vero.isabelle.benchmark import render
            render(adapter.observer.bench,workspace,
                {'c0001:prove':'by simp','c0002:prove':'by simp'})
            adapter.observer.audit_workspace_snapshot=None
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(workspace,PARENT)
            self.assertIn(PARENT,stop.sealed)
            frozen=root/'immutable-raw'
            for path in workspace.glob('*.thy'):
                shutil.copy2(path,frozen/path.name)
            adapter.observer.audit_workspace_snapshot=frozen

            class EditDuringSeal(Native):
                def build_generated_session(self,theories):
                    self_test.insert_later_helper(workspace)
                    (workspace/(CHILD+'.thy')).unlink()
                    return super().build_generated_session(theories)

            self_test=self
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',EditDuringSeal):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,CHILD)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual(stop.sealed,set())
            invalid=[row['data'] for row in ledger.rows
                if row['kind']=='seal_invalidated']
            self.assertTrue(any(row['theory']==PARENT and
                row['reason']=='own_source_changed' for row in invalid))
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])

    def test_undecodable_theory_does_not_hide_another_changed_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            from vero.isabelle.benchmark import render
            render(adapter.observer.bench,workspace,
                {'c0001:prove':'by simp','c0002:prove':'by simp'})
            adapter.observer.audit_workspace_snapshot=None
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(workspace,PARENT)
            self.assertIn(PARENT,stop.sealed)
            frozen=root/'immutable-raw'
            for path in workspace.glob('*.thy'):
                shutil.copy2(path,frozen/path.name)
            adapter.observer.audit_workspace_snapshot=frozen

            class EditDuringSeal(Native):
                def build_generated_session(self,theories):
                    self_test.insert_later_helper(workspace)
                    (workspace/(CHILD+'.thy')).write_bytes(b'\xff')
                    return super().build_generated_session(theories)

            self_test=self
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',EditDuringSeal):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,CHILD)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual(stop.sealed,set())
            malformed=[row['data'] for row in ledger.rows
                if row['kind']=='source_invalid']
            self.assertEqual(malformed[-1]['invalid_theories'],[CHILD])
            invalid=[row['data'] for row in ledger.rows
                if row['kind']=='seal_invalidated']
            self.assertTrue(any(row['theory']==PARENT and
                row['reason']=='own_source_changed' for row in invalid))

    def test_symlink_theory_does_not_revoke_independent_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            from vero.isabelle.benchmark import render
            render(adapter.observer.bench,workspace,
                {'c0001:prove':'by simp','c0002:prove':'by simp'})
            adapter.observer.audit_workspace_snapshot=None
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(workspace,PARENT)
            frozen=root/'immutable-raw'
            for path in workspace.glob('*.thy'):
                shutil.copy2(path,frozen/path.name)
            adapter.observer.audit_workspace_snapshot=frozen

            class EditDuringSeal(Native):
                def build_generated_session(self,theories):
                    self_test.insert_later_helper(workspace)
                    child=workspace/(CHILD+'.thy')
                    child.unlink()
                    child.symlink_to('/etc/passwd')
                    return super().build_generated_session(theories)

            self_test=self
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',EditDuringSeal):
                with self.assertRaisesRegex(ValueError,'generated source'):
                    adapter.check(workspace,CHILD)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])
            self.assertEqual(stop.sealed,set())
            bad=[row['data'] for row in ledger.rows
                if row['kind']=='source_extraction_invalid']
            self.assertEqual(bad[-1]['affected_theories'],[CHILD])

    def test_edit_after_result_file_but_before_seal_event_is_reconciled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)

            def edit_after_result(path,value):
                outcome=actual_durable(path,value)
                if Path(path).name=='result.json':
                    self.insert_later_helper(workspace)
                return outcome

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native), \
                    patch('regen.execution.vero_checker_adapter.durable',
                        side_effect=edit_after_result):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual([row for row in ledger.rows
                if row['kind']=='seal_result' and row['data']['theory']==PARENT],[])
            self.assertNotIn(PARENT,stop.sealed)

    def test_edit_inside_seal_result_append_is_durably_invalidated(self):
        for edit_prefix in (False,True):
            with self.subTest(edit_prefix=edit_prefix), \
                    tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                adapter,workspace,ledger,stop=self.prepared(root)
                append=ledger.append

                def edit_after_append(kind,**data):
                    row=append(kind,**data)
                    if kind=='seal_result':
                        if edit_prefix:
                            path=workspace/(PARENT+'.thy')
                            marker='(* VERO START c0001 prove_aux *)'
                            source=path.read_text()
                            path.write_text(source.replace(marker,
                                marker+'\nlemma vero_aux_prior: True by simp\n',1))
                        else:self.insert_later_helper(workspace)
                    return row

                with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native), \
                        patch.object(ledger,'append',side_effect=edit_after_append):
                    with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                        adapter.check(workspace,PARENT)

                valid=[] if edit_prefix else ['c0001']
                self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                    valid)
                self.assertEqual(stop.sealed,set())
                self.assertEqual(adapter.observer.seals,{})
                kinds=[row['kind'] for row in ledger.rows]
                self.assertLess(kinds.index('seal_result'),
                    kinds.index('seal_invalidated'))
                restarted_ledger=Ledger(root/'ledger','dynamic-imports')
                restarted=VeroObserver(restarted_ledger,Stop(restarted_ledger),
                    workspace,adapter.observer.bench)
                self.assertEqual(restarted.seals,{})
                self.assertEqual(set(restarted.accepted_receipts),set(valid))

    def test_edit_inside_seal_completed_callback_is_invalidated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            completed=adapter.observer.seal_completed

            def edit_after_completed(theory,result):
                completed(theory,result)
                self.insert_later_helper(workspace)

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native), \
                    patch.object(adapter.observer,'seal_completed',
                        side_effect=edit_after_completed):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual(adapter.observer.seals,{})
            self.assertEqual(stop.sealed,set())

    def test_native_time_prefix_edit_invalidates_prior_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            adapter.observer.audit_workspace_snapshot=None
            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',Native):
                adapter.check(workspace,PARENT)
            frozen=root/'immutable-raw'
            for path in workspace.glob('*.thy'):
                shutil.copy2(path,frozen/path.name)
            adapter.observer.audit_workspace_snapshot=frozen

            class EditDuringNative(Native):
                def _check(self,source,theory,*,timeout):
                    result=super()._check(source,theory,timeout=timeout)
                    path=workspace/(PARENT+'.thy')
                    marker='(* VERO START c0001 prove_aux *)'
                    current=path.read_text()
                    path.write_text(current.replace(marker,
                        marker+'\nlemma vero_aux_prior: True by simp\n',1))
                    return result

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',
                    EditDuringNative):
                with self.assertRaisesRegex(ValueError,'raw workspace changed'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(adapter.observer.accepted_receipts,{})
            self.assertEqual(adapter.observer.seals,{})
            self.assertEqual(experiment_summary(ledger)['historical_first_green_contract_ids'],
                ['c0001'])

    def test_illegal_later_contract_edit_preserves_earlier_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            from vero.isabelle.benchmark import digest, render
            body={'schema_version':2,'backend':'isabelle','mode':'proof',
                'base_import':'Main','contracts':[
                    {'id':'c0001','module':PARENT,'statement':'lemma first: True',
                        'context':[]},
                    {'id':'c0002','module':PARENT,'statement':'lemma second: True',
                        'context':[]}],
                'planning':{'imports':{PARENT:[]}}}
            bench={**body,'benchmark_id':digest(body)}
            adapter.observer.bench=bench
            adapter.frozen_plan={**adapter.frozen_plan,'imports':{PARENT:()}}
            (workspace/(CHILD+'.thy')).unlink()
            render(bench,workspace,{'c0001:prove':'by simp',
                'c0002:prove':'by simp'})
            adapter.observer.audit_workspace_snapshot=None

            class TwoContractNative(Native):
                def _check(self,source,theory,*,timeout):
                    log=self.work_dir/(theory+'.log')
                    log.write_text('VERO_RESULT c0001 prove clean\n'
                        'VERO_RESULT c0002 prove clean\n')
                    return {'returncode':0,'wall_ms':1000,
                        'source':str(self.work_dir/(theory+'.thy')),
                        'log':str(log)}

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',
                    TwoContractNative):
                adapter.check(workspace,PARENT)
            self.assertEqual(set(adapter.observer.accepted_receipts),
                {'c0001','c0002'})
            frozen=root/'immutable-raw'
            for path in frozen.glob('*.thy'):path.unlink()
            for path in workspace.glob('*.thy'):
                shutil.copy2(path,frozen/path.name)
            adapter.observer.audit_workspace_snapshot=frozen

            class EditLaterProof(TwoContractNative):
                def _check(self,source,theory,*,timeout):
                    result=super()._check(source,theory,timeout=timeout)
                    path=workspace/(PARENT+'.thy')
                    text=path.read_text()
                    start='(* VERO START c0002 prove *)'
                    end='(* VERO END c0002 prove *)'
                    first=text.index(start)+len(start)
                    last=text.index(end,first)
                    path.write_text(text[:first]+'\nby blast\n'+text[last:])
                    return result

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',
                    EditLaterProof):
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            self.assertEqual(experiment_summary(ledger)['current_valid_contract_ids'],
                ['c0001'])
            self.assertEqual(experiment_summary(ledger)['historical_first_green_contract_ids'],
                ['c0001','c0002'])
            invalid=[row['data']['target_id'] for row in ledger.rows
                if row['kind']=='contract_green_invalidated']
            self.assertEqual(invalid,['c0002'])
            restarted_ledger=Ledger(root/'ledger','dynamic-imports')
            restarted=VeroObserver(restarted_ledger,Stop(restarted_ledger),
                workspace,bench)
            self.assertEqual(set(restarted.accepted_receipts),{'c0001'})

    def test_later_contract_polarity_swap_preserves_earlier_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            adapter,workspace,ledger,stop=self.prepared(root)
            from vero.isabelle.benchmark import digest, render
            body={'schema_version':2,'backend':'isabelle','mode':'proof',
                'base_import':'Main','contracts':[
                    {'id':'c0001','module':PARENT,'statement':'lemma first: True',
                        'context':[]},
                    {'id':'c0002','module':PARENT,'statement':'lemma second: True',
                        'context':[]}],
                'planning':{'imports':{PARENT:[]}}}
            bench={**body,'benchmark_id':digest(body)}
            adapter.observer.bench=bench
            adapter.frozen_plan={**adapter.frozen_plan,'imports':{PARENT:()}}
            (workspace/(CHILD+'.thy')).unlink()
            render(bench,workspace,{'c0001:prove':'by simp',
                'c0002:prove':'by simp'})
            adapter.observer.audit_workspace_snapshot=None

            class TwoContractNative(Native):
                def _check(self,source,theory,*,timeout):
                    log=self.work_dir/(theory+'.log')
                    log.write_text('VERO_RESULT c0001 prove clean\n'
                        'VERO_RESULT c0002 prove clean\n')
                    return {'returncode':0,'wall_ms':1000,
                        'source':str(self.work_dir/(theory+'.thy')),
                        'log':str(log)}

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',
                    TwoContractNative):
                adapter.check(workspace,PARENT)
            frozen=root/'immutable-raw'
            for path in frozen.glob('*.thy'):path.unlink()
            for path in workspace.glob('*.thy'):
                shutil.copy2(path,frozen/path.name)
            adapter.observer.audit_workspace_snapshot=frozen

            class SwapLaterPolarity(TwoContractNative):
                def _check(self,source,theory,*,timeout):
                    result=super()._check(source,theory,timeout=timeout)
                    path=workspace/(PARENT+'.thy')
                    text=path.read_text()
                    for polarity,replacement in [('prove','oops'),
                            ('disprove','by simp')]:
                        start=f'(* VERO START c0002 {polarity} *)'
                        end=f'(* VERO END c0002 {polarity} *)'
                        first=text.index(start)+len(start)
                        last=text.index(end,first)
                        text=text[:first]+'\n'+replacement+'\n'+text[last:]
                    path.write_text(text)
                    return result

            with patch('regen.execution.vero_checker_adapter.IsabelleChecker',
                    SwapLaterPolarity):
                with self.assertRaisesRegex(ValueError,'outside seal'):
                    adapter.check(workspace,PARENT)

            self.assertEqual(set(adapter.observer.accepted_receipts),{'c0001'})
            invalid=[row['data']['target_id'] for row in ledger.rows
                if row['kind']=='contract_green_invalidated']
            self.assertEqual(invalid,['c0002'])
            self.assertEqual(experiment_summary(ledger)['historical_first_green_contract_ids'],
                ['c0001','c0002'])


if __name__=='__main__':
    unittest.main()
