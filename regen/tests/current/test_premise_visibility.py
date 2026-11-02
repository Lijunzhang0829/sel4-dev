"""Synthetic checker evidence, real acceptance journals and read-only discovery.

These tests do not execute Isabelle and do not assert native fact applicability.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from regen.execution.import_revisions import commit_import_change, native_imports
from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import context_graph, journal, read
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.planning.proof_graph import build_graph
from regen.planning.proof_workspace_agent import WorkspaceController
from regen.planning.theory_document import candidate, check_document, render
from regen.tests.current.seal_fixture import seal
from regen.tests.current.test_import_semantics import BASE, synthetic_semantics
from regen.tests.current.test_proof_batches import Checker


class PremiseVisibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.packages, self.logs = {}, {}
        files = [{'theory': name, 'file_slot': name + '.thy'} for name in ('Source', 'Target', 'Third')]
        listing = {'files': files}
        self.run = {'run_id': 'visibility-run', 'execution_mode': 'repository-projection-v1',
            'base_session': 'Semantic_Base', 'base_import': BASE[0], 'implementation': {},
            'contract_package_dir': str(self.root / 'packages'),
            'session_schedule': {'file_list_sha256': digest(listing), 'plan_id': 'visibility-plan'}}
        for row in files:
            name = row['theory']
            key = name.lower() + '_contract'
            package = {'theory': name, 'file_slot': row['file_slot'],
                'repository_projection': {'nodes': []}, 'contracts': [{
                    'contract_id': key, 'name': key, 'exact_statement': f'lemma {key}: "True"',
                    'contract_owned_context': []}]}
            package['package_payload_sha256'] = digest(package)
            self.packages[name] = package
            durable(self.root / 'packages' / (row['file_slot'] + '.json'), package)
        durable(self.root / 'run.json', self.run)
        durable(self.root / 'execution-input.json', {'file_list': listing, 'files': files,
            'package_hashes': {p['file_slot']: p['package_payload_sha256'] for p in self.packages.values()}})
        self.checker = Checker()
        for name, package in self.packages.items():
            graph = context_graph(build_graph(package, {'generated_nodes': []},
                                              run_id='visibility-run--' + name, revision=1), [])
            state = {'graph': graph, 'proofs': {}, 'accepted_order': [], 'rounds': {},
                     'revisions': {}, 'status': 'planned', 'seal_id': None}
            source = render(package, state, BASE)
            initial = candidate(package, state, source, BASE)
            directory = self.root / 'theories' / name
            durable(directory / 'imports.json', [])
            log = journal(self.root, self.run, package)
            log.append('initial', initial, {'check': self.checker.check_graph(initial['graph'])})
            self.logs[name] = log
        self.workspace = SharedProofWorkspace(self.root)

    def prove(self, name):
        old = self.logs[name].replay(repair=False)
        package = self.packages[name]
        imports = native_imports(self.run, old.get('context_imports', []))
        source = render(package, old, imports).replace('oops', 'by simp')
        new = candidate(package, old, source, imports)
        checker = Checker()
        checker.imports = lambda: imports
        evidence = check_document(checker, old, new, source)
        evidence['sealable_generation'] = True
        self.logs[name].append('document', new, evidence)
        return new

    def seal(self, name):
        state = deepcopy(self.logs[name].replay(repair=False))
        self.logs[name].append('seal', state,
                               seal(state, native_imports(self.run, state.get('context_imports', []))))

    def change_imports(self, consumer, providers):
        bindings = []
        for name in providers:
            directory = self.root / 'theories' / name
            bindings.append({'checkpoint': 'Regen_' + name, 'package': self.packages[name],
                'origin': {'run': self.run, 'imports': [],
                    'commits': [read(p) for p in sorted((directory / 'acceptance/commits').glob('*.json'))]}})
        old = self.logs[consumer].replay(repair=False)
        names = ['Regen_' + name for name in providers]
        directory = self.root / 'theories' / consumer
        semantics = synthetic_semantics(self.packages[consumer],
            native_imports(self.run, old.get('context_imports', [])), native_imports(self.run, names))
        durable(directory / 'contexts' / digest({'imports': names}) / 'sealed-premises.json', {'records': []})
        runtime = (self.run, self.packages[consumer], directory,
                   self.logs[consumer], SimpleNamespace(), names)
        return commit_import_change(runtime, old,
            {'check': semantics['after']['check'], 'semantic_check': semantics}, bindings)

    def source(self, target='Target'):
        return self.workspace.index(theory='Source', target_theory=target)

    def test_pending_green_sealed_imported_and_removed_are_distinct(self):
        pending = self.source()
        self.assertEqual(pending['records'], [])
        self.assertEqual(pending['theories'][0]['pending_contract_count'], 1)
        self.prove('Source')
        green = self.source()['records'][0]
        self.assertEqual(green['visibility']['status'], 'accepted_source_unsealed')
        self.assertTrue(green['visibility']['needs_import'])
        self.assertFalse(green['visibility']['provider_exports_available'])
        ref = green['source_ref']
        self.seal('Source')
        sealed = self.source()['records'][0]
        self.assertEqual(sealed['visibility']['status'], 'sealed_requires_import')
        self.assertTrue(sealed['visibility']['provider_exports_available'])
        self.change_imports('Target', ['Source'])
        imported = self.source()
        row = imported['records'][0]['visibility']
        self.assertEqual(row['status'], 'imported_provider_version_verified')
        self.assertFalse(row['needs_import'])
        self.assertEqual(row['context_revision'], 1)
        self.assertFalse(row['native_availability_checked'])
        self.assertFalse(row['applicability_checked'])
        old_ref = self.workspace.read([ref], target_theory='Target')['records'][0]
        self.assertTrue(old_ref['visibility']['source_content_matches_current'])
        self.assertNotEqual(old_ref['visibility']['source_reference_commit_id'],
                            old_ref['visibility']['provider_commit_id'])
        self.change_imports('Target', [])
        removed = self.source()
        self.assertEqual(removed['records'][0]['visibility']['status'], 'sealed_requires_import')
        self.assertNotEqual(imported['target_context']['environment_id'],
                            removed['target_context']['environment_id'])
        self.assertEqual(removed['target_context']['context_revision'], 2)

    def test_transitive_imports_bind_each_provider_version(self):
        self.prove('Source'); self.seal('Source')
        self.change_imports('Third', ['Source'])
        self.prove('Third'); self.seal('Third')
        self.change_imports('Target', ['Third'])
        result = self.source()
        row = result['records'][0]['visibility']
        self.assertEqual(row['status'], 'imported_provider_version_verified')
        self.assertEqual(result['target_context']['committed_imports'], ['Regen_Third'])
        self.assertEqual(set(result['target_context']['provider_versions']), {'Source', 'Third'})

    def test_draft_import_text_does_not_change_accepted_visibility_or_write_files(self):
        self.prove('Source'); self.seal('Source')
        draft = self.root / 'theories/Target/draft.thy'
        draft.write_text('theory Target imports Generated_Refine.Regen_Source begin end')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.source()
        self.assertEqual(result['target_context']['committed_imports'], [])
        self.assertFalse(result['target_context']['draft_imports_considered'])
        self.assertEqual(result['records'][0]['visibility']['status'], 'sealed_requires_import')
        self.workspace.read([result['records'][0]['source_ref']], target_theory='Target')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_import_revision_retires_old_green_but_keeps_historical_reference(self):
        self.prove('Source')
        reference = self.source()['records'][0]['source_ref']
        self.prove('Third'); self.seal('Third')
        self.change_imports('Source', ['Third'])
        self.assertEqual(self.source()['records'], [])
        old = self.workspace.read([reference], target_theory='Target')['records'][0]
        self.assertEqual(old['visibility']['status'], 'historical_source_requires_recheck')
        self.assertFalse(old['visibility']['source_content_matches_current'])
        self.assertTrue(old['reference_only'])

    def test_provider_version_tampering_is_rejected_before_visibility(self):
        self.prove('Source'); self.seal('Source')
        self.change_imports('Target', ['Source'])
        valid = self.source()
        old = self.logs['Target'].replay(repair=False)
        forged = deepcopy(old)
        forged['context_provider_bindings'][0]['commit_id'] = 'different-provider-version'
        with self.assertRaises(ValueError):
            self.logs['Target'].append('planner-feedback', forged, {})
        self.assertEqual(self.source(), valid)
        source_commit = self.root / 'theories/Source/acceptance/commits/000003.json'
        damaged = json.loads(source_commit.read_text())
        damaged['state']['seal_id'] = 'different-seal'
        source_commit.write_text(json.dumps(damaged))
        with self.assertRaises(ValueError):
            self.source()

    def test_same_theory_unbound_and_assigned_controller_context(self):
        self.prove('Source')
        same = self.source(target='Source')['records'][0]['visibility']
        self.assertEqual(same['status'], 'current_theory_accepted_reference')
        unknown = self.source(target=None)['records'][0]['visibility']
        self.assertEqual(unknown['status'], 'target_not_bound')
        controller = object.__new__(WorkspaceController)
        controller.shared = self.workspace
        controller.theory = 'Target'
        index = controller.handle('list_generated', {'theory': 'Source'}, {})
        self.assertEqual(index['target_context']['target_theory'], 'Target')
        record = controller.handle('read_generated', {'source_refs': [index['records'][0]['source_ref']]}, {})
        self.assertEqual(record['records'][0]['visibility']['status'], 'accepted_source_unsealed')

    def test_environment_identity_tracks_imports_and_prefix_not_feedback_commits(self):
        self.prove('Source')
        before = self.source()['target_context']
        unchanged = deepcopy(self.logs['Target'].replay(repair=False))
        unchanged['batch_notes'] = 'Read an unrelated explanation'
        self.logs['Target'].append('planner-feedback', unchanged, {})
        after_feedback = self.source()['target_context']
        self.assertEqual(before['environment_id'], after_feedback['environment_id'])
        self.assertNotEqual(before['target_commit_id'], after_feedback['target_commit_id'])
        self.prove('Target')
        after_prefix = self.source()['target_context']
        self.assertNotEqual(after_feedback['environment_id'], after_prefix['environment_id'])


if __name__ == '__main__':
    unittest.main()
