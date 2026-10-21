"""Runtime lifetime/integrity regressions; no external model or native calls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure.codex_runtime import (
    CodexRuntimeError, pin_codex_runtime, runtime_verified_settings, verify_codex_launch,
)
from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.infrastructure.progress_events import observe_events
from regen.tests.current import test_codex_workspace_session as protocol_fixture
from regen.tests.current import test_workspace_transcript as audit_fixture


class CodexRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'plugin' / 'codex'
        self.source.parent.mkdir()
        self.source.write_bytes(b'trusted Codex runtime fixture')
        self.source.chmod(0o755)
        self.host = self.source.with_name('codex-code-mode-host')
        self.host.write_bytes(b'trusted tool host fixture')
        self.settings = {'codex_bin': str(self.source),
            'codex_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
            'codex_code_mode_host_sha256': hashlib.sha256(self.host.read_bytes()).hexdigest(),
            'codex_runtime_root': str(self.root / 'runtime')}

    def launch(self, binary=None):
        return {'command': [str(binary or self.source), 'app-server', '--stdio'],
                'executable_sha256': self.settings['codex_sha256']}

    def test_pinned_runtime_and_host_survive_extension_deletion(self):
        pinned = runtime_verified_settings(self.settings)
        binary = Path(pinned['codex_bin'])
        self.assertEqual(binary.parent.name, self.settings['codex_sha256'])
        self.assertEqual(binary.read_bytes(), self.source.read_bytes())
        self.assertNotEqual(binary.stat().st_ino, self.source.stat().st_ino)
        self.source.unlink()
        self.host.unlink()
        self.source.parent.rmdir()
        self.assertEqual(pin_codex_runtime(self.settings), binary)
        self.assertEqual(verify_codex_launch(pinned, self.launch()), binary)
        self.assertEqual(verify_codex_launch(self.settings, self.launch(binary)), binary)
        self.assertTrue(binary.with_name('codex-code-mode-host').is_file())

    def test_stable_path_is_not_nested_again(self):
        pinned = runtime_verified_settings(self.settings)
        self.assertEqual(runtime_verified_settings(pinned)['codex_bin'], pinned['codex_bin'])
        no_explicit_root = {key: value for key, value in pinned.items() if key != 'codex_runtime_root'}
        self.assertEqual(runtime_verified_settings(no_explicit_root)['codex_bin'], pinned['codex_bin'])

    def test_tampered_pinned_binary_fails_closed_without_repairing_it(self):
        binary = pin_codex_runtime(self.settings)
        binary.chmod(0o755)
        binary.write_bytes(b'tampered binary')
        for operation in [lambda: pin_codex_runtime(self.settings),
                          lambda: verify_codex_launch(self.settings, self.launch())]:
            with self.assertRaisesRegex(CodexRuntimeError, 'digest mismatch'):
                operation()
        self.assertEqual(binary.read_bytes(), b'tampered binary')

    def test_tampered_companion_fails_closed(self):
        host = pin_codex_runtime(self.settings).with_name('codex-code-mode-host')
        host.chmod(0o755)
        host.write_bytes(b'tampered host')
        with self.assertRaisesRegex(CodexRuntimeError, 'digest mismatch'):
            pin_codex_runtime(self.settings)
        with self.assertRaisesRegex(CodexRuntimeError, 'digest mismatch'):
            verify_codex_launch(self.settings, self.launch())

    def test_concurrent_planners_publish_one_consistent_runtime(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as workers:
            paths = list(workers.map(lambda _: pin_codex_runtime(self.settings), range(8)))
        self.assertEqual(len(set(paths)), 1)
        receipt = json.loads((paths[0].parent / 'runtime.json').read_text())
        self.assertEqual(receipt['code_mode_host_sha256'], self.settings['codex_code_mode_host_sha256'])
        self.assertFalse(list(paths[0].parent.glob('.runtime-*')))

    def test_malformed_runtime_receipt_is_typed_failure(self):
        receipt = pin_codex_runtime(self.settings).parent / 'runtime.json'
        for invalid in ['[]', '{"version": 2}', '{']:
            receipt.write_text(invalid)
            with self.assertRaises(CodexRuntimeError):
                pin_codex_runtime(self.settings)

    def test_missing_runtime_is_typed_infrastructure_failure(self):
        self.source.unlink()
        with self.assertRaises(CodexRuntimeError) as result:
            verify_codex_launch(self.settings, self.launch())
        self.assertNotIsInstance(result.exception, ValueError)

    def test_digest_and_path_forgery_are_not_accepted_by_relocation(self):
        pinned = runtime_verified_settings(self.settings)
        unknown = self.root / 'unapproved-codex'
        unknown.write_bytes(self.source.read_bytes())
        with self.assertRaises(ValueError):
            verify_codex_launch(pinned, self.launch(unknown))
        forged = self.launch()
        forged['executable_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            verify_codex_launch(pinned, forged)
        changed = {**pinned, 'codex_sha256': '0' * 64}
        with self.assertRaises(ValueError):
            verify_codex_launch(changed, self.launch())

    def test_real_launcher_records_only_stable_verified_path(self):
        settings = {**self.settings, 'model_isolation': {'required': True, 'image': 'sha256:' + 'a' * 64}}
        with patch('regen.infrastructure.audited_llm.model_settings', return_value=settings), \
                patch('regen.infrastructure.model_isolation.ModelIsolation') as isolation, \
                patch('regen.infrastructure.codex_workspace_session.subprocess.Popen',
                      side_effect=RuntimeError('intercepted before external call')) as popen:
            isolation.return_value.command.side_effect = lambda command: command
            isolation.return_value.evidence.return_value = {'exclusive_os_isolation': True}
            with CodexWorkspaceSession(tools=protocol_fixture.TOOLS,
                    codex_bin=self.source, allow_external_call=True) as session:
                with self.assertRaisesRegex(RuntimeError, 'intercepted'):
                    session.run('x', lambda *args: {}, artifact_dir=self.root / 'session',
                                model='fixture', timeout=2)
        binary = pin_codex_runtime(settings)
        self.assertEqual(popen.call_args.args[0][0], str(binary))
        events = [json.loads(line) for line in (self.root / 'session/session-transcript.jsonl').read_text().splitlines()]
        launch = next(event['message'] for event in events if event['message'].get('event') == 'launch')
        self.assertEqual(verify_codex_launch(settings, launch), binary)

    def test_callback_runtime_failure_escapes_once_before_cleanup(self):
        events = []
        order = []
        def observe(event, payload):
            events.append((event, payload))
            if event == 'generation_runtime_integrity_failure':
                order.append('failure')
        def fail(*args):
            raise CodexRuntimeError('runtime vanished')
        with observe_events(observe), CodexWorkspaceSession(tools=protocol_fixture.TOOLS,
                command=[sys.executable, '-u', '-c', protocol_fixture.SERVER, 'normal'],
                allow_external_call=True) as session:
            with self.assertRaisesRegex(CodexRuntimeError, 'runtime vanished'):
                session.run('x', fail, artifact_dir=self.root / 'session', model='fixture',
                            timeout=2, on_abort=lambda: order.append('cleanup'))
            self.assertIsNotNone(session.process.poll())
        failures = [event for event, _ in events if event == 'generation_runtime_integrity_failure']
        self.assertEqual(len(failures), 1)
        self.assertEqual(order, ['failure', 'cleanup'])
        transcript = (self.root / 'session/session-transcript.jsonl').read_text()
        self.assertIn('CodexRuntimeError', transcript)
        self.assertNotIn('"event": "tool_finished"', transcript)

    def test_historical_full_transcript_retains_provenance_after_relocation(self):
        fixture = audit_fixture.WorkspaceTranscriptTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.settings['codex_runtime_root'] = str(self.root / 'historical-runtime')
        pinned = runtime_verified_settings(fixture.settings)
        fixture.binary.unlink()
        with patch('regen.infrastructure.workspace_transcript.model_settings', return_value=pinned):
            self.assertEqual(json.loads(fixture.record()), fixture.proposal)
            report = audit_fixture.audit_workspace_session(fixture.path, require_terminal=False)
            self.assertEqual(report['tool_calls'], 1)


if __name__ == '__main__':
    unittest.main()
