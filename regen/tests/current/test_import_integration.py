"""Current import DAGs across real journals, integration and continuation.

All native receipts are synthetic fixtures; these are acceptance/replay tests,
not claims about mathematical validity or a live Isabelle build.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import shutil
import tempfile
import unittest

from regen.execution import repository_execution as execution
from regen.execution.import_revisions import (
    _provider_bindings, commit_import_change, effective_imports, native_imports,
)
from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.proof_session_runner import generated_checkpoint_theory
from regen.execution.repository_integration import integration_manifest
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.planning.declaration_index import accepted_declarations, initial_index
from regen.planning.theory_document import candidate, render, check_document
from regen.tests.current.seal_fixture import seal, bound
from regen.tests.current.test_import_semantics import BASE, synthetic_semantics
from regen.tests.current.test_proof_batches import Checker


class ImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'source'

    def initialize(self, *, planned_provider=False, contract_outcome_policy=None):
        self.packages = {}
        for name in ('Consumer', 'Provider'):
            providers = ['Provider'] if planned_provider and name == 'Consumer' else []
            contract = name.lower() + '_goal'
            claim = 'False' if contract_outcome_policy and name == 'Consumer' else 'True'
            package = {'theory': name, 'file_slot': name + '.thy',
                'repository_projection': {'schema_version': 6,
                    'required_imports': [{'theory': p, 'reason': 'Synthetic plan requirement'} for p in providers]},
                'contracts': [{'contract_id': contract, 'name': contract,
                    'exact_statement': f'lemma {contract}: "{claim}"', 'contract_owned_context': []}]}
            package['package_payload_sha256'] = digest(package)
            self.packages[name] = package
            durable(self.root/'packages'/(name+'.thy.json'), package)
        files = [{'theory': p['theory'], 'file_slot': p['file_slot'],
                  'contract_ids': [c['contract_id'] for c in p['contracts']]}
                 for p in self.packages.values()]
        listing = {'files': files}
        self.run = {'run_id': 'dynamic-integration-fixture', 'implementation': {},
            'execution_mode': 'repository-projection-v1', 'contract_package_dir': str(self.root/'packages'),
            'session_schedule': {'file_list_sha256': digest(listing)},
            'base_import': BASE[0], 'base_session': 'Semantic_Base'}
        if contract_outcome_policy is not None:
            self.run['contract_outcome_policy'] = contract_outcome_policy
        durable(self.root/'run.json', self.run)
        durable(self.root/'execution-input.json', {'file_list': listing, 'files': files,
            'package_hashes': {p['file_slot']: p['package_payload_sha256'] for p in self.packages.values()}})
        for name, package in self.packages.items():
            imports = ['Regen_Provider'] if planned_provider and name == 'Consumer' else []
            durable(self.root/'theories'/name/'imports.json', imports)
            index = initial_index(package, run_id=self.run['run_id']+'--'+name)
            index.update(initial_graph_owner='File Planner', runtime_graph_owner='File Planner',
                         execution_mode='file-planner-batch-v1')
            state = {'graph': accepted_declarations(index, []), 'proofs': {}, 'accepted_order': [],
                     'rounds': {}, 'revisions': {}, 'status': 'generating', 'seal_id': None}
            checker = Checker()
            checker.imports = lambda imports=imports: native_imports(self.run, imports)
            self.log(name).append('initial', state, {'check': checker.check_graph(state['graph'])})
        self.seal_theory('Provider')

    def log(self, name):
        return execution.journal(self.root, self.run, self.packages[name])

    def revise(self, imports):
        package = self.packages['Consumer']
        log = self.log('Consumer')
        old = log.replay()
        directory = self.root/'theories/Consumer'
        semantics = synthetic_semantics(package,
            native_imports(self.run, effective_imports(old, log.binding['imports'])),
            native_imports(self.run, imports))
        durable(directory/'contexts'/digest({'imports': imports})/'sealed-premises.json', {'records': []})
        runtime = (self.run, package, directory, log, SimpleNamespace(), imports)
        return commit_import_change(runtime, old,
            {'check': semantics['after']['check'], 'semantic_check': semantics},
            _provider_bindings(self.root, self.run, imports))

    def accept_document(self, name):
        package, log = self.packages[name], self.log(name)
        old = log.replay()
        imports = native_imports(self.run, effective_imports(old, log.binding['imports']))
        source = render(package, old, imports).replace('\noops\n', '\nby simp\n')
        complete = candidate(package, old, source, imports)
        checker = Checker()
        checker.imports = lambda: imports
        evidence = check_document(checker, old, complete, source)
        evidence['sealable_generation'] = True
        log.append('document', complete, evidence)
        return complete, imports

    def seal_theory(self, name):
        package, log = self.packages[name], self.log(name)
        complete, imports = self.accept_document(name)
        state = deepcopy(complete)
        evidence = seal(state, imports)
        providers = effective_imports(state, log.binding['imports'])
        if providers:
            generated = generated_checkpoint_theory(package['file_slot'])
            evidence['build']['theories'] = [*providers, generated]
            evidence['build']['source_files'].update({p: (self.root/'generated'/(p+'.thy')).read_text()
                                                      for p in providers})
            root = '\n'.join(['session Generated_Refine = Semantic_Base +',
                '  sessions', '    Lib', '    CorresK', '    AInvs', '  theories',
                *('    '+p for p in providers), '',
                'session Sealed_Refine in "sealed-session" = Generated_Refine +',
                '  sessions Lib CorresK AInvs', '  theories', '    '+generated, ''])
            evidence['build'].update(bound('root', root))
            state['seal_id'] = 'theory-seal:' + digest(evidence)[:20]
        log.append('seal', state, evidence)
        (self.root/'generated').mkdir(exist_ok=True)
        (self.root/'generated'/(generated_checkpoint_theory(package['file_slot'])+'.thy')).write_text(evidence['source_text'])

    def test_added_import_controls_final_source_and_topological_build_order(self):
        self.initialize()
        self.revise(['Regen_Provider'])
        self.seal_theory('Consumer')
        _, manifest, sources = integration_manifest(self.root)
        self.assertEqual([row['theory'] for row in manifest['theories']], ['Provider', 'Consumer'])
        consumer = manifest['theories'][1]
        self.assertEqual(consumer['required_imports'], ['Provider'])
        self.assertEqual(consumer['context_revision'], 1)
        self.assertIn('Generated_Refine.Regen_Provider', sources['Regen_Consumer'])
        self.assertEqual(execution.read(self.root/'theories/Consumer/imports.json'), [])

    def test_deleted_planned_import_is_absent_from_final_graph_and_source(self):
        self.initialize(planned_provider=True)
        self.revise([])
        self.seal_theory('Consumer')
        _, manifest, sources = integration_manifest(self.root)
        self.assertEqual([row['theory'] for row in manifest['theories']], ['Consumer', 'Provider'])
        self.assertEqual(manifest['theories'][0]['required_imports'], [])
        self.assertNotIn('Generated_Refine.Regen_Provider', sources['Regen_Consumer'])
        self.assertIn(BASE[0], sources['Regen_Consumer'])
        self.assertEqual(execution.read(self.root/'theories/Consumer/imports.json'), ['Regen_Provider'])

    def test_continuation_retains_dynamic_provider_closure_and_replays_original_run_evidence(self):
        self.initialize()
        revised = self.revise(['Regen_Provider'])
        entry = execution.read(self.root/'execution-input.json')['files'][0]
        descriptor = execution._preflight_workspace_continuation(self.root, self.run, entry, None)
        self.assertEqual(descriptor['imports'], [])
        self.assertEqual(descriptor['effective_imports'], ['Regen_Provider'])
        providers = execution._sealed_provider_inputs(self.root, self.run, descriptor['effective_imports'])
        self.assertEqual([p['theory'] for p, _, _ in providers], ['Provider'])
        destination = self.root.parent/'continued'
        destination.mkdir()
        shutil.copytree(self.root/'packages', destination/'packages')
        shutil.copyfile(self.root/'execution-input.json', destination/'execution-input.json')
        continued = {**self.run, 'run_id': 'continued-fixture', 'contract_package_dir': str(destination/'packages')}
        durable(destination/'run.json', continued)
        execution._reuse_sealed_theories(destination, continued, self.root, providers)
        execution._carry_workspace_continuation(self.root, self.run, destination, continued, descriptor, None)
        snapshot = SharedProofWorkspace(destination)._snapshot('Consumer')
        self.assertEqual(snapshot['state']['context_imports'], ['Regen_Provider'])
        self.assertEqual(snapshot['state']['context_revision'], 1)
        self.assertEqual(snapshot['state']['graph'], revised['graph'])
        self.assertEqual(snapshot['state']['proofs'], {})
        self.assertEqual(_provider_bindings(destination, continued, ['Regen_Provider'])[0]['origin']['run'], continued)
        self.assertEqual(execution.read(destination/'theories/Consumer/imports.json'), [])

    def test_later_refutation_does_not_rebind_historical_import_events_on_cold_replay(self):
        from regen.execution.contract_refutation import check_refutation
        from regen.planning.theory_outcomes import CONTRACT_POLICY, _publish, current_dispositions
        from regen.tests.current.test_theory_outcome_continuation import SyntheticNativeChecker
        self.initialize(contract_outcome_policy=CONTRACT_POLICY)
        self.revise(['Regen_Provider'])
        accepted = self.revise([])
        package = self.packages['Consumer']
        checker = SyntheticNativeChecker(self.root, 'consumer_goal')
        checker.imports = lambda: BASE
        result = check_refutation(checker, package['contracts'][0], 'by simp', self.root/'negative-fixture')
        view = SimpleNamespace(root=self.root, run=self.run, theory='Consumer',
                               _state=lambda _: (package, self.log('Consumer').replay()))
        _publish(view, status='refuted', reason='Synthetic native transport fixture', origin='native_checker',
                 details={'certificate': result['certificate']}, entry_ids=['consumer_goal'])
        log = self.log('Consumer')
        cold = ProofCommits(log.directory, log.binding, log.validate)
        self.assertEqual(cold.replay(repair=False), accepted)
        self.assertEqual(current_dispositions(view)['consumer_goal']['binding']['context_revision'], 2)

    def test_new_context_unknown_preserves_old_green_history_but_blocks_new_positive_acceptance(self):
        from regen.planning.theory_outcomes import CONTRACT_POLICY, current_dispositions, record_unresolved
        self.initialize(contract_outcome_policy=CONTRACT_POLICY)
        old, _ = self.accept_document('Consumer')
        self.assertIn('consumer_goal', old['proofs'])
        accepted = self.revise(['Regen_Provider'])
        package = self.packages['Consumer']
        view = SimpleNamespace(root=self.root, run=self.run, theory='Consumer',
                               _state=lambda _: (package, self.log('Consumer').replay()))
        record_unresolved(view, reason='Synthetic revised-context proof search exhausted',
                          origin='model', entry_ids=['consumer_goal'])
        log = self.log('Consumer')
        cold = ProofCommits(log.directory, log.binding, log.validate)
        self.assertEqual(cold.replay(repair=False), accepted)
        self.assertEqual(current_dispositions(view)['consumer_goal']['status'], 'unknown')
        with self.assertRaisesRegex(ValueError, 'positive proof'):
            self.accept_document('Consumer')


if __name__ == '__main__':
    unittest.main()
