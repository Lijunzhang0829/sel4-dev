"""Recovery stage barriers, bounded fresh sessions, and provider heap isolation."""
import hashlib
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from regen.infrastructure.codex_workspace_session import SessionStalled
from regen.planning.parallel_file_planner import (
    _execution_policy, _retry_stalled, _stage_eligible, run_parallel_workspace,
)
from regen.execution.isabelle_node_checker import IsabelleChecker


class RecoveryTests(unittest.TestCase):
    def test_explicit_overload_retry_is_bounded_and_cleanup_safe(self):
        from regen.infrastructure.codex_workspace_session import ProviderOverloaded, SessionProtocolError
        self.assertTrue(_retry_stalled(ProviderOverloaded('capacity'), 1, 1))
        self.assertFalse(_retry_stalled(ProviderOverloaded('capacity'), 2, 1))
        self.assertFalse(_retry_stalled(SessionProtocolError('unknown'), 1, 1))
        error = ProviderOverloaded('capacity'); error.add_note('cleanup failed')
        self.assertFalse(_retry_stalled(error, 1, 1))

    def test_only_bound_explicit_capacity_is_classified(self):
        from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, ProviderOverloaded, SessionProtocolError
        from regen.planning.parallel_file_planner import tools
        session = CodexWorkspaceSession(tools=tools(True))
        session.thread_id, session.turn_id = 'thread', 'turn'
        message = {'method':'error','params':{'threadId':'thread','turnId':'turn',
                   'willRetry':False,'error':{'codexErrorInfo':'serverOverloaded'}}}
        with self.assertRaises(ProviderOverloaded):session._notification(message)
        message['params']['turnId']='foreign'
        with self.assertRaises(SessionProtocolError) as caught:session._notification(message)
        self.assertNotIsInstance(caught.exception, ProviderOverloaded)

    def test_policy_validation_and_cleanup_retry_guard(self):
        for policy in ({'stages':[['A'],['A']]}, {'stages':[['B']]},
                       {'session_stall_retries':True}, {'session_stall_retries':4}):
            with self.assertRaises(ValueError):
                _execution_policy(policy, ['A'])
        self.assertTrue(_retry_stalled(SessionStalled('timeout'), 1, 1))
        self.assertFalse(_retry_stalled(SessionStalled('timeout'), 2, 1))
        self.assertFalse(_retry_stalled(RuntimeError('quota'), 1, 1))
        error = SessionStalled('timeout'); error.add_note('cleanup failed')
        self.assertFalse(_retry_stalled(error, 1, 1))
        rows = [{'theory':'A','status':'sealed','selected':True},
                {'theory':'B','status':'pending','selected':True}]
        policy = {'stages':[['A'],['B']]}
        self.assertEqual(_stage_eligible(rows,policy,['A']),{'A'})
        self.assertEqual(_stage_eligible(rows,policy),{'B'})

    def exercise(self, always_fail):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root/'codex'; binary.write_text('fixture')
            states = dict.fromkeys(('A','B','C','D'), 'pending')
            attempts = {}; paths = []; mutex = threading.Lock()
            class Overview:
                selected = list(states)
                run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                       'checker_context_root':str(root),'checker_context_id':'fixture'}
                options = {'model':'test','call_timeout':900,'check_timeout':900,'candidate_timeout':120}
                def __init__(self,*args,**kwargs): pass
                def list_workspace(self):
                    with mutex:
                        return {'theories':[{'theory':name,'status':state,'selected':True,
                            'contracts':[name],'ready_for_proof_action':state=='pending'}
                            for name,state in states.items()]}
            def session(root,theory,**kwargs):
                with mutex:
                    attempts[theory] = attempts.get(theory,0)+1
                    if theory in {'C','D'}:
                        self.assertEqual((states['A'],states['B']),('sealed','sealed'))
                    if theory == 'A':
                        paths.append(kwargs['directory'])
                        if always_fail or attempts[theory] == 1:
                            raise SessionStalled('fixture timeout')
                    states[theory] = 'sealed'
                return {'theory':theory,'status':'sealed'}
            settings = {'codex_bin':str(binary),'codex_runtime_root':str(root/'codex-runtime'),
                        'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch('regen.planning.parallel_file_planner.WorkspaceController',Overview), patch(
                    'regen.planning.parallel_file_planner.run_theory_session',side_effect=session), patch(
                    'regen.infrastructure.audited_llm.model_settings',return_value=settings), patch(
                    'regen.preparation.checker_context.load_context',return_value={'checker_context_id':'fixture'}):
                result = run_parallel_workspace(root,allow_external_call=True,integrate=False,
                    execution_policy={'stages':[['A','B'],['C','D']], 'session_stall_retries':1})
            self.assertEqual(attempts['A'],2)
            self.assertNotEqual(paths[0],paths[1])
            self.assertTrue((paths[0]/'failure.json').is_file())
            if always_fail:
                self.assertNotIn('C',attempts)
                self.assertEqual(result['blocked']['C']['reason'],'priority_stage_incomplete')
            else:
                self.assertEqual(result['status'],'selected_theories_sealed')
                self.assertFalse(result['failures'])

    def test_retry_then_advance(self): self.exercise(False)
    def test_retry_exhaustion_blocks_later_stage(self): self.exercise(True)

    def test_failed_seal_keeps_provider_session_definition_and_heap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = IsabelleChecker(root,root/'work',previous_theories=['Provider'],
                heaps_dir=root,l4v_dir=root,semantic_workspace=root,isabelle_dir=root)
            heap = root/'Generated_Refine'; heap.write_bytes(b'provider heap')
            (checker.generated_dir/'Target.thy').write_text('target fixture')
            (checker.generated_dir/'Provider.thy').write_text('provider fixture')
            with patch('regen.execution.isabelle_node_checker.native_run',
                       return_value=subprocess.CompletedProcess([],1,'','failed')) as run:
                result = checker.build_sealed_session(['Provider','Target'])
            definition = (checker.generated_dir/'ROOT').read_text()
            parent, child = definition.split('session Sealed_Refine')
            self.assertIn('    Provider',parent)
            self.assertNotIn('Target',parent)
            self.assertIn('= Generated_Refine +',child)
            self.assertIn('    Target',child)
            self.assertEqual(run.call_args.args[0][-1],'Sealed_Refine')
            self.assertEqual(heap.read_bytes(),b'provider heap')
            self.assertEqual(result['verdict'],'red')
