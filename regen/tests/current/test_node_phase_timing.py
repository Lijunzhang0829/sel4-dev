from pathlib import Path
import unittest
from regen.execution.node_phase_timing import timed_declaration, parse_phase_timings, BOUNDARIES
from regen.execution.isabelle_node_checker import IsabelleChecker
from regen.tools.profile_saved_node_phases import prefix_inputs
from regen.execution.node_phase_timing import ARCH_BOUNDARIES, parse_arch_states
from regen.tools.arch_locale_profile import profiling_command, parse_profile


class NodePhaseTimingTest(unittest.TestCase):
    def test_boundaries_preserve_statement_proof_and_nested_context(self):
        node = {'id': 'helper:test', 'name': 'test', 'context': ['context begin', 'context begin'],
                'statement': 'lemma test: "True"'}
        proof = 'proof -\n show True by simp\nqed'
        source = timed_declaration(node, proof)
        self.assertIn(node['statement'], source)
        self.assertIn(proof, source)
        self.assertEqual(source.count('\nend\n'), 1)
        self.assertIn('\nend\nend\nML ', source)
        self.assertNotIn('ML_command', source)
        self.assertIn('ML_prf', source)
        self.assertIn('Thm.consolidate', source)
        self.assertEqual(source.count('REGEN_PHASE'), 6)

    def log(self, boundaries=BOUNDARIES, stamps=range(6)):
        return '\n'.join(f'REGEN_PHASE {"x".encode().hex()} {b} {s}'
                         for b, s in zip(boundaries, stamps))

    def test_complete_and_disjoint_intervals(self):
        row = parse_phase_timings(self.log())['nodes']['x']
        self.assertTrue(row['complete'])
        self.assertEqual(row['total_wall_ms'], 5000)
        self.assertEqual(sum(row['phase_wall_ms'].values()), 5000)

    def test_partial_never_becomes_complete(self):
        row = parse_phase_timings(self.log(BOUNDARIES[:3]))['nodes']['x']
        self.assertFalse(row['complete'])
        self.assertIsNone(row['total_wall_ms'])
        self.assertEqual(set(row['phase_wall_ms']), {'context_open', 'statement'})

    def test_corrupt_sequences_are_not_durations(self):
        for log in (self.log()+self.log(), self.log(stamps=[0, 2, 1, 3, 4, 5]),
                    self.log(BOUNDARIES[1:])):
            row = parse_phase_timings(log)['nodes']['x']
            self.assertFalse(row['complete'])
            self.assertTrue(row['errors'])
            self.assertEqual(row['phase_wall_ms'], {})

    def test_renderer_defaults_and_forbidden_guard(self):
        checker = object.__new__(IsabelleChecker)
        checker.previous_theories = []; checker.base_import = 'Main'
        node = {'id': 'x', 'name': 'x', 'statement': 'lemma x: "True"', 'context': []}
        graph = {'nodes': [node], 'execution_order': ['x']}
        plain = checker._source(graph, {'x': 'by simp'}, theory_name='Test')
        self.assertNotIn('REGEN_PHASE', plain)
        self.assertEqual(plain, checker._source(graph, {'x': 'by simp'}, theory_name='Test', phase_timing=False))
        detailed = checker._source(graph, {'x': 'by simp'}, theory_name='Test', phase_timing=True)
        self.assertIn('REGEN_PHASE', detailed)
        self.assertIn('phase timing requires parallel_proofs=0', detailed)
        self.assertNotIn('parallel_proofs', plain)
        with self.assertRaises(ValueError):
            checker._source(graph, {'x': 'sorry'}, theory_name='Test', phase_timing=True)

    def test_prefix_is_original_order_and_does_not_mutate(self):
        graph = {'nodes': [{'id': x} for x in 'abcd'], 'accepted_order': list('bacd'),
                 'execution_order': list('abcd')}
        proofs = {x: x for x in 'abcd'}
        selected, chosen = prefix_inputs(graph, proofs)
        self.assertEqual(selected['execution_order'], list('bac'))
        self.assertEqual(list(chosen), list('bac'))
        self.assertEqual(len(graph['nodes']), 4)
        with self.assertRaises(ValueError):
            prefix_inputs(graph, proofs, 5)

    def test_arch_split_preserves_commands_and_proof(self):
        node = {'id': 'x', 'name': 'x', 'context': ['context begin interpretation Arch .'],
                'statement': 'lemma x: "True"'}
        text = timed_declaration(node, 'by simp', split_arch=True)
        self.assertEqual(text.count('interpretation Arch\n'), 1)
        self.assertIn('\n.\n', text)
        self.assertIn(node['statement'], text)
        self.assertIn('\nby simp\n', text)
        self.assertEqual(text.count('REGEN_ARCH_PHASE'), 8)
        self.assertEqual(text.count('REGEN_ARCH_STATE'), 4)
        with self.assertRaises(ValueError):
            timed_declaration({**node, 'context': ['context begin']}, 'by simp', split_arch=True)

    def test_arch_parser_has_disjoint_seven_phases(self):
        log = self.log(ARCH_BOUNDARIES, range(8)).replace('REGEN_PHASE', 'REGEN_ARCH_PHASE')
        row = parse_phase_timings(log)['nodes']['x']
        self.assertTrue(row['complete'])
        self.assertEqual(sum(row['phase_wall_ms'].values()), 7000)
        self.assertEqual(row['phase_wall_ms']['interpretation_finish'], 1000)
        self.assertNotIn('context_open', row['phase_wall_ms'])

    def test_arch_registration_observations_require_all_boundaries(self):
        text = '\n'.join(f'REGEN_ARCH_STATE 78 {s} {n} 0 {level}' for s, n, level in
                         [('before', 0, 1), ('anonymous', 0, 2), ('interpreted', 1, 2), ('closed', 0, 1)])
        self.assertEqual(parse_arch_states(text)['x'][2]['local_registrations'], 1)
        with self.assertRaises(ValueError):
            parse_arch_states(text.split('\n', 1)[1])

    def test_cpu_profile_preserves_only_fixed_theory_load(self):
        original = ['isabelle', 'process', '-o', 'threads=4', '-T', '/candidate/Timing_Scheduler_Prefix']
        converted = profiling_command(original)
        self.assertEqual(converted[:4], original[:4])
        self.assertEqual(converted[4], '-e')
        self.assertIn('ML_Profiling.profile_time', converted[5])
        self.assertEqual(original[4], '-T')
        for args in (['isabelle'], [*original, '-T', 'other'], [*original[:-1], 'other']):
            with self.assertRaises(ValueError):
                profiling_command(args)

    def test_native_profile_requires_matching_sample_totals_and_metadata(self):
        log = 'REGEN_ARCH_LOCALE Example.Arch 5 6 7\nprofile_time:\n 2 first\n 3 second\n 5 TOTAL\n'
        result = parse_profile(log)
        self.assertEqual(result['total_samples'], 5)
        self.assertEqual(result['entries'][0]['name'], 'second')
        self.assertEqual(result['locales'][0]['theorem_slots'], 6)
        for text in (log.replace('5 TOTAL', '6 TOTAL'), log.split('\n', 1)[1], 'empty'):
            with self.assertRaises(ValueError):
                parse_profile(text)


if __name__ == '__main__':
    unittest.main()
