"""A clean runtime repair carries costs once, without granting a new allowance."""
import tempfile
from pathlib import Path
import unittest
from regen.execution.proof_commits import durable, digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, review_parallel_budget_clock


class RuntimeContinuationTests(unittest.TestCase):
    def test_clean_repair_preserves_spend_and_claims_source_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';target=root/'target'
            run={'run_id':'source','implementation':{'old':'a'}};new={'run_id':'target','implementation':{'new':'b'}}
            policy={'model':'gpt-6-astra','candidate_timeout':300}
            durable(source/'run.json',run);durable(source/'parallel-workspace-policy.json',policy)
            durable(target/'run.json',new);durable(target/'workspace-continuation.json',{'source_run':str(source)})
            ledger=Ledger(source/'effective-generation-budget','source')
            ledger.append('parallel_budget_binding',policy=POLICY,run_sha256=digest(run),
                contracts_sha256=digest({'A':['a']}),configuration_sha256=digest(policy))
            clock=[0.];stage=EffectiveTimeBudget(ledger,100,monotonic=lambda:clock[0])
            stage.register({'A':['a']});stage.select('A',worker_id='A');clock[0]=20.
            stage.cancel('authorized runtime repair');stage.close()
            reviewed=review_parallel_budget_clock(source)
            review={'policy':'native-stage-runtime-repair-v1','authorization':'user approved repair',
                'source_run_sha256':digest(run),'target_implementation_sha256':digest(new['implementation'])}
            receipt=carry_parallel_budget(source,target,authorization=review['authorization'],
                clock_review=reviewed['clock_review'],native_runtime_review=review)
            self.assertEqual(receipt['carried_effective_seconds'],20)
            self.assertEqual(receipt['total_limit_seconds'],100)
            self.assertEqual(receipt['refund_seconds'],0)
            self.assertTrue((source/'native-watchdog-budget-transfer.json').exists())
            resumed=EffectiveTimeBudget(Ledger(target/'effective-generation-budget','target'),100,
                monotonic=lambda:clock[0],resume_authorization='same approved repair')
            self.assertEqual(resumed.snapshot()['effective_seconds'],20)
            again=root/'again';durable(again/'run.json',{**new,'run_id':'again'})
            durable(again/'workspace-continuation.json',{'source_run':str(source)})
            with self.assertRaisesRegex(ValueError,'already claimed'):
                carry_parallel_budget(source,again,authorization=review['authorization'],
                    clock_review=reviewed['clock_review'],native_runtime_review=review)


if __name__=='__main__':unittest.main()
