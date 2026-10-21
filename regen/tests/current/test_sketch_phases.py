"""Phases preserve acceptance authority and retain bounded local retries."""
import unittest
from regen.tests.current.test_sketch_workflow import WorkflowTests
from regen.planning.sketch_workflow import probe_helpers, workflow_view, diagnose_failure, _draft
from regen.planning.sketch_phases import transition_proof, guard_attempt, formal_allowed

class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.base=WorkflowTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.store=self.base.store;self.store.require_phases=True
    def sha(self):return self.store.read()['source_sha256']
    def probe(self):return probe_helpers(self.base.checker,self.store,self.sha())['results'][0]
    def change(self,proof):
        from regen.planning.theory_document import parse,render
        from regen.planning.sketch_workflow import _draft
        entries,nodes,proofs=_draft(self.store,self.sha())
        source=self.store.path.read_text()
        old=proofs.get('helper','oops')
        marker='lemma helper: "True & True"\n(* REGEN-PROOF *)\n'
        self.base.save(source.replace(marker+old,marker+proof))
    def test_two_probes_then_formal_transition(self):
        self.change('by simp');self.base.checker.verdict='red'
        first=self.probe()
        with self.assertRaisesRegex(ValueError,'unchanged probe'):self.probe()
        self.change('by auto');second=self.probe()
        self.change('by blast')
        with self.assertRaisesRegex(ValueError,'allowance exhausted'):self.probe()
        result=transition_proof(self.store,self.sha(),second['failure_id'],'formal_leaf','Continue the same obligation as budgeted proof work')
        self.assertFalse(result['budget_reset']);self.assertEqual(result['new_green'],[])
        _,nodes,proofs=_draft(self.store,self.sha())
        self.assertTrue(formal_allowed(self.store,nodes,'helper'))
        guard_attempt(self.store,nodes,proofs,['helper'])
        with self.assertRaisesRegex(ValueError,'formal proof phase'):self.probe()
        self.assertNotIn('helper',self.store.state['proofs'])
    def test_one_failed_repair_requires_bound_escalation(self):
        first=self.base.fail('by simp')
        _,nodes,proofs=_draft(self.store,self.sha())
        with self.assertRaisesRegex(ValueError,'change the failed'):guard_attempt(self.store,nodes,proofs,['helper'])
        self.change('by auto')
        _,nodes,proofs=_draft(self.store,self.sha());guard_attempt(self.store,nodes,proofs,['helper'])
        from regen.planning.sketch_workflow import _failure
        second=_failure(self.store,nodes,'helper',proofs['helper'],{'verdict':'red'},'formal:test2')
        self.change('by blast');_,nodes,proofs=_draft(self.store,self.sha())
        with self.assertRaisesRegex(ValueError,'one local repair'):guard_attempt(self.store,nodes,proofs,['helper'])
        transition_proof(self.store,self.sha(),second,'revise_consumer','Replace the local proof structure')
        guard_attempt(self.store,nodes,proofs,['helper'])
        self.change('by fastforce');_,nodes,proofs=_draft(self.store,self.sha())
        with self.assertRaisesRegex(ValueError,'bound to the saved'):guard_attempt(self.store,nodes,proofs,['helper'])
    def test_transition_cannot_fake_child_or_changed_interface(self):
        f=self.base.fail()
        with self.assertRaisesRegex(ValueError,'saved child'):transition_proof(self.store,self.sha(),f,'decompose_helper','split')
        with self.assertRaisesRegex(ValueError,'changed helper'):transition_proof(self.store,self.sha(),f,'revise_interface','weaken')
