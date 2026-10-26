"""Repair launch arguments change only explicitly authorized runtime bindings."""
import json
from pathlib import Path
import tempfile
import unittest

from regen.tools.parallel_repair_continuation import repair_experiment


class RepairExperimentTests(unittest.TestCase):
    def test_candidate_limit_changes_while_model_resources_and_budget_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'; source.mkdir()
            target = Path(tmp)/'target'
            command = ['/usr/bin/python3', '-m', 'regen.execution.proof_session_runner',
                'parallel-workspace', '--run-dir', str(source), '--candidate-timeout', '120',
                '--check-timeout', '900', '--effective-seconds', '172800',
                '--native-slots', '1', '--max-parallel-theories', '2', '--planner-model', 'gpt-5.6-sol',
                '--execution-policy', '/old/policy.json', '--resume-authorization', 'old-user']
            original = {'command': command, 'model': 'gpt-5.6-sol', 'reasoning_effort': 'xhigh',
                        'effective_seconds': 172800, 'include_planning': True,
                        'contract_visibility': 'owner-blind-v1', 'cwd': '/old/application'}
            (source/'experiment.json').write_text(json.dumps(original))
            changed = repair_experiment(source, target, Path('/new/application'), 'new-user', 300)
            cmd = changed['command']
            self.assertEqual(cmd[cmd.index('--candidate-timeout')+1], '300')
            for key in ['--check-timeout', '--effective-seconds', '--native-slots',
                        '--max-parallel-theories', '--planner-model']:
                self.assertEqual(cmd[cmd.index(key)+1], command[command.index(key)+1])
            self.assertEqual(cmd[cmd.index('--run-dir')+1], str(target))
            self.assertEqual(cmd[cmd.index('--resume-authorization')+1], 'new-user')
            self.assertEqual(changed['reasoning_effort'], 'xhigh')
            self.assertFalse(changed['historical_costs_reset'])
            self.assertEqual(json.loads((source/'experiment.json').read_text()), original)
            for invalid in (0, True, 120, 30, 300.5):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    repair_experiment(source, target, Path('/new/application'), 'user', invalid)


if __name__ == '__main__':
    unittest.main()
