import hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from regen.planning.generic_codex_agent import Controller,instructions
from regen.infrastructure.generic_continuation import rollback_checkpoint
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.experiment_ledger import Ledger

class PlanLockTests(unittest.TestCase):
    def test_post_acceptance_revision_is_rejected_without_mutation(self):
        c=object.__new__(Controller);c.current={'epoch':4};c.planner=Mock();c.draft={'epoch':4}
        with self.assertRaisesRegex(ValueError,'locked'):
            c.plan({'action':'begin_revision','reason':'optimize checking'}, {})
        self.assertEqual(c.draft,{'epoch':4});c.planner.dispatch.assert_not_called()
        with self.assertRaisesRegex(ValueError,'locked'):
            c.plan({'action':'submit','proposal':{}},{})
        self.assertIn('Do not split, merge, move contracts or replan',instructions())
        fresh=object.__new__(Controller);fresh.current=None;fresh.planner=Mock();fresh.draft={'epoch':1}
        with self.assertRaisesRegex(ValueError,'one initial plan'):
            fresh.plan({'action':'begin_revision','reason':'unreachable'}, {})
        fresh.planner.dispatch.assert_not_called()
    def test_initial_planning_and_post_acceptance_reads_remain_available(self):
        c=object.__new__(Controller);c.current=None;c.planner=Mock();c.planner.dispatch.return_value={'valid':True}
        self.assertEqual(c.plan({'action':'validate','proposal':{}},{'call_id':'test'}),{'valid':True})
        c.current={'epoch':4};c.planner.dispatch.return_value={'hits':[]}
        self.assertEqual(c.plan({'action':'search','values':['x'],'limit':1},{'call_id':'test'}),{'hits':[]})
    def test_rollback_notes_hide_future_and_keep_new_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=object.__new__(Controller);c.root=Path(tmp)
            (c.root/'continuation-origin.json').write_text(json.dumps({'hidden_event_ranges':[[20,40]]}))
            self.assertTrue(c._visible_event({'sequence':19}))
            self.assertFalse(c._visible_event({'sequence':25}))
            self.assertTrue(c._visible_event({'sequence':41}))
    def test_restore_clears_abandoned_proof_authority_but_keeps_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            l=Ledger(tmp,'x');l.append('usage_snapshot',thread_id='t',total={'totalTokens':90},delta={'totalTokens':90},scope={})
            l.append('proof_commit',theory='New',nodes=[{'uid':'new','kind':'contract','status':'green'}])
            l.append('plan_restored',epoch=4)
            l.append('proof_commit',theory='Old',nodes=[{'uid':'old','kind':'contract','status':'sealed'}])
            s=experiment_summary(l)
            self.assertEqual(s['curve'][-1]['contracts_green'],1)
            self.assertEqual(s['usage_accounting']['total_tokens'],90)
    def test_checkpoint_binds_committed_state_and_pre_cut_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);d=root/'theories/T';(d/'document').mkdir(parents=True)
            source='theory T imports Main begin end';draft=d/'document/draft.thy';draft.write_text(source)
            version=hashlib.sha256(source.encode()).hexdigest();p=d/'document/versions'/version;p.mkdir(parents=True);(p/'source.json').write_text(json.dumps({'source':source}))
            snap={'commit_id':'old','directory':str(d),'state':{'status':'generating','batch_requests':0}}
            events=[{'kind':'proof_commit','data':{'epoch':4,'theory':'T','commit_id':'old'}}]
            with patch('regen.execution.shared_proof_workspace.SharedProofWorkspace') as ws:
                ws.return_value._snapshot.return_value=snap
                selected=rollback_checkpoint(root,{'epoch':4,'allocation':{'T':[]}},events,draft.stat().st_mtime+1)
                self.assertEqual(selected['theories']['T']['draft_version'],version)
                with self.assertRaisesRegex(ValueError,'post-cutoff draft'):
                    rollback_checkpoint(root,{'epoch':4,'allocation':{'T':[]}},events,0)
                snap['commit_id']='future'
                with self.assertRaisesRegex(ValueError,'post-cutoff or unobserved'):
                    rollback_checkpoint(root,{'epoch':4,'allocation':{'T':[]}},events,1e12)
