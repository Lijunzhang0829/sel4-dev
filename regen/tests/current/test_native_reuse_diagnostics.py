"""Optional native reuse evidence stays local and cannot alter proof verdicts."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.batch_checker import BatchWorker
from regen.execution.native_reuse_diagnostics import attach_observation, read_observation
from regen.execution.native_runtime import worker_source


OBSERVATION = '''<reuse_diagnostics schema="explicit-rule-observation-v1"
 status="observed" semantic_scope="current_goal_and_context_only" method="rule">
 <reference>generic_wrapper</reference>
 <goal><proposition>frame (s::'a store_scheme)</proposition>
  <type_variables><type_variable kind="free" name="'a"><class>HOL.type</class></type_variable>
   <type_variable kind="free" name="'e"><class>HOL.default</class></type_variable></type_variables>
  <variables><variable name="s"><type>'a store_scheme</type></variable></variables></goal>
 <facts><fact><proposition>frame (?s::unit store_scheme)</proposition>
  <type_variables><type_variable kind="schematic" name="'e" index="0"><class>HOL.default</class></type_variable></type_variables>
  <variables><variable name="s" index="0"><type>unit store_scheme</type></variable></variables></fact></facts>
 <chained_facts/>
 <matching kind="Method.rule_tac" status="no_match"/>
</reuse_diagnostics>'''


class NativeReuseDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '1.reuse-diagnostics.xml'

    def test_type_and_sort_evidence_is_readable_and_context_scoped(self):
        self.path.write_text(OBSERVATION)
        result = read_observation(self.path)
        self.assertEqual(result['semantic_scope'], 'current_goal_and_context_only')
        self.assertEqual(result['matching']['status'], 'no_match')
        self.assertEqual(result['goal']['variables'], [{'name': 's', 'type': "'a store_scheme"}])
        self.assertEqual(result['facts'][0]['variables'][0]['type'], 'unit store_scheme')
        self.assertEqual(result['goal']['type_variables'][1]['sorts'], ['HOL.default'])
        self.assertEqual(result['facts'][0]['type_variables'][0]['sorts'], ['HOL.default'])
        self.assertNotIn('unusable', result)

    def test_matching_does_not_imply_closed_proof(self):
        self.path.write_text(OBSERVATION.replace('status="no_match"/>',
            'status="matched"><remaining_subgoals>2</remaining_subgoals></matching>'))
        result = read_observation(self.path)
        self.assertEqual(result['matching'], {'kind': 'Method.rule_tac', 'status': 'matched',
                                            'remaining_subgoals': 2})

    def test_absent_observation_preserves_snapshot_identity(self):
        state = {'origin': 'before_failed_command', 'goals': ['P']}
        self.assertIs(attach_observation(state, self.path), state)
        self.assertIsNone(attach_observation(None, self.path))

    def test_diagnostic_does_not_mutate_original_snapshot(self):
        self.path.write_text(OBSERVATION)
        state = {'origin': 'before_failed_command', 'goals': ['P']}
        result = attach_observation(state, self.path)
        self.assertNotIn('reuse_diagnostics', state)
        self.assertEqual(result['goals'], state['goals'])
        self.assertEqual(result['reuse_diagnostics']['reference'], 'generic_wrapper')

    def test_malformed_optional_xml_is_not_a_check_failure(self):
        for text in ['broken', '<other/>', OBSERVATION.replace('observation-v1', 'observation-v99')]:
            with self.subTest(text=text[:30]):
                self.path.write_text(text)
                result = read_observation(self.path)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['semantic_scope'], 'current_goal_and_context_only')

    def test_skips_remain_explicit_without_invented_match_result(self):
        for status in ('unsupported', 'budget_exhausted', 'unavailable'):
            with self.subTest(status=status):
                self.path.write_text('<reuse_diagnostics schema="explicit-rule-observation-v1" '
                    f'status="{status}" semantic_scope="current_goal_and_context_only">'
                    '<reason>attribute not re-evaluated</reason></reuse_diagnostics>')
                result = read_observation(self.path)
                self.assertEqual(result['status'], status)
                self.assertNotIn('matching', result)

    def worker(self):
        worker = object.__new__(BatchWorker)
        worker.root = self.root
        worker.base = '\n'
        worker.pending = None
        worker.index = 0
        worker.name = 'test-native-worker'
        worker.label = 'T'
        worker.imports = ['Main']
        worker.closed = False
        worker.checker = SimpleNamespace(batch_candidate_timeout=60)
        worker.checkpoints = {}
        worker._progress_tokens = {}
        def transaction(*args, **kwargs):
            worker.index += 1
            self.path.write_text(OBSERVATION)
            (self.root / '1.proof-state.xml').write_text(
                '<proof_state origin="before_failed_command" command="by" line="8">'
                '<goals><goal>P</goal></goals><assumptions/></proof_state>')
            return 'RED\nFailed to apply initial proof method', 'original native log'
        worker.transaction = transaction
        return worker

    def test_batch_red_is_preserved_when_diagnostics_are_attached(self):
        worker = self.worker()
        with patch('regen.execution.native_check_receipt.native_check_receipt', return_value={}):
            result = worker.check_batch('theory T\nbegin\n\nlemma target: "P" by (rule wrapper)\nend\n', ['target'])
        self.assertEqual(result['verdict'], 'red')
        self.assertEqual(result['returncode'], 1)
        self.assertEqual(result['diagnostic'], 'RED\nFailed to apply initial proof method')
        self.assertEqual(result['native_proof_state_full']['reuse_diagnostics']['matching']['status'], 'no_match')
        self.assertIsNone(worker.pending)

    def test_sketch_red_and_nonpublication_are_preserved(self):
        worker = self.worker()
        with patch('regen.execution.native_check_receipt.native_check_receipt', return_value={}):
            result = worker.check_sketch('theory T\nbegin\n\nlemma target: "P" by (rule wrapper)\nend\n', [])
        self.assertEqual(result['verdict'], 'red')
        self.assertIs(result['publishable'], False)
        self.assertEqual(result['native_proof_state_full']['reuse_diagnostics']['matching']['status'], 'no_match')
        self.assertIsNone(worker.pending)

    def test_worker_module_injection_retains_profile_compatibility(self):
        caller = 'val x = Regen_Reuse_Diagnostics.snapshot position text tr st;'
        legacy = worker_source(SimpleNamespace(base_session='Semantic_Base'), caller)
        calg = worker_source(SimpleNamespace(base_session='Calg_Project_Base'), caller)
        self.assertIn('structure Regen_Reuse_Diagnostics', legacy)
        self.assertIn('YXML.content_of', legacy)
        self.assertNotIn('YXML.content_of', calg)
        self.assertIn('(XML.content_of o YXML.parse_body)', calg)
        self.assertEqual(worker_source(SimpleNamespace(base_session='Semantic_Base'), 'val x = 1;'), 'val x = 1;')


if __name__ == '__main__':
    unittest.main()
