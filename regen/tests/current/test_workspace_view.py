from copy import deepcopy
from types import SimpleNamespace
import unittest
from regen.planning.workspace_view import project, incremental


class WorkspaceViewTests(unittest.TestCase):
    def setUp(self):
        self.c = SimpleNamespace(run={'run_id':'r'}, theory='T')
        self.value = {'theory':'T', 'document':{'source':'exact statement', 'source_sha256':'s',
            'workflow':{'pending_failures':[{'entry_id':'a','failure_id':'f'}]}},
            'feedback':{'diagnostic':'exact failure'}, 'accepted_ids':[],
            'planning_review':{'obligations':[{'issue_id':'i','requires_generation_decision':True,
                'pending_contract_ids':['a'], 'issue':{'long':'context'}}]},
            'observations':{'query_memory':{'old':'evidence'}}, 'context_revision':0}

    def test_default_keeps_diagnostic_source_and_obligation_navigation(self):
        result = project(self.value)
        self.assertEqual(result['document']['source'], 'exact statement')
        self.assertEqual(result['feedback'],self.value['feedback'])
        self.assertTrue(result['planning_review']['obligations'][0]['requires_generation_decision'])
        self.assertNotIn('query_memory', result['observations'])
        self.assertEqual(project(self.value,section='observations')['content'],self.value['observations'])
        self.assertEqual(project(self.value,view='full'),self.value)

    def test_same_source_new_acceptance_and_context_are_not_suppressed(self):
        first = incremental(self.c,self.value,{})
        updated = deepcopy(self.value);updated['accepted_ids']=['a'];updated['context_revision']=1
        delta = incremental(self.c,updated,{'since_cursor':first['cursor']})
        self.assertEqual(delta['changed']['accepted_ids'], ['a'])
        self.assertEqual(delta['changed']['context_revision'], 1)
        self.assertNotIn('document',delta['changed'])

    def test_no_change_remove_and_null_have_distinct_semantics(self):
        first=incremental(self.c,self.value,{})
        same=incremental(self.c,self.value,{'since_cursor':first['cursor']})
        self.assertTrue(same['unchanged']);self.assertEqual(same['changed'],{})
        updated=deepcopy(self.value);del updated['observations'];updated['feedback']=None
        delta=incremental(self.c,updated,{'since_cursor':same['cursor']})
        self.assertEqual(delta['removed'],['observations']);self.assertIsNone(delta['changed']['feedback'])

    def test_restart_foreign_cursor_and_view_change_reset(self):
        cursor=incremental(self.c,self.value,{})['cursor']
        fresh=SimpleNamespace(run={'run_id':'r'},theory='T')
        self.assertTrue(incremental(fresh,self.value,{'since_cursor':cursor})['cursor_reset'])
        self.assertTrue(incremental(self.c,self.value,{'since_cursor':cursor,'view':'full'})['cursor_reset'])
        with self.assertRaises(ValueError):
            incremental(self.c,self.value,{'since_cursor':cursor,'if_source_sha256':'s'})

    def test_controller_full_section_and_delta_are_advertised_and_executable(self):
        from regen.tests.current.test_theory_document import DocumentControllerTests
        from regen.planning.parallel_file_planner import tools
        from jsonschema import Draft202012Validator
        f=DocumentControllerTests();f.setUp();self.addCleanup(f.doCleanups)
        schema=next(t['inputSchema'] for t in tools(True) if t['name']=='read_theory')
        first=f.read()
        request={'theory':'Target','since_cursor':first['cursor']}
        Draft202012Validator(schema).validate(request)
        delta=f.controller.handle('read_theory',request,{})
        self.assertEqual(delta['view_mode'],'delta');self.assertTrue(delta['unchanged'])
        full=f.controller.handle('read_theory',{'theory':'Target','view':'full'},{})
        section=f.controller.handle('read_theory',{'theory':'Target','section':'observations'},{})
        self.assertEqual(section['content'],full['observations'])
        self.assertEqual(full['document']['source'],first['document']['source'])


if __name__ == '__main__':unittest.main()
