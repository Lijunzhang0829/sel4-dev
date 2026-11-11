"""Workflow gates are per new entry; native/global authority stays fail-closed."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import validator
from regen.planning.independent_document_checks import run_checks
from regen.planning.theory_document import render, block
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker


class IndependentAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.package,self.old=fixture();self.checker=Checker()
        self.source=render(self.package,self.old,self.checker.imports()).replace('oops','by simp')
        self.commits=[]

    def commit(self,new,evidence):
        old=self.commits[-1] if self.commits else self.old
        validator(self.package)(old,new,'document',evidence)
        self.commits.append(deepcopy(new));return new

    def test_blocked_helper_does_not_block_unrelated_fixed_contract(self):
        def authorize(state,key):
            if key=='helper':return {'status':'blocked_workflow','diagnostic':'stale decomposition review',
                'missing_prerequisites':['current_review'],'next_actions':[{'request':{'action':'review_sketch'}}]}
            return {'status':'ready','sketch_authorizations':[]}
        with tempfile.TemporaryDirectory() as tmp:
            result=run_checks(self.package,self.old,self.source,self.checker,tmp,
                commit=self.commit,evidence_origin={'sealable_generation':True},authorize_entry=authorize)
        self.assertEqual(result['new_green'],['contract'])
        self.assertEqual([r['status'] for r in result['node_results']],['blocked_workflow','audited_green'])
        self.assertEqual(result['independent_entries']['blocked_workflow_ids'],['helper'])
        self.assertEqual(result['independent_entries']['not_checked_ids'],['helper'])
        self.assertEqual(self.checker.calls,1)
        self.assertEqual(self.commits[0]['proofs'],{'contract':'by simp'})

    def test_all_workflow_blocked_has_no_native_verdict_or_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_checks(self.package,self.old,self.source,self.checker,tmp,
                commit=self.commit,evidence_origin={'sealable_generation':True},
                authorize_entry=lambda state,key:{'status':'blocked_workflow','diagnostic':'needs review',
                    'missing_prerequisites':['review'],'next_actions':[]})
        self.assertEqual(self.checker.calls,0);self.assertEqual(self.commits,[])
        self.assertEqual(result['check'],{})
        self.assertEqual(result['new_green'],[])
        self.assertTrue(all(r['verdict']=='not_checked' for r in result['node_results']))

    def test_global_source_mutation_fails_before_authorization_or_native(self):
        auth=[]
        with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'fixed contract statement'):
            run_checks(self.package,self.old,self.source.replace('lemma target: "True"','lemma target: "False"'),
                self.checker,tmp,commit=self.commit,evidence_origin={},
                authorize_entry=lambda state,key:auth.append(key))
        self.assertEqual(auth,[]);self.assertEqual(self.checker.calls,0)

    def test_unknown_authority_error_is_not_reclassified_as_workflow_block(self):
        def corrupt(state,key):raise ValueError('sketch certificate integrity mismatch')
        with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'integrity mismatch'):
            run_checks(self.package,self.old,self.source,self.checker,tmp,
                commit=self.commit,evidence_origin={},authorize_entry=corrupt)
        self.assertEqual(self.checker.calls,0);self.assertEqual(self.commits,[])


class WorkflowControllerIsolationTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current.test_proof_workspace_agent import ProofWorkspaceAgentTest
        from regen.execution.check_executor import CheckExecutor,NativeResources
        from regen.planning.parallel_file_planner import TheoryController
        self.owner=ProofWorkspaceAgentTest();self.owner.setUp();self.addCleanup(self.owner.doCleanups)
        self.owner.fresh_target()
        package=next(p for p in self.owner.packages if p['theory']=='Target')
        package['contracts'].append({'contract_id':'independent','name':'independent',
            'exact_statement':'lemma independent: "True | False"','contract_owned_context':[]})
        package['package_payload_sha256']=digest({k:v for k,v in package.items() if k!='package_payload_sha256'})
        durable(self.owner.root/'packages/Target.thy.json',package)
        inputs=json.loads((self.owner.root/'execution-input.json').read_text())
        inputs['package_hashes']['Target.thy']=package['package_payload_sha256']
        durable(self.owner.root/'execution-input.json',inputs)
        self.resources=NativeResources(1,1,1);self.executor=CheckExecutor(self.owner.root/'tasks',workers=1)
        self.addCleanup(self.resources.close);self.addCleanup(self.executor.close)
        self.controller=TheoryController(self.owner.root,'Target',resources=self.resources,executor=self.executor,
            document_mode=True,generation_strategy_name='sketch-reviewed-v1')
        self.addCleanup(self.controller.close)
        self.checkers,_=self.owner.local_runtime(self.controller);self.serial=0
        draft=self.controller.handle('read_theory',{'theory':'Target'},{})['document']
        helper={'id':'blocked_helper','name':'blocked_helper','role':'helper','context':[],
                'statement':'lemma blocked_helper: "True & True"'}
        position=draft['source'].index('(* REGEN-BEGIN')
        source=draft['source'][:position]+block(helper,'by simp')+draft['source'][position:]
        source=source.replace('lemma target_contract: "(0::nat)=0"\n(* REGEN-PROOF *)\noops',
            'lemma target_contract: "(0::nat)=0"\n(* REGEN-PROOF *)\nusing blocked_helper by simp')
        source=source.replace('lemma independent: "True | False"\n(* REGEN-PROOF *)\noops',
                              'lemma independent: "True | False"\n(* REGEN-PROOF *)\nby simp')
        self.group={'target_entry_id':'target_contract','helper_ids':['blocked_helper'],
                    'use_sites':[{'helper_id':'blocked_helper','consumer_line':1,'fact':'blocked_helper'}]}
        self.saved=self.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                             'source':source,'sketches':[self.group]})
        self.source=source

    def call(self,proposal):
        self.serial+=1
        return self.controller.handle('proof_action',{'theory':'Target','proposal':proposal},
            {'thread_id':'thread','turn_id':'turn','call_id':str(self.serial),
             'transcript_path':'synthetic-test','transcript_offset':1})

    def test_real_sketch_gate_blocks_one_helper_but_fixed_contract_is_accepted(self):
        result=self.call({'action':'check_theory','source_sha256':self.saved['source_sha256'],
                          'entry_ids':['blocked_helper','independent']})
        feedback=result['feedback']
        self.assertEqual(feedback['new_green'],['independent'])
        by_id={r['entry_id']:r for r in feedback['entry_results']}
        self.assertEqual(by_id['blocked_helper']['status'],'blocked_workflow')
        self.assertTrue(by_id['blocked_helper']['missing_prerequisites'])
        self.assertTrue(by_id['blocked_helper']['next_actions'])
        self.assertEqual(by_id['independent']['verdict'],'green')
        state=self.controller._state('Target')[1]
        self.assertEqual(set(state['proofs']),{'independent'})
        self.assertEqual(self.controller.handle('read_theory',{'theory':'Target'},{})['document']['source'],self.source)
        self.assertEqual(self.checkers['Target'].calls,2)  # Scaffold plus one isolated proof.

    def test_certificate_integrity_fault_stops_the_request_before_native(self):
        store,state=self.controller._document()
        from regen.planning.sketch_check import interface
        from regen.planning.theory_document import parse
        entries,proofs=parse(self.source,store.package,store.imports);nodes={n['id']:n for n in entries}
        signature=digest(interface(nodes,proofs,self.group))
        durable(store.root/'sketch-certificates'/(signature+'.json'),{'receipt_sha256':'bad'})
        result=self.call({'action':'check_theory','source_sha256':self.saved['source_sha256'],
                          'entry_ids':['blocked_helper','independent']})
        self.assertEqual(result['status'],'error')
        self.assertIn('integrity mismatch',result['diagnostic'])
        self.assertEqual(self.controller._state('Target')[1]['proofs'],{})
        self.assertEqual(self.checkers['Target'].calls,0)  # Global integrity guard precedes all native work.

if __name__=='__main__':unittest.main()
