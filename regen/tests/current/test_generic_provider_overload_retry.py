import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from regen.tools.generic_serial_application import (run,provider_overload_backoff,
    private_provider_auth,fresh_provider_home,provider_auth_lease)
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
            def backoff(stage,ledger,retry,error,decision=None):
                self.assertIsInstance(error,ProviderHTTPError)
                self.assertEqual(retry,1)
                self.assertEqual(decision['delay_seconds'],30)
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

    def test_private_auth_survives_refresh_and_continuation_outside_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seed=root/'auth.json'
            seed.write_text('{"tokens":{"refresh_token":"old"}}')
            first=root/'out-1';first.mkdir()
            auth,origin,_=private_provider_auth(first,seed)
            self.assertEqual(origin,'provided_seed')
            self.assertFalse(auth.is_relative_to(first))
            self.assertEqual(auth.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(auth.stat().st_mode & 0o777,0o600)
            with fresh_provider_home(auth) as home:
                Path(home,'auth.json').write_text('{"tokens":{"refresh_token":"new"}}')
            self.assertIn('new',auth.read_text())
            self.assertIn('old',seed.read_text())
            second=root/'out-2';second.mkdir()
            inherited,origin,_=private_provider_auth(second,seed,continue_from=first)
            self.assertEqual(origin,'continuation_private_state')
            self.assertEqual(inherited.read_text(),auth.read_text())
            self.assertFalse(any(p.name=='auth.json' for p in first.rglob('*')))
            self.assertFalse(any(p.name=='auth.json' for p in second.rglob('*')))

    def test_active_duplicate_seed_is_refused_even_when_file_paths_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);one=root/'one.json';two=root/'two.json'
            one.write_text('{"tokens":{"refresh_token":"same"},"access_token":"a"}')
            two.write_text('{"tokens":{"refresh_token":"same"},"access_token":"b"}')
            with provider_auth_lease(root/'out-1',one):
                with self.assertRaisesRegex(ValueError,'active in another run'):
                    with provider_auth_lease(root/'out-2',two):pass

    def test_different_output_parents_contend_in_independent_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);left=root/'left';right=root/'right'
            left.mkdir();right.mkdir()
            first=left/'auth.json';second=right/'auth.json'
            refresh='fixture-'+hashlib.sha256(str(root).encode()).hexdigest()
            first.write_text(json.dumps({'tokens':{'refresh_token':refresh},'access_token':'a'}))
            second.write_text(json.dumps({'tokens':{'refresh_token':refresh},'access_token':'b'}))
            script=("import sys\n"
                "from regen.tools.generic_serial_application import provider_auth_lease\n"
                "with provider_auth_lease(sys.argv[1],sys.argv[2]):\n"
                " print('held',flush=True)\n"
                " sys.stdin.readline()\n")
            with subprocess.Popen([sys.executable,'-c',script,str(left/'out'),str(first)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True) as holder:
                self.assertEqual(holder.stdout.readline().strip(),'held',
                    holder.stderr.read() if holder.poll() is not None else 'child failed to lock')
                with self.assertRaisesRegex(ValueError,'active in another run'):
                    with provider_auth_lease(right/'out',second):pass
                holder.stdin.write('\n');holder.stdin.flush()
                self.assertEqual(holder.wait(timeout=5),0)
            with provider_auth_lease(right/'out',second):pass

    def test_auth_lease_rejects_unsafe_directory_and_nonlocal_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seed=root/'auth.json';seed.write_text('{}')
            target=root/'real';target.mkdir(mode=0o700)
            alias=root/'link';alias.symlink_to(target)
            with self.assertRaisesRegex(ValueError,'symlink'):
                with provider_auth_lease(root/'out',seed,lease_dir=alias):pass
            nested=target/'nested';nested.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError,'symlink'):
                with provider_auth_lease(root/'out',seed,lease_dir=alias/'nested'/'leases'):pass
            target.chmod(0o755)
            with self.assertRaisesRegex(ValueError,'mode 0700'):
                with provider_auth_lease(root/'out',seed,lease_dir=target):pass
            target.chmod(0o700)
            with patch('regen.tools.generic_serial_application._local_lock_filesystem',
                       side_effect=ValueError('provider auth lease requires a known local filesystem')):
                with self.assertRaisesRegex(ValueError,'known local filesystem'):
                    with provider_auth_lease(root/'out',seed,lease_dir=target):pass

    def test_authentication_status_pauses_without_retry_or_secret_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=root/'client';client.write_bytes(b'test')
            auth=root/'auth';auth.write_text('{}')
            cfg=root/'config';cfg.write_text('{}')
            args=SimpleNamespace(output=str(root/'out'),model_config=str(cfg),auth=str(auth),
                dataset=str(root),model='gpt-6-astra',reasoning='medium',idle_timeout=900,
                check_timeout=900,inactivity_seconds=7200)
            sessions=[]
            class C:
                def __init__(self,output,dataset,ledger,stage,config):
                    self.ledger=ledger;self.scope={};self.resources=object()
                    self.executor=Mock();self.planner=Mock()
                def handle(self,*args):pass
                def close(self):pass
            class S:
                def __init__(self,**kwargs):
                    sessions.append(self);self.thread_id='thread';self.turn_id='turn';self._usage=None
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def run(self,*args,**kwargs):
                    raise ProviderHTTPError({'kind':'http_status','http_status':401})
            settings={'codex_bin':str(client),
                'codex_sha256':hashlib.sha256(client.read_bytes()).hexdigest(),
                'role_models':{'File Planner':'gpt-6-astra'}}
            with patch('regen.tools.generic_serial_application.model_settings',return_value=settings), \
                 patch('regen.tools.generic_serial_application.Controller',C), \
                 patch('regen.tools.generic_serial_application.CodexWorkspaceSession',S), \
                 patch('regen.tools.generic_serial_application.provider_overload_backoff') as backoff:
                report=run(args)
            self.assertFalse(report['passed'])
            self.assertEqual(report['stop_reason'],'provider_authentication_required')
            self.assertEqual(len(sessions),1)
            backoff.assert_not_called()
            ledger=Ledger(root/'out','out')
            pauses=[r for r in ledger.rows if r['kind']=='provider_recovery_paused']
            self.assertEqual(len(pauses),1)
            self.assertEqual(pauses[0]['data']['failure'],
                {'kind':'http_status','http_status':401})
