"""Offline synthetic credentials only; no provider or Docker calls."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure.private_auth_home import (
    PERSIST_ENV, PrivateAuthPersistenceError, clean_codex_home,
)


class PrivateAuthHomeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.store = self.base / 'private-store'
        self.store.mkdir(mode=0o700)
        self.auth = self.store / 'auth.json'
        self.auth.write_text('{"synthetic":"old"}')
        self.auth.chmod(0o600)

    def environment(self):
        return patch.dict(os.environ, {PERSIST_ENV: '1', 'CODEX_HOME': str(self.store)})

    def test_default_discards_refresh_and_never_copies_configuration(self):
        (self.store / 'config.toml').write_text('synthetic config')
        with patch.dict(os.environ, {PERSIST_ENV: '0'}):
            with clean_codex_home(self.store) as state:
                self.assertEqual([p.name for p in state.home.iterdir()], ['auth.json'])
                (state.home / 'auth.json').write_text('{"synthetic":"new"}')
        self.assertEqual(self.auth.read_text(), '{"synthetic":"old"}')
        self.assertFalse((self.store / '.auth-refresh.lock').exists())
        self.assertFalse(state.home.exists())

    def test_refresh_is_private_durable_and_reused_without_session_files(self):
        with self.environment():
            with clean_codex_home() as state:
                state.require_stopped(lambda: True)
                (state.home / 'auth.json').write_text('{"synthetic":"new"}')
                (state.home / 'session.log').write_text('not an input')
                self.assertEqual(self.auth.read_text(), '{"synthetic":"old"}')
            self.assertEqual(self.auth.read_text(), '{"synthetic":"new"}')
            self.assertEqual(stat.S_IMODE(self.auth.stat().st_mode), 0o600)
            with clean_codex_home() as next_state:
                self.assertEqual((next_state.home / 'auth.json').read_text(), '{"synthetic":"new"}')
                self.assertEqual([p.name for p in next_state.home.iterdir()], ['auth.json'])
        self.assertFalse((self.store / 'session.log').exists())
        self.assertFalse(state.home.exists())

    def test_exception_still_retains_refresh_after_confirmed_cleanup(self):
        error = RuntimeError('synthetic model failure')
        with self.environment(), self.assertRaises(RuntimeError) as raised:
            with clean_codex_home() as state:
                state.require_stopped(lambda: True)
                (state.home / 'auth.json').write_text('{"synthetic":"new"}')
                raise error
        self.assertIs(raised.exception, error)
        self.assertEqual(self.auth.read_text(), '{"synthetic":"new"}')

    def test_unconfirmed_cleanup_does_not_overwrite_or_mask_primary_error(self):
        with self.environment():
            with self.assertRaises(PrivateAuthPersistenceError):
                with clean_codex_home() as state:
                    state.require_stopped(lambda: False)
                    (state.home / 'auth.json').write_text('{"synthetic":"new"}')
            error = RuntimeError('synthetic primary error')
            with self.assertRaises(RuntimeError) as raised:
                with clean_codex_home() as state:
                    state.require_stopped(lambda: False)
                    raise error
        self.assertIs(raised.exception, error)
        self.assertIn('PrivateAuthPersistenceError', error.__notes__[0])
        self.assertEqual(self.auth.read_text(), '{"synthetic":"old"}')

    def test_single_source_lock_prevents_concurrent_refresh_and_is_released(self):
        with self.environment():
            with clean_codex_home():
                with self.assertRaisesRegex(PrivateAuthPersistenceError, 'already in use'):
                    with clean_codex_home():
                        self.fail('second model transport must not start')
            with clean_codex_home():
                pass

    def test_missing_auth_is_optional_only_for_legacy_proof_callers(self):
        self.auth.unlink()
        with patch.dict(os.environ, {PERSIST_ENV: '0'}):
            with clean_codex_home(self.store) as state:
                self.assertEqual(list(state.home.iterdir()), [])
            with self.assertRaisesRegex(ValueError, 'authentication unavailable'):
                with clean_codex_home(self.store, require_auth=True):
                    pass
        with self.environment(), self.assertRaises(FileNotFoundError):
            with clean_codex_home():
                pass

    def test_explicit_private_home_is_required(self):
        with patch.dict(os.environ, {PERSIST_ENV: '1'}, clear=True), self.assertRaisesRegex(ValueError, 'explicit private'):
            with clean_codex_home():
                pass
        with self.environment(), patch.object(Path, 'home', return_value=self.store.parent):
            ordinary = self.store.parent / '.codex'
            ordinary.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, 'user Codex HOME'):
                with clean_codex_home(ordinary):
                    pass

    def test_store_must_be_private_and_outside_artifacts(self):
        with self.environment(), self.assertRaisesRegex(ValueError, 'outside application and result'):
            with clean_codex_home(forbidden_roots=(self.base,)):
                pass
        for name in ('logs', 'release', 'reports'):
            artifact_home = self.base / name / 'home'
            artifact_home.mkdir(parents=True, mode=0o700)
            with self.environment(), self.assertRaisesRegex(ValueError, 'outside application and result'):
                with clean_codex_home(artifact_home):
                    pass
        self.store.chmod(0o755)
        with self.environment(), self.assertRaisesRegex(ValueError, '0700'):
            with clean_codex_home():
                pass

    def test_symlink_store_auth_and_lock_are_rejected(self):
        link = self.base / 'link'
        link.symlink_to(self.store, target_is_directory=True)
        with self.environment(), self.assertRaisesRegex(ValueError, 'symlink paths'):
            with clean_codex_home(link):
                pass
        self.auth.unlink()
        other = self.base / 'not-the-store'
        other.write_text('{"synthetic":"seed"}')
        self.auth.symlink_to(other)
        with self.environment(), self.assertRaises(OSError):
            with clean_codex_home():
                pass
        self.auth.unlink()
        self.auth.write_text('{"synthetic":"old"}')
        self.auth.chmod(0o600)
        lock = self.store / '.auth-refresh.lock'
        lock.unlink()
        lock.symlink_to(other)
        with self.environment(), self.assertRaises(OSError):
            with clean_codex_home():
                pass
        self.assertEqual(other.read_text(), '{"synthetic":"seed"}')

    def test_atomic_failure_or_invalid_refreshed_auth_preserves_store(self):
        replace = os.replace
        def reject_store_write(source, destination):
            if Path(destination) == self.auth:
                raise OSError('synthetic write failure')
            return replace(source, destination)
        with self.environment():
            with self.assertRaises(OSError), patch(
                    'regen.infrastructure.private_auth_home.os.replace', side_effect=reject_store_write):
                with clean_codex_home() as state:
                    (state.home / 'auth.json').write_text('{"synthetic":"new"}')
            with self.assertRaises(ValueError):
                with clean_codex_home() as state:
                    (state.home / 'auth.json').write_text('')
        self.assertEqual(self.auth.read_text(), '{"synthetic":"old"}')
        self.assertEqual([p.name for p in self.store.glob('.auth-*')], ['.auth-refresh.lock'])


class CallerRefreshTests(unittest.TestCase):
    def test_repository_planner_retains_only_after_model_cleanup(self):
        from regen.planning import workspace_planner as planner
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = base / 'private'
            store.mkdir(mode=0o700)
            auth = store / 'auth.json'
            auth.write_text('{"synthetic":"old"}')
            auth.chmod(0o600)
            output, public, workspace, api = [base / n for n in ('output', 'public', 'workspace', 'api')]
            for path in (output, public, workspace, api):
                path.mkdir()
            test = self
            class Isolation:
                instance = None
                name = 'synthetic-isolation'
                def __init__(self, **kwargs):
                    self.home = kwargs['home']
                    self.closed = False
                    (Path(kwargs['base']) / 'guardian.ready').touch()
                    Isolation.instance = self
                def evidence(self): return {}
                def close(self):
                    test.assertEqual(auth.read_text(), '{"synthetic":"old"}')
                    self.closed = True
            class Process:
                returncode = None
                def poll(self): return self.returncode
                def communicate(self, text, timeout):
                    (Isolation.instance.home / 'auth.json').write_text('{"synthetic":"new"}')
                    self.returncode = 0
                def terminate(self): self.returncode = 0
                def wait(self, timeout=None): self.returncode = 0
            with patch.dict(os.environ, {PERSIST_ENV: '1', 'CODEX_HOME': str(store)}), \
                    patch.object(planner, 'ModelIsolation', Isolation), \
                    patch.object(planner, 'container_command', return_value=['synthetic-model']), \
                    patch.object(planner.subprocess, 'Popen', side_effect=lambda *a, **k: Process()):
                meta, _ = planner.call_worker({'model_isolation': {'image': 'synthetic'}, 'codex_bin': 'synthetic',
                    'codex_sha256': 'synthetic'}, {'model': {'model': 'synthetic'}}, output, public, workspace, api, 'synthetic', 1)
            self.assertEqual(meta['returncode'], 0)
            self.assertTrue(Isolation.instance.closed)
            self.assertEqual(auth.read_text(), '{"synthetic":"new"}')
            self.assertNotIn('auth.json', {p.name for p in output.rglob('*')})

    def test_theory_planner_retains_refresh_after_session_exit(self):
        from regen.planning.parallel_file_planner import run_theory_session
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'run'
            root.mkdir()
            store = base / 'private'
            store.mkdir(mode=0o700)
            auth = store / 'auth.json'
            auth.write_text('{"synthetic":"old"}')
            auth.chmod(0o600)
            test = self
            class Controller:
                options = {'model': 'synthetic', 'call_timeout': 10, 'check_timeout': 10, 'candidate_timeout': 10}
                run = {'run_id': 'synthetic'}
                model = 'synthetic'
                task_ids = set()
                def __init__(self, *args, **kwargs):
                    self.root = root
                    self.state = {'status': 'generating', 'graph': {'nodes': [{'id': 'goal'}]}, 'proofs': {}}
                def handle(self, *args): return {'theories': []}
                def _state(self, theory): return {}, self.state
                def target_contract_green(self): return False
                def close(self): pass
            class Session:
                def __init__(self, **kwargs): self.home = kwargs['codex_home']; self._closed = False
                def __enter__(self): return self
                def __exit__(self, *args):
                    test.assertEqual(auth.read_text(), '{"synthetic":"old"}')
                    self._closed = True
                def run(self, prompt, handler, **kwargs):
                    (self.home / 'auth.json').write_text('{"synthetic":"new"}')
                    handler.__self__.state['status'] = 'sealed'
                    return {'status': 'completed'}
            with patch.dict(os.environ, {PERSIST_ENV: '1'}), \
                    patch('regen.planning.parallel_file_planner.TheoryController', Controller), \
                    patch('regen.infrastructure.codex_workspace_session.CodexWorkspaceSession', Session), \
                    patch('regen.infrastructure.workspace_transcript.audit_workspace_session', return_value={'terminal': True}):
                result = run_theory_session(root, 'Synthetic', resources=type('Resources', (), {'policy': {}})(),
                    executor=object(), settings={'codex_bin': 'synthetic', 'reasoning_effort': 'low'},
                    directory=root / 'session', codex_home=store)
            self.assertEqual(result['status'], 'sealed')
            self.assertEqual(auth.read_text(), '{"synthetic":"new"}')
            self.assertFalse(list(root.rglob('auth.json')))


if __name__ == '__main__':
    unittest.main()
