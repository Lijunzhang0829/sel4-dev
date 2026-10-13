"""Progress is accepted proof changes, not messages, attempts or fresh copies."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import durable
from regen.infrastructure.incremental_log import enable, ObjectStore
from regen.tools import proof_idle_watch as watch
from regen.tools import deferred_workspace_handoff as handoff


class ProofIdleWatchTest(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.run=self.root/'run';enable(self.run)
        self.state={'cursors':{},'seen':{},'last_progress_at':100.0}

    def commit(self, number, proofs, when, event='batch', run=None, theory='A'):
        root=run or self.run
        path=root/'theories'/theory/'acceptance/commits'/f'{number:06d}.json'
        durable(path,{'event':event,'commit_id':str(number),'sequence':number,
            'state':{'proofs':proofs},'evidence':{'large_unused':'x'*100000}},immutable=True)
        os.utime(path,(when,when))
        return path

    def test_contract_and_helper_reset_global_timer(self):
        self.commit(1,{},110,'initial')
        self.commit(2,{'contract:c':'by simp'},200)
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],200)
        self.commit(3,{'contract:c':'by simp','helper:h':'by auto'},300)
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],300)

    def test_exact_thirty_minute_boundary(self):
        self.assertFalse(watch.expired(self.state,1899.99,1800))
        self.assertTrue(watch.expired(self.state,1900,1800))

    def test_contract_only_helpers_do_not_extend_two_hour_wall_clock(self):
        self.state.update(progress_scope='contract',continuous_started_at=150)
        self.commit(1,{'contract:c':'by simp'},200)
        self.commit(2,{'contract:c':'by simp','helper:h':'by auto'},7000)
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],200)
        self.assertFalse(watch.expired(self.state,7399.9,7200))
        self.assertTrue(watch.expired(self.state,7400,7200))
        self.commit(3,{'contract:c':'by auto'},7500)
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],7500)

    def test_authorized_resume_excludes_paused_wall_time(self):
        self.state['continuous_started_at']=10000
        self.assertFalse(watch.expired(self.state,17199,7200))
        self.assertTrue(watch.expired(self.state,17200,7200))
        saved=json.loads(json.dumps(self.state))
        self.assertTrue(watch.expired(saved,17200,7200))

    def test_failed_attempts_queries_and_seals_do_not_reset_timer(self):
        self.commit(1,{},110,'initial')
        self.commit(2,{'helper:h':'by simp'},200)
        self.commit(3,{'helper:h':'by simp'},900,'observe')
        self.commit(4,{'helper:h':'by simp'},1000,'seal')
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],200)

    def test_deletion_and_identical_reacceptance_do_not_credit_progress(self):
        self.commit(1,{'helper:h':'by simp'},200)
        self.commit(2,{},250,'revoke')
        self.commit(3,{'helper:h':'by simp'},300)
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],200)
        self.commit(4,{'helper:h':'by auto'},400)
        watch.scan([self.run],self.state)
        self.assertEqual(self.state['last_progress_at'],400)

    def test_continuation_and_monitor_restart_preserve_timer(self):
        self.commit(1,{'helper:h':'by simp'},200)
        watch.scan([self.run],self.state)
        saved=json.loads(json.dumps(self.state))
        other=self.root/'continuation';enable(other)
        self.commit(1,{'helper:h':'by simp'},900,'initial',run=other)
        watch.scan([self.run,other],saved)
        self.assertEqual(saved['last_progress_at'],200)
        self.commit(2,{'helper:h':'by simp','contract:c':'by auto'},950,run=other)
        watch.scan([self.run,other],saved)
        self.assertEqual(saved['last_progress_at'],950)

    def test_projection_never_reads_embedded_evidence(self):
        path=self.commit(1,{'helper:h':'by simp'},200)
        store=ObjectStore(self.run)
        root=store.node(json.loads(path.read_text())['root'])[0]
        evidence=root[1]['evidence']
        while evidence[0]=='ref':evidence=store.node(evidence[1])[0]
        forbidden=evidence[1]['large_unused'][1]
        original=ObjectStore.node
        def guard(obj,key,*a,**k):
            self.assertNotEqual(key,forbidden)
            return original(obj,key,*a,**k)
        with patch.object(ObjectStore,'node',guard):
            _,proofs=watch.projection(path)
        self.assertEqual(proofs,{'helper:h':'by simp'})

    def test_changed_commit_is_not_silently_accepted(self):
        path=self.commit(1,{'helper:h':'by simp'},200)
        watch.scan([self.run],self.state)
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed'):
            watch.scan([self.run],self.state)

    def test_marker_prevents_handoff_start_even_after_gate(self):
        marker=self.root/'pause.json';marker.write_text('{}')
        cfg={'pause_marker':str(marker)}
        with self.assertRaises(handoff.HandoffPaused):
            handoff.check_pause(cfg)
        with patch.object(handoff.service,'start') as start:
            with self.assertRaises(handoff.HandoffPaused):
                with handoff.launch_interlock(cfg):start('would-start')
            start.assert_not_called()

    def test_pause_saves_checkpoint_and_stops_running_proof_service(self):
        cfg={'run_dirs':[str(self.run)],'pause_marker':str(self.root/'pause.json'),
             'checkpoint_path':str(self.root/'checkpoint.json'),
             'state_path':str(self.root/'state.json'),'status_path':str(self.root/'status.json'),
             'timeout_seconds':1800,'poll_seconds':1,'handoff_unit':'dummy'}
        durable(self.run/'run.json',{'run':'test'})
        with patch.object(watch,'services',side_effect=[
                {str(self.run):{'running':True}}, {str(self.run):{'running':False}}]), \
             patch.object(watch.service,'stop') as stop, \
             patch.object(watch.service,'_show',return_value={'MainPID':'0'}):
            watch.pause(cfg,self.state)
        stop.assert_called_once_with(self.run)
        saved=json.loads(Path(cfg['checkpoint_path']).read_text())
        self.assertEqual(saved['status'],'paused')
        self.assertFalse(saved['pause_marker']['automatic_resume_allowed'])
        self.assertEqual(json.loads(Path(cfg['status_path']).read_text())['phase'],'paused')


if __name__=='__main__':unittest.main()
