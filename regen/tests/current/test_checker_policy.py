from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.checker_policy import (
    ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT, policy_binding, resolve_complete_context_policy,
)
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.repository_integration import expected_sealed_source


class CheckerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.graph = {'theory':'Demo', 'file_slot':'Demo.thy',
            'nodes':[{'id':n, 'name':n, 'statement':f'lemma {n}: "True"',
                      'context':['context begin interpretation Arch .']} for n in ('a','b')],
            'execution_order':['a','b']}
        self.proofs = {'a':'by simp', 'b':'by (rule a)'}

    def checker(self, root, policy):
        return IsabelleChecker(root, root/'work', heaps_dir=root/'heaps', l4v_dir=root/'l4v',
            semantic_workspace=root/'semantic', complete_context_policy=policy)

    def test_missing_policy_keeps_legacy_binding(self):
        self.assertEqual(resolve_complete_context_policy({}), ISOLATED_CONTEXT)
        self.assertEqual(policy_binding({}), {})
        self.assertEqual(policy_binding({'complete_context_policy':ISOLATED_CONTEXT}), {})
        self.assertEqual(policy_binding({'complete_context_policy':ARCH_REUSE_CONTEXT}),
                         {'complete_context_policy':ARCH_REUSE_CONTEXT})

    def test_invalid_policy_fails_before_directories(self):
        for value in (None, True, '', 'arch', [], 1):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with self.assertRaises(ValueError):
                    self.checker(root, value)
                self.assertFalse((root/'work').exists())

    def test_all_final_gates_use_same_policy_but_batches_do_not(self):
        for policy in (ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temp:
                c = self.checker(Path(temp), policy)
                expected = policy == ARCH_REUSE_CONTEXT
                self.assertEqual(c.complete_source(self.graph,self.proofs),
                    c._source(self.graph,self.proofs,theory_name='Demo',reuse_arch_context=expected))
                with patch.object(c, '_check', return_value={'verdict':'red'}) as native:
                    c.check_complete(self.graph,self.proofs)
                    self.assertEqual(native.call_args.args[0],c._source(self.graph,self.proofs,
                        theory_name='Complete_Demo',diagnostic_markers=True,reuse_arch_context=expected))
                    c.audit_complete_theory(self.graph,self.proofs)
                    audit = native.call_args.args[0]
                    self.assertEqual(audit.count('interpretation Arch .'),1 if expected else 2)
                # No optimization of source-prefix protocol or statement-only shells.
                self.assertEqual(c._source(self.graph,self.proofs,theory_name='Batch').count('interpretation Arch .'),2)
                self.assertEqual(c._source(self.graph,{},theory_name='Declarations',statements_only=True).count('oops'),2)

    def test_integration_uses_run_policy_and_same_imports(self):
        for policy in (ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT):
            with tempfile.TemporaryDirectory() as temp:
                c = self.checker(Path(temp), policy)
                c.previous_theories = ['Parent']; c.import_all_previous = True
                run = {'base_import':c.base_import, 'complete_context_policy':policy}
                self.assertEqual(expected_sealed_source(run,self.graph,self.proofs,['Parent'],'Regen_Demo'),
                    c.complete_source(self.graph,self.proofs,theory_name='Regen_Demo'))

    def test_factory_propagates_policy(self):
        from regen.execution.repository_execution import checker_for
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = dict(l4v=str(root),semantic_workspace=str(root),base_session='Base',
                base_import='Main',base_catalog='base',image='image',isabelle_dir=str(root),
                complete_context_policy=ARCH_REUSE_CONTEXT)
            c = checker_for(root,run,root/'checker',900,[])
            self.assertEqual(c.complete_context_policy,ARCH_REUSE_CONTEXT)

    def test_continuation_cannot_change_render_policy(self):
        from regen.execution.repository_execution import validate_continuation
        with self.assertRaisesRegex(ValueError,'complete context policy'):
            validate_continuation({}, {'complete_context_policy':ARCH_REUSE_CONTEXT}, {}, {'run':{}})

    def test_init_routes_policy_without_changing_legacy_call(self):
        from regen.execution.proof_session_runner import _init_run_unlocked
        for policy in (ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT):
            with patch('regen.execution.proof_session_runner.read_json',
                       return_value={'artifact_type':'repository-file-generation-list'}), \
                 patch('regen.execution.repository_execution.init_run', return_value={}) as init:
                _init_run_unlocked(Path('/unused'),Path('/input'),run_id='test',
                    schedule_path=Path('/schedule'),complete_context_policy=policy)
                init.assert_called_once_with(Path('/unused'),Path('/input'),'test',Path('/schedule'),
                    **policy_binding({'complete_context_policy':policy}))

    def test_unsupported_legacy_init_fails_without_loading_environment(self):
        from regen.execution.proof_session_runner import _init_run_unlocked
        with patch('regen.execution.proof_session_runner.environment_context') as env:
            with self.assertRaisesRegex(ValueError,'new refine-dataset'):
                _init_run_unlocked(Path('/unused'),Path('/input'),run_id='test',
                    complete_context_policy=ARCH_REUSE_CONTEXT)
            env.assert_not_called()


if __name__ == '__main__':
    unittest.main()
