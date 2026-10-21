import unittest

from regen.tools.stratified_sketch_checkpoints import sample


class SamplingTests(unittest.TestCase):
    def test_repeatable_stratified_sample_without_replacement(self):
        pool=[{'tier':tier,'source_run':str(n),'theory':'T','target_contract_id':str(n)}
              for tier in ('easy','medium','hard') for n in range(5)]
        chosen=sample(pool,20261014)
        self.assertEqual(chosen,sample(list(reversed(pool)),20261014))
        self.assertEqual(len(chosen),6)
        for tier in ('easy','medium','hard'):
            rows=[r for r in chosen if r['tier']==tier]
            self.assertEqual(len(rows),2)
            self.assertEqual(len({r['target_contract_id'] for r in rows}),2)

    def test_insufficient_stratum_is_rejected_without_resampling(self):
        with self.assertRaisesRegex(ValueError,'not enough'):
            sample([],1)
