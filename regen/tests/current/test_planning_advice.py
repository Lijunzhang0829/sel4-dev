"""Synthetic authority-state tests; these tests do not claim native proof checks."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.planning.declaration_index import initial_index, accepted_declarations
from regen.planning.planning_advice import static_advice, planning_advice
from regen.planning.theory_document import DocumentStore, render


def contract(key, context=None):
    return {'contract_id': key, 'name': key,
            'exact_statement': f'lemma {key}: "True"',
            'contract_owned_context': context or [], 'contract_owned_attributes': ['simp'],
            'contract_owned_attributes_raw': '[simp]'}


def package(name, keys):
    return {'theory': name, 'file_slot': name + '.thy',
            'contracts': [contract(key) for key in keys], 'repository_projection': {}}


def state_for(pkg, accepted=(), status='planned', imports=None):
    graph = initial_index(pkg, run_id='test--' + pkg['theory'])
    graph = accepted_declarations(graph, accepted)
    return {'graph': graph, 'proofs': {key: 'by simp' for key in accepted},
            'accepted_order': list(accepted), 'status': status,
            'seal_id': 'test-seal' if status in ('sealed', 'exported_partial') else None,
            'context_imports': list(imports or []), 'context_revision': 0}


def suggest(pkg, keys, target='target', premise_ids=None):
    pkg['repository_projection']['proof_direction_suggestions'] = [{
        'id': 'direction-1', 'target_contract_id': target,
        'direction': 'Try the candidates if useful; otherwise prove directly.',
        'candidate_contract_ids': list(keys), 'candidate_premise_ids': premise_ids or [],
        'open_questions': ['Does the proposed reuse actually apply?']}]


class Shared:
    def __init__(self, packages, states):
        self.packages = {p['theory']: p for p in packages}
        self.states = states
        self.entries = {p['theory']: {'file_slot': p['file_slot'],
            'contract_ids': [c['contract_id'] for c in p['contracts']]} for p in packages}
        self.fixed_contract_ids = [c['contract_id'] for p in packages for c in p['contracts']]
        self.read_packages, self.read_snapshots = [], []

    def _package(self, theory):
        self.read_packages.append(theory)
        return self.packages[theory]

    def _snapshot(self, theory):
        self.read_snapshots.append(theory)
        state = self.states.get(theory)
        return None if state is None else {'state': state, 'commit_id': digest(state),
            'directory': '/never-read-when-context-imports-present', 'package': self.packages[theory]}


class PlanningAdviceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = package('Target', ['first', 'target', 'later'])
        self.state = state_for(self.package)
        self.shared = Shared([self.package], {'Target': self.state})
        self.controller = SimpleNamespace(root=self.root, run={'run_id': 'test', 'base_import': 'Base.Root'},
            shared=self.shared, _runtime=lambda *_: self.fail('advice must not create a checker'))
        self.store = DocumentStore(self.root/'document', self.package, self.state,
                                   ['Base.Root'], allow_import_edits=True)

    def reset_source(self):
        self.store.state = self.state
        self.store.path.write_text(render(self.package, self.state, self.store.checked_imports))

    def candidates(self, entry_id='target'):
        result = planning_advice(self.controller, self.store, self.state, entry_id)
        return {item['contract_id']: item for item in result['proof_direction_suggestions'][0]['candidate_contracts']}

    def test_absent_and_helper_advice_are_empty_and_legacy_intent_survives(self):
        self.assertEqual(static_advice(self.package), {})
        self.package['repository_projection']['helper_decisions'] = [
            {'id': 'helper-plan', 'intent': 'derive a monotonicity lemma', 'for_contracts': ['target']}]
        self.assertEqual(static_advice(self.package, 'helper-entry'), {})
        self.assertEqual(static_advice(self.package, 'first'), {})
        self.assertEqual(static_advice(self.package, 'target')['intents']['helper_decisions'][0]['id'], 'helper-plan')

    def test_pending_draft_proof_is_never_positive_authority(self):
        suggest(self.package, ['first'])
        draft = state_for(self.package, ['first'])
        self.store.path.write_text(render(self.package, draft, self.store.checked_imports))
        row = self.candidates()['first']
        self.assertEqual(row['authority_status'], 'pending')
        self.assertFalse(row['can_use_as_current_premise'])
        self.assertIn('Prove it first', row['reason'])
        self.assertEqual(self.state['proofs'], {})

    def test_accepted_local_prefix_has_visibility_not_applicability(self):
        suggest(self.package, ['first', 'later'])
        self.state = state_for(self.package, ['first', 'later'])
        self.reset_source()
        rows = self.candidates()
        self.assertEqual(rows['first']['authority_status'], 'local_green')
        self.assertTrue(rows['first']['can_use_as_current_premise'])
        self.assertFalse(rows['first']['applicability_verified'])
        self.assertEqual(rows['later']['visibility_status'], 'local_after_target')
        self.assertFalse(rows['later']['can_use_as_current_premise'])

    def test_changed_proof_or_imports_requires_recheck(self):
        suggest(self.package, ['first'])
        self.state = state_for(self.package, ['first'])
        self.reset_source()
        self.store.path.write_text(self.store.path.read_text().replace('by simp', 'by auto'))
        self.assertEqual(self.candidates()['first']['visibility_status'], 'local_recheck_required')
        self.reset_source()
        self.store.path.write_text(self.store.path.read_text().replace('"Base.Root"', '"Other.Root"'))
        result = planning_advice(self.controller, self.store, self.state, 'target')
        self.assertTrue(result['imports_pending'])
        self.assertFalse(result['proof_direction_suggestions'][0]['candidate_contracts'][0]['can_use_as_current_premise'])

    def test_changed_earlier_proof_retires_later_candidate(self):
        suggest(self.package, ['later'])
        # Move target after both accepted declarations, preserving fixed identities.
        self.state = state_for(self.package, ['first', 'later'])
        self.state['graph']['document_order'] = ['first', 'later', 'target']
        self.state['graph']['execution_order'] = ['first', 'later', 'target']
        self.reset_source()
        self.assertTrue(self.candidates()['later']['can_use_as_current_premise'])
        self.store.path.write_text(self.store.path.read_text().replace('by simp', 'by auto', 1))
        self.assertFalse(self.candidates()['later']['can_use_as_current_premise'])

    def external(self, *, status='sealed', imported=True, accepted=True):
        source = package('Source', ['external'])
        untouched = package('Unrelated', ['untouched'])
        self.shared = Shared([self.package, source, untouched], {
            'Target': self.state, 'Source': state_for(source, ['external'] if accepted else [], status)})
        self.controller.shared = self.shared
        suggest(self.package, ['external'])
        self.store.checked_imports = ['Generated_Refine.Regen_Source'] if imported else ['Base.Root']
        self.reset_source()
        return source

    def test_external_sealed_imported_and_unrelated_packages_not_read(self):
        self.external()
        before = deepcopy(self.state)
        row = self.candidates()['external']
        self.assertEqual(row['visibility_status'], 'checked_import_visible')
        self.assertTrue(row['can_use_as_current_premise'])
        self.assertNotIn('Unrelated', self.shared.read_packages)
        self.assertNotIn('Unrelated', self.shared.read_snapshots)
        self.assertEqual(self.state, before)

    def test_external_statuses_and_checked_import_boundary(self):
        for status, imported, expected in [
                ('planned', True, 'unsealed_reference'),
                ('sealed', False, 'sealed_not_imported'),
                ('exported_partial', True, 'checked_import_visible')]:
            with self.subTest(status=status, imported=imported):
                self.external(status=status, imported=imported)
                self.assertEqual(self.candidates()['external']['visibility_status'], expected)
        self.external(imported=False)
        self.store.path.write_text(self.store.path.read_text().replace('"Base.Root"', '"Generated_Refine.Regen_Source"'))
        self.assertEqual(self.candidates()['external']['visibility_status'], 'sealed_not_imported')
        self.external(imported=True)
        self.store.path.write_text(self.store.path.read_text().replace('"Generated_Refine.Regen_Source"', '"Base.Root"'))
        self.assertEqual(self.candidates()['external']['visibility_status'], 'checked_import_visible_draft_pending')
        self.assertFalse(self.candidates()['external']['can_use_as_current_premise'])

    def test_transitive_import_visibility(self):
        self.external(imported=False)
        middle = package('Middle', ['middle'])
        self.shared.packages['Middle'] = middle
        self.shared.entries['Middle'] = {'file_slot': 'Middle.thy', 'contract_ids': ['middle']}
        self.shared.states['Middle'] = state_for(middle, ['middle'], 'sealed', ['Regen_Source'])
        self.store.checked_imports = ['Generated_Refine.Regen_Middle']
        self.reset_source()
        self.assertTrue(self.candidates()['external']['can_use_as_current_premise'])

    def test_unstarted_pending_and_false_owner_hint_fail_closed(self):
        self.external(accepted=False)
        self.shared.states['Source'] = None
        self.assertEqual(self.candidates()['external']['authority_status'], 'pending')
        del self.shared.entries['Source']['contract_ids']
        self.package['repository_projection']['proof_direction_contracts'] = [
            dict(contract('external'), provider_theory='Unrelated')]
        self.assertEqual(self.candidates()['external']['authority_status'], 'unresolved_reference')

    def test_disposed_candidates_never_become_premises(self):
        self.external(accepted=False)
        self.controller.run['contract_outcome_policy'] = 'contract-dispositions-v1'
        for status in ('refuted', 'unknown'):
            with self.subTest(status=status), patch(
                    'regen.planning.theory_outcomes._saved_dispositions',
                    return_value={'external': {'status': status, 'reason': 'test saved disposition'}}), patch(
                    'regen.planning.theory_outcomes.validate_dispositions', return_value=True):
                row = self.candidates()['external']
                self.assertEqual(row['authority_status'], status)
                self.assertFalse(row['can_use_as_current_premise'])

    def test_real_unknown_receipt_is_read_without_recovery_or_writes(self):
        from regen.planning.theory_outcomes import record_unresolved
        suggest(self.package, ['first'])
        self.controller.run['contract_outcome_policy'] = 'contract-dispositions-v1'
        self.controller.theory = 'Target'
        self.controller._state = lambda _: (self.package, self.state)
        record_unresolved(self.controller, reason='Search exhausted for this optional candidate',
                          origin='model', entry_ids=['first'])
        before = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        row = self.candidates()['first']
        self.assertEqual(row['authority_status'], 'unknown')
        self.assertFalse(row['can_use_as_current_premise'])
        after = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_compact_full_and_target_filtering(self):
        self.external()
        projection = self.package['repository_projection']
        projection['proof_direction_suggestions'][0]['candidate_premise_ids'] = ['fact-1']
        projection['proof_direction_premises'] = [{'record_id': 'fact-1',
            'canonical_name': 'HOL.TrueI', 'exact_statement': 'True', 'attributes': ['intro']}]
        projection['proof_direction_contracts'] = [dict(contract('external'), provider_theory='Source')]
        # Runtime expansion uses the actual fixed package, never reference prose.
        projection['proof_direction_contracts'][0]['exact_statement'] = 'lemma forged: "False"'
        projection['proof_direction_suggestions'].append({
            'id': 'other', 'target_contract_id': 'later', 'direction': 'another route',
            'candidate_contract_ids': [], 'candidate_premise_ids': [], 'open_questions': []})
        compact = planning_advice(self.controller, self.store, self.state)
        full = planning_advice(self.controller, self.store, self.state, 'target')
        self.assertEqual(len(compact['proof_direction_suggestions']), 2)
        self.assertEqual(len(full['proof_direction_suggestions']), 1)
        c1 = compact['proof_direction_suggestions'][0]['candidate_contracts'][0]
        c2 = full['proof_direction_suggestions'][0]['candidate_contracts'][0]
        self.assertNotIn('exact_statement', c1)
        self.assertEqual(c2['exact_statement'], 'lemma external: "True"')
        self.assertEqual(c2['contract_owned_attributes'], ['simp'])
        self.assertEqual(c2['contract_owned_attributes_raw'], '[simp]')
        premise = full['proof_direction_suggestions'][0]['candidate_premises'][0]
        self.assertEqual(premise['record']['exact_statement'], 'True')
        self.assertFalse(premise['can_use_as_current_premise'])
        self.assertFalse(static_advice(self.package, 'target')['availability_checked'])
        self.assertEqual(static_advice(self.package, 'target')['candidate_premise_references'][0]['record_id'], 'fact-1')

    def test_real_workspace_reads_verified_source_without_head_repair(self):
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        from regen.tests.current.test_shared_proof_workspace import setup_run
        run, packages, initial, _ = setup_run(self.root/'real', contract_dependency=True)
        target = deepcopy(packages[1])
        suggest(target, ['source_contract'], target='target_contract')
        target['repository_projection']['proof_direction_contracts'] = [
            dict(packages[0]['contracts'][0], provider_theory='Source')]
        shared = SharedProofWorkspace(self.root/'real')
        controller = SimpleNamespace(root=self.root/'real', run=run, shared=shared)
        store = DocumentStore(self.root/'real-document', target, initial['Target'], [run['base_import']])
        head = self.root/'real/theories/Source/acceptance/HEAD.json'
        head.unlink()
        before = {str(path): path.read_bytes() for path in (self.root/'real').rglob('*') if path.is_file()}
        result = planning_advice(controller, store, initial['Target'], 'target_contract')
        row = result['proof_direction_suggestions'][0]['candidate_contracts'][0]
        self.assertEqual(row['authority_status'], 'unsealed_green')
        self.assertFalse(row['can_use_as_current_premise'])
        after = {str(path): path.read_bytes() for path in (self.root/'real').rglob('*') if path.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
