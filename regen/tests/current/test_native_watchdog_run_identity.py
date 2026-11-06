"""A repaired run can retain a distinctive identity when its directory is named proof."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.tools import parallel_repair_continuation as repair


class NativeWatchdogRunIdentityTests(unittest.TestCase):
    def test_prepare_passes_explicit_execution_identity_to_latest_workspace_fork(self):
        class ForkReached(Exception):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'; source.mkdir()
            target = Path(tmp)/'new-experiment/proof'
            review_path = Path(tmp)/'review.json'
            review = {'target_implementation_sha256':'reviewed'}
            def read(path):
                if path == review_path:
                    return review
                if path == source/'parallel-workspace-policy.json':
                    return {'execution_policy':{}}
                raise AssertionError(path)
            with patch.object(repair,'native_watchdog_repair_review',return_value=review), \
                 patch.object(repair,'read_artifact',side_effect=read), \
                 patch.object(repair,'native_watchdog_repair_experiment',return_value={}), \
                 patch('regen.tools.parallel_workspace_service.status',return_value={
                     'running':False,'identity_verified':True,'status':'paused'}), \
                 patch('regen.execution.proof_session_runner.acquire_run_mutation_lock',return_value='lock'), \
                 patch('regen.execution.proof_session_runner.release_run_mutation_lock') as release, \
                 patch('regen.infrastructure.parallel_generation_budget.review_parallel_budget_clock',return_value={
                     'clock_review':{}, 'inherited_reviews':[{
                         'policy':'verified-native-watchdog-interruption-no-refund-v1'}]}), \
                 patch('regen.execution.repository_execution.fork_workspace_continuation',side_effect=ForkReached) as fork:
                with self.assertRaises(ForkReached):
                    repair.prepare_native_watchdog(source,target,Path(tmp)/'manifest.json',
                        authorization='repair approved',implementation_review=review_path,
                        run_id='refine-recovery-002')
                fork.assert_called_once_with(source,target,run_id='refine-recovery-002')
                release.assert_called_once_with('lock')

    def test_cli_passes_run_id_only_to_native_watchdog_preparation(self):
        args = ['repair','--source','/source','--output','/output/proof',
            '--manifest','/manifest.json','--authorization','approved',
            '--native-watchdog-repair','--implementation-review','/review.json',
            '--run-id','refine-recovery-002']
        with patch('sys.argv',args), patch.object(repair,'prepare_native_watchdog',return_value={}) as prepare, \
             contextlib.redirect_stdout(io.StringIO()):
            repair.main()
        self.assertEqual(prepare.call_args.kwargs['run_id'],'refine-recovery-002')
        self.assertEqual(prepare.call_args.kwargs['implementation_review'],Path('/review.json'))

    def test_legacy_mode_rejects_the_new_identity_option(self):
        args = ['repair','--source','/source','--output','/output/proof',
            '--manifest','/manifest.json','--authorization','approved','--run-id','recovery']
        with patch('sys.argv',args), patch.object(repair,'prepare') as legacy, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                repair.main()
        self.assertEqual(caught.exception.code,2)
        legacy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
