from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.planning.theory_document import (DocumentStore,block,render,parse,candidate,
    baseline,check_document,validate_document_commit,source_hash)
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker
from regen.execution.repository_execution import validator
from regen.planning.parallel_file_planner import tools


class TheoryDocumentTests(unittest.TestCase):
    def setUp(self):
        self.package,self.old=fixture()
        self.checker=Checker()
        self.imports=self.checker.imports()
        self.source=render(self.package,self.old,self.imports)

    def accept(self,source,old=None):
        old=self.old if old is None else old
        new=candidate(self.package,old,source,self.imports)
        evidence=check_document(self.checker,old,new,source)
        evidence['sealable_generation']=True
        validate_document_commit(self.package,old,new,evidence)
        validator(self.package)(old,new,'document',evidence)
        return new,evidence

    def test_roundtrip_fixed_slots_and_no_model_graph_tools(self):
        nodes,proofs=parse(self.source,self.package,self.imports)
        self.assertEqual(proofs,{})
        self.assertEqual({n['id'] for n in nodes},{'helper','contract'})
        self.assertNotIn('submit_check',{t['name'] for t in tools(True)})
        self.assertIn('proof_action',{t['name'] for t in tools(True)})

    def test_local_file_order_replaces_dependency_edges(self):
        source=self.source.replace('oops','by simp',1).replace('oops','using helper by simp',1)
        new,evidence=self.accept(source)
        self.assertEqual(new['accepted_order'],['helper','contract'])
        self.assertTrue(all(not n['dependencies'] for n in new['graph']['nodes']))
        self.assertNotIn('oops',evidence['check']['source_content'])

    def test_unfilled_slots_never_enter_proof_source(self):
        source=self.source.replace('oops','by simp',1)
        new,evidence=self.accept(source)
        self.assertEqual(new['accepted_order'],['helper'])
        self.assertNotIn('lemma target',evidence['check']['source_content'])

    def test_rewrite_or_delete_green_helper_rechecks_consumers(self):
        source=self.source.replace('oops','by simp')
        old,_=self.accept(source)
        nodes={n['id']:n for n in old['graph']['nodes']}
        source=source.replace(block(nodes['helper'],'by simp'),'').replace('by simp','by blast')
        new,evidence=self.accept(source,old)
        self.assertEqual(evidence['document_check_mode'],'full')
        self.assertEqual(new['accepted_order'],['contract'])
        self.assertIn('by blast',evidence['check']['source_content'])
        self.assertNotIn('lemma helper',evidence['check']['source_content'])
        self.assertEqual(old['accepted_order'],['helper','contract'])
        with self.assertRaises(ValueError):validator(self.package)(old,new,'batch',evidence)
        tampered=deepcopy(evidence);tampered['document_check_mode']='append'
        with self.assertRaises(ValueError):validate_document_commit(self.package,old,new,tampered)

    def test_failed_rewrite_does_not_validate_or_mutate_accepted_state(self):
        source=self.source.replace('oops','by simp');old,_=self.accept(source)
        before=deepcopy(old);source=source.replace('by simp','by fail')
        new=candidate(self.package,old,source,self.imports)
        self.checker.verdict='red';evidence=check_document(self.checker,old,new,source)
        evidence['sealable_generation']=True
        with self.assertRaises(ValueError):validator(self.package)(old,new,'document',evidence)
        self.assertEqual(old,before)

    def test_publication_policy_rejects_helper_loss_but_old_history_replays(self):
        source=self.source.replace('oops','by simp')
        old,_=self.accept(source)
        nodes={n['id']:n for n in old['graph']['nodes']}
        source=source.replace(block(nodes['helper'],'by simp'),'')
        new,evidence=self.accept(source,old)  # historical semantics remain replayable
        evidence['document_publication_policy']='preserve-accepted-ids-v1'
        with self.assertRaisesRegex(ValueError,'cannot remove accepted'):
            validate_document_commit(self.package,old,new,evidence)

    def test_reorder_green_proofs_requires_full_check(self):
        source=self.source.replace('oops','by simp');old,_=self.accept(source)
        nodes={n['id']:n for n in old['graph']['nodes']}
        helper=block(nodes['helper'],'by simp');contract=block(nodes['contract'],'by simp')
        reordered=source.replace(helper+contract,contract+helper)
        new,evidence=self.accept(reordered,old)
        self.assertEqual(new['accepted_order'],['contract','helper'])
        self.assertEqual(evidence['document_check_mode'],'full')

    def test_fixed_contract_import_context_and_admission_guards(self):
        for source in [self.source.replace('lemma target: "True"','lemma target: "False"'),
                       self.source.replace('Semantic_Base.Semantic_Root','Bad'),
                       self.source.replace('oops','sorry',1),
                       self.source.replace('oops','oops\nby simp',1),
                       self.source.replace('oops','by simp\nlemma extra: "True" by simp',1),
                       self.source.replace('"role": "contract"','"role": "helper"')]:
            with self.subTest(source=source),self.assertRaises(ValueError):
                parse(source,self.package,self.imports)

    def test_native_not_cosmetic_heuristics_decides_helper_validity(self):
        source=self.source.replace('lemma helper: "True & True"','lemma helper: "True"').replace('oops','by simp')
        new,_=self.accept(source)
        self.assertEqual(len(new['proofs']),2)

    def test_document_validation_has_no_graph_planning_dependency(self):
        package=deepcopy(self.package)
        package.pop('repository_projection')
        source=self.source.replace('oops','by simp')
        with patch('regen.planning.proof_graph.build_graph',side_effect=AssertionError('legacy builder')):
            new=candidate(package,self.old,source,self.imports)
            from regen.planning.proof_graph import validate_graph
            validate_graph(new['graph'],package)
        self.assertEqual(new['graph']['declaration_index_version'],1)
        self.assertNotIn('contract_routes',new['graph'])
        nodes,_=parse(source,package,self.imports)
        self.assertTrue(all('dependencies' not in n and 'consumers' not in n for n in nodes))

    def test_document_index_rejects_injected_edges_routes_and_execution_order(self):
        from regen.planning.declaration_index import validate_index
        new=candidate(self.package,self.old,self.source,self.imports)
        for field in ('dependencies','consumers'):
            bad=deepcopy(new['graph']);bad['nodes'][0][field]=['contract']
            with self.assertRaises(ValueError):validate_index(bad,self.package)
        bad=deepcopy(new['graph']);bad['contract_routes']={'contract':'helper'}
        with self.assertRaises(ValueError):validate_index(bad,self.package)
        bad=deepcopy(new['graph']);bad['execution_order'].reverse()
        with self.assertRaises(ValueError):validate_index(bad,self.package)

    def test_model_view_hides_planner_edges_but_keeps_audit_dependencies(self):
        from regen.planning.declaration_index import public_view
        new=candidate(self.package,self.old,self.source,self.imports)
        value=public_view({'graph':new['graph'],'audit':{'dependencies':['Actual.fact']}})
        self.assertNotIn('graph',value)
        self.assertNotIn('dependencies',value['declaration_index']['entries'][0])
        self.assertEqual(value['audit']['dependencies'],['Actual.fact'])

    def test_previous_document_evidence_remains_replayable(self):
        source=self.source.replace('oops','by simp')
        new=candidate(self.package,self.old,source,self.imports,legacy=True)
        self.assertNotIn('declaration_index_version',new['graph'])
        evidence=check_document(self.checker,self.old,new,source)
        evidence['sealable_generation']=True
        validate_document_commit(self.package,self.old,new,evidence)
        validator(self.package)(self.old,new,'document',evidence)

    def test_persistent_draft_stale_edit_and_exact_version_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=DocumentStore(tmp,self.package,self.old,self.imports)
            original=store.read()
            saved=store.edit({'action':'edit_theory','base_sha256':original['source_sha256'],
                'edits':[{'old':'lemma helper: "True & True"\n(* REGEN-PROOF *)\noops',
                         'new':'lemma helper: "True & True"\n(* REGEN-PROOF *)\nby fail'}]}, {'origin':'test'})
            self.assertFalse(saved['checked'])
            self.assertIn('by fail',DocumentStore(tmp,self.package,self.old,self.imports).read()['source'])
            with self.assertRaisesRegex(ValueError,'stale'):
                store.edit({'action':'edit_theory','base_sha256':original['source_sha256'],'source':self.source},{})
            with self.assertRaises(ValueError):store.checked_source(original['source_sha256'])
            self.assertEqual(source_hash(store.checked_source(saved['source_sha256'])),saved['source_sha256'])
            self.assertEqual(len(list((Path(tmp)/'versions').glob('*/source.json'))),2)

    def test_evidence_rejects_wrong_source_and_proof(self):
        source=self.source.replace('oops','by simp');new,evidence=self.accept(source)
        for field,value in [('document_source',source.replace('by simp','by blast')),
                            ('document_old_binding','wrong')]:
            bad=deepcopy(evidence);bad[field]=value
            with self.assertRaises(ValueError):validate_document_commit(self.package,self.old,new,bad)
        bad=deepcopy(new);bad['proofs']['contract']='by blast'
        with self.assertRaises(ValueError):validate_document_commit(self.package,self.old,bad,evidence)


class DocumentControllerTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current.test_proof_workspace_agent import ProofWorkspaceAgentTest
        from regen.execution.check_executor import CheckExecutor,NativeResources
        from regen.planning.parallel_file_planner import TheoryController
        self.fixture=ProofWorkspaceAgentTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.resources=NativeResources(1,1,1);self.executor=CheckExecutor(self.fixture.root/'tasks',workers=1)
        self.addCleanup(self.resources.close);self.addCleanup(self.executor.close)
        self.controller=TheoryController(self.fixture.root,'Target',resources=self.resources,
            executor=self.executor,document_mode=True)
        self.addCleanup(self.controller.close)
        self.checkers,_=self.fixture.local_runtime(self.controller)
        self.serial=0

    def call(self,proposal):
        self.serial+=1
        return self.controller.handle('proof_action',{'theory':'Target','proposal':proposal},
            {'thread_id':'t','turn_id':'u','call_id':str(self.serial),'transcript_path':'synthetic-test-transcript'})

    def read(self):
        return self.controller.handle('read_theory',{'theory':'Target'},{})

    def test_hot_projection_matches_full_reconstruction_without_global_scan(self):
        from regen.execution import repository_execution as execution
        draft = self.read()['document']
        saved = self.call({'action':'edit_theory', 'base_sha256':draft['source_sha256'],
                           'edits':[{'old':'oops', 'new':'by simp'}]})
        self.call({'action':'check_theory', 'source_sha256':saved['source_sha256']})
        root = self.fixture.root
        state = self.controller._state('Target')[1]
        execution.progress(root)  # Seed a fully verified, rebuildable display.
        with patch.object(execution, 'journal', side_effect=AssertionError('global replay')):
            hot = execution.publish_theory_progress(root, 'Target', state)
        self.assertEqual(hot, execution.progress(root))
        execution._PROGRESS_SNAPSHOTS.pop(str(root.resolve()), None)
        self.assertEqual(execution.publish_theory_progress(root, 'Target', state), hot)

    def test_baseline_does_not_add_selection_document_replay(self):
        with patch.object(self.controller, '_document', wraps=self.controller._document) as document:
            result = self.read()
        self.assertEqual(document.call_count, 1)
        self.assertNotIn('helper_selection',result)

    def test_document_rejects_legacy_mutation_and_reuse_receipt_tools(self):
        self.assertNotIn('prepare_reuse',{t['name'] for t in tools(True)})
        with self.assertRaisesRegex(ValueError,'legacy graph'):
            self.call({'action':'submit_batch','proofs':{'contract':'by simp'}})
        with self.assertRaisesRegex(ValueError,'check_theory'):
            self.controller.handle('submit_check',{}, {})
        with self.assertRaisesRegex(ValueError,'read_generated'):
            self.controller.handle('prepare_reuse',{}, {})
        self.assertFalse(self.checkers)

    def test_fresh_document_initializes_without_legacy_builder(self):
        self.fixture.fresh_target()
        # The fixture deletes its seeded journal; isolate its process-local replay cache.
        from collections import OrderedDict
        self.fixture.stack.enter_context(patch('regen.execution.proof_commits._VERIFIED_PREFIXES', OrderedDict()))
        with patch('regen.planning.proof_graph.build_graph',side_effect=AssertionError('legacy builder')), \
             patch('regen.planning.batch_file_planner.build_graph',side_effect=AssertionError('legacy builder')):
            draft=self.read()['document']
            saved=self.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                             'edits':[{'old':'oops','new':'by simp'}]})
            result=self.call({'action':'check_theory','source_sha256':saved['source_sha256']})
            self.assertIn('feedback',result,result)
            self.assertEqual(result['feedback']['status'],'accepted',result)
        state=self.controller._state('Target')[1]
        self.assertEqual(state['graph']['declaration_index_version'],1)

    def test_edit_check_rewrite_failure_and_seal_current_source_gate(self):
        before=self.read();draft=before['document']
        self.assertNotIn('input',before)
        self.assertEqual(self.checkers['Target'].calls,0)
        saved=self.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                         'edits':[{'old':'oops','new':'by simp'}]})
        self.assertEqual(saved['status'],'draft_saved')
        self.assertEqual(self.checkers['Target'].calls,0)
        accepted=self.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        self.assertEqual(accepted['feedback']['status'],'accepted',accepted)
        _,state=self.controller._state('Target')
        self.assertEqual(state['document_source_sha256'],saved['source_sha256'])
        self.assertIn('document_order',state['graph'])
        self.checkers['Target'].verdict='red'
        saved=self.call({'action':'edit_theory','base_sha256':saved['source_sha256'],
                         'edits':[{'old':'by simp','new':'by fail'}]})
        failed=self.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        self.assertEqual(failed['feedback']['status'],'rejected_batch',failed)
        self.assertEqual(self.controller._state('Target')[1]['proofs'],state['proofs'])
        self.assertIn('by fail',self.read()['document']['source'])
        with self.assertRaisesRegex(ValueError,'seal requires'):
            self.call({'action':'finish','source_sha256':saved['source_sha256']})
        self.fixture.llm.assert_not_called()

    def test_green_diagnostic_draft_cannot_remove_accepted_proof(self):
        draft=self.read()['document']
        saved=self.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                         'edits':[{'old':'oops','new':'by simp'}]})
        self.assertEqual(self.call({'action':'check_theory',
            'source_sha256':saved['source_sha256']})['feedback']['status'],'accepted')
        before=deepcopy(self.controller._state('Target')[1])
        diagnostic=self.call({'action':'edit_theory','base_sha256':saved['source_sha256'],
                             'edits':[{'old':'by simp','new':'oops'}]})
        checked=self.call({'action':'check_theory','source_sha256':diagnostic['source_sha256']})
        feedback=checked['feedback']
        self.assertEqual(feedback['check']['verdict'],'green',checked)
        self.assertEqual(feedback['status'],'draft_checked',checked)
        self.assertFalse(feedback['accepted'])
        self.assertEqual(feedback['new_green'],[])
        after=self.controller._state('Target')[1]
        for key in ('proofs','accepted_order','graph','document_source_sha256'):
            self.assertEqual(after[key],before[key],key)
        self.assertEqual(self.read()['document']['source_sha256'],diagnostic['source_sha256'])
        self.assertEqual(self.read()['accepted_source_sha256'],saved['source_sha256'])
        # Reconstruct projections from the durable journal, not process cache.
        from collections import OrderedDict
        with patch('regen.execution.proof_commits._VERIFIED_PREFIXES',OrderedDict()):
            recovered=self.controller._state('Target')[1]
            self.assertEqual(recovered['proofs'],before['proofs'])
            self.assertEqual(recovered['document_source_sha256'],saved['source_sha256'])
        with self.assertRaisesRegex(ValueError,'seal requires'):
            self.call({'action':'finish','source_sha256':diagnostic['source_sha256']})
        restored=self.call({'action':'edit_theory','base_sha256':diagnostic['source_sha256'],
                           'edits':[{'old':'oops','new':'by simp'}]})
        self.assertEqual(self.call({'action':'check_theory',
            'source_sha256':restored['source_sha256']})['feedback']['status'],'accepted')

if __name__=='__main__':unittest.main()
