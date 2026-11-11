"""Exercise the production argument parser, without launching a model."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.execution import proof_session_runner as runner
from regen.planning.generation_policy import generation_strategy

class GuidedSketchCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def freeze(self,name):
        (self.root/'parallel-workspace-policy.json').write_text(json.dumps({
            'generation_strategy':generation_strategy(name),'file_planner_interface':'document',
            'budget_mode':'normal-runtime','budget_seconds':1000}))

    def test_frozen_guided_strategy_reaches_parallel_runner(self):
        self.freeze('sketch-guided-v1')
        argv=['runner','parallel-workspace','--run-dir',str(self.root),
              '--planner-model','gpt-6-astra','--generation-strategy','sketch-guided-v1']
        with patch('sys.argv',argv), patch('regen.planning.parallel_file_planner.run_parallel_workspace',
                return_value={'status':'repository_verified'}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),0)
        self.assertEqual(run.call_args.kwargs['generation_strategy_name'],'sketch-guided-v1')
        self.assertEqual(run.call_args.kwargs['model'],'gpt-6-astra')
    def test_frozen_phased_strategy_reaches_parallel_runner(self):
        self.freeze('sketch-phased-v1')
        argv=['runner','parallel-workspace','--run-dir',str(self.root),
              '--planner-model','gpt-6-astra','--generation-strategy','sketch-phased-v1']
        with patch('sys.argv',argv), patch('regen.planning.parallel_file_planner.run_parallel_workspace',
                return_value={'status':'repository_verified'}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),0)
        self.assertEqual(run.call_args.kwargs['generation_strategy_name'],'sketch-phased-v1')
        self.assertEqual(run.call_args.kwargs['model'],'gpt-6-astra')
