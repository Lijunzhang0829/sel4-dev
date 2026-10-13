import unittest
from copy import deepcopy

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.tools.arch_reuse_audit import snapshot, parse


class ArchReuseTest(unittest.TestCase):
    def setUp(self):
        self.checker = object.__new__(IsabelleChecker)
        self.checker.previous_theories = []
        self.checker.base_import = 'Main'
        self.graph = {'nodes': [dict(id=n, name=n, statement=f'lemma {n}: "True"',
            context=['context begin interpretation Arch .']) for n in ('a', 'b', 'c')],
            'execution_order': ['a', 'b', 'c']}
        self.proofs = dict.fromkeys(('a', 'b', 'c'), 'by simp')

    def render(self, **kwargs):
        return self.checker._source(self.graph, self.proofs, theory_name='Test', **kwargs)

    def test_opt_in_preserves_input_and_inner_scopes(self):
        saved = deepcopy(self.graph)
        self.assertEqual(self.render().count('interpretation Arch .'), 3)
        text = self.render(reuse_arch_context=True)
        self.assertEqual(text.count('interpretation Arch .'), 1)
        self.assertEqual(text.count('\ncontext begin\n'), 3)
        self.assertEqual(text.count('\nend\n'), 5)  # three nodes, shared scope, theory
        self.assertEqual(saved, self.graph)
        for n in self.graph['nodes']:
            self.assertIn(n['statement']+'\nby simp\nend', text)

    def test_context_change_breaks_group(self):
        self.graph['nodes'][1]['context'] = []
        self.assertEqual(self.render(reuse_arch_context=True).count('interpretation Arch .'), 2)

    def test_attribute_barrier_and_exported_prefix(self):
        text = self.render(reuse_arch_context=True)
        self.assertIn('Proof_Context.put_thms false (name, NONE)', text)
        self.assertIn('["a", "b"]', text)
        self.assertIn('Proof_Context.transfer_facts', text)
        self.graph['nodes'][1]['statement'] = 'lemma b [simp]: "True"'
        text = self.render(reuse_arch_context=True)
        self.assertEqual(text.count('interpretation Arch .'), 3)
        self.assertNotIn('Proof_Context.transfer_facts', text)

    def test_no_missing_proofs_or_extra_facts(self):
        self.proofs = {'b': 'by simp'}
        text = self.render(reuse_arch_context=True)
        self.assertNotIn('lemma a:', text)
        self.assertNotIn('lemma c:', text)
        self.assertEqual(text.count('interpretation Arch .'), 1)

    def test_guards_and_incompatible_diagnostics(self):
        for kw in ({'phase_timing': True}, {'statements_only': True}):
            with self.assertRaises(ValueError):
                self.render(reuse_arch_context=True, **kw)
        self.proofs['a'] = 'sorry'
        with self.assertRaises(ValueError):
            self.render(reuse_arch_context=True)

    def test_snapshot_parser_fails_closed(self):
        text = 'REGEN_REUSE_SNAPSHOT export facts 4 '+'a'*40+'\n'
        self.assertEqual(parse(text)['export/facts']['count'], 4)
        for bad in ('', text+text):
            with self.assertRaises(ValueError):
                parse(bad)
        with self.assertRaises(ValueError):
            snapshot('bad label')
        with self.assertRaises(ValueError):
            snapshot('ok', targets=['bad"name'])

    def test_native_control_actually_exercises_reuse_and_barrier(self):
        from regen.tools.arch_reuse_controls import fixture
        self.graph, self.proofs = fixture()
        self.assertEqual(self.render().count('interpretation Arch .'), 5)
        text = self.render(reuse_arch_context=True)
        self.assertEqual(text.count('interpretation Arch .'), 3)
        self.assertIn('Proof_Context.transfer_facts', text)
        self.assertIn('local config leaked', text)
        self.assertIn('future fact leaked', text)

    def test_semantic_audit_requires_every_scope_and_prefix_target(self):
        from regen.tools.arch_reuse_audit import expected_keys
        keys = expected_keys(self.graph)
        self.assertEqual(len(keys), 80)
        self.assertIn('before-2/deps_a', keys)
        self.assertIn('export/target_c', keys)
        self.assertNotIn('before-0/target_a', keys)

    def test_heap_comparison_loads_sealed_read_only(self):
        from pathlib import Path
        from regen.tools.compare_arch_reuse_heaps import probe_command
        command = ['docker', 'run', '--name', 'old', '--network', 'none',
                   '--mount', 'type=bind,src=/heaps,dst=/replay-heaps',
                   '--mount', 'type=bind,src=/work,dst=/candidate,readonly',
                   '--mount', 'type=bind,src=/parent,dst=/parent-heaps,readonly',
                   'image', 'isabelle', 'process', '-l', 'Generated_Refine', '-T', '/candidate/Old']
        changed = probe_command(command, Path('/probe'))
        self.assertEqual(changed[changed.index('-l')+1], 'Sealed_Refine')
        self.assertEqual(changed[changed.index('-T')+1], '/candidate/Compare_Sealed')
        self.assertIn('type=bind,src=/heaps,dst=/replay-heaps,readonly', changed)
        self.assertIn('type=bind,src=/probe,dst=/candidate,readonly', changed)
        self.assertEqual(command[command.index('--name')+1], 'old')
        unsafe = list(command)
        unsafe[unsafe.index('none')] = 'host'
        with self.assertRaises(ValueError):
            probe_command(unsafe, Path('/probe'))
        unsafe = [v.replace('dst=/parent-heaps,readonly', 'dst=/parent-heaps') for v in command]
        with self.assertRaises(ValueError):
            probe_command(unsafe, Path('/probe'))


if __name__ == '__main__':
    unittest.main()
