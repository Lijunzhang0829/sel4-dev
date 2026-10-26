"""The explicit checker repair cannot change experimental settings or history."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure import generic_continuation as migration


class NativeLocaleRepairTests(unittest.TestCase):
    def setUp(self):
        self.source=Path('/runs/codex-crefine-owner-blind-sol-xhigh-effective48h-planning-included-fresh-20261025-001')
        self.changed={'execution/batch_checker.ML','infrastructure/incremental_log.py','infrastructure/generic_continuation.py',
                      'tools/generic_serial_application.py'}
        self.old_files={name:'old-'+name for name in self.changed}
        self.old_files['execution/batch_checker.ML']='58b3287e0dce172e3bc43ad9968094c43fc3546f855eac2431f16f7b4394f2ec'
        self.old_files['infrastructure/incremental_log.py']='aceca00294facc7338df06cabc4603441824509de1ebce8de430d16e408e5218'
        self.old_files['execution/check_executor.py']='unchanged'
        self.new_files={**self.old_files,**{name:'new-'+name for name in self.changed}}
        self.new_files['execution/batch_checker.ML']=migration._CREFINE_LOCALE_REPAIR_CHECKER
        self.new_files['infrastructure/incremental_log.py']='100b827467855dbfb29977d23a2ec27521f7143ab15b0628807376ad9b65a8e3'
        self.old={'application_sha256':digest(self.old_files),'run_id':'old','budget_mode':'effective-time',
                  'model':'gpt-5.6-sol','effort':'xhigh','include_planning_time':True,'native_slots':1}
        self.new={**self.old,'application_sha256':digest(self.new_files),'run_id':'new'}
        self.history=SimpleNamespace(rows=[{'kind':'experiment_paused','event_id':'immutable-tail','data':{}}])

    def audit(self, *, source=None, config=None, files=None, history=None):
        with patch.object(migration,'_CREFINE_LOCALE_REPAIR_SOURCE_APPLICATION',digest(self.old_files)), \
                patch.object(migration,'_implementation_files',side_effect=[self.old_files,files or self.new_files]):
            return migration._audited_locale_audit_repair(source or self.source,self.old,
                config or self.new,history or self.history,Path('/frozen-source'))

    def test_receipt_binds_exact_source_tail_and_only_the_audited_changes(self):
        receipt=self.audit()
        self.assertEqual(set(receipt['changed_files']),self.changed)
        self.assertEqual(receipt['source_last_event_id'],'immutable-tail')
        self.assertEqual(receipt['source_application_sha256'],self.old['application_sha256'])
        self.assertEqual(receipt['new_application_sha256'],self.new['application_sha256'])
        self.assertNotIn('refund_seconds',receipt)

    def test_repair_refuses_changed_model_resources_and_planning_accounting(self):
        for key,value in [('effort','medium'),('native_slots',2),('include_planning_time',False)]:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'frozen experiment settings'):
                self.audit(config={**self.new,key:value})

    def test_repair_refuses_unrelated_or_unverified_checker_changes(self):
        for name in ['execution/check_executor.py','execution/batch_checker.ML']:
            files={**self.new_files,name:'unaudited'}
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'unaudited executable code'):
                self.audit(files=files,config={**self.new,'application_sha256':digest(files)})

    def test_repair_refuses_a_live_or_different_run(self):
        with self.assertRaisesRegex(ValueError,'paused owner-blind'):
            self.audit(history=SimpleNamespace(rows=[{'kind':'tool_started'}]))
        with self.assertRaisesRegex(ValueError,'paused owner-blind'):
            self.audit(source=Path('/runs/other-run'))

    def test_repair_notice_requires_recorded_migration_and_never_declares_green(self):
        binding={'source_error':[]}
        self.assertNotIn('fact-name audit was repaired',migration.prompt(binding))
        binding['implementation_migration']='codex-crefine-native-locale-audit-20261025-v1'
        text=migration.prompt(binding)
        self.assertIn('fact-name audit was repaired',text)
        self.assertIn('remain unaccepted',text)
        self.assertIn('fresh complete native audit',text)


if __name__=='__main__':
    unittest.main()
