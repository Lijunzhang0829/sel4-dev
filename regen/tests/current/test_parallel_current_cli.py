"""Public launch simplification preserves exact frozen replay and budget costs."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from regen.execution.parallel_cli import add_arguments, launch_options
from regen.planning.generation_policy import generation_strategy


class CurrentParallelCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.parser=argparse.ArgumentParser();add_arguments(self.parser)

    def options(self,*extra):
        args=self.parser.parse_args(['--run-dir',str(self.root),*extra])
        return launch_options(args)

    def freeze(self,**updates):
        value={'generation_strategy':generation_strategy('sketch-reviewed-v1'),
            'file_planner_interface':'document','budget_mode':'normal-runtime','budget_seconds':28800,
            'selected_theories':['A'],'execution_policy':{
                'stages':[],'session_stall_retries':0,'provider_feedback_retries':1,
                'no_progress_turn_limit':3,'retry_unresolved':False}}
        value.update(updates)
        (self.root/'parallel-workspace-policy.json').write_text(json.dumps(value))
        return value

    def test_public_surface_has_nineteen_options_without_retired_selectors(self):
        public=[a for a in self.parser._actions if a.option_strings
            and a.help != argparse.SUPPRESS and a.dest!='help']
        self.assertEqual(len(public),19)
        visible=self.parser.format_help()
        for name in ('--allow-external-call','--file-planner-interface','--execution-policy',
                     '--budget-mode','--effective-seconds','--wall-seconds','--extend-effective-seconds',
                     '--codex-home','--focus-contract-id'):
            self.assertNotIn(name,visible)
        self.assertIn('{baseline,sketch-reviewed-v3}',visible)

    def test_new_generation_command_enables_model_and_fixed_current_interface(self):
        opts=self.options()
        self.assertTrue(opts['allow_external_call'])
        self.assertEqual(opts['file_planner_interface'],'document')
        self.assertEqual(opts['budget_mode'],'experiment-runtime')
        self.assertEqual(opts['budget_seconds'],172800)
        self.assertIsNone(opts['execution_policy'])

    def test_new_run_rejects_legacy_graph_strategies_and_budget_flags(self):
        for extra in (('--file-planner-interface','graph'),
                      ('--generation-strategy','sketch-reviewed-v1'),
                      ('--generation-strategy','sketch-guided-v1'),
                      ('--generation-strategy','sketch-phased-v1'),
                      ('--budget-mode','effective-time'),
                      ('--effective-seconds','3600'),('--wall-seconds','3600'),
                      ('--extend-effective-seconds','3600')):
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.options(*extra)

    def test_new_run_rejects_custom_execution_policy(self):
        path=self.root/'custom.json';path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'retired'):self.options('--execution-policy',str(path))

    def test_frozen_normal_run_inherits_budget_and_legacy_strategy(self):
        before=self.freeze()
        opts=self.options('--generation-strategy','sketch-reviewed-v1','--resume-authorization','user-resume')
        self.assertEqual(opts['budget_seconds'],28800)
        self.assertEqual(opts['budget_mode'],'normal-runtime')
        self.assertEqual(opts['execution_policy'],before['execution_policy'])
        self.assertEqual(opts['resume_authorization'],'user-resume')
        self.assertEqual(json.loads((self.root/'parallel-workspace-policy.json').read_text()),before)
        # Original old-normal launch semantics ignore this legacy allowance.
        self.assertEqual(self.options('--effective-seconds','172800')['budget_seconds'],28800)

    def test_frozen_clock_and_interface_cannot_be_replaced(self):
        self.freeze()
        for extra in (('--file-planner-interface','graph'),('--budget-mode','effective-time'),
                      ('--budget-seconds','172800')):
            with self.assertRaises(ValueError):self.options(*extra)

    def test_frozen_execution_policy_allows_only_same_normalized_values(self):
        before=self.freeze();path=self.root/'replay-policy.json';path.write_text('{}')
        self.assertEqual(self.options('--execution-policy',str(path))['execution_policy'],before['execution_policy'])
        path.write_text(json.dumps({'no_progress_turn_limit':7}))
        with self.assertRaisesRegex(ValueError,'frozen'):self.options('--execution-policy',str(path))

    def test_legacy_budget_limit_inherited_without_resetting_or_rewriting_ledger(self):
        self.freeze(budget_mode='effective-time')
        from regen.infrastructure.experiment_ledger import Ledger
        ledger=Ledger(self.root/'effective-generation-budget','legacy')
        row=ledger.append('time_budget_checkpoint',state={'base_seconds':1000,'seconds':1200,'effective_seconds':900})
        opts=self.options('--resume-authorization','resume')
        self.assertEqual(opts['effective_seconds'],1000)
        self.assertEqual(opts['budget_mode'],'effective-time')
        self.assertIsNone(opts['extend_effective_seconds'])
        self.assertEqual(Ledger(ledger.root,'legacy').rows,[row])
        with self.assertRaisesRegex(ValueError,'conflicting'):
            self.options('--budget-seconds','1000','--effective-seconds','2000')

    def test_main_forwards_current_launch_without_external_call_switch(self):
        from regen.execution.proof_session_runner import main
        with patch('sys.argv',['runner','parallel-workspace','--run-dir',str(self.root),
                '--budget-seconds','28800']),redirect_stdout(io.StringIO()),patch(
                'regen.planning.parallel_file_planner.run_parallel_workspace',return_value={'status':'partial'}) as run:
            self.assertEqual(main(),1)
        self.assertTrue(run.call_args.kwargs['allow_external_call'])
        self.assertEqual(run.call_args.kwargs['budget_seconds'],28800)

    def test_supervisor_accepts_current_command_without_redundant_external_switch(self):
        from regen.tools.parallel_workspace_service import _manifest
        command=[sys.executable,'-m','regen.execution.proof_session_runner',
                 'parallel-workspace','--run-dir',str(self.root),'--budget-seconds','28800']
        (self.root/'experiment.json').write_text(json.dumps({'command':command,'cwd':str(self.root)}))
        self.assertEqual(_manifest(self.root)[0]['command'],command)
