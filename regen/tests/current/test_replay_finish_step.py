"""Isolation/configuration controls only: no native prover or model is launched.

The 122-node fixture is generic test data, with fixture-local expected hashes.
It checks input guards and the real renderer, not mathematical validity.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.infrastructure.workspace_transcript import audit_workspace_session
from regen.planning.batch_file_planner import seal_theory
from regen.tools import replay_finish_step as replay


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@contextmanager
def saved_fixture(source):
    directory = source/'theories'/replay.THEORY
    request = directory/'batch-requests/000121'
    ids = [f'helper:fixture_{number}' for number in range(122)]
    graph = {'nodes': [{'id': key, 'statement': f'lemma fixture_{number}: "True"',
                       'context': []} for number, key in enumerate(ids)],
             'execution_order': ids[:], 'accepted_order': ids[:],
             'context_policy': 'committed-prefix-v1'}
    proofs = dict.fromkeys(ids, 'by simp')
    document = 'theory Fixture_Document imports Main begin end\n'
    document_sha = hashlib.sha256(document.encode()).hexdigest()
    payload = {'graph': graph, 'last_proof_feedback': {'candidate': {
        'proofs': proofs, 'document_source': document}},
        'unproved_node_ids': [], 'green_node_ids': ids[:]}
    renderer = object.__new__(replay.native.IsabelleChecker)
    renderer.previous_theories, renderer.import_all_previous = ['Fixture_Ancestor'], True
    complete_text = renderer._source(graph, proofs, theory_name='Complete_'+replay.THEORY,
                                     diagnostic_markers=True)
    complete = source/'check-tasks'/replay.TASK/'isabelle'/('Complete_'+replay.THEORY+'.thy')
    complete.parent.mkdir(parents=True)
    complete.write_text(complete_text)
    write_json(source/'run.json', {'fixture': True})
    write_json(request/'input.json', payload)
    write_json(request/'proposal.json', {'action': 'finish', 'source_sha256': document_sha})
    write_json(directory/'document/versions'/document_sha/'source.json', {'source': document})
    write_json(directory/'provider-session.json', {'imports': ['Fixture_Ancestor']})
    with patch.multiple(replay, DOCUMENT_SHA=document_sha,
                        SOURCE_SHA=hashlib.sha256(complete_text.encode()).hexdigest()):
        yield {'source': source, 'directory': directory, 'request': request,
               'payload': payload, 'complete': complete, 'renderer': renderer,
               'document': directory/'document/versions'/document_sha/'source.json'}


class ReplayFinishStepTests(unittest.TestCase):
    def test_destination_rejects_overlap_existing_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, existing = root/'source', root/'existing'
            source.mkdir(); existing.mkdir()
            alias = root/'alias'
            alias.symlink_to(source, target_is_directory=True)
            for output in (source, source/'nested-output', root, alias/'nested-output'):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    replay.validate_destination(source, output)
            with self.assertRaises(FileExistsError):
                replay.validate_destination(source, existing)
            self.assertEqual(replay.validate_destination(source, root/'fresh'), (source, root/'fresh'))

    def test_invalid_destination_stops_before_loading_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(replay, 'load_inputs') as load:
            source = Path(tmp)/'source'; source.mkdir()
            with self.assertRaises(ValueError):
                replay.run(source, source/'nested')
            load.assert_not_called()

    def test_input_binding_accepts_exact_122_node_fixture_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as tmp, saved_fixture(Path(tmp)/'source') as fixture:
            before = {path: replay.sha(path) for path in fixture['source'].rglob('*') if path.is_file()}
            _, directory, graph, proofs, ancestors, proposal, complete, doc = replay.load_inputs(fixture['source'])
            self.assertEqual(len(graph['nodes']), 122)
            self.assertEqual(len(proofs), 122)
            self.assertEqual(ancestors, ['Fixture_Ancestor'])
            self.assertEqual(proposal['action'], 'finish')
            self.assertEqual((directory, complete, doc),
                             (fixture['directory'], fixture['complete'], fixture['document']))
            self.assertEqual(before, {path: replay.sha(path) for path in fixture['source'].rglob('*') if path.is_file()})

    def test_saved_source_document_and_candidate_hash_mutations_are_rejected(self):
        for mutation in ('complete', 'document', 'candidate', 'proof_set', 'green_order'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp, \
                    saved_fixture(Path(tmp)/'source') as fixture:
                payload = deepcopy(fixture['payload'])
                if mutation == 'complete':
                    fixture['complete'].write_text(fixture['complete'].read_text()+'\n')
                elif mutation == 'document':
                    write_json(fixture['document'], {'source': 'changed'})
                elif mutation == 'candidate':
                    payload['last_proof_feedback']['candidate']['document_source'] += '\n'
                elif mutation == 'proof_set':
                    payload['last_proof_feedback']['candidate']['proofs'].pop('helper:fixture_0')
                else:
                    payload['green_node_ids'].reverse()
                write_json(fixture['request']/'input.json', payload)
                with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                    replay.load_inputs(fixture['source'])

    def test_wrong_proof_count_is_rejected_even_with_matching_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp, saved_fixture(Path(tmp)/'source') as fixture:
            payload = fixture['payload']
            key = payload['graph']['nodes'].pop()['id']
            payload['graph']['execution_order'].remove(key)
            payload['graph']['accepted_order'].remove(key)
            payload['green_node_ids'].remove(key)
            proofs = payload['last_proof_feedback']['candidate']['proofs']; proofs.pop(key)
            text = fixture['renderer']._source(payload['graph'], proofs,
                theory_name='Complete_'+replay.THEORY, diagnostic_markers=True)
            fixture['complete'].write_text(text)
            write_json(fixture['request']/'input.json', payload)
            with patch.object(replay, 'SOURCE_SHA', hashlib.sha256(text.encode()).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                    replay.load_inputs(fixture['source'])

    def test_renderer_must_match_frozen_source_bytes_exactly(self):
        with tempfile.TemporaryDirectory() as tmp, saved_fixture(Path(tmp)/'source') as fixture:
            payload = fixture['payload']
            payload['last_proof_feedback']['candidate']['proofs']['helper:fixture_0'] = 'by auto'
            write_json(fixture['request']/'input.json', payload)
            with self.assertRaisesRegex(ValueError, 'renderer differs'):
                replay.load_inputs(fixture['source'])

    def command(self, phase='process'):
        return ['docker', 'run', '--name', 'isolated-unit-test', '--rm', '--network', 'none',
                '--mount', 'type=bind,src=/protected/heaps,dst=/root/.isabelle/heaps',
                '--mount', 'type=bind,src=/isolated/generated,dst=/generated,readonly',
                '-e', 'L4V_ARCH=ARM', 'fixture-image',
                '/workspace/verification/isabelle/bin/isabelle', phase, '-o', 'threads=4',
                '-l', 'Generated_Refine', '-T', '/candidate/Complete_Scheduler_Queues']

    def test_command_keeps_parent_readonly_and_writes_only_separate_heap_store(self):
        for phase in ('process', 'build'):
            original = self.command(phase)
            command = replay.readonly_command(original, Path('/isolated'))
            self.assertIn('type=bind,src=/protected/heaps,dst=/parent-heaps,readonly', command)
            self.assertIn('type=bind,src=/isolated/build-heaps,dst=/replay-heaps', command)
            self.assertIn('ISABELLE_HEAPS_SYSTEM=/parent-heaps', command)
            self.assertIn('ISABELLE_HEAPS=/replay-heaps', command)
            self.assertEqual(command[command.index(phase):], original[original.index(phase):])
            self.assertEqual(command[command.index('--cpus')+1], '4')
            self.assertEqual(command[command.index('--memory')+1], '16g')
            self.assertIn('type=bind,src=/protected/heaps,dst=/root/.isabelle/heaps', original)

    def test_command_refuses_unowned_missing_or_duplicate_parent_mounts(self):
        original = self.command()
        variants = [original[0:2]+original[4:],
                    [x for x in original if not x.startswith('type=bind,src=/protected/heaps')],
                    original+['--mount', 'type=bind,src=/other,dst=/root/.isabelle/heaps']]
        for command in variants:
            with self.subTest(command=command), self.assertRaises(ValueError):
                replay.readonly_command(command, Path('/isolated'))

    def test_command_rejects_missing_changed_or_duplicate_thread_budget(self):
        command = self.command()
        variants = [[value for value in command if value != 'threads=4'],
                    ['threads=2' if value == 'threads=4' else value for value in command],
                    command+['-o', 'threads=4']]
        for changed in variants:
            with self.subTest(command=changed), self.assertRaisesRegex(ValueError, 'exactly four'):
                replay.readonly_command(changed, Path('/isolated'))

    def test_protocol_timeout_and_thread_budget_are_fixed_not_cli_overrides(self):
        self.assertEqual((replay.TIMEOUT, replay.THREADS), (900, 4))
        for flag in ('--timeout', '--threads'):
            with patch.object(sys, 'argv', ['replay-finish', '--source-run', '/unused',
                    '--output', '/unused-output', flag, '2']), patch.object(replay, 'run') as run, \
                    patch.object(sys, 'stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    replay.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()

    def test_native_budget_cli_defaults_to_900_and_allows_explicit_1800(self):
        for extra, expected in (([], 900), (['--native-timeout-seconds', '1800'], 1800),
                                (['--reuse-arch-context'], 900)):
            with self.subTest(expected=expected), patch.object(sys, 'argv', ['replay-finish',
                    '--source-run', '/unused', '--output', '/unused-output', *extra]), \
                    patch.object(replay, 'run', return_value={'step_pass': False}) as run, \
                    patch.object(sys, 'stdout', io.StringIO()):
                self.assertEqual(replay.main(), 1)
                run.assert_called_once_with(Path('/unused'), Path('/unused-output'),
                                            native_timeout_seconds=expected,
                                            reuse_arch_context='--reuse-arch-context' in extra)

    def test_invalid_native_budget_is_rejected_before_inputs_or_output_creation(self):
        for value in (-1, 0, 899, 3601, True, False, 900.0, '1800', None):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(replay, 'load_inputs') as load, \
                    patch.object(replay.native, 'native_run') as native_run:
                source, output = Path(tmp)/'source', Path(tmp)/'output'
                source.mkdir()
                with self.assertRaises(ValueError):
                    replay.run(source, output, native_timeout_seconds=value)
                load.assert_not_called()
                native_run.assert_not_called()
                self.assertFalse(output.exists())
                self.assertEqual(list(source.iterdir()), [])

    def test_default_and_extended_budgets_only_change_native_phase_and_wall_guard(self):
        # These substitutes observe configuration plumbing only. Neither a
        # prover, Docker, app-server nor acceptance callback executes here.
        for native_budget in (900, 1800, 3600):
            with self.subTest(native_budget=native_budget), tempfile.TemporaryDirectory() as tmp, \
                    saved_fixture(Path(tmp)/'source') as fixture:
                source, output = fixture['source'], Path(tmp)/'output'
                directory = fixture['directory']
                config = {'image': 'fixture-image', 'l4v': str(source/'l4v'),
                          'isabelle_dir': str(source/'isabelle'),
                          'semantic_workspace': str(source/'semantic'),
                          'base_session': 'Fixture', 'base_import': 'Fixture.Main',
                          'base_catalog': '/semantic-base',
                          'premise_snapshot': str(source/'premise.json')}
                write_json(source/'run.json', config)
                write_json(source/'premise.json', {})
                cache = source/'cache.json'; write_json(cache, {})
                write_json(source/'check-tasks'/replay.TASK/'task.json', {})
                write_json(source/'packages'/(replay.THEORY+'.thy.json'), {})
                (directory/'generated').mkdir()
                (directory/'generated/ROOT').write_text('')
                inputs = replay.load_inputs(source)
                limits = SimpleNamespace(check=Mock(), register=Mock(), stop=threading.Event(),
                    monitor=Mock(ident=None), cleanup=Mock(return_value=[]), failure=None)
                observer = SimpleNamespace(rows=[], lock=threading.Lock(), active=None,
                    thread=Mock(ident=None), stop=threading.Event())
                session = MagicMock()
                session.__enter__.return_value = session
                native_result = SimpleNamespace(returncode=17, stdout='', stderr='control-only failure')
                def observe_protocol_run(*args, **kwargs):
                    self.assertEqual(kwargs['timeout'], 900)
                    # Exercise the instrumented timeout guard; original native
                    # execution is a mock returning failure, never fake GREEN.
                    with self.assertRaises(ValueError):
                        replay.native.native_run(self.command(), timeout=native_budget+1)
                    self.assertIs(replay.native.native_run(self.command(), timeout=native_budget), native_result)
                    return {'status': 'completed'}
                session.run.side_effect = observe_protocol_run
                with ExitStack() as stack:
                    stack.enter_context(patch.object(replay, 'load_inputs', return_value=inputs))
                    stack.enter_context(patch.object(replay, 'validate_frozen_context',
                        return_value=({'architecture': 'ARM'}, cache)))
                    stack.enter_context(patch.object(replay, 'protected_snapshot', return_value={}))
                    stack.enter_context(patch.object(replay, 'production_hashes', return_value={}))
                    checker = stack.enter_context(patch.object(replay.native, 'IsabelleChecker'))
                    native_run = stack.enter_context(patch.object(replay.native, 'native_run', return_value=native_result))
                    limit_factory = stack.enter_context(patch.object(replay, 'Limits', return_value=limits))
                    stack.enter_context(patch.object(replay, 'NativeObserver', return_value=observer))
                    stack.enter_context(patch.object(replay, 'CheckExecutor'))
                    resources = stack.enter_context(patch.object(replay, 'NativeResources'))
                    protocol = stack.enter_context(patch.object(replay, 'CodexWorkspaceSession', return_value=session))
                    stack.enter_context(patch.object(replay, 'audit_workspace_session',
                        return_value={'sealable_generation': False}))
                    stack.enter_context(patch.object(replay, 'protocol_metrics',
                        return_value={'within_latency_limit': False}))
                    kwargs = {} if native_budget == 900 else {'native_timeout_seconds': native_budget}
                    result = replay.run(source, output, **kwargs)
                self.assertNotIn('failure', result)
                self.assertFalse(result['step_pass'])
                self.assertEqual(checker.call_args.kwargs['timeout'], native_budget)
                self.assertEqual(limit_factory.call_args.args[1], 3*native_budget+180)
                self.assertEqual(protocol.call_args.kwargs['progress_timeout'], 900)
                self.assertEqual(native_run.call_args.kwargs['timeout'], native_budget)
                native_run.assert_called_once()
                resources.assert_called_once_with(slots=1, threads=4, resident_limit=1)
                manifest = replay.read(output/'manifest.json')
                self.assertEqual(manifest['timeout_per_native_phase'], native_budget)
                self.assertEqual(manifest['source_timeout_per_native_phase'], 900)
                self.assertEqual(manifest['protocol_progress_timeout_seconds'], 900)
                self.assertEqual(manifest['total_wall_budget_seconds'], 3*native_budget+180)
                self.assertEqual(manifest['threads'], 4)
                self.assertFalse(manifest['real_model'])
                self.assertEqual(replay.TIMEOUT, 900)

    def test_synthetic_stdio_replay_can_never_claim_real_model_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            arguments = {'theory': replay.THEORY, 'proposal': {'action': 'finish',
                         'source_sha256': replay.DOCUMENT_SHA}}
            write_json(root/'request.json', arguments)
            with CodexWorkspaceSession(tools=[{'name': 'proof_action', 'description': 'test replay',
                    'inputSchema': {'const': arguments}}], allow_external_call=True,
                    command=[sys.executable, '-u', '-c', replay.SERVER, str(root/'request.json'),
                             str(root/'producer.jsonl')]) as session:
                result = session.run('local control only', lambda *args: {'native_step_pass': False},
                                     artifact_dir=root/'protocol', model='synthetic-no-model', timeout=2)
            self.assertEqual(result['status'], 'completed')
            transcript = root/'protocol/session-transcript.jsonl'
            audit = audit_workspace_session(transcript, require_real=False)
            self.assertFalse(audit['sealable_generation'])
            with self.assertRaisesRegex(ValueError, 'synthetic or unmarked'):
                audit_workspace_session(transcript, require_real=True)

    def test_production_seal_stops_after_non_green_complete(self):
        for verdict in ('red', 'timeout', 'unknown'):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                checker = Mock()
                checker.check_complete.return_value = {'verdict': verdict}
                state = {'graph': {'nodes': [{'id': 'target'}]},
                         'proofs': {'target': 'by simp'}, 'status': 'active'}
                with patch('regen.analysis.retained_semantic_impact.audit_theory') as audit:
                    sealed, feedback = seal_theory(directory, {}, {'file_slot': 'Target.thy'},
                                                  directory, checker, [], state)
                self.assertIsNone(sealed)
                self.assertEqual(feedback, {'stage': 'complete', 'check': {'verdict': verdict}})
                checker.check_complete.assert_called_once_with(state['graph'], state['proofs'])
                audit.assert_not_called()
                checker.audit_complete_theory.assert_not_called()
                checker.complete_source.assert_not_called()
                checker.build_generated_session.assert_not_called()
                checker.build_sealed_session.assert_not_called()
                self.assertEqual(list(directory.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
