"""Statement probes may reuse Arch interpretation, never accepted proof facts."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from regen.execution.isabelle_node_checker import IsabelleChecker, failed_statement_declaration
from regen.execution.checker_policy import ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT


class DeclarationContextReuseTests(unittest.TestCase):
    def setUp(self):
        self.checker = object.__new__(IsabelleChecker)
        self.checker.previous_theories = []
        self.checker.base_import = 'Main'
        self.index = {'revision': 1, 'nodes': [dict(id=n, name=n,
            statement=f'lemma {n}: "True"',
            context=['context begin interpretation Arch .']) for n in ('a', 'b', 'c')],
            'execution_order': ['a', 'b', 'c']}

    def render(self):
        return self.checker._source(self.index, {}, theory_name='Probe',
            statements_only=True, reuse_declaration_context=True)

    def test_adjacent_statements_abort_in_independent_scopes(self):
        saved = deepcopy(self.index)
        source = self.render()
        self.assertEqual(source.count('interpretation Arch .'), 1)
        self.assertEqual(source.count('\ncontext begin\n'), 3)
        self.assertEqual(source.splitlines().count('end'), 5)
        self.assertEqual(source.count('oops'), 3)
        for node in self.index['nodes']:
            self.assertIn(node['statement'] + '\n  oops\nend', source)
        self.assertNotIn('local_setup', source)
        self.assertNotIn('Proof_Context.transfer_facts', source)
        self.assertEqual(saved, self.index)

    def test_context_boundaries_are_not_coalesced_or_reordered(self):
        for context in ([], ['context Other begin'],
                        ['context begin interpretation Arch .', 'context begin'],
                        ['context begin interpretation Arch . ']):
            with self.subTest(context=context):
                self.index['nodes'][1]['context'] = context
                source = self.render()
                self.assertEqual(source.count('interpretation Arch .'), 2 + int(any('Arch .' in c for c in context)))
                self.assertLess(source.index('lemma a'), source.index('lemma b'))
                self.assertLess(source.index('lemma b'), source.index('lemma c'))
                self.assertIn('lemma a: "True"\n  oops\nend\nend', source)

    def test_failure_mapping_uses_actual_optimized_source_lines(self):
        self.index['nodes'][1]['statement'] = 'lemma b:\n  "undefined_symbol"'
        source = self.render()
        line = source[:source.index('  "undefined_symbol"')].count('\n') + 1
        diagnostic = f'Unknown constant (line {line} of "Probe.thy")'
        self.assertEqual(failed_statement_declaration(self.index, source, diagnostic), 'b')

    def test_document_entrypoint_records_policy_and_legacy_stays_isolated(self):
        for policy in (ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT):
            self.checker.complete_context_policy = policy
            with patch('regen.execution.declaration_cache.checked_declarations',
                       side_effect=lambda *a: {'verdict':'green', 'diagnostic':''}) as native:
                result = self.checker.check_declarations(self.index)
                self.assertEqual(native.call_args.args[1].count('interpretation Arch .'), 1)
                self.assertEqual(result['declaration_context_policy'], 'arch-declarations-reuse-v1')
                self.assertFalse(result['proofs_checked'])
                self.assertEqual(result['check_scope'], 'all-document-declarations')
                result = self.checker.check_graph(self.index)
                self.assertEqual(native.call_args.args[1].count('interpretation Arch .'), 3)
                self.assertEqual(result['declaration_context_policy'], 'isolated-v1')

    def test_cannot_enable_declaration_reuse_for_proof_rendering(self):
        with self.assertRaisesRegex(ValueError, 'requires statements_only'):
            self.checker._source(self.index, {'a':'by simp'}, theory_name='Proof',
                                 reuse_declaration_context=True)
        plain = self.checker._source(self.index, {}, theory_name='Old', statements_only=True)
        self.assertEqual(plain.count('interpretation Arch .'), 3)

    def test_large_theory_interpretation_count_depends_on_context_runs(self):
        self.index['nodes'] = [dict(id=f'n{i}', name=f'n{i}', statement=f'lemma n{i}: "True"',
            context=['context begin interpretation Arch .']) for i in range(286)]
        self.index['execution_order'] = [n['id'] for n in self.index['nodes']]
        source = self.render()
        self.assertEqual(source.count('interpretation Arch .'), 1)
        self.assertEqual(source.count('oops'), 286)
        self.assertEqual(source.count('\ncontext begin\n'), 286)


if __name__ == '__main__':
    unittest.main()
