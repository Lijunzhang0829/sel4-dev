import tempfile,unittest
from regen.infrastructure.experiment_activity import ActivityStop
from regen.infrastructure.experiment_ledger import Ledger
from regen.execution.check_tasks import CheckCancelled
from regen.tools.contract_inactivity_guard import ContractDeadline

class ContractOnlyTests(unittest.TestCase):
    def test_helpers_logged_but_never_extend_wall_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            now=[0.];l=Ledger(d,'t',clock=lambda:now[0]);s=ActivityStop(l,7200)
            for t,uid in [(100,'helper:T:h'),(7000,'helper-slot:T:helpers')]:
                now[0]=t;self.assertTrue(s.observe(uid,'new',kind='draft'));self.assertEqual(s.deadline,7200)
                self.assertFalse(l.rows[-1]['data']['refreshes_inactivity'])
            now[0]=7200
            with self.assertRaises(CheckCancelled):s.check()
            self.assertEqual(s.reason,'no_contract_update')
    def test_new_contract_content_resets_but_repeated_content_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            now=[0.];l=Ledger(d,'t',clock=lambda:now[0]);s=ActivityStop(l)
            now[0]=100;s.observe('contract:c','by simp',kind='draft');self.assertEqual(s.deadline,7300)
            now[0]=200;s.observe('contract:c','by simp',kind='draft');self.assertEqual(s.deadline,7300)
            s.observe('contract:c',{'status':'green'},kind='validation');self.assertEqual(s.deadline,7400)
    def test_live_guard_uses_existing_contract_time_not_activation_or_helper_time(self):
        s=ContractDeadline(['c'])
        s.consume({'kind':'activity_started','time_unix':10})
        s.consume({'kind':'target_updated','time_unix':100,'data':{'target_id':'c'}})
        s.consume({'kind':'target_updated','time_unix':7000,'data':{'target_id':'helper:T:h'}})
        self.assertEqual(s.deadline,7300);self.assertFalse(s.expired(7299));self.assertTrue(s.expired(7300))
