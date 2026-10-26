import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from regen.tools import reviewed_planning
from regen.tools.reviewed_planning import decisions_valid,replace_ids

class ReviewedPlanningTests(unittest.TestCase):
    def test_complete_decisions(self):
        review={'issues':[{'id':'I1'}]}
        good={'issue_id':'I1','decision':'local','reason':'local proof suffices','tradeoff':'no boundary change','remaining_risk':'proof pending'}
        decisions_valid([good],review)
        for bad in [[],[good,good],[{**good,'issue_id':'I2'}],[{**good,'reason':''}],[{**good,'decision':'proved'}]]:
            with self.assertRaises(ValueError):decisions_valid(bad,review)

    def test_public_ids_include_prose(self):
        x={'ids':['contract:long:id'],'reason':'Use contract:long:id here'}
        self.assertEqual(replace_ids(x,{'contract:long:id':'C0001'}),{'ids':['C0001'],'reason':'Use C0001 here'})

    def test_unlimited_review_policy_and_model_are_bound_and_dispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'input.json'; source.write_text('{}')
            policy = root/'policy.json'; policy.write_text(json.dumps({'model':{}}))
            output = root/'output'
            argv = ['reviewed_planning', '--input', str(source), '--policy', str(policy),
                    '--output', str(output), '--model', 'gpt-5.6-sol', '--reasoning-effort', 'medium',
                    '--allow-external-call']
            # Stop at review dispatch: this test exercises CLI/config forwarding,
            # and must not start either a planner or a provider.
            with patch('sys.argv', argv), \
                    patch.object(reviewed_planning, 'run_command',
                                 side_effect=[None, RuntimeError('review dispatch reached')]) as command:
                with self.assertRaisesRegex(RuntimeError, 'review dispatch reached'):
                    reviewed_planning.main()
            binding = json.loads((output/'binding.json').read_text())
            self.assertIsNone(binding['review_max_repairs'])
            self.assertEqual(binding['review_output_repair_policy'], reviewed_planning.OUTPUT_REPAIR_POLICY)
            self.assertEqual(binding['model'], 'gpt-5.6-sol')
            args = command.call_args_list[1].args[2]
            self.assertNotIn('--max-repairs', args)
            self.assertEqual(args[args.index('--model')+1], 'gpt-5.6-sol')

if __name__=='__main__':unittest.main()
