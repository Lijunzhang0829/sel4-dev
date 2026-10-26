"""No providers or Isabelle: one cumulative clock across reviewed planning and proof."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import (
    initialize_parallel_budget, PLANNING_IMPORT_STARTED, PLANNING_IMPORT_COMPLETED)


class PlanningBudgetHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base/'proof'
        self.root.mkdir()
        self.planning = self.base/'planning'
        self.planning.mkdir()
        self.dataset = self.base/'dataset'
        self.dataset.mkdir()
        self.now = [0.]
        self.settings = {'reasoning_effort':'xhigh', 'role_models':{
            'File Planner':'test-model', 'Lemma Prover':'test-model'}}
        self.config = self.base/'models.json'
        self.write(self.config, self.settings)
        self.input_path = self.dataset/'repository-planning-input.json'
        self.write(self.input_path, {'contracts':[{'contract_id':'a'}, {'contract_id':'b'}]})
        self.plan = {'plan_id':'test-plan', 'revision':1,
            'theories':[{'name':'A','contract_ids':['a']}, {'name':'B','contract_ids':['b']}]}
        self.listing = {'plan_id':'test-plan', 'plan_revision':1,
            'files':[{'theory':'A','contract_ids':['a']}, {'theory':'B','contract_ids':['b']}]}
        self.plan_path = self.planning/'revision/repository-plan.json'
        self.list_path = self.planning/'revision/file-generation-list.json'
        self.write(self.plan_path, self.plan)
        self.write(self.list_path, self.listing)
        self.write(self.planning/'frozen-plan.json', {'state':'frozen',
            'plan':str(self.plan_path), 'file_generation_list':str(self.list_path),
            'plan_sha256':self.sha(self.plan_path)})
        self.run = {'run_id':'proof', 'execution_input_kind':'refine-dataset',
            'checker_context_root':str(self.dataset), 'model_config_path':str(self.config),
            'execution_models':self.settings['role_models'], 'session_schedule':{
                'source':str(self.list_path), 'file_list_sha256':digest(self.listing),
                'plan_id':'test-plan', 'plan_revision':1}}
        self.rows = [{'theory':'A','contracts':[{'id':'a'}], 'green_count':0},
                     {'theory':'B','contracts':[{'id':'b'}], 'green_count':0}]
        self.overview = SimpleNamespace(run=self.run,
            list_workspace=lambda:{'theories':self.rows})
        self.binding = {'model':'test-model'}
        self.source = self.prepare_planning(20.)
        settings_patch = patch('regen.infrastructure.audited_llm.model_settings',
                               return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.stages = []
        self.addCleanup(lambda:[stage.close() for stage in self.stages])

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True)+'\n')

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def prepare_planning(self, spent):
        ledger = Ledger(self.planning/'budget', self.planning.name)
        stage = EffectiveTimeBudget(ledger, seconds=100, include_planning=True,
            monotonic=lambda:self.now[0])
        stage.begin_planning()
        self.now[0] += spent
        stage.end_planning()
        stage.close()
        self.write(self.planning/'budget-receipt.json', {
            'schema_version':1, 'policy':POLICY, 'state':'complete', 'include_planning':True,
            'input_sha256':self.sha(self.input_path),
            'frozen_plan_sha256':self.sha(self.planning/'frozen-plan.json'),
            'repository_plan_sha256':self.sha(self.plan_path),
            'file_generation_list_sha256':self.sha(self.list_path),
            'ledger_run_id':self.planning.name, 'ledger_tail_event_id':ledger.rows[-1]['event_id'],
            'seconds':100, 'model':'test-model', 'reasoning_effort':'xhigh'})
        return ledger

    def start(self, **options):
        options = {'seconds':100, 'planning_budget':self.planning, **options}
        def factory(ledger, **kwargs):
            return EffectiveTimeBudget(ledger, monotonic=lambda:self.now[0], **kwargs)
        with patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget',
                   side_effect=factory):
            stage, bridge = initialize_parallel_budget(
                self.root, self.overview, self.binding, **options)
        self.stages.append(stage)
        self.addCleanup(bridge.close)
        return stage, bridge

    def test_planning_and_proof_share_allowance_and_preserve_source_events(self):
        self.now[0] = 1000  # The process gap is not charged.
        stage, bridge = self.start()
        self.assertEqual(stage.snapshot()['effective_seconds'], 20)
        self.assertEqual(stage.snapshot()['planning_seconds'], 20)
        self.assertEqual(stage.snapshot()['remaining_seconds'], 80)
        bridge.handle('theory_generation_started', {'theory':'A'})
        self.now[0] += 10
        self.assertEqual(stage.snapshot()['effective_seconds'], 30)
        self.assertEqual(stage.snapshot()['worker_seconds'], 10)
        imported = [row for row in stage.ledger.rows if 'planning_source_event_id' in row['data']]
        self.assertEqual(len(imported), len(self.source.rows))
        for old, new in zip(self.source.rows, imported):
            self.assertEqual(new['data']['planning_source_event_id'], old['event_id'])
            self.assertEqual(new['data']['planning_source_sequence'], old['sequence'])
            self.assertEqual(new['time_unix'], old['time_unix'])
            self.assertEqual({k:v for k,v in new['data'].items()
                if not k.startswith('planning_source_')}, old['data'])

    def test_resume_retains_planning_without_reimport_or_downtime(self):
        stage, bridge = self.start()
        bridge.handle('theory_generation_started', {'theory':'A'})
        self.now[0] += 5
        stage.close()
        self.now[0] += 1000
        # Passing the original source remains supported and never charges twice.
        resumed, _ = self.start(resume_authorization='continue same experiment')
        self.assertTrue(resumed.snapshot()['include_planning'])
        self.assertEqual(resumed.snapshot()['effective_seconds'], 25)
        self.assertEqual(sum(row['kind'] == PLANNING_IMPORT_COMPLETED
            for row in resumed.ledger.rows), 1)
        resumed.close()
        # Omitting the optional source does not reset the persisted binding.
        again, _ = self.start(planning_budget=None, resume_authorization='continue again')
        self.assertTrue(again.snapshot()['include_planning'])
        self.assertEqual(again.snapshot()['planning_seconds'], 20)
        self.assertEqual(again.snapshot()['effective_seconds'], 25)

    def test_handoff_does_not_implicitly_authorize_later_resumes(self):
        stage, _ = self.start()
        stage.close()
        count = len(stage.ledger.rows)
        with self.assertRaisesRegex(ValueError, 'explicit resume authorization'):
            self.start()
        self.assertEqual(len(Ledger(self.root/'effective-generation-budget', 'proof').rows), count)

    def test_resume_without_source_still_checks_active_model_effort(self):
        stage, _ = self.start()
        stage.close()
        self.settings['reasoning_effort'] = 'medium'
        with self.assertRaisesRegex(ValueError, 'model or reasoning effort differs'):
            self.start(planning_budget=None, resume_authorization='continue')

    def test_same_planning_source_cannot_start_a_second_proof_run(self):
        stage, _ = self.start()
        stage.close()
        self.root = self.base/'second-proof'
        self.root.mkdir()
        self.run['run_id'] = 'second-proof'
        with self.assertRaisesRegex(ValueError, 'already transferred'):
            self.start()

    def test_tampered_ledger_is_rejected_before_target_clock_exists(self):
        event = self.planning/'budget/events/000000001.json'
        value = json.loads(event.read_text())
        value['data']['seconds'] = 200
        self.write(event, value)
        with self.assertRaisesRegex(ValueError, 'ledger chain mismatch'):
            self.start()
        self.assertFalse((self.root/'effective-generation-budget/events').exists())

    def test_new_source_tail_is_not_silently_accepted(self):
        self.source.append('extra-source-work')
        with self.assertRaisesRegex(ValueError, 'ledger tail'):
            self.start()

    def test_changed_input_and_plan_are_rejected(self):
        original = self.input_path.read_bytes()
        self.input_path.write_bytes(original+b' ')
        with self.assertRaisesRegex(ValueError, 'input differs'):
            self.start()
        self.input_path.write_bytes(original)
        self.plan_path.write_bytes(self.plan_path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'plan or file-list hash mismatch'):
            self.start()

    def test_changed_final_allocation_and_model_are_rejected(self):
        self.rows[0]['contracts'] = [{'id':'b'}]
        with self.assertRaisesRegex(ValueError, 'contract allocation differs'):
            self.start()
        self.rows[0]['contracts'] = [{'id':'a'}]
        self.settings['reasoning_effort'] = 'medium'
        with self.assertRaisesRegex(ValueError, 'model or reasoning effort differs'):
            self.start()

    def test_exhausted_planning_cannot_get_a_new_allowance(self):
        # A coherent hash chain and receipt still cannot authorize spent >= cap.
        last = self.source.rows[-1]
        last['data']['state'].update(effective_seconds=100., planning_seconds=100.)
        last['event_id'] = digest({k:v for k,v in last.items() if k != 'event_id'})
        self.write(self.planning/'budget/events'/f"{last['sequence']:09d}.json", last)
        receipt_path = self.planning/'budget-receipt.json'
        receipt = json.loads(receipt_path.read_text())
        receipt['ledger_tail_event_id'] = last['event_id']
        self.write(receipt_path, receipt)
        with self.assertRaisesRegex(ValueError, 'allowance exhausted'):
            self.start()
        self.assertFalse((self.planning/'budget-transfer.json').exists())

    def test_total_limit_and_fresh_extension_cannot_change_during_handoff(self):
        with self.assertRaisesRegex(ValueError, 'allowance mismatch'):
            self.start(seconds=200)
        with self.assertRaisesRegex(ValueError, 'cannot extend'):
            self.start(extension_seconds=10, resume_authorization='unrelated')

    def test_duplicate_and_incomplete_import_markers_fail_closed(self):
        stage, _ = self.start()
        stage.close()
        completed = next(row for row in stage.ledger.rows if row['kind'] == PLANNING_IMPORT_COMPLETED)
        stage.ledger.append(PLANNING_IMPORT_COMPLETED, **completed['data'])
        with self.assertRaisesRegex(ValueError, 'incomplete or duplicate'):
            self.start(resume_authorization='retry')
        other = self.base/'incomplete'
        ledger = Ledger(other/'effective-generation-budget', 'proof')
        ledger.append(PLANNING_IMPORT_STARTED, source='partial')
        self.root = other
        with self.assertRaisesRegex(ValueError, 'incomplete or duplicate'):
            self.start(resume_authorization='retry')

    def test_old_default_starts_with_no_planning_cost(self):
        stage, _ = self.start(planning_budget=None)
        self.assertFalse(stage.snapshot()['include_planning'])
        self.assertEqual(stage.snapshot()['effective_seconds'], 0)
        self.assertFalse((self.planning/'budget-transfer.json').exists())
        self.assertEqual(set(stage.ledger.rows[0]['data']),
            {'policy','run_sha256','contracts_sha256','configuration_sha256'})

    def test_cli_and_service_accept_exact_planning_source(self):
        from regen.execution.proof_session_runner import main
        from regen.tools.parallel_workspace_service import _manifest
        command = [sys.executable, '-m', 'regen.execution.proof_session_runner',
            'parallel-workspace', '--run-dir', str(self.root),
            '--planning-budget', str(self.planning), '--allow-external-call']
        self.write(self.root/'experiment.json', {'command':command, 'cwd':str(self.base)})
        self.assertEqual(_manifest(self.root)[0]['command'], command)
        with patch('sys.argv', ['runner', *command[3:]]), redirect_stdout(io.StringIO()), \
                patch('regen.planning.parallel_file_planner.run_parallel_workspace',
                      return_value={'status':'partial'}) as run:
            self.assertEqual(main(), 1)
        self.assertEqual(run.call_args.kwargs['planning_budget'], self.planning)


if __name__ == '__main__':
    unittest.main()
