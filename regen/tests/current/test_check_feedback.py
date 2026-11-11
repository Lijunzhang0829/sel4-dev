"""Model display tests; no native process or model is started."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.planning.batch_file_planner import compact_feedback, read_diagnostic
from regen.planning.check_feedback import model_feedback, readable_diagnostic


def yxml(text):
    # Entity/type markup like native Isabelle pretty output; deliberately much
    # larger than the useful goal, including metadata before the last subgoal.
    return '\x05\x06block\x05' + ''.join(
        '\x05\x06entity\x06name=' + ('type_metadata_' * 200) + '\x05' + line
        + '\x05\x06\x05' for line in text.splitlines(keepends=True)) + '\x05\x06\x05'


class CheckFeedbackTests(unittest.TestCase):
    def test_native_markup_is_removed_before_truncation(self):
        plain = 'Failed to finish proof\ngoal (2 subgoals):\n 1. A \\<Longrightarrow> B\n 2. C\nAt command "by"'
        raw = yxml(plain)
        self.assertGreater(len(raw), 12000)
        original = {'verdict':'red', 'diagnostic':raw, 'log_content':'startup\n'+raw,
                    'log':'/native/1.log', 'log_sha256':'unchanged', 'source_content':'lemma x: True by simp',
                    'source':'/native/1.thy', 'source_sha256':'source-hash',
                    'failure_stage':'consumer_proof', 'consumer_check_started':True}
        saved = deepcopy(original)
        for render in (compact_feedback, model_feedback):
            with self.subTest(render=render.__name__):
                view = render(original)
                self.assertEqual(view['diagnostic'], plain)
                self.assertNotIn('diagnostic_excerpt', view)
                self.assertEqual(view['diagnostic_rendering']['raw_sha256'], digest(raw))
                self.assertFalse(view['diagnostic_rendering']['incomplete'])
                self.assertEqual(view['log_sha256'], 'unchanged')
                self.assertEqual(view['source_sha256'], 'source-hash')
                self.assertEqual(view['failure_stage'], 'consumer_proof')
                self.assertTrue(view['consumer_check_started'])
                self.assertNotIn('log_content', view)
                self.assertNotIn('source_content', view)
        self.assertEqual(original, saved)

    def test_long_readable_goal_is_bounded_after_rendering_and_identity_is_separate(self):
        plain = 'Failed to apply initial proof method\n' + ('long goal\n' * 2000)
        raw = yxml(plain)
        view = model_feedback({'diagnostic':raw})
        self.assertEqual(view['diagnostic'], plain[:12000])
        self.assertEqual(view['diagnostic_excerpt']['total_chars'], len(plain))
        self.assertEqual(view['diagnostic_excerpt']['sha256'], digest(plain))
        self.assertEqual(view['diagnostic_rendering']['raw_sha256'], digest(raw))
        self.assertEqual(view['diagnostic_rendering']['raw_chars'], len(raw))
        self.assertEqual(model_feedback(view), view)

    def test_legacy_raw_excerpt_never_becomes_a_claim_of_complete_feedback(self):
        raw = yxml('first line\nsecond line\n')
        old = {'diagnostic':raw[:12000],
               'diagnostic_excerpt':{'incomplete':True, 'sha256':'full-raw-hash', 'total_chars':40000}}
        view = model_feedback(old)
        self.assertTrue(view['diagnostic_rendering']['source_incomplete'])
        self.assertTrue(view['diagnostic_rendering']['incomplete'])
        self.assertIsNone(view['diagnostic_rendering']['readable_chars'])
        self.assertEqual(view['diagnostic_rendering']['raw_sha256'], 'full-raw-hash')
        self.assertEqual(view['diagnostic_excerpt'], old['diagnostic_excerpt'])

    def test_structured_rule_observation_and_exact_declarations_survive(self):
        reuse = {'schema':'explicit-rule-observation-v1', 'status':'observed',
                 'goal':{'proposition':'x = (0 :: nat)', 'variables':[{'name':'x','type':'nat'}]},
                 'matching':{'status':'no_match'}}
        value = {'document':{'source':'whole source\n'*3000},
                 'entry':{'statement':'complete proposition\n'*3000},
                 'check':{'diagnostic':yxml('Failed to apply initial proof method'),
                          'native_proof_state_full':{'goals':['private raw state'], 'reuse_diagnostics':reuse}}}
        view = model_feedback(compact_feedback(value))
        self.assertEqual(view['document'], value['document'])
        self.assertEqual(view['entry'], value['entry'])
        self.assertEqual(view['check']['reuse_diagnostics'], reuse)
        self.assertNotIn('native_proof_state_full', view['check'])

    def test_nested_independent_checks_preserve_every_result_and_raw_reads_are_exact(self):
        value = {'status':'checked_independently', 'checks':[
            {'entry_ids':['first'], 'feedback':{'status':'failed', 'check':{
                'diagnostic':yxml('first error'), 'log_content':'full first', 'verdict':'red'}}},
            {'entry_ids':['second'], 'feedback':{'status':'accepted', 'check':{
                'diagnostic':'GREEN', 'log_content':'full second', 'verdict':'green'}}}],
            'checked_delta_ids':['first','second']}
        view = model_feedback(value)
        self.assertEqual(view['checked_delta_ids'], ['first','second'])
        self.assertEqual([c['feedback']['check']['verdict'] for c in view['checks']], ['red','green'])
        self.assertEqual(view['checks'][0]['feedback']['check']['diagnostic'], 'first error')
        self.assertEqual(model_feedback(value, raw=True), value)

    def test_explicit_bound_history_and_diagnostic_pages_round_trip(self):
        original = {'diagnostic':yxml('raw proof'), 'log_content':'complete log'}
        full = {'view':'full', 'bindings':{'sha256':digest(original)}, 'content_id':'saved', 'content':original}
        page = {'offset':17, 'end':21, 'total_chars':30000, 'sha256':'raw-sha', 'text':'\x05\x06bl'}
        value = {'observations':[{'result':full}], 'diagnostic_page':page, 'diagnostic_pages':[page]}
        self.assertEqual(model_feedback(value), value)
        self.assertEqual(compact_feedback(full), full)

    def test_raw_diagnostic_paging_is_unchanged(self):
        raw = yxml('Failed to finish proof\ngoal (1 subgoal):\n 1. P')
        feedback = {'check':{'diagnostic':raw}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root/'batch-requests/000001'; folder.mkdir(parents=True)
            path = folder/'feedback.json'; path.write_text(json.dumps(feedback))
            before = path.read_bytes()
            value = read_diagnostic(root, {'batch_history':[{'request_number':1,
                'feedback_sha256':digest(feedback)}]}, {'request_number':1,'offset':6,'limit':80})
            self.assertEqual(value['diagnostic_page']['text'], raw[6:86])
            self.assertEqual(value['diagnostic_page']['sha256'], digest(raw))
            self.assertEqual(model_feedback(value), value)
            self.assertEqual(path.read_bytes(), before)

    def test_plain_text_and_isabelle_escapes_are_unchanged(self):
        self.assertEqual(readable_diagnostic('P \\<longrightarrow> Q\n'), 'P \\<longrightarrow> Q\n')
        self.assertEqual(model_feedback({'diagnostic':'ordinary error'}), {'diagnostic':'ordinary error'})


class ControllerFeedbackBoundaryTests(unittest.TestCase):
    def setUp(self):
        from regen.tests.current import test_proof_workspace_agent as fixtures
        from regen.execution.check_executor import CheckExecutor, NativeResources
        from regen.planning.parallel_file_planner import TheoryController
        self.fixture = fixtures.ProofWorkspaceAgentTest(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        resources = NativeResources(1,1,1); executor = CheckExecutor(self.fixture.root/'tasks', workers=1)
        self.addCleanup(resources.close); self.addCleanup(executor.close)
        self.controller = TheoryController(self.fixture.root, 'Target', resources=resources,
                                           executor=executor, document_mode=True)
        self.addCleanup(self.controller.close); self.fixture.local_runtime(self.controller)

    def test_all_default_model_paths_cross_one_projection_boundary(self):
        raw = {'check':{'diagnostic':yxml('Failed to finish proof\n 1. pending'),
                        'log_content':'complete evidence', 'log':'/native/log', 'log_sha256':'hash'},
               'checked_delta_ids':['helper'], 'node_results':[{'node_id':'helper','status':'failed'}]}
        calls = [('read_theory', {'theory':'Target'})] + [
            ('proof_action', {'theory':'Target','proposal':{'action':action}})
            for action in ['check_theory','check_sketch','read_sketch','read_failure','read_entry']]
        for name, arguments in calls:
            with self.subTest(tool=name, action=arguments.get('proposal')), patch.object(
                    self.controller, '_handle', return_value=deepcopy(raw)):
                view = self.controller.handle(name, arguments, {})
                self.assertEqual(view['check']['diagnostic'], 'Failed to finish proof\n 1. pending')
                self.assertNotIn('log_content', view['check'])
                self.assertEqual(view['check']['log_sha256'], 'hash')
                self.assertEqual(view['entry_results'][0]['status'], 'failed')

    def test_explicit_raw_reads_skip_both_feedback_and_vocabulary_projection(self):
        raw = {'entry':{'node_id':'unchanged'},'check':{'diagnostic':yxml('original'),
                                                       'log_content':'complete evidence'}}
        for action in ['read_sketch','read_failure']:
            with self.subTest(action=action), patch.object(self.controller, '_handle', return_value=deepcopy(raw)):
                view = self.controller.handle('proof_action', {'theory':'Target',
                    'proposal':{'action':action,'view':'raw'}}, {})
                self.assertEqual(view, raw)

    def test_read_dispatch_raw_uses_existing_bound_readers_and_rejects_unknown_view(self):
        store = object(); raw = {'check':{'diagnostic':yxml('original')}}
        with patch.object(self.controller, '_document', return_value=(store, {})), patch(
                'regen.planning.sketch_check.read_receipt', return_value=raw) as reader:
            result = self.controller._handle('proof_action', {'theory':'Target','proposal':{
                'action':'read_sketch','receipt_id':'a'*32,'view':'raw'}}, {})
            reader.assert_called_once_with(store, 'a'*32, 'full')
            self.assertEqual(result, raw)
        with patch.object(self.controller, '_document', return_value=(store, {})), patch(
                'regen.planning.sketch_assessment.read_failure', return_value=deepcopy(raw)) as reader:
            result = self.controller._handle('proof_action', {'theory':'Target','proposal':{
                'action':'read_failure','failure_id':'failure','view':'raw'}}, {})
            self.assertEqual(result, raw)
            with self.assertRaisesRegex(ValueError, 'optional view'):
                self.controller._handle('proof_action', {'theory':'Target','proposal':{
                    'action':'read_failure','failure_id':'failure','view':'invalid'}}, {})


if __name__ == '__main__':
    unittest.main()
