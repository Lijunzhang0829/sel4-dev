"""The deadline is theory-local effective time, not wall or global progress."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import durable
from regen.infrastructure.incremental_log import enable
from regen.tools import proof_idle_watch as watch
from regen.tools import theory_effective_clock as clock


class TheoryEffectiveClockTest(unittest.TestCase):
    def setUp(self):
        self.state = {'clock_mode': clock.MODE, 'progress_scope': 'new_contract',
                      'cursors': {}, 'seen': {}, 'last_progress_at': 0}
        self.data = clock.clock_state(self.state)
        self.root = '/run/test'
        self.observed = {self.root: {'running': True}}

    def event(self, at, event, theory='A', **fields):
        if event.startswith('model_work_'):
            fields.setdefault('model_lifecycle_policy', 2)
        row = {'at': at, 'event': event, 'theory': theory, 'run_dir': self.root, **fields}
        self.data['events'].append(row)
        return row

    def start(self, at=0, theory='A', generation='g'):
        return self.event(at, 'theory_generation_started', theory,
                          generation_id=generation, clock_schema=clock.MODE,
                          model_lifecycle_policy=2)

    def stop(self, at, theory='A', generation='g'):
        return self.event(at, 'theory_generation_stopped', theory, generation_id=generation)

    def phase(self, start, end, theory='A', identity='p', category='runtime_setup'):
        self.event(start, 'generation_exclusion_started', theory, phase_id=identity, category=category)
        self.event(end, 'generation_exclusion_finished', theory, phase_id=identity, category=category)

    def accepted(self, at, theory='A', ids=('contract:c',)):
        self.data['progress'].append({'at': at, 'theory': theory, 'run_dir': self.root, 'proof_ids': list(ids)})

    def evaluate(self, now, timeout=3600):
        return clock.evaluate(self.state, self.observed, now, timeout)

    def test_independent_theories_and_exact_boundary(self):
        self.start(theory='A'); self.start(theory='B')
        self.phase(500, 1500, theory='A')
        self.accepted(3500, theory='B')
        rows = self.evaluate(4599)
        self.assertEqual(rows['expired_theories'], [])
        self.assertEqual(rows['theories']['A']['idle_effective_seconds'], 3599)
        rows = self.evaluate(4600)
        self.assertEqual(rows['expired_theories'], ['A'])
        self.assertEqual(rows['theories']['A']['first_crossing_at'], 4600)
        self.assertEqual(rows['theories']['B']['idle_effective_seconds'], 1100)

    def test_v3_callback_scoped_overlap_is_accepted_without_leaking_preparation(self):
        self.start()['model_lifecycle_policy']=3
        self.phase(0,100)
        for at,event in ((10,'model_work_observed'),(20,'model_work_observation_finished'),
                         (30,'model_work_observed'),(40,'model_work_observation_finished')):
            self.event(at,event,model_lifecycle_policy=3)
        self.assertEqual(self.evaluate(100)['theories']['A']['effective_seconds'],20)

    def test_overlapping_preparations_union_not_double_subtract(self):
        self.start()
        self.phase(100, 300, identity='a')
        self.phase(200, 400, identity='b', category='heap_preparation')
        self.assertEqual(self.evaluate(500)['theories']['A']['effective_seconds'], 200)

    def test_retry_and_observer_restart_preserve_balance_not_downtime(self):
        self.start(); self.stop(1000)
        self.start(10000, generation='retry')
        self.assertEqual(self.evaluate(11000)['theories']['A']['idle_effective_seconds'], 2000)
        self.state = json.loads(json.dumps(self.state)); self.data = clock.clock_state(self.state)
        self.assertEqual(self.evaluate(12600)['expired_theories'], ['A'])
        self.assertEqual(self.data['snapshot']['first_crossing_at'], 12600)

    def test_closed_nonrunning_service_does_not_accrue(self):
        self.start(); self.stop(100)
        self.observed[self.root]['running'] = False
        self.assertEqual(self.evaluate(90000)['theories']['A']['effective_seconds'], 100)

    def test_lost_stop_refuses_to_count_unknown_downtime(self):
        self.start(); self.observed[self.root]['running'] = False
        with self.assertRaisesRegex(clock.ClockEvidenceError, 'without generation-stop'):
            self.evaluate(90000)

    def test_late_success_does_not_erase_between_poll_deadline(self):
        self.start()
        self.assertEqual(self.evaluate(3590)['expired_theories'], [])
        self.accepted(3605)
        row = self.evaluate(3610)
        self.assertEqual(row['expired_theories'], ['A'])
        self.assertEqual(row['first_crossing_at'], 3600)
        self.assertEqual(row['theories']['A']['idle_effective_seconds'], 5)

    def test_contract_at_exact_deadline_wins_tie_and_batch_is_one_reset(self):
        self.start(); self.accepted(3600, ids=('contract:a', 'contract:b'))
        row = self.evaluate(3601)
        self.assertEqual(row['expired_theories'], [])
        self.assertEqual(row['theories']['A']['new_contract_count'], 2)
        self.assertEqual(row['theories']['A']['idle_effective_seconds'], 1)

    def test_candidate_queue_and_check_timeout_remain_counted(self):
        self.start()
        self.event(100, 'native_queued', task_id='c')
        self.event(200, 'native_slot_acquired', task_id='c')
        self.event(210, 'isabelle_batch_wait_started', task_id='c', waiting_for='1.response', worker='w')
        self.event(300, 'native_slot_released', task_id='c')
        self.assertEqual(self.evaluate(400)['theories']['A']['effective_seconds'], 400)

    def test_only_matching_prep_queue_deducted_once_and_not_admission_gap(self):
        self.start()
        self.event(100, 'native_queued', task_id='p')
        self.event(200, 'native_slot_acquired', task_id='p')
        self.event(220, 'isabelle_started', task_id='p', label='Declaration_Check_x')
        self.event(250, 'isabelle_finished', task_id='p', label='Declaration_Check_x')
        self.event(260, 'isabelle_batch_wait_started', task_id='p', waiting_for='ready', worker='w')
        self.event(300, 'isabelle_batch_wait_finished', task_id='p', waiting_for='ready', worker='w')
        self.event(310, 'native_slot_released', task_id='p')
        self.assertEqual(self.evaluate(400)['theories']['A']['effective_seconds'], 230)

    def test_unclassified_queue_defers_decision_without_refunding_work(self):
        self.start(); self.event(3500, 'native_queued', task_id='p')
        row = self.evaluate(3601)
        self.assertEqual(row['theories']['A']['idle_effective_seconds'], 3601)
        self.assertEqual(row['expired_theories'], [])
        self.event(3610, 'native_slot_acquired', task_id='p')
        self.event(3620, 'native_slot_released', task_id='p')
        self.assertEqual(self.evaluate(3621)['expired_theories'], ['A'])

    def test_queue_started_after_crossing_cannot_hide_expiry(self):
        self.start(); self.event(3700, 'native_queued', task_id='p')
        row = self.evaluate(3800)
        self.assertEqual(row['expired_theories'], ['A'])
        self.assertFalse(row['theories']['A']['deadline_decision_deferred'])

    def test_failed_ready_wait_closes_on_task_stop(self):
        self.start()
        self.event(100, 'native_queued', task_id='p')
        self.event(100, 'native_slot_acquired', task_id='p')
        self.event(120, 'isabelle_batch_wait_started', task_id='p', waiting_for='ready', worker='w')
        self.event(300, 'check_task_stopped', task_id='p')
        self.assertEqual(self.evaluate(400)['theories']['A']['effective_seconds'], 220)

    def test_hot_declaration_excluded_but_symbol_observation_counted(self):
        self.start()
        self.event(100, 'isabelle_observation_started', label='Declaration_Check_x')
        self.event(200, 'isabelle_observation_failed', label='Declaration_Check_x')
        self.event(210, 'isabelle_observation_started', label='Symbol_Query')
        self.event(300, 'isabelle_observation_finished', label='Symbol_Query')
        self.assertEqual(self.evaluate(400)['theories']['A']['effective_seconds'], 300)

    def test_observed_model_work_overrides_preparation_and_stops_at_terminal(self):
        self.start(); self.phase(100, 300)
        self.event(150, 'model_work_observed')
        self.event(200, 'model_work_observation_finished')
        self.assertEqual(self.evaluate(400)['theories']['A']['effective_seconds'], 250)

    def test_explicit_phase_does_not_classify_an_ordinary_lease_queue(self):
        self.start()
        self.event(100, 'native_queued', task_id='p')
        self.event(200, 'native_slot_acquired', task_id='p')
        self.phase(220, 230, category='provider_stream_retry')
        self.event(300, 'native_slot_released', task_id='p')
        self.assertEqual(self.evaluate(400)['theories']['A']['effective_seconds'], 390)

    def test_legacy_logs_and_backward_observer_clock_fail_closed(self):
        self.event(10, 'isabelle_started', label='Symbol_Query')
        with self.assertRaisesRegex(clock.ClockEvidenceError, 'legacy'):
            self.evaluate(100)
        self.data['events'].clear(); self.start()
        self.evaluate(100)
        with self.assertRaisesRegex(clock.ClockEvidenceError, 'backwards'):
            self.evaluate(99)

    def test_continuation_accumulates_same_theory(self):
        self.start(); self.stop(2000)
        self.observed[self.root]['running'] = False
        self.root = '/run/continuation'
        self.observed[self.root] = {'running': True}
        self.start(10000, generation='next')
        row = self.evaluate(11600)
        self.assertEqual(row['expired_theories'], ['A'])
        self.assertEqual(row['first_crossing_at'], 11600)

    def test_effective_policy_rejects_changed_body_scope_or_different_budget(self):
        config = {'clock_mode': clock.MODE, 'progress_scope': 'new_contract',
                  'timeout_seconds': 3600, 'poll_seconds': 20}
        watch.validate_policy(config)
        for fields in ({'progress_scope': 'contract'}, {'timeout_seconds': 7200}):
            with self.assertRaisesRegex(ValueError, 'requires new_contract and 3600'):
                watch.validate_policy({**config, **fields})

    def test_missing_schema_requires_receipt_bound_finite_bootstrap_grace(self):
        with self.assertRaisesRegex(clock.ClockEvidenceError, 'unavailable'):
            self.evaluate(100000)
        self.data['launches'] = {self.root: 100000}
        self.assertEqual(self.evaluate(100010)['awaiting_schema_bootstrap_grace'], [self.root])
        with self.assertRaisesRegex(clock.ClockEvidenceError, 'unavailable'):
            self.evaluate(100061)

    def test_dropped_historical_service_observation_refuses_budget_reset(self):
        self.start(); self.stop(100)
        self.observed.clear()
        with self.assertRaisesRegex(clock.ClockEvidenceError, 'historical service'):
            self.evaluate(1000)

    def test_existing_audited_preparation_categories_are_supported(self):
        self.start()
        for index, category in enumerate(('statement_preflight', 'prefix_preparation', 'provider_build', 'seal')):
            self.phase(index*100, index*100+10, identity=category, category=category)
        self.assertEqual(self.evaluate(500)['theories']['A']['effective_seconds'], 460)

    def test_incremental_tail_partial_line_restart_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root/'service-runner.log'
            value = {'event': 'theory_generation_started', 'theory': 'A', 'generation_id': 'g',
                     'clock_schema': clock.MODE, 'time': datetime.fromtimestamp(100, timezone.utc).isoformat()}
            line = '[regen-event] '+json.dumps(value)+'\n'
            path.write_text(line+'[regen-event] ')
            clock.ingest([root], self.state)
            self.assertEqual(len(self.data['events']), 1)
            offset = self.data['logs'][str(path)]['offset']
            self.assertEqual(offset, len(line))
            clock.ingest([root], self.state)
            self.assertEqual(len(self.data['events']), 1)
            path.write_text('X'+path.read_text()[1:])
            with self.assertRaisesRegex(clock.ClockEvidenceError, 'boundary changed'):
                clock.ingest([root], self.state)

    def test_deleted_observed_log_is_not_silently_zero_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root/'service-runner.log'
            path.write_text('')
            clock.ingest([root], self.state)
            path.unlink()
            with self.assertRaisesRegex(clock.ClockEvidenceError, 'disappeared'):
                clock.ingest([root], self.state)

    def test_only_first_contract_id_acceptance_resets_not_body_helpers_or_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); enable(root)
            def commit(n, proofs, at, event='batch'):
                path = root/'theories/A/acceptance/commits'/f'{n:06d}.json'
                durable(path, {'event': event, 'commit_id': str(n), 'sequence': n,
                               'state': {'proofs': proofs}}, immutable=True)
                os.utime(path, (at, at))
            commit(1, {'contract:inherited': 'by simp'}, 100, 'initial')
            commit(2, {'contract:inherited': 'by auto', 'helper:h': 'by simp'}, 200)
            commit(3, {'contract:new': 'by simp'}, 300)
            commit(4, {}, 400, 'revoke')
            commit(5, {'contract:new': 'by auto'}, 500)
            watch.scan([root], self.state)
            self.assertEqual(self.state['last_progress_at'], 300)
            self.assertEqual(len(self.data['progress']), 1)
            self.assertEqual(self.data['progress'][0]['proof_ids'], ['contract:new'])
            watch.scan([root], self.state)
            self.assertEqual(len(self.data['progress']), 1)

    def test_moving_inherited_uid_to_another_theory_is_not_new(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); enable(root)
            for theory, event, at in [('A', 'initial', 100), ('B', 'batch', 200)]:
                path = root/'theories'/theory/'acceptance/commits/000001.json'
                durable(path, {'event': event, 'commit_id': theory, 'sequence': 1,
                               'state': {'proofs': {'contract:uid': 'by simp'}}}, immutable=True)
                os.utime(path, (at, at))
            watch.scan([root], self.state)
            self.assertEqual(self.data['progress'], [])


class EffectiveWatchIntegrationTest(unittest.TestCase):
    def test_failed_diagnostic_and_marker_writes_still_stop_verified_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); run = root/'run'; run.mkdir()
            (run/'service-launch.json').write_text('{"created_at":"1970-01-01T00:00:00+00:00"}')
            cfg = {'run_dirs': [str(run)], 'timeout_seconds': 3600, 'poll_seconds': 1,
                   'clock_mode': clock.MODE, 'progress_scope': 'new_contract', 'handoff_unit': 'dummy',
                   **{key: str(root/(key+'.json')) for key in
                      ('pause_marker', 'checkpoint_path', 'state_path', 'status_path')}}
            with patch.object(watch, 'scan', side_effect=clock.ClockEvidenceError('lost evidence')), \
                 patch.object(watch, 'report', side_effect=OSError('disk full')), \
                 patch.object(watch, 'durable', side_effect=OSError('disk full')), \
                 patch.object(watch, 'services', return_value={str(run): {'running': True}}), \
                 patch.object(watch.service, 'stop') as stop:
                with self.assertRaisesRegex(OSError, 'disk full'):
                    watch.run(cfg)
            stop.assert_called_once_with(run)
            self.assertFalse(Path(cfg['checkpoint_path']).exists())

    def exercise(self, legacy=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); run = root/'run'; enable(run)
            durable(run/'run.json', {'run': 'test'})
            durable(run/'service-launch.json', {'created_at': '1970-01-01T00:00:00+00:00'})
            log = run/'service-runner.log'
            event = {'event': 'isabelle_started', 'label': 'Symbol_Query'} if legacy else {
                'event': 'theory_generation_started', 'clock_schema': clock.MODE, 'generation_id': 'g'}
            event.update(theory='A', time='1970-01-01T00:00:00+00:00')
            log.write_text('[regen-event] '+json.dumps(event)+'\n')
            cfg = {'run_dirs': [str(run)], 'timeout_seconds': 3600, 'poll_seconds': 1,
                   'clock_mode': clock.MODE, 'progress_scope': 'new_contract', 'handoff_unit': 'dummy',
                   **{key: str(root/(key+'.json')) for key in
                      ('pause_marker', 'checkpoint_path', 'state_path', 'status_path')}}
            active = {str(run): {'running': True, 'status': 'running'}}
            inactive = {str(run): {'running': False, 'status': 'stopped'}}

            def stopped(_root):
                with log.open('a') as stream:
                    stream.write('[regen-event] '+json.dumps({
                        'event': 'theory_generation_stopped', 'generation_id': 'g', 'theory': 'A',
                        'time': '1970-01-01T01:00:01+00:00'})+'\n')

            observations = [active, active, inactive] if legacy else [active, active, active, inactive]
            with patch.object(watch, 'services', side_effect=observations), \
                 patch.object(watch.service, 'stop', side_effect=stopped) as stop, \
                 patch.object(watch.service, '_show', return_value={'MainPID': '0'}), \
                 patch.object(watch.time, 'time', return_value=3601):
                watch.run(cfg)
            stop.assert_called_once_with(run)
            saved = json.loads(Path(cfg['checkpoint_path']).read_text())
            self.assertEqual(saved['status'], 'paused')
            self.assertFalse(saved['pause_marker']['automatic_resume_allowed'])
            return saved

    def test_effective_deadline_uses_existing_stop_checkpoint_protocol(self):
        saved = self.exercise()
        self.assertEqual(saved['reason'], 'theory_no_new_contract_effective_timeout')
        self.assertEqual(saved['effective_clock']['snapshot']['first_crossing_at'], 3600)

    def test_unknown_clock_evidence_stops_services_instead_of_exiting_unmonitored(self):
        saved = self.exercise(legacy=True)
        self.assertEqual(saved['reason'], 'effective_clock_evidence_error')
        self.assertIn('reconciliation_required', saved['effective_clock'])


if __name__ == '__main__':
    unittest.main()
