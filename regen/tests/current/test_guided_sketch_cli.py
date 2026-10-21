"""Exercise the production argument parser, without launching a model."""
import contextlib
import io
import unittest
from unittest.mock import patch
from regen.execution import proof_session_runner as runner

class GuidedSketchCliTests(unittest.TestCase):
    def test_guided_strategy_reaches_parallel_runner(self):
        argv=['runner','parallel-workspace','--run-dir','/tmp/unused-cli-test',
              '--planner-model','gpt-6-astra','--generation-strategy','sketch-guided-v1']
        with patch('sys.argv',argv), patch('regen.planning.parallel_file_planner.run_parallel_workspace',
                return_value={'status':'repository_verified'}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),0)
        self.assertEqual(run.call_args.kwargs['generation_strategy_name'],'sketch-guided-v1')
        self.assertEqual(run.call_args.kwargs['model'],'gpt-6-astra')
    def test_phased_strategy_reaches_parallel_runner(self):
        argv=['runner','parallel-workspace','--run-dir','/tmp/unused-cli-test',
              '--planner-model','gpt-6-astra','--generation-strategy','sketch-phased-v1']
        with patch('sys.argv',argv), patch('regen.planning.parallel_file_planner.run_parallel_workspace',
                return_value={'status':'repository_verified'}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),0)
        self.assertEqual(run.call_args.kwargs['generation_strategy_name'],'sketch-phased-v1')
        self.assertEqual(run.call_args.kwargs['model'],'gpt-6-astra')
