"""Positive export scope and tamper tests; native smoke is a separate check."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from regen.execution.partial_theory_export import POLICY, build_export_scope, export_graph
from regen.execution.proof_commits import digest
from regen.execution.seal_evidence import state_binding, validate_seal
from regen.planning.declaration_index import initial_index, accepted_declarations
from regen.planning.theory_outcomes import current_dispositions, record_unresolved
from regen.tests.current.seal_fixture import evidence


class PartialTheoryExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.package = {'theory': 'Partial', 'file_slot': 'Partial.thy', 'contracts': [
            {'contract_id': key, 'name': key, 'exact_statement': f'lemma {key}: "{claim}"',
             'contract_owned_context': []}
            for key, claim in [('a', 'True'), ('b', 'False')]]}
        graph = initial_index(self.package, run_id='partial-unit')
        self.state = {'graph': accepted_declarations(graph, ['a']), 'proofs': {'a': 'by simp'},
                      'accepted_order': ['a'], 'status': 'generating', 'rounds': {'b': 3}}
        self.run = {'run_id': 'partial-unit', 'contract_outcome_policy': POLICY}
        self.controller = SimpleNamespace(root=Path(self.temp.name), run=self.run, theory='Partial',
            _state=lambda _: (deepcopy(self.package), deepcopy(self.state)))
        record_unresolved(self.controller, reason='unit fixture exhausted its search', origin='controller',
                          entry_ids=['b'])
        self.dispositions = current_dispositions(self.controller)

    def sealed_fixture(self):
        scope = build_export_scope(self.package, self.state, self.dispositions, runtime=self.run,
                                   expected_imports=['Semantic_Base.Semantic_Root'])
        view = deepcopy(self.state); view['graph'] = export_graph(self.state)
        # Explicit synthetic native evidence, only for pure replay tests.
        ev = evidence(view)
        ev.update(seal_evidence_version=4, state_binding=state_binding(self.state), export_scope=scope)
        state = deepcopy(self.state)
        state.update(status='exported_partial', contract_dispositions=deepcopy(self.dispositions),
                     seal_id='theory-seal:' + digest(ev)[:20])
        return state, ev

    def test_partial_export_keeps_full_coverage_and_only_positive_facts(self):
        state, ev = self.sealed_fixture()
        validate_seal(state, ev, runtime=self.run, expected_package=self.package)
        self.assertEqual([n['id'] for n in state['graph']['nodes']], ['a', 'b'])
        self.assertEqual(ev['export_scope']['exported_node_ids'], ['a'])
        self.assertEqual(ev['export_scope']['omitted_node_ids'], ['b'])
        self.assertIn('lemma a:', ev['source_text'])
        self.assertNotIn('lemma b:', ev['source_text'])
        self.assertNotIn('sorry', ev['source_text'])
        self.assertEqual(state['rounds'], {'b': 3})

    def test_scope_tampering_is_rejected_even_after_rehashing_seal(self):
        def source_without_b(ev):
            ev['export_scope']['package']['contracts'].pop()
        changes = [
            ('lost fixed target', source_without_b),
            ('foreign export', lambda e: e['export_scope']['exported_node_ids'].append('b')),
            ('lost omission', lambda e: e['export_scope']['omitted_node_ids'].clear()),
            ('missing disposition', lambda e: e['export_scope']['dispositions'].clear()),
            ('wrong projection', lambda e: e['export_scope'].update(export_graph_sha256='bad')),
            ('changed fixed goal', lambda e: e['export_scope']['package']['contracts'][1].update(exact_statement='lemma b: "True"')),
        ]
        for label, mutate in changes:
            with self.subTest(label=label):
                state, ev = self.sealed_fixture(); mutate(ev)
                state['seal_id'] = 'theory-seal:' + digest(ev)[:20]
                with self.assertRaises(ValueError):
                    validate_seal(state, ev, runtime=self.run)

    def test_disposition_cannot_be_changed_or_transplanted(self):
        state, ev = self.sealed_fixture()
        state['contract_dispositions']['b']['status'] = 'refuted'
        with self.assertRaisesRegex(ValueError, 'dispositions'):
            validate_seal(state, ev, runtime=self.run)
        state, ev = self.sealed_fixture()
        with self.assertRaisesRegex(ValueError, 'binding'):
            validate_seal(state, ev, runtime={**self.run, 'run_id': 'another-run'})
        with self.assertRaisesRegex(ValueError, 'package'):
            validate_seal(state, ev, runtime=self.run, expected_package={**self.package, 'changed': True})

    def test_partial_export_cannot_fall_back_to_legacy_audit_policy(self):
        state, ev = self.sealed_fixture()
        ev.pop('audit_policy')
        state['seal_id'] = 'theory-seal:' + digest(ev)[:20]
        with self.assertRaisesRegex(ValueError, 'unsupported seal audit policy'):
            validate_seal(state, ev, runtime=self.run)

    def test_partial_export_requires_new_policy_and_cannot_claim_full_success(self):
        state, ev = self.sealed_fixture()
        with self.assertRaisesRegex(ValueError, 'policy'):
            validate_seal(state, ev, runtime={})
        state['status'] = 'sealed'
        with self.assertRaisesRegex(ValueError, 'whole theory'):
            validate_seal(state, ev, runtime=self.run)
        state['status'] = 'exported_partial'; ev['seal_evidence_version'] = 3
        state['seal_id'] = 'theory-seal:' + digest(ev)[:20]
        with self.assertRaisesRegex(ValueError, 'version 4'):
            validate_seal(state, ev, runtime=self.run)

    def test_pending_fixed_target_and_positive_disposition_overlap_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'every unproved'):
            build_export_scope(self.package, self.state, {}, runtime=self.run)
        state = deepcopy(self.state); state['proofs']['b'] = 'by simp'; state['accepted_order'].append('b')
        with self.assertRaisesRegex(ValueError, 'every unproved'):
            build_export_scope(self.package, state, self.dispositions, runtime=self.run)

    def test_exported_proof_cannot_retain_unproved_graph_prerequisite(self):
        state = deepcopy(self.state); state['graph']['nodes'][0]['dependencies'] = ['b']
        with self.assertRaisesRegex(ValueError, 'unproved prerequisite'):
            export_graph(state)

    def test_zero_positive_export_has_no_synthetic_theorem(self):
        self.state['proofs'] = {}; self.state['accepted_order'] = []
        self.state['graph'] = accepted_declarations(self.state['graph'], [])
        record_unresolved(self.controller, reason='second independent search exhausted', origin='controller',
                          entry_ids=['a'])
        self.dispositions = current_dispositions(self.controller)
        state, ev = self.sealed_fixture()
        validate_seal(state, ev, runtime=self.run, expected_package=self.package)
        self.assertEqual(ev['export_scope']['exported_node_ids'], [])
        self.assertEqual(ev['dependency_receipts'], {})
        self.assertNotIn('lemma ', ev['source_text'])
        self.assertNotIn('sorry', ev['source_text'])

    def test_disposed_draft_body_is_skipped_without_changing_received_source(self):
        from regen.planning.batch_file_planner import disposition_check_source
        from regen.planning.theory_document import render, parse
        draft = deepcopy(self.state); draft['proofs']['b'] = 'by fail'
        imports = ['Semantic_Base.Semantic_Root']
        source = render(self.package, draft, imports)
        checked, selected = disposition_check_source(self.package, self.state, source,
                                                     imports, None, self.dispositions)
        self.assertEqual(selected, ['a'])
        _, proofs = parse(checked, self.package, imports)
        self.assertEqual(proofs, self.state['proofs'])
        self.assertIn('by fail', source)
        self.assertNotIn('by fail', checked)
        self.assertIn('lemma b: "False"\n(* REGEN-PROOF *)\noops', checked)
        with self.assertRaisesRegex(ValueError, 'disposed contract'):
            disposition_check_source(self.package, self.state, source, imports, ['b'], self.dispositions)

    def test_disposition_projection_cannot_drop_an_accepted_proof(self):
        from regen.planning.batch_file_planner import disposition_check_source
        from regen.planning.theory_document import render
        draft = deepcopy(self.state); draft['proofs'] = {'b': 'by fail'}
        imports = ['Semantic_Base.Semantic_Root']
        source = render(self.package, draft, imports)
        with self.assertRaisesRegex(ValueError, 'retain every accepted'):
            disposition_check_source(self.package, self.state, source, imports, None, self.dispositions)

    def test_planner_sees_disposed_fixed_target_without_scheduling_it_as_pending(self):
        from regen.planning.batch_file_planner import planner_input, prepare_prompt
        payload = planner_input(self.package, self.state, dispositions=self.dispositions)
        self.assertEqual(payload['unproved_node_ids'], [])
        self.assertEqual(payload['pending_fixed_contract_ids'], [])
        self.assertEqual(payload['contract_dispositions']['b']['status'], 'unknown')
        self.assertEqual([n['id'] for n in payload['graph']['nodes']], ['a', 'b'])
        compact, prompt = prepare_prompt(payload)
        self.assertIn('b', compact['contract_dispositions'])
        self.assertIn('also permits a partial export', prompt)
        self.assertIn('never sorry', prompt)
        _, legacy_prompt = prepare_prompt(planner_input(self.package, self.state))
        self.assertNotIn('CONTRACT DISPOSITIONS POLICY', legacy_prompt)


if __name__ == '__main__':
    unittest.main()
