"""Evidence-bound continuation retains costs and rejects unrelated open-item faults."""
import json
import unittest
from regen.tests.current import test_parallel_budget_continuation as fixtures
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import review_parallel_budget_clock, carry_parallel_budget


class HandlerStopReviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.ParallelBudgetContinuationTests.setUp(self)
        ledger = Ledger(self.source/'effective-generation-budget', 'source')
        self.event = ledger.append('generation_clock_untrusted', theory='A', evidence={
            'reason':'session_stopped_with_open_model_item', 'thread_id':'thread',
            'turn_id':'turn', 'generation_id':'generation', 'item_ids':['open']})
        now = self.event['time_unix']
        params = {'threadId':'thread','turnId':'turn'}
        self.rows = [
            {'time_unix':now-3,'direction':'in','message':{'method':'item/started','params':{
                **params,'item':{'type':'agentMessage','id':'open'}}}},
            {'time_unix':now-2,'direction':'in','message':{'id':7,'method':'item/tool/call','params':{
                **params,'tool':'proof_action','arguments':{'theory':'A','proposal':{'action':'report_unresolved'}}}}},
            {'time_unix':now-1,'direction':'out','message':{'id':7,'result':{'success':True,'contentItems':[
                {'type':'inputText','text':json.dumps({'theory_stop':True,'theory_outcome':{
                    'status':'unknown','binding':{'theory':'A'}}})}]}}},
            {'time_unix':now,'direction':'local','message':{'event':'provider_request_finished',
                'generation_id':'generation','thread_id':'thread','turn_id':'turn',
                'outcome':'retained','reason':'theory_outcome'}},
            {'time_unix':now+.01,'direction':'local','message':{'event':'turn_stopped_by_handler',
                'thread_id':'thread','turn_id':'turn','status':'stopped_by_handler','stop_reason':'theory_outcome'}}]
        self.path = self.source/'parallel-sessions/session/A/session-transcript.jsonl'
        self.path.parent.mkdir(parents=True)
        self.save()

    def save(self):
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))

    def test_verified_handler_stop_carries_every_second_and_original_uncertainty(self):
        before = Ledger(self.source/'effective-generation-budget','source').rows
        reviewed = review_parallel_budget_clock(self.source)
        result = carry_parallel_budget(self.source,self.target,authorization='repair authorized',
            clock_review=reviewed['clock_review'])
        self.assertEqual(result['carried_effective_seconds'],20)
        self.assertEqual(result['refund_seconds'],0)
        self.assertTrue(result['historical_uncertainty_retained'])
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,before)
        self.assertEqual(reviewed['inherited_reviews'][0]['handler_stop_line'],5)
        inherited = review_parallel_budget_clock(self.target)
        self.assertEqual(inherited['inherited_reviews'][0], reviewed['inherited_reviews'][0])

    def test_missing_or_foreign_handler_stop_cannot_authorize_review(self):
        for change in ('absent','foreign','wrong_reason'):
            with self.subTest(change=change):
                original = self.rows[-1]['message'].copy()
                if change=='absent': self.rows[-1]['message']['event']='session_failed'
                elif change=='foreign': self.rows[-1]['message']['turn_id']='another'
                else:self.rows[-1]['message']['stop_reason']='unexpected'
                self.save()
                with self.assertRaises(ValueError):review_parallel_budget_clock(self.source)
                self.rows[-1]['message']=original

    def test_rejected_tool_or_different_open_items_are_not_witnesses(self):
        self.rows[2]['message']['result']['success']=False
        self.save()
        with self.assertRaises(ValueError):review_parallel_budget_clock(self.source)
        self.rows[2]['message']['result']['success']=True
        self.rows[0]['message']['params']['item']['id']='different'
        self.save()
        with self.assertRaises(ValueError):review_parallel_budget_clock(self.source)

    def test_real_terminal_shape_invalidates_open_item_witness(self):
        self.rows.insert(-1, {'time_unix':self.event['time_unix'], 'direction':'in',
            'message':{'method':'turn/completed','params':{'threadId':'thread',
                'turn':{'id':'turn','status':'completed'}}}})
        self.save()
        with self.assertRaises(ValueError):review_parallel_budget_clock(self.source)

    def test_even_valid_text_change_cannot_rebind_inherited_witness(self):
        reviewed=review_parallel_budget_clock(self.source)
        carry_parallel_budget(self.source,self.target,authorization='repair authorized',clock_review=reviewed['clock_review'])
        self.rows[0]['message']['params']['item']['text']='changed evidence'
        self.save()
        with self.assertRaisesRegex(ValueError,'witness changed'):
            review_parallel_budget_clock(self.target)

    def test_changed_ancestor_witness_is_rechecked_after_continuation(self):
        reviewed=review_parallel_budget_clock(self.source)
        carry_parallel_budget(self.source,self.target,authorization='repair authorized',clock_review=reviewed['clock_review'])
        self.rows[-1]['message']['stop_reason']='unexpected'
        self.save()
        with self.assertRaises(ValueError):review_parallel_budget_clock(self.target)


if __name__=='__main__':unittest.main()
