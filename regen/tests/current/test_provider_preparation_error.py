"""Provider failures are fatal infrastructure results, not proof feedback."""
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io
from regen.execution.check_tasks import NativePreparationError
from regen.execution.repository_execution import ProviderPreparationError


class ProviderPreparationErrorTest(unittest.TestCase):
    def test_categories_preserve_evidence_without_claiming_oom(self):
        for code, category in [(124,'timeout'),(137,'killed'),(1,'build_failed')]:
            with self.subTest(code=code):
                result={'returncode':code,'diagnostic':'original failure','verdict':'red'}
                error=ProviderPreparationError(result)
                self.assertIsInstance(error,NativePreparationError)
                self.assertEqual(error.category,category)
                self.assertEqual(error.result,result)
                result['diagnostic']='mutated'
                self.assertEqual(error.result['diagnostic'],'original failure')
                self.assertNotIn('OOM',str(error))

    def test_executor_diagnostic_can_be_rehydrated(self):
        error=ProviderPreparationError('serialized infrastructure failure')
        self.assertIsInstance(error,NativePreparationError)
        self.assertIn('serialized infrastructure failure',str(error))

    def test_diagnostic_is_bounded_but_structured_evidence_retained(self):
        error=ProviderPreparationError({'returncode':124,'diagnostic':'x'*20000})
        self.assertLess(len(str(error)),12100)
        self.assertEqual(len(error.result['diagnostic']),20000)

    def test_cli_keeps_provider_budget_separate(self):
        from regen.execution.proof_session_runner import main
        for extra, expected_heap, expected_timeout in [([],None,1800),
                (['--provider-build-timeout','1500','--build-ml-heap-mb','11000'],11000,1500)]:
            with self.subTest(extra=extra), patch('sys.argv',[
                    'runner','parallel-workspace','--run-dir','unused',
                    '--allow-external-call',*extra]), patch(
                    'regen.planning.parallel_file_planner.run_parallel_workspace',
                    return_value={'status':'repository_verified'}) as launch, redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
                options=launch.call_args.kwargs
                self.assertEqual(options['provider_build_timeout'],expected_timeout)
                self.assertEqual(options['build_ml_heap_mb'],expected_heap)
                self.assertEqual(options['check_timeout'],900)
                self.assertEqual(options['candidate_timeout'],120)


if __name__=='__main__':unittest.main()
