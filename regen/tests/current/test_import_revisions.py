"""Synthetic journal/import tests only; native behavior has a separate smoke test."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from regen.execution.import_revisions import (
    CONTEXT_QUERY_FIELDS, checkpoint_imports, effective_imports, native_imports,
    revised_state, provider_summary, validate_provider_binding, commit_import_change,
)
from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.repository_execution import context_graph, read, validator
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.planning.proof_graph import build_graph
from regen.planning.theory_document import candidate, render, check_document
from regen.tests.current.test_proof_commits import fixture
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.seal_fixture import seal
from regen.tests.current.test_import_semantics import BASE, synthetic_semantics


class ImportRevisionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = {'run_id': 'r', 'implementation': {}, 'session_schedule': {'source': 'synthetic'},
                    'base_import': BASE[0], 'base_session': 'Semantic_Base'}
        self.consumer, self.consumer_dir, self.log = self.make_journal('Consumer')
        provider, directory, provider_log = self.make_journal('Provider')
        sealed = deepcopy(provider_log.replay())
        provider_log.append('seal', sealed, seal(sealed))
        self.provider = {'checkpoint': generated_checkpoint_theory(provider['file_slot']),
            'package': provider, 'origin': {'run': self.run, 'imports': [],
                'commits': [read(p) for p in sorted((directory/'acceptance/commits').glob('*.json'))]}}

    def make_journal(self, name):
        package, state = fixture()
        package.update(theory=name, file_slot=name + '.thy')
        package['package_payload_sha256'] = digest(package)
        helpers = [n for n in state['graph']['nodes'] if n['role'] != 'contract']
        state['graph'] = context_graph(build_graph(package, {'generated_nodes': helpers},
                                                 run_id='r', revision=1), [])
        source = render(package, state, BASE)
        initial = candidate(package, state, source, BASE)
        initial.update(batch_requests=7, batch_history=[{'status': 'failed', 'request': 6}],
                       batch_notes='Keep planning memory')
        initial['rounds'] = {'contract': 3}
        initial['revisions'] = {'contract': 2}
        for field in CONTEXT_QUERY_FIELDS:
            initial[field] = {'synthetic-old-context': True}
        directory = self.root/name
        binding = {**self.run['session_schedule'], 'run_id': self.run['run_id'],
            'implementation': digest(self.run['implementation']),
            'package_sha256': package['package_payload_sha256'], 'imports': []}
        log = ProofCommits(directory/'acceptance', binding, validator(package, self.run, []))
        checker = Checker()
        log.append('initial', initial, {'check': checker.check_graph(initial['graph'])})
        complete_source = source.replace('oops', 'by simp')
        complete = candidate(package, initial, complete_source, BASE)
        evidence = check_document(checker, initial, complete, complete_source)
        evidence['sealable_generation'] = True
        log.append('document', complete, evidence)
        return package, directory, log

    def prepare_commit(self, ancestors, providers=None, old=None):
        old = self.log.replay() if old is None else old
        providers = [self.provider] if providers is None else providers
        semantics = synthetic_semantics(self.consumer, native_imports(self.run, effective_imports(old, [])),
                                        native_imports(self.run, ancestors))
        evidence = {'check': semantics['after']['check'], 'semantic_check': semantics}
        prepared = self.consumer_dir/'contexts'/digest({'imports': ancestors})/'sealed-premises.json'
        durable(prepared, {'records': [{'record_id': 'synthetic-new-context'}]})
        runtime = (self.run, self.consumer, self.consumer_dir, self.log, SimpleNamespace(), ancestors)
        return old, runtime, evidence, providers

    def test_acceptance_replays_and_clears_current_proofs_and_derived_queries_only(self):
        old, runtime, evidence, providers = self.prepare_commit([self.provider['checkpoint']])
        original = deepcopy(old)
        accepted = commit_import_change(runtime, old, evidence, providers)
        self.assertEqual(old, original)
        self.assertEqual(accepted['context_revision'], 1)
        self.assertEqual(accepted['context_imports'], [self.provider['checkpoint']])
        self.assertEqual(accepted['proofs'], {})
        self.assertEqual(accepted['accepted_order'], [])
        self.assertEqual(accepted['graph']['accepted_order'], [])
        for field in CONTEXT_QUERY_FIELDS:
            self.assertNotIn(field, accepted)
        for field in ('rounds', 'revisions', 'batch_requests', 'batch_history', 'batch_notes'):
            self.assertEqual(accepted[field], original[field])
        self.assertEqual(read(self.consumer_dir/'sealed-premises.json')['records'],
                         [{'record_id': 'synthetic-new-context'}])
        reopened = ProofCommits(self.log.directory, self.log.binding,
                               validator(self.consumer, self.run, []))
        self.assertEqual(reopened.replay(), accepted)
        prior = reopened.replay_snapshot(commit_id=read(self.log.directory/'commits/000002.json')['commit_id'])
        self.assertEqual(prior['state']['proofs'], original['proofs'])

    def test_add_delete_add_keep_initial_binding_and_advance_context_versions(self):
        for revision, ancestors in enumerate(([self.provider['checkpoint']], [], [self.provider['checkpoint']]), 1):
            old, runtime, evidence, providers = self.prepare_commit(ancestors,
                [self.provider] if ancestors else [])
            state = commit_import_change(runtime, old, evidence, providers)
            self.assertEqual(state['context_revision'], revision)
            self.assertEqual(effective_imports(state, []), ancestors)
            self.assertEqual(self.log.binding['imports'], [])
        self.assertEqual(self.log.replay()['batch_requests'], 7)

    def test_rejected_semantics_does_not_publish_context_or_recall_projection(self):
        old, runtime, evidence, providers = self.prepare_commit([self.provider['checkpoint']])
        durable(self.consumer_dir/'sealed-premises.json', {'records': ['old']})
        evidence['semantic_check']['valid'] = False
        before_files = list((self.log.directory/'commits').glob('*.json'))
        with self.assertRaises(ValueError):
            commit_import_change(runtime, old, evidence, providers)
        self.assertEqual(self.log.replay(), old)
        self.assertEqual(list((self.log.directory/'commits').glob('*.json')), before_files)
        self.assertEqual(read(self.consumer_dir/'sealed-premises.json'), {'records': ['old']})

    def test_provider_requires_same_run_certified_terminal_chain(self):
        self.assertEqual(validate_provider_binding(self.provider, self.run), [])
        changes = (
            lambda p: p['origin']['run'].update(run_id='another-run'),
            lambda p: p['origin']['commits'].pop(),
            lambda p: p.update(checkpoint='Regen_Wrong'),
            lambda p: p['package'].update(package_payload_sha256='wrong'),
        )
        for change in changes:
            provider = deepcopy(self.provider)
            change(provider)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_provider_binding(provider, self.run)

    def test_rehashed_provider_native_tampering_still_fails(self):
        provider = deepcopy(self.provider)
        terminal = provider['origin']['commits'][-1]
        terminal['evidence']['complete']['log_content'] = 'tampered'
        terminal['commit_id'] = digest({k: v for k, v in terminal.items() if k != 'commit_id'})
        with self.assertRaisesRegex(ValueError, 'evidence bytes mismatch'):
            validate_provider_binding(provider, self.run)

    def test_event_cannot_keep_old_green_or_refund_costs_or_mismatch_provider_versions(self):
        old, runtime, native, providers = self.prepare_commit([self.provider['checkpoint']])
        ancestors = runtime[-1]
        evidence = {**native, 'previous_imports': [], 'context_imports': ancestors,
                    'context_revision': 1, 'imports': native_imports(self.run, ancestors),
                    'provider_bindings': providers}
        new = revised_state(old, ancestors, 1, [provider_summary(p) for p in providers])
        changes = (
            lambda s: s['rounds'].clear(),
            lambda s: s.update(proofs=old['proofs'], accepted_order=old['accepted_order']),
            lambda s: s['context_provider_bindings'][0].update(seal_id='wrong'),
            lambda s: s.update(context_revision=True),
        )
        for change in changes:
            bad = deepcopy(new)
            change(bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.log.append('imports', bad, evidence)
        self.assertEqual(self.log.replay(), old)

    def test_changed_context_cannot_be_smuggled_into_feedback(self):
        old = self.log.replay()
        new = deepcopy(old)
        new.update(context_imports=[], context_revision=1, context_provider_bindings=[])
        with self.assertRaisesRegex(ValueError, 'outside an imports revision'):
            self.log.append('planner-feedback', new, {})

    def test_noop_and_stale_snapshot_do_not_commit(self):
        old, runtime, evidence, providers = self.prepare_commit([], [])
        with self.assertRaisesRegex(ValueError, 'change the context'):
            commit_import_change(runtime, old, evidence, providers)
        stale = deepcopy(old)
        stale['batch_notes'] = 'outdated'
        with self.assertRaisesRegex(ValueError, 'changed while preparing'):
            commit_import_change(runtime, stale, evidence, providers)

    def test_native_import_names_are_explicit_and_canonical(self):
        self.assertEqual(checkpoint_imports(self.run, BASE), [])
        self.assertEqual(checkpoint_imports(self.run, ['Generated_Refine.Regen_Provider']), ['Regen_Provider'])
        for imports in ([], ['Other.Library'], BASE + ['Generated_Refine.Regen_Provider'], BASE + BASE):
            with self.subTest(imports=imports), self.assertRaises(ValueError):
                checkpoint_imports(self.run, imports)


if __name__ == '__main__':
    unittest.main()
