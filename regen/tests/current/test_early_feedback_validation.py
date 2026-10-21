"""Input-integrity tests; actual Isabelle coverage is in the replay report."""
import json

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.tools import early_feedback_validation as validation


def fixture_case(tmp_path):
    task = tmp_path/'check-tasks'/'saved'
    task.mkdir(parents=True)
    candidate = tmp_path/'1.thy'
    prefix = '\nlemma accepted: "True" by simp\n'
    delta = '\nlemma candidate: "True" by simp'
    text = 'theory Test\nimports Main\nbegin\n'+prefix+delta+'\nend\n'
    candidate.write_text(text)
    candidate.with_suffix('.request').write_text(delta)
    (tmp_path/'imports').write_text('Main\n')
    check = dict(source=str(candidate),source_sha256=validation.sha(text),
                 delta_sha256=validation.sha(delta),prefix_sha256=validation.sha(prefix))
    (task/'result.json').write_text(json.dumps(dict(feedback=dict(check=check),
        green_node_ids=['helper:accepted','contract:Refine.X:original:abcdef'])))
    return candidate, prefix


class InputIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.candidate, self.prefix = fixture_case(self.root)
        self.tasks = patch.object(validation,'TASKS',[(1,'saved','command expected')])
        self.tasks.start()
        self.addCleanup(self.tasks.stop)

    def test_frozen_candidate_and_prefix_identity(self):
        case, = validation.load_cases(self.root)
        self.assertEqual(case['prefix'],self.prefix)
        self.assertEqual(case['names'],['accepted','original'])

    def test_changed_saved_source_fails_closed(self):
        self.candidate.write_text(self.candidate.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'candidate hash mismatch'):
            validation.load_cases(self.root)

    def test_changed_saved_delta_fails_closed(self):
        self.candidate.with_suffix('.request').write_text('lemma injected: "True" by simp')
        with self.assertRaisesRegex(ValueError,'candidate hash mismatch'):
            validation.load_cases(self.root)

    def test_net_savings_charge_missed_preflight(self):
        rows=[{'full':{'wall_ms':100},'preflight':{'wall_ms':20},
               'same_error_detected_early':hit} for hit in (True,False,False)]
        result=validation.summarize({'cases':rows,'pass_validation':True,'prefix_preparation_seconds':900})
        self.assertEqual(result['optimized_strategy_ms'],260)
        self.assertEqual(result['net_saved_ms'],40)
