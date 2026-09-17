"""Rejected statements must never become active obligations."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from regen.tests.current import test_repository_planner as fixtures
from regen.execution import repository_execution as execution
from regen.planning.proof_graph import build_graph
from regen.execution.isabelle_node_checker import IsabelleChecker


class SimulatedCrash(RuntimeError): pass
class RevisionAccepted(RuntimeError): pass


class StatementRepairTest(unittest.TestCase):
    def exercise(self, *, crash=False, exhausted=False, python_rejection=False):
        f=fixtures.RepositoryPlannerTest();f.setUp();self.addCleanup(f.doCleanups);f.call([f.proposal],allow=True)
        root=f.root/'execution';run=execution.init_run(root,f.root,'r',f.output/'file-generation-list.json')
        package=execution.package_for(run,'Generated_Read.thy')
        graph=build_graph(package,{'generated_nodes':[]},run_id='r',revision=1)
        contract=graph['nodes'][0]['id']
        class Checker(IsabelleChecker):
            def _check(self,source_text,label,**kwargs):
                source=self.work_dir/(label+'.thy');source.write_text(source_text)
                log=self.work_dir/(label+'.log');log.write_text('test checker')
                bad='BROKEN_STATEMENT' in source_text
                return {'verdict':'red' if bad else 'green','returncode':1 if bad else 0,
                        'source':str(source),'log':str(log),'diagnostic':'Malformed command BROKEN_STATEMENT' if bad else ''}
        checker=Checker(f.root,root/'checks',heaps_dir=root,l4v_dir=f.fixture.l4v,semantic_workspace=root)
        checker._persistent_disabled=True
        calls=[]
        def revise(p,active,result,green,**kw):
            calls.append(deepcopy(kw['decision_context']))
            self.assertEqual(active['revision'],1)
            self.assertEqual(active['nodes'],graph['nodes'])
            self.assertEqual(kw['generated_records'],root/'theories/Generated_Read/sealed-premises.json')
            if len(calls)>1:
                rejection=calls[-1]['previous_revision_rejections'][0]
                self.assertEqual(rejection['check']['verdict'],'red')
                self.assertIn('Malformed command',rejection['check']['diagnostic'])
                self.assertEqual(rejection['base_revision'],1)
            if crash and len(calls)==2: raise SimulatedCrash()
            bad=exhausted or len(calls)==1
            if python_rejection and bad:
                (root/'theories/Generated_Read/graph-revisions'/f"decision-{kw['decision_index']:03d}").mkdir(parents=True,exist_ok=True)
                from regen.execution.graph_revision_agent import ProposalRejected
                raise ProposalRejected('Malformed command in generated graph',{'operation':'ADD','generated_nodes':[]})
            child={'id':'child','name':'child','role':'helper',
                   'statement':'lemma child: "BROKEN_STATEMENT"' if bad else 'lemma child: "True"',
                   'context':[],'dependencies':[],'strategy':'direct'}
            proposal={'generated_nodes':[child], 'contract_dependencies':{contract:['child']}}
            candidate=build_graph(p,proposal,run_id='r',revision=len(calls)+1)
            return candidate,{'decision':'REVISE_GRAPH','operation':'SPLIT','reason':'decompose','generated_nodes':[child]}
        options=dict(provider='codex',planner_model='model',revision_model='model',prover_model='model',
                     lemma_backend='whole-proof',timeout=5,max_attempts=1,max_node_rounds=3,max_node_revisions=3 if crash else 2)
        red={'status':'red','node_id':contract,'residual':'goal: True','attempts':[],'sealable_generation':True}
        with patch.object(execution,'checker_for',return_value=checker), \
             patch('regen.execution.proof_session_runner.frozen_role_model',return_value='model'), \
             patch('regen.planning.file_planner.plan_file',return_value=graph), \
             patch('regen.execution.lemma_prover.prove_node',side_effect=[red,RevisionAccepted()]) as prover, \
             patch('regen.execution.graph_revision_agent.revise_graph',side_effect=revise):
            if crash:
                with self.assertRaises(SimulatedCrash): execution.run_file(root,'Generated_Read',**options)
                pending=execution.journal(root,run,package).replay()
                self.assertEqual(pending['graph']['revision'],1)
                self.assertEqual(pending['revision_rejection']['candidate_revision'],2)
                self.assertEqual(prover.call_count,1)
            if exhausted:
                outcome=execution.run_file(root,'Generated_Read',**options)
                self.assertEqual(outcome['status'],'failed')
                self.assertEqual(prover.call_count,1)
            else:
                with self.assertRaises(RevisionAccepted): execution.run_file(root,'Generated_Read',**options)
                self.assertEqual(prover.call_count,2)
        state=execution.journal(root,run,package).replay()
        self.assertEqual(state['revisions'][contract],3 if crash else 2)
        if exhausted:
            self.assertEqual(state['graph']['revision'],1)
            self.assertIn('statement correction',state['reason'])
        else:
            self.assertEqual(state['graph']['revision'],4 if crash else 3)
            self.assertNotIn('revision_rejection',state)
            self.assertEqual(next(n for n in state['graph']['nodes'] if n['id']==contract)['statement'],graph['nodes'][0]['statement'])
        commits=[json.loads(p.read_text()) for p in (root/'theories/Generated_Read/acceptance/commits').glob('*.json')]
        self.assertFalse(any('BROKEN_STATEMENT' in json.dumps(c['state']['graph']) for c in commits))

    def test_rejected_statement_corrected_before_graph_commit(self): self.exercise()
    def test_crash_restores_rejection_and_charged_budget(self): self.exercise(crash=True)
    def test_repeated_invalid_statements_stop_at_budget(self): self.exercise(exhausted=True)

    def test_python_graph_rejection_returns_to_gra_without_another_proof_round(self): self.exercise(python_rejection=True)
    def test_python_graph_rejections_are_bounded(self): self.exercise(python_rejection=True,exhausted=True)
