from copy import deepcopy
from pathlib import Path
import unittest
from regen.tests.current import test_repository_planner as fixtures
from regen.planning import repository_planner as planner
from regen.execution.repository_execution import projection_package, init_run, progress
from regen.planning.file_planner import planning_environment, planner_prompt

class TheoryPlanTest(unittest.TestCase):
    def setUp(self):
        f=fixtures.RepositoryPlannerTest();f.policy_file=Path(__file__).parents[2]/'config/repository-planning-policy.json';f.setUp()
        self.addCleanup(f.doCleanups);self.f=f
        a,b=[c['contract_id'] for c in f.payload['contracts']]
        def theory(name,contracts,predecessors=(),intents=()):
            return {'name':name,'purpose':'Prove assigned results','grouping_reason':'Keep related operations together',
                    'contract_ids':contracts,'auxiliary_intents':list(intents),
                    'suggested_predecessors':[{'theory':p,'reason':'Reuse its established operations'} for p in predecessors],
                    'premise_ids':[]}
        self.proposal={'schema_version':3,'ordering_rationale':'Foundation first; prefer Zeta over Alpha among independent ready theories.',
            'theories':[theory('Zeta',[a],['Foundation']),theory('Alpha',[b]),theory('Foundation',[],intents=[{'id':'bridge','intent':'shared bridge','for_contracts':[a,b]}])]}

    def bundle(self,proposal=None):return planner.build_bundle(self.f.payload,proposal or self.proposal)

    def test_edges_override_priority_and_ready_ties_use_array(self):
        plan=self.bundle()['repository-plan.json']
        self.assertEqual(plan['priority_order'],['Zeta','Alpha','Foundation'])
        self.assertEqual(plan['theory_order'],['Alpha','Foundation','Zeta'])
        self.assertEqual(plan['theory_depths'],{'Alpha':0,'Foundation':0,'Zeta':1})
        self.assertNotIn('nodes',plan);self.assertNotIn('node_order',plan)
        self.assertEqual(planner.summary(plan, 'created')['auxiliary_intents'], 1)
        self.assertNotIn('auxiliary_nodes', planner.summary(plan, 'created'))
        p=deepcopy(self.proposal);p['theories'][0]['suggested_predecessors']=[]
        self.assertEqual(self.bundle(p)['repository-plan.json']['theory_order'],['Zeta','Alpha','Foundation'])

    def test_no_helpers_or_edges_is_valid_with_rationale(self):
        p=deepcopy(self.proposal);p['theories']=p['theories'][:2];p['theories'][0]['suggested_predecessors']=[]
        self.assertEqual(self.bundle(p)['repository-plan.json']['suggested_theory_edges'],[])
        p['ordering_rationale']=' '
        with self.assertRaisesRegex(ValueError,'rationale'):self.bundle(p)

    def test_reject_local_graph_and_statement_overrides(self):
        for field,value in [('nodes',[]),('dependencies',[]),('exact_statement','lemma forged: True')]:
            with self.subTest(field=field):
                p=deepcopy(self.proposal);p['theories'][0][field]=value
                with self.assertRaises(ValueError):self.bundle(p)
        p=deepcopy(self.proposal);p['theories'][2]['auxiliary_intents'][0]['suggested_dependencies']=[]
        with self.assertRaises(ValueError):self.bundle(p)

    def test_cycles_unknown_self_duplicate_and_missing_reason(self):
        variants=[ [{'theory':'Missing','reason':'x'}], [{'theory':'Zeta','reason':'x'}],
                   [{'theory':'Foundation','reason':''}], [{'theory':'Foundation','reason':'x'}]*2 ]
        for preds in variants:
            p=deepcopy(self.proposal);p['theories'][0]['suggested_predecessors']=preds
            with self.assertRaises(ValueError):self.bundle(p)
        p=deepcopy(self.proposal);p['theories'][2]['suggested_predecessors']=[{'theory':'Zeta','reason':'cycle'}]
        with self.assertRaisesRegex(ValueError,'cyclic'):self.bundle(p)

    def test_contract_coverage_unknown_premise_and_intent_target(self):
        p=deepcopy(self.proposal);p['theories'][1]['contract_ids']=p['theories'][0]['contract_ids']
        with self.assertRaisesRegex(ValueError,'exactly once'):self.bundle(p)
        p=deepcopy(self.proposal);p['theories'][0]['premise_ids']=['hidden']
        with self.assertRaises(ValueError):self.bundle(p)
        p=deepcopy(self.proposal);p['theories'][2]['auxiliary_intents'][0]['for_contracts']=['unknown']
        with self.assertRaises(ValueError):self.bundle(p)

    def test_projection_support_context_and_no_local_dependency_graph(self):
        artifacts=self.bundle();projection=artifacts['files/Foundation.json']
        p=projection_package(projection,artifacts['repository-plan.json'])
        self.assertEqual(len(p['support_contracts']),2);self.assertEqual(p['contracts'],[])
        self.assertNotIn('nodes',p['repository_projection'])
        text=planner_prompt(p,planning_environment(p));self.assertIn('auxiliary_intents',text);self.assertIn('shared bridge',text)
        z=artifacts['files/Zeta.json'];self.assertFalse(z['suggested_providers'][0]['usable_as_premise'])

    def test_audited_roundtrip_recovery_and_runner_init(self):
        summary,calls=self.f.call([self.proposal],allow=True);self.assertEqual(calls,1)
        projection=planner.load_file_projection(self.f.root,self.f.output,'Foundation');self.assertEqual(projection['schema_version'],3)
        path=self.f.output/'files/Zeta.json';path.unlink();self.f.call();self.assertTrue(path.exists())
        run_dir=self.f.root/'proof-run';init_run(run_dir,self.f.root,'theory-v3',self.f.output/'file-generation-list.json')
        status=progress(run_dir);self.assertEqual([f['theory'] for f in status['files']],['Alpha','Foundation','Zeta']);self.assertEqual(status['contracts'],2)

    def test_prompt_expresses_theory_boundary_and_priority(self):
        text=planner.prompt(self.f.payload)
        self.assertIn('THEORY level',text);self.assertIn('ARRAY ORDER',text)
        self.assertIn('Do not output contract/helper dependency edges',text)
