"""Private VERO auth state never invokes the provider or checker."""
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from regen.infrastructure.vero_auth_state import (
    existing_vero_auth_source,prepare_vero_auth_state,rotate_vero_auth_state,
)
from regen.tools.generic_serial_application import provider_auth_lease


class VeroAuthStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.seed=self.root/'seed'/'auth.json'
        self.seed.parent.mkdir(mode=0o700)
        self.seed.write_text('{"token":"initial"}')
        self.seed.chmod(0o600)
        self.output=self.root/'results'/'run-001'
        self.output.mkdir(parents=True)

    def test_fresh_and_resume_use_refreshed_auth_directly_outside_output(self):
        state=prepare_vero_auth_state(self.output,self.seed)
        self.assertFalse(state.resumed)
        self.assertFalse(state.home.is_relative_to(self.output))
        self.assertEqual(state.auth.read_text(),'{"token":"initial"}')
        self.assertEqual(stat.S_IMODE(state.home.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(state.auth.stat().st_mode),0o600)
        self.assertEqual(state.lease_sources,(self.seed,state.auth))

        # Codex refreshes auth.json in the mounted HOME during the run.
        state.auth.write_text('{"token":"refreshed"}')
        (state.home/'sessions').mkdir()
        (state.home/'sessions'/'old.jsonl').write_text('old context')
        resumed=prepare_vero_auth_state(self.output,self.seed,resume=True)
        self.assertTrue(resumed.resumed)
        self.assertEqual(resumed.home,state.home)
        self.assertEqual(resumed.auth.read_text(),'{"token":"refreshed"}')
        self.assertFalse((resumed.home/'sessions').exists())
        self.assertTrue(list((resumed.home.parent/'archived-homes').glob('segment-*/sessions/old.jsonl')))
        self.assertEqual(stat.S_IMODE(resumed.auth.stat().st_mode),0o600)

    def test_distinct_outputs_are_isolated_and_resume_requires_prior_state(self):
        first=prepare_vero_auth_state(self.output,self.seed)
        other=self.root/'results'/'run-002'
        other.mkdir()
        with self.assertRaises(FileNotFoundError):
            prepare_vero_auth_state(other,self.seed,resume=True)
        second=prepare_vero_auth_state(other,self.seed)
        self.assertNotEqual(first.home,second.home)
        first.auth.write_text('{"token":"run-one"}')
        self.assertEqual(second.auth.read_text(),'{"token":"initial"}')
        with self.assertRaises(FileExistsError):
            prepare_vero_auth_state(self.output,self.seed)

    def test_interrupted_rotation_recovers_current_token(self):
        state=prepare_vero_auth_state(self.output,self.seed)
        state.auth.write_text('{"token":"refreshed"}')
        (state.home/'sessions').mkdir()
        staging=state.home.with_name('home.next')
        staging.mkdir(mode=0o700)
        state.auth.rename(staging/'auth.json')
        self.assertEqual(existing_vero_auth_source(self.output,self.seed),
                         staging/'auth.json')
        recovered=prepare_vero_auth_state(self.output,self.seed,resume=True)
        self.assertEqual(recovered.auth.read_text(),'{"token":"refreshed"}')
        self.assertFalse((recovered.home/'sessions').exists())

    def test_rejects_state_inside_output_symlink_and_weak_directory_mode(self):
        with self.assertRaises(ValueError):
            prepare_vero_auth_state(self.output,self.seed,state_base=self.output/'auth-state')
        base=self.root/'private-state'
        base.mkdir(mode=0o700)
        state=prepare_vero_auth_state(self.output,self.seed,state_base=base)
        state.home.chmod(0o755)
        with self.assertRaises(ValueError):
            prepare_vero_auth_state(self.output,self.seed,resume=True,state_base=base)
        state.home.chmod(0o700)
        state.auth.unlink()
        state.auth.symlink_to(self.seed)
        with self.assertRaises(ValueError):
            prepare_vero_auth_state(self.output,self.seed,resume=True,state_base=base)

    def test_explicit_private_base_is_reusable_on_resume(self):
        base=self.root/'shared-private-mount'/'auth-states'
        base.parent.mkdir(mode=0o700)
        state=prepare_vero_auth_state(self.output,self.seed,state_base=base)
        state.auth.write_text('{"token":"refreshed"}')
        resumed=prepare_vero_auth_state(self.output,self.seed,resume=True,state_base=base)
        self.assertEqual(resumed.auth.read_text(),'{"token":"refreshed"}')
        self.assertEqual(stat.S_IMODE(base.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(resumed.home.parent.stat().st_mode),0o700)

    def test_next_model_invocation_receives_only_refreshed_auth(self):
        state=prepare_vero_auth_state(self.output,self.seed)
        state.auth.write_text('{"token":"refreshed"}')
        (state.home/'sessions').mkdir()
        (state.home/'sessions'/'earlier.jsonl').write_text('old context')
        rotate_vero_auth_state(state)
        self.assertEqual(state.auth.read_text(),'{"token":"refreshed"}')
        self.assertEqual([item.name for item in state.home.iterdir()],['auth.json'])

    def test_shared_host_lease_rejects_parallel_use_of_seed(self):
        first=prepare_vero_auth_state(self.output,self.seed)
        second_output=self.root/'results'/'run-002'
        second_output.mkdir()
        second=prepare_vero_auth_state(second_output,self.seed)
        lease_dir=self.root/'host-private-leases'
        with provider_auth_lease(self.output,*first.lease_sources,lease_dir=lease_dir):
            with self.assertRaisesRegex(ValueError,'active in another run'):
                with provider_auth_lease(second_output,*second.lease_sources,
                                         lease_dir=lease_dir):
                    self.fail('same auth seed must not run concurrently')

    def test_failed_fresh_copy_removes_only_new_private_run(self):
        base=self.root/'private-state'
        with patch('regen.infrastructure.vero_auth_state._copy_seed',
                   side_effect=OSError('copy failed')):
            with self.assertRaisesRegex(OSError,'copy failed'):
                prepare_vero_auth_state(self.output,self.seed,state_base=base)
        self.assertEqual(list(base.iterdir()),[])


if __name__=='__main__':
    unittest.main()
