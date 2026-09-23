"""Persistent workspace control using real journals and synthetic checker evidence.

No Codex process, model request or Isabelle process is launched by these tests.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import journal
from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
from regen.planning import proof_workspace_agent as agent
from regen.tests.current.test_shared_proof_workspace import setup_run
from regen.tests.current.test_proof_batches import Checker


class ProofWorkspaceAgentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run, self.packages, self.initial, self.source = setup_run(self.root)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('regen.execution.proof_session_runner.frozen_role_model', return_value='test'))
        self.stack.enter_context(patch('regen.infrastructure.progress_events.emit_event'))

    def controller(self, **options):
        controller = agent.WorkspaceController(self.root, **options)
        self.addCleanup(controller.close)
        return controller

    def fresh_target(self, **projection):
        shutil.rmtree(self.root/'theories/Target')
        if projection:
            package = self.packages[1]
            package['repository_projection'] = projection
            package['package_payload_sha256'] = digest({k:v for k,v in package.items() if k != 'package_payload_sha256'})
            durable(self.root/'packages/Target.thy.json', package)
            inputs = json.loads((self.root/'execution-input.json').read_text())
            inputs['package_hashes']['Target.thy'] = package['package_payload_sha256']
            durable(self.root/'execution-input.json', inputs)

    def local_runtime(self, controller):
        checkers = {}
        setup_calls = []
        def setup(root, theory, timeout):
            setup_calls.append(theory)
            package = next(p for p in self.packages if p['theory'] == theory)
            directory = root/'theories'/theory
            checker = Checker(); checker.close_prefix = Mock()
            checkers[theory] = checker
            return self.run, package, directory, journal(root,self.run,package), checker, []
        self.stack.enter_context(patch('regen.execution.repository_execution.setup', side_effect=setup))
        self.stack.enter_context(patch('regen.execution.repository_execution.progress', return_value={}))
        self.stack.enter_context(patch('regen.infrastructure.progress_events.configure_event_monitor'))
        self.monitor_close = self.stack.enter_context(patch('regen.infrastructure.progress_events.close_event_monitor'))
        self.llm = self.stack.enter_context(patch('regen.planning.batch_file_planner.call_llm', side_effect=AssertionError('unexpected model call')))
        self.record = self.stack.enter_context(patch('regen.infrastructure.workspace_transcript.record_workspace_action',
            side_effect=lambda prefix,prompt,proposal,metadata,**identity: json.dumps(proposal)))
        self.stack.enter_context(patch('regen.planning.batch_file_planner.validate_call_artifact', return_value={'sealable_generation':True}))
        self.audit = self.stack.enter_context(patch('regen.infrastructure.workspace_transcript.audit_workspace_session', return_value={}))
        return checkers, setup_calls

    def call(self, controller, theory, proposal):
        return controller.handle('proof_action', {'theory':theory,'proposal':proposal},
                                 {'transcript_path':'synthetic-test-transcript','call_id':'test'})

    def test_read_not_started_state_and_other_theory_green_are_read_only(self):
        self.fresh_target()
        controller = self.controller(theories=['Target'])
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(agent, '_run_batch_file') as execute:
            overview = controller.handle('list_workspace', {}, {})
            self.assertEqual(overview['worker_count'], 1)
            self.assertEqual(next(t for t in overview['theories'] if t['theory']=='Target')['status'], 'not_started')
            target = controller.handle('read_theory', {'theory':'Target'}, {})['input']
            self.assertEqual(target['green_node_ids'], [])
            self.assertEqual(target['unproved_node_ids'], ['target_contract'])
            self.assertEqual(target['requests_used'], 0)
            source = controller.handle('list_generated', {'theory':'Source'}, {})
            self.assertEqual(source['total'], 2)
            refs = [row['source_ref'] for row in source['records']]
            examples = controller.handle('read_generated', {'source_refs':refs}, {})
            self.assertTrue(all(r['reference_only'] for r in examples['records']))
            execute.assert_not_called()
        after = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_blocked_theory_remains_readable_without_starting_native_worker(self):
        self.fresh_target(schema_version=6, required_imports=[{'theory':'Source','reason':'Required'}])
        controller = self.controller(theories=['Target'])
        with patch.object(agent, '_run_batch_file') as execute:
            view = controller.handle('read_theory', {'theory':'Target'}, {})
            self.assertIn('target_contract', view['input']['unproved_node_ids'])
            result = self.call(controller, 'Target', {'action':'finish'})
            self.assertEqual(result['status'], 'not_ready')
            self.assertEqual(result['unsealed_providers'], ['Source'])
            execute.assert_not_called()
            self.assertEqual(controller.runtime_cache, {})

    def test_one_tool_action_one_charge_no_extra_model_and_finish_failure_feedback(self):
        controller = self.controller(theories=['Target'])
        checkers, setups = self.local_runtime(controller)
        result = self.call(controller, 'Target', {'action':'submit_batch','proofs':{'target_contract':'by simp'}})
        self.assertEqual(result['feedback']['status'], 'accepted')
        self.assertEqual(result['green_node_ids'], ['target_contract'])
        self.assertEqual(result['request_number'], 1)
        with patch('regen.planning.batch_file_planner.seal_theory', return_value=(None, {
                'stage':'semantic-audit','diagnostic':'specific audit failure', 'check':{'verdict':'red'}})):
            result = self.call(controller, 'Target', {'action':'finish'})
        self.assertEqual(result['feedback']['status'], 'seal_failed')
        self.assertEqual(result['feedback']['diagnostic'], 'specific audit failure')
        self.assertFalse(result['all_selected_sealed'])
        self.assertEqual(result['request_number'], 2)
        self.assertEqual(result['green_node_ids'], ['target_contract'])
        self.assertEqual(setups, ['Target'])
        self.assertEqual(self.record.call_count, 2)
        self.llm.assert_not_called()
        self.audit.assert_called_once_with('synthetic-test-transcript', require_terminal=False, require_real=True)
        checkers['Target'].close_prefix.assert_not_called()
        self.monitor_close.assert_not_called()

    def test_premature_finish_returns_rejection_instead_of_claiming_success(self):
        controller = self.controller(theories=['Target'])
        _, _ = self.local_runtime(controller)
        result = self.call(controller, 'Target', {'action':'finish'})
        self.assertEqual(result['feedback']['status'], 'rejected_request')
        self.assertIn('every active node proved', result['feedback']['diagnostic'])
        self.assertEqual(result['request_number'], 1)
        self.assertEqual(result['green_node_ids'], [])
        self.assertFalse(result['all_selected_sealed'])

    def test_switching_theory_closes_previous_prefix_before_the_next_worker(self):
        controller = self.controller()
        checkers, setups = self.local_runtime(controller)
        first = self.call(controller, 'Source', {'action':'read_proofs','node_ids':['left']})
        self.assertEqual(first['feedback']['status'], 'available')
        self.assertEqual(setups, ['Source'])
        events = []
        checkers['Source'].close_prefix.side_effect = lambda: events.append('source-closed')
        original_setup = __import__('regen.execution.repository_execution', fromlist=['setup']).setup
        original_side_effect = original_setup.side_effect
        def setup(root, theory, timeout):
            if theory == 'Target': events.append('target-setup')
            return original_side_effect(root, theory, timeout)
        original_setup.side_effect = setup
        self.call(controller, 'Target', {'action':'submit_batch','proofs':{'target_contract':'by simp'}})
        self.assertEqual(events[:2], ['source-closed','target-setup'])
        self.assertEqual(len(controller.runtime_cache), 2)
        self.assertEqual(setups, ['Source','Target'])
        controller.close()
        self.assertEqual(controller.runtime_cache, {})
        checkers['Target'].close_prefix.assert_called_once()

    def test_model_selection_and_selected_target_boundary(self):
        with self.assertRaisesRegex(ValueError, 'run-frozen'):
            self.controller(model='other')
        controller = self.controller(theories=['Target'])
        with patch.object(agent, '_run_batch_file') as execute:
            with self.assertRaisesRegex(ValueError, 'outside selected'):
                self.call(controller, 'Source', {'action':'read_proofs','node_ids':['left']})
            execute.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'distinct theories'):
            self.controller(theories=['Target','Target'])

    def test_startup_failure_releases_real_run_lock_and_opt_in_does_not_start(self):
        with patch.object(agent, 'WorkspaceController', side_effect=ValueError('synthetic startup failure')) as create, \
             patch('regen.infrastructure.progress_events.close_event_monitor') as close:
            with self.assertRaises(PermissionError):
                agent.run_workspace(self.root)
            create.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'synthetic startup failure'):
                agent.run_workspace(self.root, allow_external_call=True)
            close.assert_called_once_with('workspace-failed')
        token = acquire_run_mutation_lock(self.root, operation='test')
        release_run_mutation_lock(token)
        failures = list((self.root/'workspace-sessions').glob('*/failure.json'))
        self.assertEqual(len(failures), 1)
        self.assertEqual(json.loads(failures[0].read_text())['error_type'], 'ValueError')


if __name__ == '__main__':
    unittest.main()
