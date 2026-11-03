"""Real journal/visibility/query paths with synthetic checked proof fixtures."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from regen.execution.generated_discovery import prepare_discovery
from regen.execution.proof_commits import durable
from regen.execution.proof_session_runner import publish_generated_records
from regen.planning.batch_file_planner import batch_query, query_binding, execute_query, action_record
from regen.tests.current import test_premise_visibility as visibility_fixtures
from regen.tests.current import test_premise_database_v2 as database_fixtures


class RepositoryGeneratedRecallTests(unittest.TestCase):
    def setUp(self):
        self.f=visibility_fixtures.PremiseVisibilityTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.db=database_fixtures.PremiseDatabaseV2Tests();self.db.setUp();self.addCleanup(self.db.doCleanups)
        self.f.run['premise_database']=str(self.db.db)
        durable(self.f.root/'run.json',self.f.run)
        self.root=self.f.root
        self.f.prove('Source');self.f.seal('Source')
        self.publish()
        self.number=0

    def publish(self, extra=()):
        records=[]
        for name in self.f.packages:
            state=self.f.logs[name].replay(repair=False)
            if state['status']=='sealed':
                records.extend(publish_generated_records(self.root,state['graph'],state['seal_id'],
                    self.f.packages[name],records_only=True))
        durable(self.root/'generated-premises.json',{'records':records+list(extra)})
        return records

    def query(self, *, structural=False, discovery=None, purpose='helper', indexed=True):
        self.number+=1
        directory=self.root/'theories/Target/batch-requests'/f'{self.number:06d}'
        directory.mkdir(parents=True)
        durable(directory.parent.parent/'sealed-premises.json',{'records':[]})
        state=self.f.logs['Target'].replay(repair=False)
        request={'goal_query':True,'context_node_id':'target_contract','scope':'sealed',
                 'retrieval_policy':'indexed-v1','candidate_budget':20,'purpose':purpose,
                 'structural_rerank':structural}
        if not indexed:
            request.pop('retrieval_policy');request.pop('candidate_budget')
        result=batch_query(self.f.run,self.f.packages['Target'],state,Mock(),directory,request,
                           **({'_discovery':discovery} if discovery else {}))
        return result,directory,state,request

    def test_unimported_sealed_fact_is_discoverable_with_exact_provider_and_import(self):
        page,_,_,_=self.query()
        self.assertEqual(len(page['records']),1)
        record=page['records'][0]
        self.assertEqual(record['provider_theory'],'Source')
        self.assertEqual(record['import_name'],'Generated_Refine.Regen_Source')
        self.assertEqual(record['provider_seal_id'],self.f.logs['Source'].replay(repair=False)['seal_id'])
        self.assertTrue(record['requires_import'])
        self.assertFalse(record['can_use_as_current_premise'])
        self.assertFalse(record['is_proof_authority'])
        self.assertEqual(record['availability'],'sealed_requires_import')

    def test_structural_matching_skips_unimported_candidates_then_uses_accepted_import(self):
        with patch('regen.execution.premise_structure.rerank') as native:
            page,_,_,_=self.query(structural=True)
            native.assert_not_called()
        self.assertEqual(page['records'][0]['rerank_evidence']['status'],'requires_import')
        self.f.change_imports('Target',['Source'])
        def matched(checker,context,records):
            self.assertEqual([r['provider_theory'] for r in records],['Source'])
            return {'ordered_record_ids':[r['record_id'] for r in records],
                    'evidence':{r['record_id']:{'status':'conclusion_match','is_proof_authority':False} for r in records}}
        with patch('regen.execution.premise_structure.rerank',side_effect=matched) as native:
            page,_,_,_=self.query(structural=True)
            native.assert_called_once()
        self.assertFalse(page['records'][0]['requires_import'])
        self.assertTrue(page['records'][0]['can_use_as_current_premise'])
        self.f.change_imports('Target',[])
        page,_,_,_=self.query()
        self.assertTrue(page['records'][0]['requires_import'])

    def test_catalog_alone_cannot_certify_an_unsealed_or_tampered_fact(self):
        self.f.prove('Third')
        state=self.f.logs['Third'].replay(repair=False)
        forged=publish_generated_records(self.root,state['graph'],'forged-seal',
            self.f.packages['Third'],records_only=True)
        records=self.publish(extra=forged)
        wrong=deepcopy(records[0]);wrong['exact_statement']='lemma source_contract: "True & True"'
        durable(self.root/'generated-premises.json',{'records':[wrong,*forged]})
        page,_,_,_=self.query()
        self.assertEqual(page['records'],[])
        self.assertEqual(page['candidate_pipeline']['generated']['unverified_candidates_rejected'],2)

    def test_new_seal_changes_query_identity_and_old_snapshot_stays_exact(self):
        _,directory,state,request=self.query()
        snapshot=prepare_discovery(self.f.run,self.f.packages['Target'],directory,request)
        before=query_binding(self.f.run,state,directory,request,generated_discovery=snapshot.binding)
        self.f.prove('Third');self.f.seal('Third');self.publish()
        after=query_binding(self.f.run,state,directory,request)
        self.assertNotEqual(before['generated_discovery'],after['generated_discovery'])
        old,_,_,_=self.query(discovery=snapshot)
        self.assertEqual([r['provider_theory'] for r in old['records']],['Source'])
        latest,_,_,_=self.query()
        self.assertEqual({r['provider_theory'] for r in latest['records']},{'Source','Third'})

    def test_proof_example_discovery_retains_certified_body_without_import_authority(self):
        page,_,_,_=self.query(purpose='proof_example')
        self.assertEqual(page['records'][0]['proof_body'],'by simp')
        self.assertTrue(page['records'][0]['requires_import'])
        self.assertFalse(page['records'][0]['can_use_as_current_premise'])

    def test_default_query_also_bounds_repository_generated_candidates(self):
        page,_,_,_=self.query(indexed=False)
        self.assertTrue(page['records'][0]['requires_import'])
        self.assertLessEqual(page['candidate_pipeline']['generated']['vector_candidates_scored'],100)

    def test_saved_query_is_reused_until_a_new_provider_seal_changes_discovery(self):
        from regen.planning import batch_file_planner
        _,directory,state,request=self.query()
        request.pop('structural_rerank')
        first=execute_query(self.f.run,self.f.packages['Target'],state,Mock(),directory,request)
        durable(directory/'feedback.json',first,immutable=True)
        proposal={'action':'query','request':request}
        state['batch_history']=[action_record(proposal,first,1,state=state)]
        state['batch_facts']=first['records']
        following=directory.with_name('000002');following.mkdir()
        with patch.object(batch_file_planner,'batch_query',side_effect=AssertionError('cached query must not rerun')):
            cached=execute_query(self.f.run,self.f.packages['Target'],state,Mock(),following,request)
        self.assertEqual(cached['query_identity'],first['query_identity'])
        self.assertEqual(cached['query_progress']['reused_request'],1)
        self.f.prove('Third');self.f.seal('Third');self.publish()
        newest=directory.with_name('000003');newest.mkdir()
        result=execute_query(self.f.run,self.f.packages['Target'],state,Mock(),newest,request)
        self.assertNotEqual(result['query_identity'],first['query_identity'])
        self.assertEqual({r['provider_theory'] for r in result['records']},{'Source','Third'})
