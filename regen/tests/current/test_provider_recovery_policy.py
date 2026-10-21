"""Recovery policy, not lifetime allowance; no provider or mathematical hints."""
import unittest
from regen.infrastructure.provider_http_failure import recovery_decision, recovery_streak, RECOVERY_POLICY


class RecoveryPolicyTests(unittest.TestCase):
    def test_legacy_receipts_are_evidence_not_exhausted_allowance(self):
        rows=[{'kind':'provider_http_retry_scheduled','data':{'theory':'A','retry_number':1}}]*10
        self.assertEqual(recovery_streak(rows,'A'),0)

    def test_all_http_statuses_receive_retry(self):
        for status in range(400,600):
            self.assertTrue(recovery_decision({'http_status':status},0,enabled=True)['retry'])

    def test_transient_outage_never_exhausts_and_delay_is_bounded(self):
        for status in (None,408,409,425,429,500,502,503,504):
            result=recovery_decision({'http_status':status},100000,enabled=True)
            self.assertTrue(result['retry']);self.assertEqual(result['delay_seconds'],300)

    def test_definitive_auth_and_configuration_fault_requires_intervention_after_retry(self):
        for status in (400,401,403,404,422):
            result=recovery_decision({'http_status':status},1,enabled=True)
            self.assertFalse(result['retry'])
            self.assertEqual(result['reason'],'provider_http_requires_intervention')

    def test_bounded_codex_window_and_auth_pause(self):
        for status in (401,403):
            self.assertEqual(recovery_decision({'http_status':status},0,enabled=True,
                elapsed_seconds=0,window_seconds=1800,pause_auth_immediately=True),
                {'retry':False,'reason':'provider_authentication_required'})
        self.assertEqual(recovery_decision({'http_status':503},1,enabled=True,
            elapsed_seconds=1740,window_seconds=1800),
            {'retry':False,'reason':'provider_recovery_window_exhausted'})
        self.assertEqual(recovery_decision({'http_status':503},0,enabled=True,
            elapsed_seconds=0,window_seconds=1800)['delay_seconds'],30)

    def test_never_overlap_unclean_owner_even_for_network_fault(self):
        self.assertFalse(recovery_decision({'http_status':503},0,enabled=True,cleanup_failed=True)['retry'])

    def test_success_and_theory_identity_separate_incidents(self):
        rows=[{'kind':'provider_http_retry_scheduled','data':{'theory':'A',
            'consecutive_failures':3,'retry_policy':RECOVERY_POLICY,'failure':{'http_status':503}}}]
        self.assertEqual(recovery_streak(rows,'A'),3)
        self.assertEqual(recovery_streak(rows,'B'),0)
        self.assertEqual(recovery_streak(rows,'A',{'http_status':401}),0)
        rows.append({'kind':'provider_transport_progress','data':{'theory':'A'}})
        self.assertEqual(recovery_streak(rows,'A'),0)

    def test_progress_timer_deducts_reconnection_not_proof_work(self):
        from unittest.mock import patch
        from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession,ProgressWatch
        s=CodexWorkspaceSession(tools=[{'name':'query','description':'fixture','inputSchema':{'type':'object'}}])
        now=[0.]
        s._progress=ProgressWatch(10,clock=lambda:now[0])
        with patch('regen.infrastructure.codex_workspace_session.time.monotonic',side_effect=lambda:now[0]), \
                patch('regen.infrastructure.progress_events.emit_event'):
            now[0]=5;s._begin_stream_retry({'message':'HTTP 503'})
            now[0]=100;s._finish_stream_retry()
            s._progress.check()
            self.assertEqual(s._progress.last_progress,95)
