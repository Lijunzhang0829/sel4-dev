"""Adapter-level checks for the generic runner's trusted effective clock."""
from contextlib import redirect_stdout
from contextvars import copy_context
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from regen.infrastructure.codex_workspace_session import ProviderOverloaded
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.infrastructure.progress_events import emit_event, generation_exclusion, observe_events
from regen.infrastructure.theory_activity import POLICY, TheoryActivityStop
from regen.planning.generic_codex_agent import Controller
from regen.tools.generic_serial_application import run


class GenericEffectiveActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [0.]
        self.ledger = Ledger(self.root / 'clock', 'test', clock=lambda: self.now[0])
        self.stage = TheoryActivityStop(self.ledger, 100, monotonic=lambda: self.now[0])
        self.addCleanup(self.stage.close)
        self.stage.register({'A': ['a1', 'a2'], 'B': ['b1']})
        self.stage.select('A')

    def controller(self):
        c = Controller.__new__(Controller)
        c.root = self.root
        c.stage = self.stage
        c.ledger = self.ledger
        c.current = {'epoch': 1, 'allocation': {'A': ['a1', 'a2'], 'B': ['b1']},
                     'run': str(self.root / 'proof'), 'plan_id': 'p'}
        proof = Path(c.current['run'])
        proof.mkdir(exist_ok=True)
        (proof / 'execution-input.json').write_text(json.dumps({'files': [
            {'theory': 'A'}, {'theory': 'B'}]}))
        c.config = {'run_id': 'test', 'model': 'gpt-6-astra', 'effort': 'medium'}
        c.contracts = [{'contract_id': uid} for uid in ('a1', 'a2', 'b1')]
        c.scope = {'target_ids': [], 'theory_ids': [], 'phase': 'analysis'}
        c.planner = SimpleNamespace(view=SimpleNamespace(to_original={}))
        c.draft = {'epoch': 1}
        c.controllers = {}
        c.observe_updates = Mock()
        return c

    def idle(self, theory='A'):
        return self.stage.snapshot()['theories'][theory]['idle_seconds']

    def test_formal_launch_rejects_wrong_fixed_contract_count_before_provider_setup(self):
        dataset=self.root/'inventory';dataset.mkdir()
        (dataset/'repository-planning-input.json').write_text(json.dumps({
            'contracts':[{'contract_id':'only-one'}]}))
        args=SimpleNamespace(output=str(self.root/'bad-run'),dataset=str(dataset),
            expected_contracts=287)
        with self.assertRaisesRegex(ValueError,'expected 287, found 1'):
            run(args)

    def test_scope_phase_cannot_exclude_and_shared_scope_cannot_deselect(self):
        c = self.controller()
        self.now[0] = 10
        c.handle('set_work_scope', {'target_ids': ['a1'], 'theory_ids': ['A'], 'phase': 'seal'}, {})
        self.now[0] = 20
        self.assertEqual(self.idle(), 20)
        c.handle('set_work_scope', {'target_ids': [], 'theory_ids': [], 'phase': 'planning'}, {})
        self.now[0] = 30
        self.assertEqual(self.idle(), 30)
        c.handle('set_work_scope', {'target_ids': [], 'theory_ids': ['A', 'B'], 'phase': 'analysis'}, {})
        self.now[0] = 40
        self.assertEqual(self.idle(), 40)
        c.handle('set_work_scope', {'target_ids': ['b1'], 'theory_ids': ['B'], 'phase': 'proof'}, {})
        self.now[0] = 50
        self.assertEqual(self.idle(), 40)
        self.assertEqual(self.idle('B'), 10)

    def test_accepted_plan_selects_initial_pending_theory(self):
        c = self.controller()
        self.stage.select(None)
        c.register_activity_plan()
        self.assertEqual(self.stage.active_theory, 'A')
        self.now[0] = 10
        self.assertEqual(self.idle(), 10)

    def test_scope_cannot_select_sealed_theory_to_stop_pending_clock(self):
        c = self.controller()
        self.stage.register(c.current['allocation'], baseline_green=['a1', 'a2'])
        self.stage.mark_sealed('A')
        self.stage.select('B')
        c.handle('set_work_scope', {'target_ids': ['a1'], 'theory_ids': ['A'], 'phase': 'analysis'}, {})
        self.now[0] = 10
        self.assertEqual(self.stage.active_theory, 'B')
        self.assertEqual(self.idle('B'), 10)

    def test_register_baseline_filters_helpers_other_epoch_and_open_contracts(self):
        c = self.controller()
        nodes = [
            {'uid': 'a1', 'kind': 'contract', 'status': 'green'},
            {'uid': 'a2', 'kind': 'contract', 'status': 'open'},
            {'uid': 'helper:A:h', 'kind': 'helper', 'status': 'green'},
        ]
        self.ledger.append('proof_commit', epoch=1, event='batch', theory='A', nodes=nodes)
        self.ledger.append('proof_commit', epoch=0, event='batch', theory='B',
                           nodes=[{'uid': 'b1', 'kind': 'contract', 'status': 'green'}])
        self.now[0] = 20
        c.register_activity_plan()
        self.assertEqual(self.idle(), 20)
        self.assertEqual(self.stage.snapshot()['theories']['A']['green_contract_ids'], ['a1'])
        self.assertEqual(self.stage.snapshot()['theories']['B']['green_contract_ids'], [])
        self.assertFalse(any(r['kind'] == 'theory_contract_first_green' for r in self.ledger.rows))

    def test_authoritative_commit_counts_only_first_green_and_mark_seal(self):
        c = self.controller()
        record = {'state': {'graph': {'theory': 'A'}, 'status': 'open'},
                  'commit_id': 'commit-1', 'event': 'batch', 'evidence': {}}
        rows = [{'uid': 'a1', 'kind': 'contract', 'theory': 'A', 'status': 'green'},
                {'uid': 'helper:A:h', 'kind': 'helper', 'theory': 'A', 'status': 'green'}]
        self.now[0] = 20
        with patch('regen.planning.generic_codex_agent.node_rows', return_value=rows):
            c.committed(record)
            self.now[0] = 30
            c.committed({**record, 'commit_id': 'commit-2'})
        self.assertEqual(self.idle(), 10)
        sealed = [dict(row, status='sealed') for row in rows]
        sealed.append({'uid': 'a2', 'kind': 'contract', 'theory': 'A', 'status': 'sealed'})
        record = {**record, 'commit_id': 'seal-3', 'event': 'seal',
                  'state': {'graph': {'theory': 'A'}, 'status': 'sealed'}}
        with patch('regen.planning.generic_codex_agent.node_rows', return_value=sealed):
            c.committed(record)
        self.now[0] = 35
        self.stage.check()
        self.assertTrue(self.stage.snapshot()['theories']['A']['sealed'])
        self.assertEqual(self.stage.active_theory, 'B')
        self.assertEqual(self.idle('B'), 5)

    def test_status_handles_no_wall_deadline(self):
        c = self.controller()
        with self.stage.excluded('heap_preparation'):
            result = c.status()
        self.assertIsNone(result['deadline_unix'])
        self.assertIsNone(result['remaining_seconds'])
        self.assertEqual(result['generation_clock']['policy'], POLICY)
        self.assertFalse(any(r['kind'] == 'activity_started' for r in self.ledger.rows))

    def test_native_context_observer_and_overlap_use_actual_phase_events(self):
        bridge = GenerationActivityBridge(self.stage)
        with redirect_stdout(io.StringIO()), observe_events(bridge.handle):
            context = copy_context()
            errors = []
            def worker():
                try:
                    with generation_exclusion('heap_preparation'):
                        self.now[0] += 30
                        emit_event('model_work_observed')
                        self.now[0] += 20
                        emit_event('model_work_observation_finished')
                        self.now[0] += 40
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=lambda: context.run(worker))
            thread.start(); thread.join()
            self.assertFalse(errors)
            self.assertEqual(self.idle(), 20)
            emit_event('theory_generation_stopped')
            self.now[0] += 1000
            emit_event('theory_generation_started')
            self.now[0] += 10
            self.assertEqual(self.idle(), 30)
        bridge.close()

    def test_unclosed_native_phase_causes_pause(self):
        bridge = GenerationActivityBridge(self.stage)
        bridge.handle('generation_exclusion_started', {'category': 'heap_preparation', 'phase_id': 'x'})
        self.now[0] = 1000
        bridge.close()
        self.assertTrue(self.stage.event.is_set())
        self.assertEqual(self.stage.reason, 'unclosed_harness_phase')
        self.assertEqual(self.idle(), 0)

    def test_continuation_rejects_legacy_clock_and_retry_policy_drift(self):
        from regen.infrastructure.generic_continuation import prepare
        source = self.root / 'legacy'; source.mkdir()
        config = {key: 1 for key in (
            'model', 'effort', 'threads', 'transport_idle_timeout',
            'preflight_seconds', 'check_timeout', 'candidate_timeout',
            'provider_retry_limit', 'native_slots', 'resident_limit',
            'native_cpus', 'native_memory_mb', 'native_pids_limit',
            'provider_build_timeout')}
        config.update(shared_host_lock_dir=str(self.root/'shared-native'),
                      shared_host_slots=1,shared_host_cleanup_timeout=30)
        config['inactivity_policy'] = POLICY
        for key, old in [('inactivity_policy', 'old-wall-clock'), ('provider_retry_limit', 3),
                         ('shared_host_lock_dir', str(self.root/'other-native')),
                         ('shared_host_slots', 2), ('shared_host_cleanup_timeout', 60)]:
            launch = {'config': {**config, key: old}, 'dataset': str(self.root),
                      'inactivity_seconds': 100}
            (source / 'launch.json').write_text(json.dumps(launch))
            with self.assertRaisesRegex(ValueError, 'continuation policy differs: ' + key):
                prepare(self.root / 'continued', source, self.root, config, 100)

    def test_audited_crefine_pool_migration_only_changes_admission(self):
        from regen.infrastructure import generic_continuation as migration
        from regen.execution.proof_commits import digest
        old_files={'tools/generic_serial_application.py':'old-tool',
            'infrastructure/generic_continuation.py':'old-continuation',
            'execution/check_executor.py':'unchanged'}
        new_files={**old_files,'tools/generic_serial_application.py':'new-tool',
            'infrastructure/generic_continuation.py':'new-continuation'}
        old_dir='/data/zljj/vero-a-unified/experiment-stability-20261015-005/shared-native-slots'
        new_dir=('/data/zljj/vero-a-unified/crefine-preparation-20261017-001/'
            'runtime/shared-native-slots-2-20261019')
        old={'application_sha256':digest(old_files),'budget_mode':'effective-time',
            'native_slots':1,'shared_host_lock_dir':old_dir,'shared_host_slots':1,
            'shared_host_cleanup_timeout':30}
        new={**old,'application_sha256':digest(new_files),
            'shared_host_lock_dir':new_dir,'shared_host_slots':2}
        history=SimpleNamespace(rows=[{'kind':'experiment_paused','event_id':'last'}])
        source=self.root/'codex-crefine-sol-medium-effective48h-004'
        with patch.object(migration,'_CREFINE_POOL_SOURCE_APPLICATION',digest(old_files)), \
             patch.object(migration,'_implementation_files',side_effect=[old_files,new_files]):
            event=migration._audited_crefine_pool_migration(source,old,new,history,
                self.root/'frozen-application')
        self.assertEqual(event['old_resources']['shared_host_slots'],1)
        self.assertEqual(event['new_resources']['shared_host_slots'],2)
        self.assertEqual(len(event['changed_files']),2)
        with patch.object(migration,'_CREFINE_POOL_SOURCE_APPLICATION',digest(old_files)):
            with self.assertRaisesRegex(ValueError,'must only move Codex'):
                migration._audited_crefine_pool_migration(source,old,
                    {**new,'native_slots':2},history,self.root/'frozen-application')

    def test_continuation_requires_clean_clock_close_before_copying(self):
        from regen.infrastructure.generic_continuation import prepare
        source=self.root/'unclean';source.mkdir()
        config={key:1 for key in ('model','effort','threads','transport_idle_timeout',
            'preflight_seconds','check_timeout','candidate_timeout')}
        config['inactivity_policy']=POLICY
        (source/'launch.json').write_text(json.dumps({'config':config,
            'dataset':str(self.root),'inactivity_seconds':100}))
        (source/'result.json').write_text(json.dumps({'passed':False}))
        old=Ledger(source,'old')
        old.append('theory_activity_state',boundary='checkpoint',state={})
        old.append('experiment_finished',passed=False)
        with self.assertRaisesRegex(ValueError,'unobserved shutdown tail'):
            prepare(self.root/'destination',source,self.root,config,100)
        self.assertFalse((self.root/'destination').exists())

    def _runner(self, *, exhaust):
        client = self.root / 'client'; client.write_bytes(b'test-client')
        auth = self.root / 'fake-auth'; auth.write_text('{}')
        config = self.root / 'model-config'; config.write_text('{}')
        args = SimpleNamespace(output=str(self.root / 'out'), model_config=str(config),
            auth=str(auth), dataset=str(self.root), model='gpt-6-astra', reasoning='medium',
            idle_timeout=900, check_timeout=900, inactivity_seconds=100,
            provider_retry_limit=1, provider_recovery_window_seconds=60)
        controllers = []; sessions = []; stages = []; now = self.now
        class C:
            def __init__(self, output, dataset, ledger, stage, cfg):
                self.ledger = ledger; self.stage = stage; self.scope = {}
                self.resources = object(); self.executor = Mock(); self.planner = Mock()
                stage.register({'A': ['a1', 'a2']})
                if stage.active_theory is None:
                    stage.select('A')
                now[0] += 400  # Restoring controllers/journals must be excluded too.
                controllers.append(self)
            def handle(self, *a):
                pass
            def close(self):
                pass
        class S:
            def __init__(self, **kwargs):
                self.index = len(sessions); sessions.append(self)
                self.thread_id = str(self.index + 1) * 8 + '-1111-1111-1111-111111111111'
                self.turn_id = 'turn'; self._usage = {'total': {'totalTokens': 10}}
                self.home = Path(kwargs['codex_home'])
                assert {p.name for p in self.home.iterdir()} == {'auth.json'}
                (self.home / 'sessions').mkdir()
                now[0] += 500  # Auth/session bootstrap is explicit recovery.
            def __enter__(self):
                return self
            def __exit__(self, *args):
                emit_event('theory_generation_stopped')
            def run(self, prompt, handler, **kwargs):
                emit_event('theory_generation_started')
                if self.index == 0:
                    now[0] += 15
                    stages[0].observe('a1', {'status': 'green'}, kind='validation', theory='A')
                    now[0] += 5
                    with generation_exclusion('heap_preparation'):
                        now[0] += 1000
                    raise ProviderOverloaded('provider capacity')
                now[0] += 20
                if exhaust:
                    raise ProviderOverloaded('provider still busy')
                controllers[-1].ledger.append('integrated', verified=True)
                return {'usage': self._usage}
        def make_stage(ledger, seconds):
            stage = TheoryActivityStop(ledger, seconds, monotonic=lambda: now[0])
            stage.start = Mock()  # Fake time is driven synchronously in this test.
            stages.append(stage)
            return stage
        def backoff(stage, ledger, retry, error, decision=None):
            self.assertEqual(decision['delay_seconds'],30)
            now[0] += 2000
            stage.check()
        with redirect_stdout(io.StringIO()), \
             patch('regen.tools.generic_serial_application.model_settings', return_value={
                 'codex_bin': str(client), 'codex_sha256': hashlib.sha256(client.read_bytes()).hexdigest(),
                 'role_models': {'File Planner': 'gpt-6-astra'}}), \
             patch('regen.tools.generic_serial_application.Controller', C), \
             patch('regen.tools.generic_serial_application.CodexWorkspaceSession', S), \
             patch('regen.tools.generic_serial_application.TheoryActivityStop', make_stage), \
             patch('regen.tools.generic_serial_application.provider_overload_backoff', side_effect=backoff):
            result = run(args)
        self.assertEqual(len(sessions), 2, result)
        self.assertEqual(len(controllers), 2, result)
        self.assertEqual(result['generation_clock']['theories']['A']['idle_seconds'], 25)
        self.assertEqual(result['generation_clock']['theories']['A']['effective_seconds'], 40)
        self.assertEqual(result['generation_clock']['theories']['A']['green_contract_ids'], ['a1'])
        self.assertNotIn('accounting_error', result)
        return result

    def test_real_runner_retry_preserves_budget_and_excludes_confirmed_recovery(self):
        result = self._runner(exhaust=False)
        self.assertTrue(result['passed'], result)
        self.assertIsNone(result['stop_reason'])

    def test_real_runner_retry_exhaustion_pauses_without_budget_renewal(self):
        result = self._runner(exhaust=True)
        self.assertFalse(result['passed'])
        self.assertEqual(result['stop_reason'], 'provider_recovery_window_exhausted')
        self.assertTrue(result['generation_clock']['paused'])


if __name__ == '__main__':
    unittest.main()
