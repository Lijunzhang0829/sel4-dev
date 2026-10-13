import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from regen.infrastructure.incremental_log import enable,read_artifact,write_artifact,FORMAT
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import UsageSnapshots,experiment_summary


class LoggingTests(unittest.TestCase):
    def test_merkle_event_replay_integrity_and_summary_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);enable(root);ledger=Ledger(root,'run')
            ledger.append('experiment_started')
            ledger.append('tool_started',arguments={'source':'large proof body '*2000})
            UsageSnapshots(ledger).record('thread',{'inputTokens':10,'outputTokens':2,'totalTokens':12},scope={'target_ids':['c']},source='test')
            ledger.append('proof_commit',theory='T',commit_id='commit',journal_artifact='journal.json',nodes=[{'uid':'c','kind':'contract','status':'green'}])
            ledger.append('experiment_finished')
            path=root/'events/000000002.json'
            self.assertEqual(json.loads(path.read_text())['$log_format'],FORMAT)
            replay=Ledger(root,'run');self.assertEqual(replay.rows,ledger.rows)
            result=subprocess.run([sys.executable,'-m','regen.tools.experiment_log','summary',str(root)],capture_output=True,text=True,check=True)
            summary=json.loads(result.stdout)
            self.assertEqual(summary['usage_accounting']['total_tokens'],12)
            self.assertEqual(summary['targets']['c']['journal_artifact'],'journal.json')
            self.assertEqual(summary['curve'][-1]['contracts_green'],1)
            modified=read_artifact(path);modified['data']['arguments']['source']='tampered'
            write_artifact(path,modified)
            with self.assertRaisesRegex(ValueError,'chain mismatch'):Ledger(root,'run')

    def test_failed_session_retains_latest_usage_and_reports_cleanup_failure(self):
        from regen.tools.generic_serial_application import run
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=root/'client';client.write_text('test client')
            config=root/'config.json';config.write_text('{}');auth=root/'auth.json';auth.write_text('{}')
            class Session:
                def __init__(self,**kwargs):self.thread_id='abc-123';self.turn_id='turn';self._usage=None
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def run(self,*args,**kwargs):
                    self._usage={'total':{'inputTokens':100,'outputTokens':9,'totalTokens':109}}
                    raise RuntimeError('provider disconnected before callback')
            controller=Mock();controller.scope={'target_ids':[]}
            controller.close.side_effect=RuntimeError('cleanup failed')
            args=SimpleNamespace(output=str(root/'run'),model_config=str(config),dataset=str(root),auth=str(auth),
                model='gpt-6-astra',reasoning='medium',idle_timeout=1,check_timeout=1,candidate_timeout=2,preflight_seconds=3,inactivity_seconds=7200)
            with patch('regen.tools.generic_serial_application.model_settings',return_value={'codex_bin':str(client),'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest()}),patch('regen.tools.generic_serial_application.Controller',return_value=controller),patch('regen.tools.generic_serial_application.CodexWorkspaceSession',Session):
                result=run(args)
            launch=json.loads((root/'run/launch.json').read_text())['config']
            self.assertEqual((launch['preflight_seconds'],launch['check_timeout'],launch['candidate_timeout']),(3,1,2))
            self.assertFalse(result['passed']);self.assertEqual(result['usage']['total']['totalTokens'],109)
            ledger=Ledger(root/'run','run');summary=experiment_summary(ledger)
            self.assertEqual(summary['usage_accounting']['total_tokens'],109)
            self.assertTrue(any(r['kind']=='run_error' for r in ledger.rows))
            final=ledger.rows[-1]
            self.assertEqual(final['kind'],'experiment_finished')
            self.assertEqual(final['data']['cleanup_error'],'cleanup failed')
            self.assertEqual(json.loads((root/'run/experiment-summary.json').read_text()),summary)

    def test_notification_usage_has_original_transcript_receipt(self):
        from regen.tests.current.test_codex_workspace_session import CodexWorkspaceSessionTest
        with tempfile.TemporaryDirectory() as tmp,CodexWorkspaceSessionTest().session() as session:
            receipts=[]
            def observe(method,params):
                if method=='thread/tokenUsage/updated':receipts.append(session.last_notification_receipt)
            session.run('test',lambda *_:{'ok':True},artifact_dir=tmp,model='test',timeout=2,on_notification=observe)
            self.assertEqual(len(receipts),1)
            receipt=receipts[0]
            raw=Path(receipt['transcript_path']).read_bytes()[:receipt['transcript_offset']]
            event=json.loads(raw.splitlines()[-1])
            self.assertEqual(event['message']['parsed']['method'],'thread/tokenUsage/updated')
            self.assertEqual(hashlib.sha256(event['message']['raw_json'].encode()).hexdigest(),receipt['raw_message_sha256'])

    def test_removed_helper_and_failed_contract_are_not_reported_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp),'run')
            ledger.append('proof_commit',theory='T',nodes=[
                {'uid':'c','kind':'contract','status':'green'},
                {'uid':'h','kind':'helper','status':'green'}])
            ledger.append('proof_commit',theory='T',nodes=[
                {'uid':'c','kind':'contract','status':'failed'}])
            summary=experiment_summary(ledger)
            self.assertEqual(summary['targets']['h']['status'],'removed')
            self.assertEqual(summary['targets']['c']['status'],'failed')
            self.assertEqual(summary['curve'][-1]['contracts_green'],0)
            self.assertEqual(summary['curve'][-1]['helpers_green'],0)
            self.assertIsNone(summary['usage_accounting']['total_tokens'])
            ledger.append('plan_accepted')
            self.assertEqual(experiment_summary(ledger)['targets']['c']['status'],'invalidated_by_plan_revision')
