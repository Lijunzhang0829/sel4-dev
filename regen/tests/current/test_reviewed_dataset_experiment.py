"""The launch supervisor stops on failed planning and forwards one allowance."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.tools.reviewed_dataset_experiment import run


class DatasetExperimentLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=dict(model='gpt-6-astra',reasoning_effort='low',budget_seconds=172800)
        (self.root/'experiment-config.json').write_text(json.dumps(self.config))
        (self.root/'dataset-acceptance.json').write_text(json.dumps(dict(status='pass')))

    def test_failed_planning_never_initializes_or_starts_generation(self):
        with patch.dict(os.environ),patch('regen.tools.application_release.verify'), \
             patch('regen.tools.reviewed_dataset_experiment.subprocess.run') as planning, \
             patch('regen.execution.proof_session_runner.init_run') as init, \
             patch('regen.tools.parallel_workspace_service.start') as start:
            planning.return_value.returncode=2
            self.assertEqual(run(self.root),2)
            init.assert_not_called();start.assert_not_called()
            self.assertEqual(json.loads((self.root/'phase-status.json').read_text())['status'],'stopped')

    def test_current_clock_handoff_and_single_worker_are_forwarded(self):
        (self.root/'experiment-config.json').write_text(json.dumps({**self.config,'check_timeout':300}))
        planning=self.root/'planning';planning.mkdir()
        (planning/'frozen-plan.json').write_text(json.dumps(dict(state='frozen',file_generation_list=str(planning/'files.json'))))
        def init(proof,*args,**kwargs):proof.mkdir()
        with patch.dict(os.environ),patch('regen.tools.application_release.verify'), \
             patch('regen.tools.reviewed_dataset_experiment.subprocess.run') as call, \
             patch('regen.execution.proof_session_runner.init_run',side_effect=init), \
             patch('regen.tools.parallel_workspace_service.start',return_value=dict(unit='verified.service')), \
             patch('regen.tools.parallel_workspace_memory_guard.monitor',return_value=dict(stop_confirmed=True)):
            call.return_value.returncode=0
            self.assertEqual(run(self.root),0)
        cmd=json.loads((self.root/'proof/experiment.json').read_text())['command']
        for flag,value in (('--budget-seconds','172800'),('--native-slots','1'),
                           ('--max-parallel-theories','1'),('--resident-limit','1'),('--native-memory-mb','16384')):
            self.assertEqual(cmd[cmd.index(flag)+1],value)
        self.assertEqual(cmd[cmd.index('--planning-budget')+1],str(planning))
        for retired in ('--wall-seconds','--effective-seconds','--budget-mode','--execution-policy'):
            self.assertNotIn(retired,cmd)
        # Exercise the real current CLI decoder: the old hard-coded v2 strategy
        # passed this launch fixture but was rejected by every fresh runner.
        from regen.execution.parallel_cli import add_arguments, launch_options
        from regen.planning.generation_policy import DEFAULT_STRATEGY
        parser=argparse.ArgumentParser();add_arguments(parser)
        options=launch_options(parser.parse_args(cmd[5:]))
        self.assertEqual(options['generation_strategy_name'],DEFAULT_STRATEGY)
        self.assertEqual(options['check_timeout'],300)
        with self.assertRaisesRegex(ValueError,'fresh launch'):run(self.root)

    def test_retired_strategy_is_rejected_before_planning(self):
        (self.root/'experiment-config.json').write_text(json.dumps({
            **self.config,'generation_strategy':'sketch-reviewed-v2'}))
        with patch('regen.tools.reviewed_dataset_experiment.subprocess.run') as call:
            with self.assertRaisesRegex(ValueError,'current generation strategy'):
                run(self.root)
        call.assert_not_called()

    def test_shared_protocol_rejects_unsealed_or_different_runtime_before_model(self):
        from regen.execution.check_protocol import descriptor,FILENAME
        (self.root/'experiment-config.json').write_text(json.dumps({**self.config,
            'check_timeout':300,'native_check_protocol':descriptor()}))
        semantic=self.root/'semantic';semantic.mkdir()
        (semantic/FILENAME).write_text(json.dumps({**descriptor(),'proof_worker_reuse':True}))
        with patch('regen.preparation.checker_context.load_context',return_value={'semantic_workspace':semantic}), \
             patch('regen.tools.reviewed_dataset_experiment.subprocess.run') as call:
            with self.assertRaisesRegex(ValueError,'differs from sealed'):
                run(self.root)
        call.assert_not_called()
