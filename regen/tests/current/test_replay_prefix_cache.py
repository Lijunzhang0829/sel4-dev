"""Repository-scale hot reads share authority, not repeated embedded histories."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gc
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import weakref

from regen.execution import proof_commits, repository_execution
from regen.execution.proof_commits import ProofCommits, digest, durable
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.planning.proof_graph import build_graph
from regen.tests.current.test_proof_batches import Checker


def repository(root, count=27, active=9):
    packages = []
    for number in range(count):
        theory = f'Theory_{number:02d}'
        package = {'theory': theory, 'file_slot': theory+'.thy',
                   'repository_projection': {'nodes': []}, 'contracts': [{
                       'contract_id': theory+'_contract', 'name': theory+'_contract',
                       'exact_statement': f'lemma {theory}_contract: "True"',
                       'contract_owned_context': []}]}
        package['package_payload_sha256'] = digest(package)
        packages.append(package)
    listing = {'files': [{'theory': p['theory'], 'file_slot': p['file_slot']} for p in packages]}
    run = {'run_id': 'cache-test', 'execution_mode': 'repository-projection-v1',
           'contract_package_dir': str(root/'packages'), 'implementation': {},
           'session_schedule': {'file_list_sha256': digest(listing), 'plan_id': 'cache-plan'}}
    durable(root/'run.json', run)
    durable(root/'execution-input.json', {'file_list': listing, 'files': listing['files'],
        'package_hashes': {p['file_slot']: p['package_payload_sha256'] for p in packages}})
    for number, package in enumerate(packages):
        durable(root/'packages'/(package['file_slot']+'.json'), package)
        if number < active:
            graph = repository_execution.context_graph(build_graph(package, {'generated_nodes': []},
                run_id=run['run_id']+'--'+package['theory'], revision=1), [])
            state = {'graph': graph, 'proofs': {}, 'accepted_order': [], 'rounds': {},
                     'revisions': {}, 'status': 'planned', 'seal_id': None}
            repository_execution.journal(root, run, package).append('initial', state,
                {'check': Checker().check_graph(graph)})
    return run, packages


class RepositoryPrefixCacheTest(unittest.TestCase):
    def test_27_theory_traversals_share_nine_nonempty_prefixes_across_readers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, packages = repository(root)
            calls = []
            original = repository_execution.validator

            def observed(package, runtime):
                validate = original(package, runtime)

                def check(*arguments):
                    calls.append(package['theory'])
                    return validate(*arguments)
                return check

            with patch.object(repository_execution, 'validator', side_effect=observed):
                first, second = SharedProofWorkspace(root), SharedProofWorkspace(root)
                first.index()
                self.assertEqual(len(calls), 9)
                calls.clear()
                for _ in range(3):
                    second.index()
                    first.index()
                    for package in packages:
                        repository_execution.journal(root, run, package).replay(repair=False)
                self.assertEqual(calls, [])

    def test_external_append_validates_only_suffix_and_writer_append_is_shared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, packages = repository(root, count=1, active=1)
            calls = []
            original = repository_execution.validator

            def observed(package, runtime):
                validate = original(package, runtime)

                def check(*arguments):
                    calls.append(arguments[2])
                    return validate(*arguments)
                return check

            with patch.object(repository_execution, 'validator', side_effect=observed):
                reader = SharedProofWorkspace(root)
                first = reader._snapshot('Theory_00')
                writer = repository_execution.journal(root, run, packages[0])
                unshared = ProofCommits(writer.directory, writer.binding, writer.validate)
                updated = deepcopy(first['state']); updated['reason'] = 'external feedback'
                unshared.append('planner-feedback', updated, {})
                calls.clear()
                self.assertEqual(reader._snapshot('Theory_00')['state']['reason'], 'external feedback')
                self.assertEqual(calls, ['planner-feedback'])
                updated['reason'] = 'shared writer feedback'
                writer.append('planner-feedback', updated, {})
                calls.clear()
                self.assertEqual(SharedProofWorkspace(root)._snapshot('Theory_00')['state']['reason'],
                                 'shared writer feedback')
                self.assertEqual(calls, [])
                # Historical reads remain bound to the earlier identity and do
                # not run the validator again or repair a missing HEAD.
                (writer.directory/'HEAD.json').unlink()
                old = reader._snapshot('Theory_00', first['commit_id'])
                self.assertEqual(old['state'], first['state'])
                self.assertFalse((writer.directory/'HEAD.json').exists())
                self.assertEqual(calls, [])

    def test_historical_reference_detects_deleted_later_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, packages = repository(root, count=1, active=1)
            reader = SharedProofWorkspace(root)
            first = reader._snapshot('Theory_00')
            writer = repository_execution.journal(root, run, packages[0])
            state = deepcopy(first['state']); state['reason'] = 'later'
            writer.append('planner-feedback', state, {})
            reader._snapshot('Theory_00')
            (writer.directory/'commits/000002.json').unlink()
            (writer.directory/'HEAD.json').unlink()
            with self.assertRaisesRegex(ValueError, 'history is missing'):
                reader._snapshot('Theory_00', first['commit_id'])

    def test_owner_validation_is_not_skipped_by_a_shared_journal_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run, packages = repository(root, count=1, active=1)
            with patch.object(repository_execution, 'validator', return_value=Mock()):
                writer = repository_execution.journal(root, run, packages[0])
                state = writer.replay(repair=False)
                state['graph']['run_id'] = 'foreign-run'
                writer.append('planner-feedback', state, {})
                with self.assertRaisesRegex(ValueError, 'different run/theory'):
                    SharedProofWorkspace(root)._snapshot('Theory_00')

    def test_cached_seal_metadata_keeps_actual_dependencies_without_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProofCommits(Path(temp), {}, Mock(), cache_namespace='seal-metadata')
            graph = {'run_id': 'r', 'theory': 'T', 'nodes': [
                {'id': 'left', 'name': 'left'}, {'id': 'right', 'name': 'right'}]}
            state = {'graph': graph, 'status': 'sealed'}
            evidence = {'dependencies': {'verdict': 'green',
                'source_content': 'theory Complete_T imports Main begin',
                'complete_theory_audit': {'proof_dependencies': {'right': {'dependencies': [
                    {'canonical_name': 'Complete_T.left'},
                    {'canonical_name': 'Parent.external'},
                    {'canonical_name': 'Complete_T.right'}]}}}}, 'raw_log': 'DO_NOT_CACHE'}
            journal.append('seal', state, evidence)
            reader = ProofCommits(Path(temp), {}, Mock(), cache_namespace='seal-metadata')
            snapshot = reader.replay_snapshot()
            self.assertEqual(snapshot['records'][0]['actual_dependencies'], {'right': ['left']})
            self.assertNotIn('DO_NOT_CACHE', repr(reader._verified))
            self.assertNotIn('source_content', repr(reader._verified))
            snapshot['records'][0]['actual_dependencies']['right'].append('pollution')
            self.assertEqual(reader.replay_snapshot()['records'][0]['actual_dependencies'],
                             {'right': ['left']})

    def test_cache_capacity_is_bounded_without_retaining_empty_theories(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
                proof_commits, '_MAX_VERIFIED_PREFIXES', 3):
            root = Path(temp)
            for index in range(6):
                ProofCommits(root/str(index), {}, Mock(), cache_namespace='bounded').append(
                    'initial', {'number': index}, {})
            self.assertLessEqual(len(proof_commits._VERIFIED_PREFIXES), 3)
            keys = tuple(proof_commits._VERIFIED_PREFIXES)
            for index in range(10):
                ProofCommits(root/f'empty-{index}', {}, Mock(), cache_namespace='bounded').replay()
            self.assertEqual(tuple(proof_commits._VERIFIED_PREFIXES), keys)

    def test_cold_validation_is_serial_but_hot_prefix_does_not_wait_for_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('cold-a', 'cold-b', 'hot'):
                ProofCommits(root/name, {}, Mock()).append('initial', {'name': name}, {})
            hot = ProofCommits(root/'hot', {}, Mock())
            hot.replay()
            first_entered, second_entered, release = (threading.Event() for _ in range(3))

            def slow(*args):
                first_entered.set()
                if not release.wait(3):
                    raise AssertionError('cold validation release was not signalled')

            def second(*args):
                second_entered.set()

            with ThreadPoolExecutor(max_workers=3) as pool:
                a = pool.submit(ProofCommits(root/'cold-a', {}, slow).replay)
                try:
                    self.assertTrue(first_entered.wait(1))
                    b = pool.submit(ProofCommits(root/'cold-b', {}, second).replay)
                    self.assertEqual(pool.submit(hot.replay).result(timeout=1), {'name': 'hot'})
                    self.assertFalse(second_entered.wait(.1))
                finally:
                    release.set()
                self.assertEqual(a.result(timeout=1), {'name': 'cold-a'})
                self.assertEqual(b.result(timeout=1), {'name': 'cold-b'})
                self.assertTrue(second_entered.is_set())

    def test_retained_validation_exception_does_not_retain_large_evidence(self):
        class Evidence(dict):
            pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ProofCommits(root, {}, lambda *args: None).append(
                'initial', {'state': 'small'}, {'large': 'x'*100000})
            references, failures = [], []
            original_loads = json.loads

            def tracked_loads(text):
                result = original_loads(text)
                if isinstance(result, dict) and 'evidence' in result:
                    result['evidence'] = Evidence(result['evidence'])
                    references.append(weakref.ref(result['evidence']))
                return result

            def reject(old, state, event, evidence):
                try:
                    raise ValueError('invalid large evidence')
                except ValueError as cause:
                    raise RuntimeError('validation failed') from cause

            with patch.object(proof_commits.json, 'loads', side_effect=tracked_loads):
                try:
                    ProofCommits(root, {}, reject).replay(repair=False)
                except RuntimeError as error:
                    failures.append(error)
            gc.collect()
            self.assertEqual(str(failures[0]), 'validation failed')
            self.assertEqual(str(failures[0].__cause__), 'invalid large evidence')
            self.assertEqual(len(references), 1)
            self.assertIsNone(references[0]())

    def test_retained_json_decode_error_drops_document_but_preserves_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root/'commits/000001.json'
            path.parent.mkdir()
            text = '{"large": "' + 'x'*100000
            path.write_text(text)
            try:
                json.loads(text)
            except json.JSONDecodeError as expected:
                diagnostic = str(expected)
                position = (expected.pos, expected.lineno, expected.colno)
            with self.assertRaises(json.JSONDecodeError) as failure:
                ProofCommits(root, {}, lambda *args: None).replay(repair=False)
            self.assertEqual(str(failure.exception), diagnostic)
            self.assertEqual((failure.exception.pos, failure.exception.lineno,
                              failure.exception.colno), position)
            self.assertEqual(failure.exception.doc, '')

    def test_repeated_historical_refs_share_only_state_and_still_check_full_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            writer = ProofCommits(root, {}, lambda *args: None, cache_namespace='historical')
            writer.append('initial', {'value': 1}, {'origin': 'DO_NOT_CACHE'*10000})
            first_id = writer._verified['commit_id']
            writer.append('step', {'value': 2}, {})
            reads = []
            original_read = Path.read_text

            def observed_read(path, *args, **kwargs):
                if path.name == '000001.json':
                    reads.append(path)
                return original_read(path, *args, **kwargs)

            with patch.object(Path, 'read_text', observed_read):
                for _ in range(8):
                    reader = ProofCommits(root, {}, lambda *args: None, cache_namespace='historical')
                    result = reader.replay_snapshot(commit_id=first_id)
                    self.assertEqual(result['state'], {'value': 1})
                    result['state']['value'] = 'polluted'
            self.assertEqual(len(reads), 1)
            self.assertNotIn('DO_NOT_CACHE', repr(proof_commits._HISTORICAL_STATES))
            (root/'commits/000002.json').unlink()
            (root/'HEAD.json').unlink()
            with self.assertRaisesRegex(ValueError, 'history is missing'):
                reader.replay_snapshot(commit_id=first_id)

    def test_historical_cache_is_bounded_and_not_shared_without_namespace(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
                proof_commits, '_MAX_HISTORICAL_STATES', 2):
            root = Path(temp)
            writer = ProofCommits(root, {}, lambda *args: None)
            ids = []
            for index in range(5):
                writer.append('step', {'value': index}, {})
                ids.append(writer._verified['commit_id'])
            for identity in ids[:-1]:
                writer.replay_snapshot(commit_id=identity)
            self.assertEqual(len(writer._historical), 2)
            reader = ProofCommits(root, {}, lambda *args: None)
            self.assertEqual(len(reader._historical), 0)
            reader.replay_snapshot(commit_id=ids[0])
            self.assertEqual(len(reader._historical), 1)
            self.assertEqual(len(writer._historical), 2)

    def test_historical_namespace_change_revalidates_before_using_cached_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            writer = ProofCommits(root, {}, lambda *args: None, cache_namespace='historical-valid')
            writer.append('initial', {'value': 1}, {})
            first_id = writer._verified['commit_id']
            writer.append('step', {'value': 2}, {})
            writer.replay_snapshot(commit_id=first_id)

            def reject(*args):
                raise ValueError('new validator rejects cached history')

            reader = ProofCommits(root, {}, reject, cache_namespace='historical-changed')
            with self.assertRaisesRegex(ValueError, 'new validator'):
                reader.replay_snapshot(commit_id=first_id)


if __name__ == '__main__':
    unittest.main()
