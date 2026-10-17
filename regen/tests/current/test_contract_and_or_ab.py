"""Evidence-chain tests for the freeze/aggregate-only AND/OR A/B layer.

All proof checks here are deterministic Python fixtures.  No provider, native
worker, Isabelle process, service, or container is started.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.execution.repository_execution import journal as repository_journal
from regen.planning.bounded_prefix import run_prefix
from regen.planning.online_policy_composition import (
    ARM_ATTESTATION_NAME,
    ARM_ATTESTATION_SCHEMA,
    EXISTING_SEARCH,
    arm_policy,
)
from regen.planning.theory_document import render
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture, check
from regen.tools import contract_and_or_ab as ab


class ContractAndOrABTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.results = self.root/'results'
        self.results.mkdir()
        self.instances = self.root/'instances'
        self.instances.mkdir()
        self.application_manifest = self.write(
            self.root/'application-manifest.json',
            {'schema': 'test-application-manifest-v1',
             'revision': 1, 'files': {}})
        self.target = 'contract'
        self.helper = 'helper'
        self.theory = 'Generated_Demo'
        package_body, _ = fixture()
        self.package_value = {
            **package_body,
            'package_payload_sha256': digest(package_body),
        }
        self.package_path = self.write(
            self.root/'package.json', self.package_value)
        self.problem_value = {
            'schema_version': 1,
            'method': 'contract-rooted-and-or-v1',
            'root_goal': self.target,
            'goals': [
                {'id': self.helper, 'role': 'helper', 'order': 1,
                 'routes': [{'id': 'helper/direct', 'requires': [],
                             'estimate_seconds': 1}]},
                {'id': self.target, 'role': 'contract', 'order': 2,
                 'routes': [{'id': 'root/bridge',
                             'requires': [self.helper],
                             'estimate_seconds': 2}]},
            ],
        }
        self.problem = self.write(self.root/'problem.json', self.problem_value)
        self.prepared_verify = patch(
            'regen.tools.prepared_checkpoint.verify',
            side_effect=lambda fixture, expected_manifest_sha256:
                json.loads((Path(fixture)/'manifest.json').read_text()))
        self.prepared_verify.start()
        self.addCleanup(self.prepared_verify.stop)
        self.derived_verify = patch(
            'regen.tools.contract_and_or_ab._verify_derived_checkpoint',
            return_value={'synthetic_test_only': True})
        self.derived_verify.start()
        self.addCleanup(self.derived_verify.stop)
        self.planning_verify = patch(
            'regen.tools.contract_and_or_ab.verify_planning_result',
            side_effect=lambda path, **kwargs: json.loads(
                Path(path).read_text()))
        self.planning_verify.start()
        self.addCleanup(self.planning_verify.stop)
        self.attestation_verify = patch(
            'regen.tools.contract_and_or_ab._verify_arm_attestation',
            side_effect=lambda plan, arm, case, problem, run_dir,
            package, journal: {
                'raw_sha256': ab._sha256(
                    Path(run_dir)/ARM_ATTESTATION_NAME),
                'synthetic_test_only': True,
            })
        self.attestation_verify.start()
        self.addCleanup(self.attestation_verify.stop)
        self.cases = []
        for step_sequence, stratum in enumerate(('ordinary', 'long'), 1):
            fixture_dir = self.root/(stratum + '-fixture')
            fixture_dir.mkdir()
            pristine_sha = digest({'pristine': stratum})
            manifest = self.write(fixture_dir/'manifest.json', {
                'source_kind': 'target-pristine-existing-v1',
                'pristine_provenance_sha256': pristine_sha,
                'pristine_semantic_receipt': {
                    'theory': self.theory,
                    'target_contract_id': self.target,
                    'accepted_proofs': 0,
                    'model_calls': 0,
                },
            })
            self.cases.append({
                'case_id': stratum + '-case',
                'stratum': stratum,
                'step_sequence': step_sequence,
                'theory': self.theory,
                'target_contract_id': self.target,
                'prepared_checkpoint': str(fixture_dir),
                'prepared_manifest_sha256': ab._sha256(manifest),
                'pristine_provenance_sha256': pristine_sha,
                'package': str(self.package_path),
            })
        self.config = {
            'random_seed': 271828,
            'model': 'gpt-6-astra',
            'reasoning_effort': 'medium',
            'problem_generation': {
                'provider': 'codex',
                'timeout_seconds': 1800,
                'max_attempts': 3,
            },
            'resources': {'native_slots': 1, 'native_threads': 4,
                          'native_memory_mb': 16384},
            'acceptance_policy': 'bounded-audited-prefix-v1',
            'application_manifest': str(self.application_manifest),
            'instances_root': str(self.instances),
            'limits': {
                'effective_proving_seconds': 3600,
                'wall_seconds': 10800,
            },
            'selection': {'ordinary': 1, 'long': 1},
            'order_design': 'paired-randomized',
            'pairs_per_case': 1,
            'cases': self.cases,
        }
        self.config_path = self.write(self.root/'config.json', self.config)
        self.plan_path = self.root/'plan.json'
        self.plan = ab.freeze_plan(self.config_path, self.plan_path)

    @staticmethod
    def write(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path.resolve()

    @staticmethod
    def reference(path):
        return {'path': str(Path(path).resolve()),
                'sha256': ab._sha256(path)}

    def transcript(self, run_dir, arm, *, missing=False, thread=None):
        path = run_dir/'provider'/'session-transcript.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{'time_unix': 1, 'type': 'notice',
                 'arm_instance_id': arm['arm_instance_id']}]
        if not missing:
            thread = thread or 'thread-' + arm['arm_instance_id']
            for when, total, input_tokens, cached, output, reasoning in (
                    (2, 100, 90, 60, 10, 4),
                    (3, 150, 135, 100, 15, 6)):
                rows.append({
                    'time_unix': when,
                    'message': {'parsed': {
                        'method': 'thread/tokenUsage/updated',
                        'params': {
                            'threadId': thread,
                            'tokenUsage': {'total': {
                                'totalTokens': total,
                                'inputTokens': input_tokens,
                                'cachedInputTokens': cached,
                                'outputTokens': output,
                                'reasoningOutputTokens': reasoning,
                            }},
                        },
                    }},
                })
        path.write_text(
            ''.join(json.dumps(row) + '\n' for row in rows),
            encoding='utf-8')
        return path

    def local_transcript(self, arm):
        path = self.root/('local-' + arm['arm_id'] + '.jsonl')
        rows = []
        for when, total, input_tokens, cached, output, reasoning in (
                (2, 100, 90, 60, 10, 4),
                (3, 180, 160, 120, 20, 8)):
            rows.append({
                'time_unix': when,
                'direction': 'local',
                'message': {
                    'event': 'turn_completed',
                    'thread_id': 'local-' + arm['arm_instance_id'],
                    'turn_id': 'turn-' + str(when),
                    'status': 'completed',
                    'usage': {'total': {
                        'totalTokens': total,
                        'inputTokens': input_tokens,
                        'cachedInputTokens': cached,
                        'cacheWriteInputTokens': 0,
                        'outputTokens': output,
                        'reasoningOutputTokens': reasoning,
                    }},
                },
            })
        path.write_text(
            ''.join(json.dumps(row) + '\n' for row in rows),
            encoding='utf-8')
        return path

    def repository(self, arm):
        run_dir = Path(arm['arm_instance_path'])
        package_body, initial = fixture()
        package = {
            **package_body,
            'package_payload_sha256': digest(package_body),
        }
        packages = run_dir/'packages'
        theory_root = run_dir/'theories'/self.theory
        packages.mkdir(parents=True)
        theory_root.mkdir(parents=True)
        self.write(theory_root/'imports.json', [])
        run = {
            'execution_mode': 'repository-projection-v1',
            'run_id': 'lineage-' + arm['case_id'],
            'session_schedule': {},
            'implementation': {},
            'contract_package_dir': str(packages),
        }
        self.write(run_dir/'run.json', run)
        self.write(run_dir/'execution-input.json', {
            'files': [{'theory': self.theory,
                       'file_slot': package['file_slot']}],
            'package_hashes': {
                package['file_slot']: package['package_payload_sha256']},
        })
        self.write(packages/(package['file_slot'] + '.json'), package)
        authority = repository_journal(run_dir, run, package)
        authority.append('initial', initial, {'check': check()})
        first = json.loads(
            (Path(authority.directory)/'commits'/'000001.json').read_text())
        return run_dir, package, initial, authority, first

    def attest(self, arm, run_dir, package, authority, initial_record):
        label = 'A' if arm['search_policy'] == EXISTING_SEARCH else 'B'
        body = {
            'schema': ARM_ATTESTATION_SCHEMA,
            'plan_id': self.plan['plan_id'],
            'arm_id': arm['arm_id'],
            'arm_instance_id': arm['arm_instance_id'],
            'case_id': arm['case_id'],
            'arm_label': label,
            'search_policy': arm_policy(label)['search_policy'],
            'input_binding_sha256': arm['input_binding_sha256'],
            'application_manifest_sha256':
                arm['application_manifest_sha256'],
            'checkpoint_sha256': arm['checkpoint_sha256'],
            'prepared_manifest_sha256': arm['prepared_manifest_sha256'],
            'pristine_provenance_sha256':
                arm['pristine_provenance_sha256'],
            'problem_sha256': digest(self.problem_value),
            'package_sha256': digest(package),
            'journal_binding_sha256': digest(authority.binding),
            'run_directory': str(run_dir),
            'theory': self.theory,
            'target_contract_id': self.target,
            'initial_journal_commit_id': initial_record['commit_id'],
            'initial_document_state_sha256':
                digest(initial_record['state']),
        }
        value = {**body, 'attestation_id': digest(body)}
        return self.write(run_dir/ARM_ATTESTATION_NAME, value)

    def lifecycle(self, run_dir, arm, authority, *,
                  effective, accepted_effective=None):
        commits = sorted((Path(authority.directory)/'commits').glob('*.json'))
        anchor = commits[-1].stat().st_mtime
        start = anchor - (accepted_effective
                          if accepted_effective is not None
                          else effective)
        if accepted_effective is not None:
            for number, path in enumerate(commits[1:], 1):
                offset = (accepted_effective if path == commits[-1]
                          else min(20 * number, accepted_effective))
                os.utime(path, (start + offset, start + offset))
        stop = start + effective
        terminal = stop + 50
        generation = 'generation-' + arm['arm_instance_id']
        def stamp(value):
            return datetime.fromtimestamp(
                value, timezone.utc).isoformat()
        events = [
            {'event': 'theory_generation_started',
             'theory': self.theory, 'generation_id': generation,
             'clock_schema': 'theory_effective_v1',
             'time': stamp(start)},
            {'event': 'theory_generation_stopped',
             'theory': self.theory, 'generation_id': generation,
             'clock_schema': 'theory_effective_v1',
             'time': stamp(stop)},
            {'event': 'runner_monitor_stopped',
             'theory': self.theory,
             'task_id': 'monitor-' + arm['arm_instance_id'],
             'time': stamp(terminal)},
        ]
        launch = self.write(run_dir/'service-launch.json', {
            'run_dir': str(run_dir),
            'created_at': stamp(start),
            'arm_instance_id': arm['arm_instance_id'],
        })
        log = run_dir/'service-runner.log'
        log.write_text(''.join(
            '[regen-event] ' + json.dumps(row) + '\n'
            for row in events), encoding='utf-8')
        return {
            'service_launch': self.reference(launch),
            'service_log': self.reference(log),
        }

    def arm_result(self, arm, *, target_green=True, status='completed',
                   missing_usage=False, duplicate_thread=None):
        run_dir, package, initial, authority, first = self.repository(arm)
        attestation = self.attest(
            arm, run_dir, package, authority, first)
        evidence_root = (
            run_dir/'theories'/self.theory/'batch-requests'/'000001'
            /'bounded-prefix')
        if target_green:
            source = render(
                package, initial, Checker().imports()).replace(
                    'oops', 'by simp')
            run_prefix(
                package, initial, source, Checker(), evidence_root,
                evidence_origin={
                    'sealable_generation': True,
                    'transcript_prefix': (
                        'synthetic-validator-test-' +
                        arm['arm_instance_id'])},
                commit=lambda new, evidence:
                    authority.append('document', new, evidence))
        elif status == 'proof_failure':
            checker = Checker()
            checker.verdict = 'red'
            source = render(
                package, initial, checker.imports()).replace(
                    'oops', 'by simp')
            run_prefix(
                package, initial, source, checker, evidence_root,
                evidence_origin={
                    'sealable_generation': True,
                    'transcript_prefix': (
                        'synthetic-validator-test-' +
                        arm['arm_instance_id'])},
                commit=lambda new, evidence:
                    authority.append('document', new, evidence))
        transcript = self.transcript(
            run_dir, arm, missing=missing_usage,
            thread=duplicate_thread)
        treatment = arm['search_policy'] == ab.AND_OR_SCHEMA
        planning_reference = None
        if treatment:
            planning_path = (
                run_dir/'treatment-planning'/
                'and-or-planning-result.json')
            planning_value = {
                'status': 'accepted',
                'result_sha256': digest({
                    'synthetic-planning': arm['arm_instance_id']}),
                'problem': self.problem_value,
                'planning_accounting': {
                    'orchestration_total_wall_ms': 0,
                    'native_preflight_wall_ms': 0,
                    'native_preflight_calls': 0,
                    'staging_cleanup_verified': True,
                },
            }
            self.write(planning_path, planning_value)
            planning_reference = self.reference(planning_path)
        effective = (
            self.plan['common']['limits']['effective_proving_seconds']
            if status == 'budget_censored'
            else 120 if treatment else 140)
        accepted = (
            effective - 10 if target_green else None)
        timing = self.lifecycle(
            run_dir, arm, authority, effective=effective,
            accepted_effective=accepted)
        native = [
            self.reference(path)
            for path in sorted(
                (run_dir/'theories'/self.theory).rglob('check.json'))
            if isinstance(json.loads(path.read_text()), dict)
            and (json.loads(path.read_text()).get('bounded_prefix') or {})
                .get('strategy') == ab.ACCEPTANCE_POLICY
        ]
        value = {
            'schema': ab.RESULT_SCHEMA,
            'plan_id': self.plan['plan_id'],
            'arm_id': arm['arm_id'],
            'arm_instance_id': arm['arm_instance_id'],
            'case_id': arm['case_id'],
            'search_policy': arm['search_policy'],
            'input_binding_sha256': arm['input_binding_sha256'],
            'application_manifest_sha256':
                arm['application_manifest_sha256'],
            'checkpoint_sha256': arm['checkpoint_sha256'],
            'prepared_manifest_sha256': arm['prepared_manifest_sha256'],
            'pristine_provenance_sha256':
                arm['pristine_provenance_sha256'],
            'problem_generation_recipe_sha256':
                arm['problem_generation_recipe_sha256'],
            'run_dir': str(run_dir),
            'planning_result': planning_reference,
            'execution_status': status,
            'acceptance_journal': str(Path(authority.directory).resolve()),
            'arm_attestation_sha256': ab._sha256(attestation),
            'session_transcripts': [self.reference(transcript)],
            'timing': timing,
            'native_checks': native,
            'probes': [],
            'proof_failures': (
                [{'scope': 'native', 'reason': 'RED'}]
                if status == 'proof_failure' else []),
            'infrastructure_failures': (
                [{'scope': 'transport', 'reason': 'synthetic'}]
                if status == 'infrastructure_failure' else []),
        }
        path = self.results/(arm['arm_id'] + '.json')
        return self.write(path, value)

    def all_success_results(self):
        for arm in self.plan['arms']:
            self.arm_result(arm)

    def test_freeze_is_deterministic_and_only_varies_search_policy(self):
        second = ab.freeze_plan(self.config_path)
        self.assertEqual(second, self.plan)
        self.assertEqual(
            {case['stratum'] for case in self.plan['selected_cases']},
            {'ordinary', 'long'})
        self.assertEqual(
            set(self.plan['search_policies']),
            {EXISTING_SEARCH, ab.AND_OR_SCHEMA})
        self.assertTrue(all(
            len(arm['arm_instance_id']) == 64
            for arm in self.plan['arms']))
        self.assertFalse(self.plan['execution_authorized'])

    def test_preregistered_direct_seed_selection_matches_manifest(self):
        ordinary = [1345, 1774, 2411, 2440, 2648, 3027, 3394]
        pool = [
            {'case_id': 'ordinary-' + str(step), 'stratum': 'ordinary',
             'step_sequence': step}
            for step in ordinary
        ] + [
            {'case_id': 'long-' + str(step), 'stratum': 'long',
             'step_sequence': step}
            for step in (5001, 5002)
        ]
        selected = ab._selected_cases(
            pool, {'ordinary': 6, 'long': 2}, 20261008)
        self.assertEqual(
            [row['step_sequence'] for row in selected
             if row['stratum'] == 'ordinary'],
            [2411, 1345, 2440, 1774, 3027, 2648])
        self.assertEqual(
            [row['step_sequence'] for row in selected
             if row['stratum'] == 'long'], [5001, 5002])
        self.assertEqual(
            [row['step_sequence'] for row in ab._execution_cases(
                selected, 20261008)],
            [2411, 2648, 5002, 2440, 1345, 1774, 3027, 5001])

    def test_last_cumulative_usage_is_not_snapshot_sum(self):
        arm = self.plan['arms'][0]
        run_dir = Path(arm['arm_instance_path'])
        run_dir.mkdir()
        observed = ab.last_cumulative_usage(
            self.transcript(run_dir, arm))
        self.assertEqual(observed['observations'], 2)
        self.assertEqual(observed['last']['usage']['total_tokens'], 150)
        self.assertEqual(
            observed['last']['usage']['noncached_input_tokens'], 35)

    def test_local_turn_completed_uses_last_cumulative_total(self):
        arm = self.plan['arms'][0]
        observed = ab.last_cumulative_usage(
            self.local_transcript(arm))
        self.assertEqual(observed['observations'], 2)
        self.assertEqual(observed['last']['usage']['total_tokens'], 180)

    def test_aggregate_replays_repository_and_raw_metrics(self):
        self.all_success_results()
        report = ab.aggregate(self.plan_path, self.results)
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['arms']), 4)
        for row in report['arms']:
            self.assertTrue(row['target_journal_green'])
            self.assertEqual(
                row['model_tokens']['totals']['total_tokens'], 150)
            if row['search_policy'] == ab.AND_OR_SCHEMA:
                self.assertEqual(
                    row['root_reachable_helpers']['new_accepted_ids'],
                    [self.helper])
            else:
                self.assertIsNone(row['root_reachable_helpers'])
            self.assertEqual(
                row['new_accepted_auxiliary']['new_accepted_ids'],
                [self.helper])
            self.assertEqual(row['native_checks']['count'], 2)
            self.assertEqual(
                row['timing_evidence']['measurement_status'], 'trusted')
        for pair in report['pairs']:
            self.assertTrue(pair['primary_comparison_eligible'])
            self.assertTrue(pair['full_metric_comparison_eligible'])
            self.assertAlmostEqual(
                pair['treatment_minus_current'][
                    'target_accepted_effective_seconds'],
                -20, places=3)
            self.assertEqual(
                pair['treatment_minus_current']['model_total_tokens'], 0)
            self.assertIsNone(pair['treatment_minus_current'][
                'new_root_reachable_helper_count'])
            self.assertEqual(pair['treatment_minus_current'][
                'new_accepted_auxiliary_count'], 0)

    def test_missing_usage_is_unknown_and_not_full_metric(self):
        for arm in self.plan['arms']:
            self.arm_result(arm, missing_usage=True)
        report = ab.aggregate(self.plan_path, self.results)
        self.assertTrue(all(
            row['model_tokens']['totals']['total_tokens'] is None
            for row in report['arms']))
        self.assertTrue(all(
            not pair['full_metric_comparison_eligible']
            for pair in report['pairs']))

    def test_codex_exec_usage_and_preplanning_wall_are_included(self):
        self.all_success_results()
        arm = self.plan['arms'][0]
        run_dir = Path(arm['arm_instance_path'])
        launch = json.loads((run_dir/'service-launch.json').read_text())
        service_started = datetime.fromisoformat(
            launch['created_at']).timestamp()
        raw = run_dir/'planner'/'file-planner.ndjson'
        raw.parent.mkdir()
        raw.write_text('\n'.join((
            json.dumps({'type': 'thread.started',
                        'thread_id': 'planner-' + arm['arm_instance_id']}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 40, 'cached_input_tokens': 10,
                'output_tokens': 10, 'reasoning_output_tokens': 3}}),
        )) + '\n', encoding='utf-8')
        def stamp(value):
            return datetime.fromtimestamp(
                value, timezone.utc).isoformat()
        self.write(raw.with_suffix('.meta.json'), {
            'provider': 'codex-exec-audited',
            'stage': 'file-planner',
            'state': 'complete',
            'timed_out': False,
            'started_at': stamp(service_started - 30),
            'finished_at': stamp(service_started - 20),
            'raw_path': str(raw),
            'raw_sha256': ab._sha256(raw),
        })
        result_path = self.results/(arm['arm_id'] + '.json')
        result = json.loads(result_path.read_text())
        result['session_transcripts'].append(self.reference(raw))
        result_path.write_text(json.dumps(result), encoding='utf-8')
        report = ab.aggregate(self.plan_path, self.results)
        row = next(item for item in report['arms']
                   if item['arm_id'] == arm['arm_id'])
        self.assertEqual(row['model_tokens']['totals']['total_tokens'], 200)
        self.assertAlmostEqual(
            row['timing_evidence']['planning_wall_seconds'], 10, places=3)
        self.assertAlmostEqual(
            row['timing_evidence']['end_to_end_attributable_wall_seconds'],
            180,
            places=3)
        self.assertAlmostEqual(
            row['timing_evidence']['elapsed_envelope_seconds'], 200,
            places=3)

    def test_theorem_support_is_counted_separately_and_in_total(self):
        problem = {
            'root_goal': 'root',
            'goals': [
                {'id': 'root', 'role': 'contract',
                 'routes': [{'requires': ['mid']}]},
                {'id': 'mid', 'role': 'theorem',
                 'routes': [{'requires': ['leaf']}]},
                {'id': 'leaf', 'role': 'helper',
                 'routes': [{'requires': []}]},
            ],
        }
        observed = ab._root_reachable_helpers(problem, {
            'initial_accepted_ids': [],
            'final_accepted_ids': ['leaf', 'mid', 'root'],
        })
        self.assertEqual(observed['new_accepted_count'], 2)
        self.assertEqual(observed['theorem']['new_accepted_ids'], ['mid'])
        self.assertEqual(
            observed['helper_or_residual']['new_accepted_ids'], ['leaf'])

    def test_hash_valid_semantically_fake_green_is_rejected(self):
        self.all_success_results()
        arm = self.plan['arms'][0]
        result = json.loads(
            (self.results/(arm['arm_id'] + '.json')).read_text())
        commits = Path(result['acceptance_journal'])/'commits'
        path = sorted(commits.glob('*.json'))[-1]
        record = json.loads(path.read_text())
        record['evidence'].pop('sealable_generation')
        body = {key: value for key, value in record.items()
                if key != 'commit_id'}
        record['commit_id'] = digest(body)
        path.write_text(json.dumps(record), encoding='utf-8')
        (Path(result['acceptance_journal'])/'HEAD.json').unlink()
        with self.assertRaisesRegex(
                ValueError, 'document lacks audited provider evidence'):
            ab.aggregate(self.plan_path, self.results)

    def test_wrong_arm_journal_is_rejected(self):
        self.all_success_results()
        first, second = self.plan['arms'][:2]
        one = self.results/(first['arm_id'] + '.json')
        two = json.loads(
            (self.results/(second['arm_id'] + '.json')).read_text())
        value = json.loads(one.read_text())
        value['acceptance_journal'] = two['acceptance_journal']
        one.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(
                ValueError, 'target theory authority'):
            ab.aggregate(self.plan_path, self.results)

    def test_unlisted_transcript_is_rejected(self):
        self.all_success_results()
        arm = self.plan['arms'][0]
        path = self.results/(arm['arm_id'] + '.json')
        value = json.loads(path.read_text())
        value['session_transcripts'] = []
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(
                ValueError, 'manifest differs from arm inventory'):
            ab.aggregate(self.plan_path, self.results)

    def test_unlisted_native_check_is_rejected(self):
        self.all_success_results()
        arm = self.plan['arms'][0]
        path = self.results/(arm['arm_id'] + '.json')
        value = json.loads(path.read_text())
        value['native_checks'] = value['native_checks'][:-1]
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(
                ValueError, 'manifest differs from arm inventory'):
            ab.aggregate(self.plan_path, self.results)

    def test_provider_session_reuse_across_arms_is_rejected(self):
        for arm in self.plan['arms']:
            self.arm_result(arm, duplicate_thread='copied-session')
        with self.assertRaisesRegex(
                ValueError, 'reused across arms'):
            ab.aggregate(self.plan_path, self.results)

    def test_budget_censor_requires_authoritative_budget_crossing(self):
        for arm in self.plan['arms']:
            self.arm_result(
                arm, target_green=False, status='budget_censored')
        report = ab.aggregate(self.plan_path, self.results)
        for policy in ab.SEARCH_POLICIES:
            summary = report['summary_by_search_policy'][policy]
            self.assertEqual(summary['budget_censored_arms'], 2)
            self.assertEqual(summary['fixed_budget_success_denominator'], 2)
            self.assertEqual(summary['target_success_rate_fixed_budget'], 0)
            self.assertEqual(summary['token_metric_eligible_arms'], 2)
            self.assertEqual(summary['decisive_proof_outcomes'], 0)
            self.assertIsNone(
                summary['target_success_rate_decisive_only'])
        self.assertTrue(all(
            pair['token_comparison_eligible']
            for pair in report['pairs']))
        self.assertTrue(all(
            pair['treatment_minus_current']['model_total_tokens'] == 0
            for pair in report['pairs']))
        self.assertTrue(all(
            pair['treatment_minus_current'][
                'target_accepted_effective_seconds'] is None
            for pair in report['pairs']))

    def test_post_acceptance_transport_failure_keeps_certified_success(self):
        for arm in self.plan['arms']:
            self.arm_result(
                arm, target_green=True, status='infrastructure_failure',
                missing_usage=True)
        report = ab.aggregate(self.plan_path, self.results)
        for row in report['arms']:
            self.assertTrue(row['target_journal_green'])
            self.assertTrue(
                row['failures'][
                    'post_acceptance_infrastructure_observed'])
            self.assertTrue(row['eligible_for_primary_paired_comparison'])
            self.assertFalse(row['eligible_for_token_metric_comparison'])
        for policy in ab.SEARCH_POLICIES:
            summary = report['summary_by_search_policy'][policy]
            self.assertEqual(summary['target_success_rate_fixed_budget'], 1)
            self.assertEqual(summary['token_metric_eligible_arms'], 0)

    def test_completed_without_target_green_is_rejected(self):
        for arm in self.plan['arms']:
            self.arm_result(
                arm, target_green=False, status='completed')
        with self.assertRaisesRegex(ValueError, 'lacks target journal GREEN'):
            ab.aggregate(self.plan_path, self.results)


if __name__ == '__main__':
    unittest.main()
