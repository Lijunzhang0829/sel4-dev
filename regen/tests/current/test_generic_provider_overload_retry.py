import hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from regen.tools.generic_serial_application import run,provider_overload_backoff
from regen.infrastructure.codex_workspace_session import ProviderOverloaded, ProviderHTTPError
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import observed_usage

class OverloadRetryTests(unittest.TestCase):
    def test_backoff_bounded_and_cancellation_checked_without_resetting_clock(self):
        stage=Mock();ledger=Mock()
        for n,delay in [(1,30),(2,60),(3,120),(4,240),(5,300),(50,300)]:
            provider_overload_backoff(stage,ledger,n,ProviderOverloaded('capacity'))
            stage.event.wait.assert_called_with(delay)
        self.assertEqual(stage.check.call_count,12)
        self.assertEqual(ledger.append.call_count,6)
    def test_overload_creates_new_session_and_controller_preserves_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=root/'client';client.write_bytes(b'test');auth=root/'auth';auth.write_text('{}');cfg=root/'config';cfg.write_text('{}')
            args=SimpleNamespace(output=str(root/'out'),model_config=str(cfg),auth=str(auth),dataset=str(root),model='gpt-6-astra',reasoning='medium',idle_timeout=900,check_timeout=900,inactivity_seconds=7200)
            controllers=[];sessions=[];artifacts=[]
            class C:
                def __init__(self,output,dataset,ledger,stage,config):
                    self.ledger=ledger;self.scope={};self.resources=object();self.executor=Mock();self.planner=Mock();controllers.append(self)
                def handle(self,*args):pass
                def close(self):pass
            class S:
                def __init__(self,**kwargs):
                    self.home=Path(kwargs['codex_home']);assert {p.name for p in self.home.iterdir()}=={'auth.json'};self.home.joinpath('sessions').mkdir();self.index=len(sessions);sessions.append(self);self.thread_id='thread'+str(self.index);self.turn_id='turn';self._usage={'total':{'totalTokens':11*(self.index+1)}}
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def run(self,prompt,handler,**kwargs):
                    artifacts.append(kwargs['artifact_dir'])
                    if self.index==0:raise ProviderOverloaded('capacity')
                    controllers[-1].ledger.append('integrated',verified=True)
                    return {'usage':self._usage}
            with patch('regen.tools.generic_serial_application.model_settings',return_value={'codex_bin':str(client),'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest(), 'role_models':{'File Planner':'gpt-6-astra'}}),patch('regen.tools.generic_serial_application.Controller',C),patch('regen.tools.generic_serial_application.CodexWorkspaceSession',S),patch('regen.tools.generic_serial_application.provider_overload_backoff') as backoff:
                report=run(args)
            self.assertTrue(report['passed'],report);self.assertEqual(len(controllers),2);self.assertEqual(len(sessions),2);backoff.assert_called_once()
            self.assertNotEqual(artifacts[0],artifacts[1]);self.assertNotEqual(sessions[0].home,sessions[1].home)
            self.assertFalse(sessions[0].home.exists());self.assertFalse(sessions[1].home.exists())
            self.assertEqual(observed_usage(Ledger(root/'out','out'))['total_tokens'],33)

    def test_http_transport_fault_uses_new_thread_and_preserves_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=root/'client';client.write_bytes(b'test')
            auth=root/'auth';auth.write_text('{}')
            cfg=root/'config';cfg.write_text('{}')
            args=SimpleNamespace(output=str(root/'out'),model_config=str(cfg),
                auth=str(auth),dataset=str(root),model='gpt-6-astra',reasoning='medium',
                idle_timeout=900,check_timeout=900,inactivity_seconds=7200)
            sessions=[];controllers=[];artifacts=[]
            class C:
                def __init__(self,output,dataset,ledger,stage,config):
                    self.ledger=ledger;self.scope={};self.resources=object()
                    self.executor=Mock();self.planner=Mock();controllers.append(self)
                def handle(self,*args):pass
                def close(self):pass
            class S:
                def __init__(self,**kwargs):
                    assert kwargs['http_retry_mode'] is True
                    self.home=Path(kwargs['codex_home']);self.index=len(sessions)
                    sessions.append(self);self.thread_id=f'thread{self.index}'
                    self.turn_id='turn';self._usage={'total':{'totalTokens':11*(self.index+1)}}
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def run(self,prompt,handler,**kwargs):
                    artifacts.append(kwargs['artifact_dir'])
                    if self.index==0:
                        raise ProviderHTTPError({'kind':'transport','http_status':None})
                    controllers[-1].ledger.append('integrated',verified=True)
                    return {'usage':self._usage}
            def backoff(stage,ledger,retry,error):
                self.assertIsInstance(error,ProviderHTTPError)
                self.assertEqual(retry,1)
            settings={'codex_bin':str(client),
                'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest(),
                'role_models':{'File Planner':'gpt-6-astra'}}
            with patch('regen.tools.generic_serial_application.model_settings',return_value=settings), \
                 patch('regen.tools.generic_serial_application.Controller',C), \
                 patch('regen.tools.generic_serial_application.CodexWorkspaceSession',S), \
                 patch('regen.tools.generic_serial_application.provider_overload_backoff',side_effect=backoff):
                report=run(args)
            self.assertTrue(report['passed'],report)
            self.assertEqual(len(sessions),2)
            self.assertEqual(len(controllers),2)
            self.assertNotEqual(*artifacts)
            self.assertEqual(observed_usage(Ledger(root/'out','out'))['total_tokens'],33)

    def test_http_backoff_writes_distinct_retry_receipt(self):
        stage=Mock();ledger=Mock()
        provider_overload_backoff(stage,ledger,1,
            ProviderHTTPError({'kind':'transport','http_status':None}))
        self.assertEqual(ledger.append.call_args.args[0],'provider_http_retry')
        stage.event.wait.assert_called_once_with(30)
