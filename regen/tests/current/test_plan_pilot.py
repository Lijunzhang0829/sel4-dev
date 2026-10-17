import copy
import json
from pathlib import Path
import tempfile
import unittest
from regen.planning.plan_pilot import PlanPilot, select_targets
from regen.tests.current.test_workspace_planning import WorkspacePlanningTest


class PlanPilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {'schema_version': 6, 'theories': [
            {'name': 'A', 'contract_ids': ['a', 'b'], 'required_imports': []},
            {'name': 'B', 'contract_ids': ['c'], 'required_imports': []},
            {'name': 'C', 'contract_ids': ['d'], 'required_imports': [{'theory': 'A', 'reason': 'needed'}]}]}
        self.payload = {'contracts': [{'contract_id': x} for x in 'abcd']}
        self.calls = []
        def attempt(c,h,o,r,p):
            self.calls.append(c['contract_id']);return {'status': 'proved', 'proof': 'by simp'}
        self.pilot = PlanPilot(self.root, {'input': 'frozen'}, {}, attempt)

    def test_selection_spreads_roots_and_never_assumes_providers(self):
        rows, skipped = select_targets(self.plan, 3)
        self.assertEqual([r['contract_id'] for r in rows], ['a', 'c', 'b'])
        self.assertEqual(skipped, ['C'])

    def test_cached_exact_binding_no_publication_and_review_required(self):
        with self.assertRaises(ValueError): self.pilot.submission(self.plan,'ok')
        r=self.pilot.run(self.payload,self.plan)
        self.assertEqual(r['production_green'],[]);self.assertFalse(r['publishable'])
        self.assertEqual(self.pilot.run(self.payload,self.plan),r);self.assertEqual(len(self.calls),3)
        with self.assertRaises(ValueError):self.pilot.submission(self.plan,'')
        self.assertTrue(self.pilot.submission(self.plan,'retain; only root startability tested'))

    def test_revision_invalidates_and_limits_rounds(self):
        self.pilot.run(self.payload,self.plan)
        changed=copy.deepcopy(self.plan);changed['theories'][0]['name']='NewA'
        with self.assertRaises(ValueError):self.pilot.submission(changed,'retain')
        self.pilot.run(self.payload,changed)
        changed['theories'][0]['name']='Third'
        with self.assertRaisesRegex(ValueError,'budget exhausted'):self.pilot.run(self.payload,changed)

    def test_tampered_receipt_rejected(self):
        self.pilot.run(self.payload,self.plan)
        p=self.root/self.pilot.key(self.plan)/'result.json'
        r=json.loads(p.read_text());r['results']=[];p.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'binding mismatch'):self.pilot.load(self.plan)

    def test_interruption_remains_charged(self):
        def failed(*a):raise RuntimeError('crash')
        self.pilot.attempt=failed
        with self.assertRaises(RuntimeError):self.pilot.run(self.payload,self.plan)
        with self.assertRaisesRegex(ValueError,'Interrupted'):self.pilot.run(self.payload,self.plan)

    def test_failed_proof_does_not_infer_bad_plan(self):
        self.pilot.attempt=lambda *a:{'status':'proof_failed','diagnostic':'unsolved'}
        r=self.pilot.run(self.payload,self.plan)
        self.assertEqual(len(r['results'][0]['attempts']),2)
        self.assertFalse(r['architecture_quality_certified'])
        self.assertTrue(self.pilot.submission(self.plan,'retain, inconclusive proof failure'))

    def test_workspace_submission_gate_and_exact_revision(self):
        f=WorkspacePlanningTest();f.setUp();self.addCleanup(f.doCleanups)
        f.h.pilot=self.pilot
        self.assertFalse(f.h.dispatch({'action':'submit','proposal':f.proposal})['valid'])
        r=f.h.dispatch({'action':'pilot','proposal':f.proposal})
        self.assertEqual(r['production_green'],[])
        self.assertFalse(f.h.dispatch({'action':'submit','proposal':f.proposal})['valid'])
        r=f.h.dispatch({'action':'submit','proposal':f.proposal,'pilot_review':'retain; scope limited'})
        self.assertTrue(r['submitted']);f.h.artifacts()

    def test_success_requires_clean_dependency_audit(self):
        from regen.planning.plan_pilot import native_status
        checked={'verdict':'green','returncode':0}
        with self.assertRaises(ValueError):native_status(checked)
        path=self.root/'dependencies.json'
        checked['automation_audit']={'proof_dependencies':str(path)}
        path.write_text(json.dumps({'has_skip_proof':True}))
        with self.assertRaises(ValueError):native_status(checked)
        path.write_text(json.dumps({'has_skip_proof':False}))
        self.assertEqual(native_status(checked),'proved')
        self.assertEqual(native_status({'verdict':'red','returncode':124}),'check_timeout')
