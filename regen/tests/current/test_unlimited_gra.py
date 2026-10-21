import unittest
from regen.execution.repository_execution import gra_revision_allowed

class UnlimitedGraTest(unittest.TestCase):
    def test_unlimited_ignores_both_caps_without_resetting_history(self):
        history={'failed':8,'other':200}
        self.assertTrue(gra_revision_allowed(history,'failed',None,16))
        self.assertEqual(history,{'failed':8,'other':200})

    def test_finite_legacy_limits_still_apply(self):
        self.assertFalse(gra_revision_allowed({'failed':8},'failed',8,16))
        self.assertFalse(gra_revision_allowed({'other':16},'failed',8,16))
        self.assertTrue(gra_revision_allowed({'failed':7},'failed',8,16))
        self.assertFalse(gra_revision_allowed({},'failed',0,16))
