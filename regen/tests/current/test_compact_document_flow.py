"""Compact baseline interface: exact source, paginated recall, no native/LLM calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from regen.planning.document_recall import context_index_page, document_observations
from regen.planning.parallel_file_planner import generation_strategy, tools
from regen.planning.theory_document import block


class ContextIndexTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            'recalled_fact_index':[{'record_id':str(i),'canonical_name':'fact_'+str(i),
                                   'source_class':'base','theory':'Main'} for i in range(213)],
            'query_index':[{'query_id':str(i),'context_node_id':'contract',
                            'task_ids':['not-needed'],'count':3} for i in range(107)],
            'planner_notes':'A precise persisted failure note, not synthetic guidance.',
            'query_memory':[], 'target_memory':{}, 'requested_attempts':[]}
        self.state = {'graph':{'theory':'T','run_id':'r'},'batch_history':[]}

    def test_default_read_is_bounded_and_all_addresses_remain_retrievable(self):
        before = deepcopy(self.context)
        view = document_observations(self.context,self.state)
        self.assertEqual(view['planner_notes'],before['planner_notes'])
        for kind,key,total in [('facts','recalled_fact_index',213),('queries','query_index',107)]:
            self.assertEqual(len(view[key]),32)
            self.assertEqual(view['indexes'][kind]['total'],total)
            self.assertTrue(view['indexes'][kind]['has_more'])
            rows = deepcopy(view[key])
            action = view['indexes'][kind]['next_action']
            while action:
                page = context_index_page(self.context,action)
                rows.extend(page['records'])
                action = page['next_action']
            expected = [{k:v for k,v in row.items() if k not in {'task_ids','count'}}
                        for row in before[key]]
            self.assertEqual(rows,expected)
        self.assertEqual(self.context,before)

    def test_stale_page_is_explicitly_rejected_and_invalid_limits_do_not_expand(self):
        first = context_index_page(self.context,{'index':'facts'})
        self.context['recalled_fact_index'].append({'record_id':'new'})
        with self.assertRaisesRegex(ValueError,'snapshot changed'):
            context_index_page(self.context,first['next_action'])
        for value in [0,101,True,-1]:
            with self.subTest(limit=value),self.assertRaises(ValueError):
                context_index_page(self.context,{'index':'facts','limit':value})
        with self.assertRaises(ValueError):
            context_index_page(self.context,{'index':'facts','source':'unapproved'})

    def test_empty_or_final_page_is_explicit(self):
        empty = context_index_page({}, {'index':'queries'})
        self.assertEqual(empty['records'],[])
        self.assertEqual(empty['total'],0)
        self.assertFalse(empty['has_more'])
        self.assertIsNone(empty['next_action'])


class CompactControllerTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        self.base=DocumentControllerTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.controller=self.base.controller

    def test_source_hash_opt_in_omits_only_unchanged_source_and_keeps_feedback(self):
        full=self.base.read()
        draft=full['document']
        unchanged=self.controller.handle('read_theory',{'theory':'Target',
            'if_source_sha256':draft['source_sha256']},{})
        self.assertNotIn('source',unchanged['document'])
        self.assertTrue(unchanged['document']['source_unchanged'])
        self.assertEqual(unchanged['feedback'],full['feedback'])
        self.assertEqual(unchanged['accepted_ids'],full['accepted_ids'])
        self.assertEqual(unchanged['observations'],full['observations'])
        self.assertEqual(self.base.read()['document']['source'],draft['source'])
        changed=self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
            'edits':[{'old':'oops','new':'by simp'}]})
        reread=self.controller.handle('read_theory',{'theory':'Target',
            'if_source_sha256':draft['source_sha256']},{})
        self.assertIn('source',reread['document'])
        self.assertEqual(reread['document']['source_sha256'],changed['source_sha256'])
        self.assertNotIn('source_unchanged',reread['document'])

    def test_page_tool_does_not_open_native_runtime_or_dispatch_a_check(self):
        before=len(self.controller.executor.records)
        with patch.object(self.controller,'_document',side_effect=AssertionError('native initialization')):
            result=self.base.call({'action':'read_context_index','index':'facts','limit':1})
        self.assertEqual(result['status'],'available')
        self.assertIn('total',result)
        self.assertEqual(len(self.controller.executor.records),before)

    def test_initial_view_only_assigned_theory_but_full_directory_is_on_demand(self):
        with patch.object(self.controller,'list_workspace',side_effect=AssertionError('global catalog scan')):
            view=self.controller.handle('list_workspace',{'assigned_only':True},{})
        self.assertEqual(view['assigned_theory'],'Target')
        self.assertNotIn('theories',view)
        self.assertNotIn('check_tasks',view)
        self.assertEqual(view['contract_count'],1)
        self.assertIn('theories',self.controller.handle('list_workspace',{},{}))

    def test_batch_helpers_and_contract_check_directly_without_any_selection(self):
        draft=self.base.read()['document']
        helper=block({'id':'fresh_helper','role':'helper','context':[],
                      'statement':'lemma fresh_helper: "True"'},'by simp')
        source=draft['source'].replace('(* REGEN-BEGIN ',helper+'(* REGEN-BEGIN ',1).replace('oops','by simp')
        saved=self.base.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source})
        with patch('regen.planning.helper_selection.require_attempt',side_effect=AssertionError('retired gate')), \
             patch('regen.planning.helper_selection.current_selection',side_effect=AssertionError('retired advice')):
            result=self.base.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        self.assertEqual(result['feedback']['status'],'accepted')
        state=self.controller._state('Target')[1]
        self.assertEqual(state['accepted_order'],['fresh_helper','target_contract'])
        self.assertNotIn('helper_selection',result)


class InterfaceCompatibilityTests(unittest.TestCase):
    def test_document_only_optional_hash_schema_and_retired_strategy(self):
        current=next(t for t in tools(True) if t['name']=='read_theory')
        validator=Draft202012Validator(current['inputSchema'])
        validator.validate({'theory':'T'})
        validator.validate({'theory':'T','if_source_sha256':'sha'})
        self.assertFalse(validator.is_valid({'theory':'T','if_source_sha256':''}))
        legacy=next(t for t in tools(False) if t['name']=='read_theory')
        self.assertNotIn('if_source_sha256',legacy['inputSchema']['properties'])
        with self.assertRaisesRegex(ValueError,'removed'):
            generation_strategy('and-or-helper-selection-v1')

    def test_current_and_exact_old_registries_audit_but_arbitrary_extension_does_not(self):
        from regen.infrastructure.workspace_transcript import approved_registry,_pre_delta_registry
        current=[{'type':'function',**t} for t in tools(True)]
        old=_pre_delta_registry(current)
        self.assertTrue(approved_registry(current))
        self.assertTrue(approved_registry(old))
        changed=deepcopy(old)
        next(t for t in changed if t['name']=='read_theory')['inputSchema']['properties']['hidden']={'type':'string'}
        self.assertFalse(approved_registry(changed))


if __name__ == '__main__':
    unittest.main()
