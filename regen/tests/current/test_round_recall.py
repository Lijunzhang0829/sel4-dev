import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.execution.round_recall import select_round_facts
from regen.execution.observation_context import attempt_brief, prior_round_summary
from regen.execution.gra_input import gra_view
from regen.tests.current.test_observation_context import fact


class RoundRecallTest(unittest.TestCase):
    def test_one_batch_deduplicates_and_bounds_full_statements(self):
        def recall(db,query,**kwargs):
            return {'query':query,'records':[fact('shared')]+[fact(query+str(i)) for i in range(4)]}
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.premise_recall.recall_premises',side_effect=recall) as call:
            path=Path(tmp)/'facts.json'
            request={'premise_queries':[{'query':x} for x in 'abcd']}
            page=select_round_facts(None,request,audit_path=path)
            audit=json.loads(path.read_text())
            self.assertEqual(call.call_count,4)
            self.assertEqual(len(page['records']),12)
            self.assertEqual(len({r['record_id'] for r in page['records']}),12)
            self.assertEqual([r['record_id'] for r in page['records'][:5]],['shared','a0','b0','c0','d0'])
            for record in page['records']:
                self.assertEqual(record['exact_statement'],fact(record['record_id'])['exact_statement'])
            self.assertEqual(sum(len(p['records']) for p in audit['pages']),20)
            self.assertNotIn('exact_statement',json.dumps(page['queries']))
            with self.assertRaises(FileExistsError):
                select_round_facts(None,request,audit_path=path)
            self.assertEqual(json.loads(path.read_text()),audit)

    def test_old_attempts_are_briefs_and_old_facts_do_not_enter_gra(self):
        old='old proof '*1000
        attempt={'attempt':1,'proof_body':old,'check':{'diagnostic':'Failed to apply proof method\ngoal: old_symbol '+('very_large_goal '*1000)}}
        brief=attempt_brief(attempt)
        self.assertNotIn(old,json.dumps(brief))
        self.assertNotIn('very_large_goal very_large_goal',json.dumps(brief))
        self.assertIn('proof_failure',brief['feedback']['categories'])
        summaries=prior_round_summary({'round_id':1,'attempts':[attempt], 'residual':attempt['check']['diagnostic'],
            'previous_round_summaries':[{'round_id':0,'feedback':old,'observation_facts':[fact('stale')]}]})
        latest={'attempt':2,'proof_body':'by simp','check':{'diagnostic':'goal: latest_symbol'}}
        view=gra_view({'current_graph':{'revision':1,'nodes':[]},'failed_node':{
            'attempts':[attempt,latest],'observation_facts':[fact('stale')],
            'observation_pages':[{'records':[fact('stale')]}],'previous_round_summaries':summaries}})
        self.assertNotIn('stale',json.dumps(view))
        self.assertNotIn(old,json.dumps(view))
        self.assertEqual(view['recent_attempts'][0]['proof_body'],'by simp')
        self.assertEqual(view['attempt_summaries'][0],brief)
        self.assertEqual(view['gra_interaction_state']['fact_batches_remaining'],1)

    def test_statement_rejection_keeps_candidate_complete_and_marks_uncommitted(self):
        statement='lemma proposed: "True"'
        view=gra_view({'current_graph':{'revision':1,'nodes':[]},'failed_node':{},
            'decision_context':{'previous_revision_rejections':[{'base_revision':1,'candidate_revision':2,
                'proposal':{'operation':'SPLIT','generated_nodes':[{'statement':statement,'context':['context X']}], 'audit_only':'noise'},
                'check':{'verdict':'red','diagnostic':'Malformed command'}}]}})
        rejection=view['previous_revision_rejection']
        self.assertFalse(rejection['committed'])
        self.assertEqual(rejection['proposal']['generated_nodes'][0],{'statement':statement,'context':['context X']})
        self.assertEqual(rejection['feedback'],'Malformed command')
        self.assertNotIn('audit_only',rejection['proposal'])

    def test_resolved_expansion_is_metadata_and_facts_come_from_catalog(self):
        from unittest.mock import Mock
        resolver=Mock(return_value={'alias':{'query':'alias','status':'resolved','expanded_term':'Base.p True',
            'type':'bool => bool','constant_names':['Base.p'],'is_fact':False,'evidence_id':'checked'}})
        def recall(db,query,**kwargs):
            return {'query':query,'effective_mode':'definition','records':[fact('definition')] if query=='Base.p' else []}
        with tempfile.TemporaryDirectory() as tmp, patch('regen.execution.premise_recall.recall_premises',side_effect=recall):
            page=select_round_facts(None,{'premise_query':'alias','mode':'definition'},audit_path=Path(tmp)/'facts.json',symbol_resolver=resolver)
        resolver.assert_called_once_with(['alias'])
        self.assertEqual([r['record_id'] for r in page['records']],['definition'])
        self.assertFalse(page['queries'][0]['symbol_resolution']['is_fact'])
        self.assertEqual(page['queries'][0]['symbol_resolution']['expanded_term'],'Base.p True')

    def test_invalid_batch_does_not_freeze_or_write_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'facts.json'
            with self.assertRaises(ValueError):
                select_round_facts(None,{'premise_query':'name','scope':'invalid'},audit_path=path)
            self.assertFalse(path.exists())
