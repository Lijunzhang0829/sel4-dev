"""Declaration-bound audit rendering and fail-closed input checks."""
from pathlib import Path
import tempfile
import unittest

from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.execution.native_fact_audit import FACT_SNAPSHOT_ML, target_resolution_ml
from regen.planning.sketch_check import render_check


def fixture(name='target', context=()):
    node = dict(id='target', name=name, role='contract',
                statement='lemma target: "True"', context=list(context), dependencies=[])
    graph = dict(theory='Synthetic', file_slot='Synthetic.thy', revision=1,
                 run_id='synthetic', nodes=[node], execution_order=['target'], accepted_order=[])
    return node, graph


class NativeFactAuditTests(unittest.TestCase):
    def checker(self, root):
        return IsabelleChecker(root, root/'isabelle', heaps_dir=root, l4v_dir=root,
                               semantic_workspace=root, base_import='Main')

    def test_resolution_rejects_empty_and_selection_names(self):
        for name in ('', None, 'target(1)', 'target[OF assms]', 'target; other'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'theorem name'):
                target_resolution_ml(name)
        self.assertIn('in fn () => facts end', FACT_SNAPSHOT_ML)
        source = target_resolution_ml('Some_Locale.target')
        self.assertIn('Binding.qualified_name "Some_Locale.target"', source)
        self.assertIn('not (Facts.defined (regen_audit_facts_before ()) name)', source)
        self.assertIn('Global_Theory.get_thms thy target_name', source)
        self.assertIn('if null targets', source)
        self.assertIn('List.app (fn thm => if null (Thm.hyps_of thm)', source)
        self.assertNotIn('Long_Name.base_name', source)
        self.assertNotIn('String.isSubstring', source)

    def test_complete_audit_is_inside_owned_scope_but_snapshot_is_final(self):
        with tempfile.TemporaryDirectory() as d:
            node, graph = fixture(context=['context Synthetic_Locale begin'])
            checker = self.checker(Path(d))
            source = checker.complete_audit_source(graph, {'target': 'by simp'})
            scope = source.index('context Synthetic_Locale begin')
            before = source.index(FACT_SNAPSHOT_ML)
            statement = source.index(node['statement'])
            lookup = source.index('val target_name =')
            close = source.index('\nend\n', lookup)
            self.assertLess(scope, before)
            self.assertLess(before, statement)
            self.assertLess(statement, lookup)
            self.assertLess(lookup, close)
            # The final automation context remains global and is captured after
            # all declaration contexts close, preserving the original semantics.
            self.assertGreater(source.index('val ctxt = Proof_Context.init_global thy'), close)
            self.assertGreater(source.index('automation-context.tsv'), close)
            self.assertEqual(source.count('val target_name ='), 1)

    def test_complete_audit_rejects_missing_proof_or_empty_graph(self):
        with tempfile.TemporaryDirectory() as d:
            _, graph = fixture()
            checker = self.checker(Path(d))
            with self.assertRaisesRegex(ValueError, 'every target proof'):
                checker.complete_audit_source(graph, {})
            with self.assertRaisesRegex(ValueError, 'at least one node'):
                checker.complete_audit_source(dict(graph, nodes=[], execution_order=[]), {})
            graph['nodes'][0]['name'] = ''
            with self.assertRaisesRegex(ValueError, 'theorem name'):
                checker.complete_audit_source(graph, {'target':'by simp'})

    def test_sketch_capture_and_audit_inside_consumer_scope(self):
        node, graph = fixture(context=['context Synthetic_Locale begin'])
        helper = dict(id='helper', name='helper', role='helper',
                      statement='lemma helper: "True"', context=node['context'])
        state = dict(graph=graph, proofs={}, accepted_order=[])
        source, prefix, admitted = render_check(state, {'target':node,'helper':helper},
            {'target':'using helper by simp'},
            {'target_entry_id':'target','helper_ids':['helper']}, ['Main'], 'synthetic')
        scope = source.rindex('context Synthetic_Locale begin')
        before = source.index(FACT_SNAPSHOT_ML)
        statement = source.index(node['statement'])
        closed = source.index('REGEN_SKETCH_CLOSED synthetic')
        self.assertLess(scope, before)
        self.assertLess(before, statement)
        self.assertLess(statement, closed)
        self.assertLess(closed, source.index('\nend\n', closed))
        self.assertEqual(admitted, ['helper'])
        self.assertNotIn(FACT_SNAPSHOT_ML, prefix)
        node['name'] = ''
        with self.assertRaisesRegex(ValueError, 'theorem name'):
            render_check(state, {'target':node,'helper':helper}, {'target':'by simp'},
                {'target_entry_id':'target','helper_ids':['helper']}, ['Main'], 'synthetic')

    def test_forbidden_proof_guard_survives_audit_insertion(self):
        with tempfile.TemporaryDirectory() as d:
            _, graph = fixture()
            with self.assertRaisesRegex(ValueError, 'forbidden command'):
                self.checker(Path(d)).complete_audit_source(graph, {'target':'sorry'})


if __name__ == '__main__':
    unittest.main()
