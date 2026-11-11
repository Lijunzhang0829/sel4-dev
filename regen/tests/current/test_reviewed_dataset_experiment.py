"""The launch supervisor stops on failed planning and forwards one allowance."""
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
        with self.assertRaisesRegex(ValueError,'fresh launch'):run(self.root)
