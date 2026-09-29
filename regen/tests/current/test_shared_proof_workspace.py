from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest, durable
from regen.execution.repository_execution import context_graph, journal
from regen.execution.proof_batches import prepare_batch, check_batch, validate_batch_commit
from regen.execution.shared_proof_workspace import SharedProofWorkspace, expand_reuse_helpers
from regen.planning.proof_graph import build_graph
from regen.tests.current.test_proof_batches import Checker


def setup_run(root, *, source_context=None, contract_dependency=False, target_started=True):
    source_context = source_context or []
    packages = []
    initial = {}
    for theory, contract, statement in [('Source', 'source_contract', 'False --> False'),
                                         ('Target', 'target_contract', '(0::nat)=0')]:
        package = {'theory': theory, 'file_slot': theory + '.thy',
                   'repository_projection': {'nodes': []}, 'contracts': [{
                       'contract_id': contract, 'name': contract,
                       'exact_statement': f'lemma {contract}: "{statement}"',
                       'contract_owned_context': source_context if theory == 'Source' else []}]}
        package['package_payload_sha256'] = digest(package)
        packages.append(package)
        helpers = [] if theory == 'Target' else [
            {'id': 'left', 'name': 'left', 'role': 'helper', 'statement': 'lemma left: "True & True"',
             'context': source_context, 'dependencies': []},
            {'id': 'right', 'name': 'right', 'role': 'helper', 'statement': 'lemma right: "True | True"',
             'context': source_context, 'dependencies': ['source_contract'] if contract_dependency else []}]
        graph = context_graph(build_graph(package, {'generated_nodes': helpers}, run_id='test-run--' + theory, revision=1), [])
        initial[theory] = {'graph': graph, 'proofs': {}, 'accepted_order': [], 'rounds': {},
                          'revisions': {}, 'status': 'planned', 'seal_id': None}
    listing = {'files': [{'theory': p['theory'], 'file_slot': p['file_slot']} for p in packages]}
    run = {'run_id': 'test-run', 'execution_mode': 'repository-projection-v1',
           'contract_package_dir': str(root / 'packages'), 'implementation': {},
           'session_schedule': {'file_list_sha256': digest(listing), 'plan_id': 'test-plan'}}
    durable(root / 'run.json', run)
    durable(root / 'execution-input.json', {'file_list': listing, 'files': listing['files'],
        'package_hashes': {p['file_slot']: p['package_payload_sha256'] for p in packages}})
    for package in packages:
        durable(root / 'packages' / (package['file_slot'] + '.json'), package)
        if package['theory'] == 'Target' and not target_started:
            continue
        j = journal(root, run, package)
        j.append('initial', initial[package['theory']], {'check': Checker().check_graph(initial[package['theory']]['graph'])})
    source_package = packages[0]
    j = journal(root, run, source_package)
    proofs = {'left': 'by simp', 'right': 'using left by simp'}
    if contract_dependency: proofs['source_contract'] = 'by simp'
    candidate = prepare_batch(source_package, initial['Source'], {'proofs': proofs})
    evidence = check_batch(Checker(), initial['Source'], candidate)
    evidence['sealable_generation'] = True
    j.append('batch', candidate, evidence)
    return run, packages, initial, candidate


class SharedProofWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run, self.packages, self.initial, self.source = setup_run(self.root)
        self.workspace = SharedProofWorkspace(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def ref(self, key='right'):
        return next(row['source_ref'] for row in self.workspace.index(theory='Source')['records']
                    if row['source_ref']['node_id'] == key)

    def bundle(self):
        return self.workspace.prepare_reuse(self.ref(), target_state=self.initial['Target'],
                                             target_node_id='target_contract')

    def test_unsealed_index_and_read_are_bounded_and_do_not_repair_head(self):
        head = self.root / 'theories/Source/acceptance/HEAD.json'
        head.unlink()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.workspace.index(query='True', limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['theories'][0]['status'], 'planned')
        proof = self.workspace.read([self.ref()])['records'][0]
        self.assertTrue(proof['reference_only'])
        self.assertEqual(proof['proof_body'], 'using left by simp')
        self.assertFalse(head.exists())
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_unchanged_index_reuses_rows_but_new_commit_invalidates_them(self):
        from unittest.mock import patch
        first=self.workspace.index(theory='Source')
        with patch.object(self.workspace,'_ref',wraps=self.workspace._ref) as ref:
            self.assertEqual(self.workspace.index(theory='Source'),first)
            ref.assert_not_called()
            j=journal(self.root,self.run,self.packages[0])
            state=deepcopy(self.source);state['reason']='new observation'
            j.append('planner-feedback',state,{})
            after=self.workspace.index(theory='Source')
            self.assertTrue(ref.called)
        self.assertNotEqual(first['records'][0]['source_ref']['commit_id'],
                            after['records'][0]['source_ref']['commit_id'])
        after['records'][0]['source_ref']['commit_id']='modified response'
        self.assertNotEqual(self.workspace.index(theory='Source')['records'][0]['source_ref']['commit_id'],
                            'modified response')

    def test_snapshot_waits_for_inflight_acceptance_transaction(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from regen.execution.proof_commits import journal_lock
        acceptance = self.root / 'theories/Source/acceptance'
        package_read = threading.Event()
        commit_listing = threading.Event()
        original_package = self.workspace._package
        original_paths = __import__(
            'regen.execution.shared_proof_workspace',
            fromlist=['commit_paths']).commit_paths

        def observed_package(theory):
            result = original_package(theory)
            package_read.set()
            return result

        def observed_paths(directory):
            commit_listing.set()
            return original_paths(directory)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(
                    self.workspace, '_package', side_effect=observed_package), patch(
                    'regen.execution.shared_proof_workspace.commit_paths',
                    side_effect=observed_paths):
                with journal_lock(acceptance):
                    future = pool.submit(self.workspace.index, theory='Source')
                    self.assertTrue(package_read.wait(1))
                    self.assertFalse(commit_listing.wait(.1))
                self.assertEqual(future.result(timeout=1)['total'], 2)
                self.assertTrue(commit_listing.is_set())

    def test_source_refs_remain_bound_to_old_commit_after_new_feedback(self):
        ref = self.ref()
        j = journal(self.root, self.run, self.packages[0])
        state = deepcopy(self.source); state['reason'] = 'updated'
        j.append('planner-feedback', state, {})
        self.assertEqual(self.workspace.read([ref])['records'][0]['source_ref'], ref)
        self.assertNotEqual(self.ref()['commit_id'], ref['commit_id'])

    def test_chain_validation_rejects_rehashed_but_invalid_native_evidence(self):
        path = self.root / 'theories/Source/acceptance/commits/000002.json'
        record = json.loads(path.read_text())
        record['evidence']['check']['verdict'] = 'red'
        record['commit_id'] = digest({k: v for k, v in record.items() if k != 'commit_id'})
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError): self.workspace.index(theory='Source')

    def test_cached_snapshot_does_not_hide_source_tampering(self):
        ref = self.ref(); self.workspace.read([ref])
        path = self.root / 'theories/Source/acceptance/commits/000002.json'
        record = json.loads(path.read_text()); record['state']['proofs']['right'] = 'by blast'
        path.write_text(json.dumps(record))
        with self.assertRaises(ValueError): self.workspace.read([ref])

    def test_wrong_proof_hash_and_foreign_run_are_rejected(self):
        for key, value in [('proof_sha256', 'bad'), ('node_sha256', 'bad'), ('run_id', 'other'),
                           ('theory', '../Other'), ('commit_id', 'missing')]:
            ref = self.ref(); ref[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.workspace.read([ref])

    def test_explicit_proof_reference_adds_undeclared_helper_dependency(self):
        bundle = self.bundle()
        self.assertEqual([h['source_ref']['node_id'] for h in bundle['helpers']], ['left', 'right'])
        left, right = bundle['helpers']
        self.assertEqual(right['node']['dependencies'], [left['node']['id']])
        self.assertIn(left['node']['name'], right['proof_body'])
        self.assertEqual(self.initial['Target']['proofs'], {})

    def test_missing_mapping_dependency_or_conflicting_name_is_rejected(self):
        bundle = self.bundle(); mapping = deepcopy(bundle['mapping']); mapping.pop('left')
        with self.assertRaisesRegex(ValueError, 'complete source helper'):
            self.workspace.prepare_reuse(self.ref(), target_state=self.initial['Target'],
                target_node_id='target_contract', mapping=mapping)
        mapping = deepcopy(bundle['mapping']); mapping['left']['name'] = 'target_contract'
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            self.workspace.prepare_reuse(self.ref(), target_state=self.initial['Target'],
                target_node_id='target_contract', mapping=mapping)

    def test_expansion_preserves_source_and_requires_normal_native_acceptance(self):
        old = deepcopy(self.initial['Target']); bundle = self.bundle()
        proposal = {'action': 'submit_batch', 'reuse_helpers': [{'bundle': bundle}],
                    'proofs': {'target_contract': 'by simp'}}
        expanded = expand_reuse_helpers(proposal, old, self.workspace)
        self.assertEqual(old['proofs'], {})
        self.assertEqual(old, self.initial['Target'])
        native_proposal = {k: v for k, v in expanded.items() if k not in {'action', 'workspace_reuse_origin'}}
        candidate = prepare_batch(self.packages[1], old, native_proposal)
        self.assertEqual(len(candidate['proofs']), 3)
        self.assertEqual(candidate['graph']['nodes'][0]['dependencies'], [bundle['root_helper_id']])
        red = Checker(); red.verdict = 'red'
        with self.assertRaises(ValueError): validate_batch_commit(old, candidate, check_batch(red, old, candidate))
        checked = check_batch(Checker(), old, candidate)
        validate_batch_commit(old, candidate, checked)
        self.assertIn('copied_source_requires_target_native_check', str(expanded['workspace_reuse_origin']))

    def test_forged_bundle_cannot_bypass_source_or_closure_binding(self):
        for mutation in ('body', 'remove_dependency', 'contract_role', 'context'):
            bundle = self.bundle()
            if mutation == 'body': bundle['helpers'][0]['proof_body'] = 'by blast'
            elif mutation == 'remove_dependency': bundle['helpers'].pop(0)
            elif mutation == 'contract_role': bundle['helpers'][0]['node']['role'] = 'contract'
            else: bundle['helpers'][0]['node']['context'] = ['context Evil']
            bundle['bundle_sha256'] = digest({k: v for k, v in bundle.items() if k != 'bundle_sha256'})
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'bundle hash/source'):
                expand_reuse_helpers({'action': 'submit_batch', 'reuse_helpers': [{'bundle': bundle}]},
                                     self.initial['Target'], self.workspace)

    def test_conflicting_batch_upsert_or_consumer_deletion_is_rejected(self):
        bundle = self.bundle()
        for graph in [{'generated_nodes': [deepcopy(bundle['helpers'][0]['node'])]},
                      {'delete_node_ids': ['target_contract']}]:
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                expand_reuse_helpers({'action': 'submit_batch', 'graph': graph,
                                     'reuse_helpers': [{'bundle': bundle}]}, self.initial['Target'], self.workspace)

    def test_same_theory_keeps_existing_read_and_reuse_route(self):
        with self.assertRaisesRegex(ValueError, 'same-theory'):
            self.workspace.prepare_reuse(self.ref(), target_state=self.source,
                                         target_node_id='source_contract')
        self.assertTrue(self.workspace.read([self.ref()])['records'][0]['reference_only'])

    def test_candidate_reference_is_hash_bound_and_never_green(self):
        proposal = {'action': 'submit_batch', 'proofs': {'source_contract': 'by fail'}}
        feedback = {'status': 'rejected_batch', 'check': {'verdict': 'red'}}
        state = deepcopy(self.source); state['batch_requests'] = 1
        state['batch_history'] = [{'request_number': 1, 'status': 'rejected_batch',
            'proposal_sha256': digest(proposal), 'feedback_sha256': digest(feedback)}]
        journal(self.root, self.run, self.packages[0]).append('planner-feedback', state, {})
        d = self.root / 'theories/Source/batch-requests/000001'
        durable(d / 'proposal.json', proposal); durable(d / 'feedback.json', feedback)
        result = self.workspace.read_attempt('Source', 1)
        self.assertEqual(result['status'], 'candidate_reference')
        self.assertTrue(result['reference_only'])
        durable(d / 'proposal.json', {'action': 'submit_batch', 'proofs': {'source_contract': 'by simp'}})
        with self.assertRaisesRegex(ValueError, 'not bound'): self.workspace.read_attempt('Source', 1)

    def test_source_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            imported = self.root / 'theories/Source/imports.json'
            p = Path(outside) / 'imports.json'; p.write_text('[]')
            imported.symlink_to(p)
            with self.assertRaisesRegex(ValueError, 'escapes'): self.workspace.index(theory='Source')

    def test_package_hash_drift_is_rejected(self):
        p = self.root / 'packages/Source.thy.json'; value = json.loads(p.read_text())
        value['contracts'][0]['exact_statement'] = 'lemma source_contract: "False"'
        p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'package binding'): self.workspace.index(theory='Source')

    def test_contract_dependency_is_not_moved_into_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, _, initial, _ = setup_run(root, contract_dependency=True)
            workspace = SharedProofWorkspace(root)
            ref = next(x['source_ref'] for x in workspace.index(theory='Source')['records']
                       if x['source_ref']['node_id'] == 'right')
            with self.assertRaisesRegex(ValueError, 'fixed contracts cannot be copied'):
                workspace.prepare_reuse(ref, target_state=initial['Target'], target_node_id='target_contract')
            cref = next(x['source_ref'] for x in workspace.index(theory='Source')['records']
                        if x['source_ref']['node_id'] == 'source_contract')
            self.assertEqual(workspace.read([cref])['records'][0]['role'], 'contract')
            with self.assertRaisesRegex(ValueError, 'fixed contracts cannot be copied'):
                workspace.prepare_reuse(cref, target_state=initial['Target'], target_node_id='target_contract')

    def test_compact_receipt_expands_without_resending_proof_bodies(self):
        bundle = self.bundle()
        receipt = bundle['reuse_request']
        self.assertNotIn('helpers', receipt)
        expanded = expand_reuse_helpers({'action': 'submit_batch', 'reuse_helpers': [receipt]},
                                       self.initial['Target'], self.workspace)
        self.assertEqual(len(expanded['proofs']), 2)
        receipt['bundle_sha256'] = 'forged'
        with self.assertRaisesRegex(ValueError, 'bundle hash/source'):
            expand_reuse_helpers({'action': 'submit_batch', 'reuse_helpers': [receipt]},
                                 self.initial['Target'], self.workspace)

    def test_cached_read_checks_import_binding_and_missing_history(self):
        ref = self.ref(); self.workspace.read([ref])
        imports = self.root / 'theories/Source/imports.json'
        imports.write_text('["Foreign"]')
        with self.assertRaisesRegex(ValueError, 'commit chain/binding'):
            self.workspace.read([ref])
        imports.unlink()
        (self.root / 'theories/Source/acceptance/commits/000002.json').unlink()
        (self.root / 'theories/Source/acceptance/HEAD.json').unlink()
        with self.assertRaisesRegex(ValueError, 'history is missing'):
            self.workspace.index(theory='Source')

    def test_target_contract_cannot_be_impersonated_with_a_helper(self):
        target = deepcopy(self.initial['Target'])
        target['graph']['nodes'][0]['statement'] = 'lemma target_contract: "True & True"'
        with self.assertRaisesRegex(ValueError, 'impersonate'):
            self.workspace.prepare_reuse(self.ref('left'), target_state=target,
                                         target_node_id='target_contract')

    def test_embedded_kernel_dependency_joins_helper_closure(self):
        from regen.execution.shared_proof_workspace import _actual_dependencies
        snapshot, node = self.workspace._resolve(self.ref())
        # Simulate a source proof using the simp context rather than an explicit
        # theorem name. Only native records qualified by the source audit theory
        # are accepted as this graph's dependencies.
        snapshot['state']['proofs']['right'] = 'by simp'
        evidence = {'dependencies': {'verdict': 'green',
            'source_content': 'theory Complete_Audit_Source imports Main begin',
            'complete_theory_audit': {'proof_dependencies': {'right': {'dependencies': [
                {'canonical_name': 'Complete_Audit_Source.left'},
                {'canonical_name': 'Some_Parent.right'}]}}}}}
        snapshot['actual_dependencies'] = _actual_dependencies(evidence, snapshot['state']['graph'])
        dependencies, _ = self.workspace._closure(snapshot, node)
        self.assertEqual(dependencies['right'], ['left'])
        self.assertEqual(list(dependencies), ['left', 'right'])

    def test_failed_attempt_preview_is_bounded(self):
        state = deepcopy(self.source)
        state['batch_history'] = [{'request_number': number, 'status': 'rejected_batch'}
                                  for number in range(1, 51)]
        journal(self.root, self.run, self.packages[0]).append('planner-feedback', state, {})
        summary = self.workspace.index(theory='Source')['theories'][0]
        self.assertEqual(summary['failed_attempt_count'], 50)
        self.assertEqual([h['request_number'] for h in summary['failed_attempts']], list(range(43, 51)))

    def test_receipt_target_failure_cannot_append_green_commit(self):
        old = deepcopy(self.initial['Target'])
        bundle = self.bundle()
        expanded = expand_reuse_helpers({'action': 'submit_batch',
            'reuse_helpers': [bundle['reuse_request']], 'proofs': {'target_contract': 'by simp'}},
            old, self.workspace)
        candidate = prepare_batch(self.packages[1], old,
            {k: v for k, v in expanded.items() if k not in {'action', 'workspace_reuse_origin'}})
        target_journal = journal(self.root, self.run, self.packages[1])
        checker = Checker(); checker.verdict = 'red'
        rejected = check_batch(checker, old, candidate)
        rejected['sealable_generation'] = True
        self.assertEqual(checker.calls, 1)
        for helper in bundle['helpers']:
            self.assertIn(helper['node']['statement'], rejected['check']['source_content'])
            self.assertIn(helper['proof_body'], rejected['check']['source_content'])
        with self.assertRaises(ValueError): target_journal.append('batch', candidate, rejected)
        self.assertEqual(target_journal.replay()['proofs'], {})
        accepted = check_batch(Checker(), old, candidate)
        accepted['sealable_generation'] = True
        accepted['workspace_reuse_origin'] = expanded['workspace_reuse_origin']
        target_journal.append('batch', candidate, accepted)
        self.assertEqual(len(target_journal.replay()['proofs']), 3)
        self.assertEqual(self.workspace.read([bundle['source_ref']])['records'][0]['proof_body'],
                         'using left by simp')

    def test_malformed_batch_patch_is_a_validation_error(self):
        receipt = self.bundle()['reuse_request']
        for graph in ({'generated_nodes': [{'id': []}]}, {'delete_node_ids': None},
                      {'delete_node_ids': [{}]}):
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                expand_reuse_helpers({'action': 'submit_batch', 'reuse_helpers': [receipt],
                                     'graph': graph}, self.initial['Target'], self.workspace)

    def test_context_mismatch_is_rejected_before_candidate_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, _, initial, _ = setup_run(root, source_context=['context begin interpretation Arch .'])
            workspace = SharedProofWorkspace(root); ref = workspace.index(theory='Source')['records'][0]['source_ref']
            with self.assertRaisesRegex(ValueError, 'context is incompatible'):
                workspace.prepare_reuse(ref, target_state=initial['Target'], target_node_id='target_contract')


if __name__ == '__main__': unittest.main()
