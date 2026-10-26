"""Real short local subprocesses; no model, network, Docker or Isabelle calls."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CheckCancelled
from regen.execution.proof_commits import digest
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
from regen.infrastructure.parallel_generation_budget import initialize_parallel_budget
from regen.infrastructure import reviewed_planning_budget as module


class ReviewedPlanningBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.counter = 0
        self.settings = {'reasoning_effort':'xhigh', 'role_models':{'File Planner':'test-model'}}
        self.config = self.base/'models.json'
        self.write(self.config, self.settings)
        self.input_path = self.base/'dataset/repository-planning-input.json'
        self.write(self.input_path, {'contracts':[{'contract_id':'a'}]})
        self.binding = {'model':'test-model', 'reasoning_effort':'xhigh',
                        'input_sha256':module.file_hash(self.input_path)}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True)+'\n')

    def make_budget(self, seconds=5, *, now=None):
        self.counter += 1
        root = self.base/('planning-'+str(self.counter))
        root.mkdir()
        def create(ledger, **kwargs):
            if now is not None:
                kwargs['monotonic'] = lambda:now[0]
            return EffectiveTimeBudget(ledger, poll_seconds=.02,
                checkpoint_seconds=.05, **kwargs)
        with patch.object(module, 'EffectiveTimeBudget', side_effect=create):
            budget = module.ReviewedPlanningBudget(root, seconds=seconds, binding=self.binding)
        self.addCleanup(lambda:budget.close(failed=True))
        plan = {'plan_id':'test-plan', 'revision':1,
                'theories':[{'name':'A','contract_ids':['a']}]}
        listing = {'plan_id':'test-plan', 'plan_revision':1,
                   'files':[{'theory':'A','contract_ids':['a']}]}
        plan_path, list_path = root/'revision/repository-plan.json', root/'revision/file-generation-list.json'
        self.write(plan_path, plan)
        self.write(list_path, listing)
        frozen = {'state':'frozen', 'plan':str(plan_path), 'file_generation_list':str(list_path),
                  'plan_sha256':module.file_hash(plan_path)}
        budget.start()
        return budget, frozen

    @staticmethod
    def run_child(budget, program, log=None):
        log = io.StringIO() if log is None else log
        budget.run([sys.executable, '-u', '-c', program], log)
        return log

    def assert_unfrozen(self, budget, frozen):
        with self.assertRaises((CheckCancelled, ValueError)):
            budget.complete(frozen)
        self.assertFalse((budget.root/'frozen-plan.json').exists())
        self.assertFalse((budget.root/'budget-receipt.json').exists())

    def test_successful_stages_charge_one_clock_and_publish_closed_receipt(self):
        budget, frozen = self.make_budget()
        log = self.run_child(budget, "import time; time.sleep(.12); print('draft complete')")
        first = budget.stage.snapshot()['effective_seconds']
        self.assertGreaterEqual(first, .10)
        self.assertIn('draft complete', log.getvalue())
        self.run_child(budget, "import time; time.sleep(.12); print('review complete')")
        receipt = budget.complete(frozen)
        self.assertGreaterEqual(receipt['effective_seconds']-first, .10)
        self.assertEqual(receipt['effective_seconds'], receipt['planning_seconds'])
        self.assertEqual(receipt['ledger_tail_event_id'], budget.ledger.rows[-1]['event_id'])
        self.assertEqual(budget.ledger.rows[-1]['data']['boundary'], 'close')
        self.assertFalse(budget.stage.snapshot()['planning_active'])
        self.assertEqual(receipt['frozen_plan_sha256'], module.file_hash(budget.root/'frozen-plan.json'))

    def test_watchdog_check_racing_clean_close_does_not_cancel_completed_plan(self):
        budget, frozen = self.make_budget()
        entered, release = threading.Event(), threading.Event()
        original = budget.stage.check
        def check():
            if threading.current_thread() is budget.stage.thread:
                entered.set()
                release.wait(timeout=2)
            return original()
        def after_close():
            budget.stage.closed.wait(timeout=2)
            release.set()
        with patch.object(budget.stage, 'check', side_effect=check):
            self.assertTrue(entered.wait(timeout=2))
            unblock = threading.Thread(target=after_close)
            unblock.start()
            try:
                receipt = budget.complete(frozen)
            finally:
                release.set()
                unblock.join(timeout=2)
        self.assertEqual(receipt['state'], 'complete')
        self.assertEqual(budget.ledger.rows[-1]['data']['boundary'], 'close')
        self.assertIsNone(budget.stage.reason)

    def test_trusted_exclusion_events_exclude_only_the_bracketed_interval(self):
        budget, frozen = self.make_budget()
        program = """import json, time
def event(name):
    print('[regen-event] '+json.dumps({'event':name,'phase_id':'native','category':'runtime_setup'}), flush=True)
event('generation_exclusion_started')
time.sleep(.30)
event('generation_exclusion_finished')
time.sleep(.10)
"""
        began = time.monotonic()
        self.run_child(budget, program)
        wall = time.monotonic()-began
        spent = budget.stage.snapshot()['effective_seconds']
        self.assertGreaterEqual(spent, .08)
        self.assertGreaterEqual(wall-spent, .24)
        self.assertFalse(budget.stage.exclusions)
        self.assertGreaterEqual(budget.complete(frozen)['remaining_seconds'], 4.)

    def test_timeout_kills_uncooperative_child_and_cannot_freeze(self):
        budget, frozen = self.make_budget(seconds=.4)
        pidfile = self.base/'child.pid'
        program = f"""import os, signal, time
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
Path({str(pidfile)!r}).write_text(str(os.getpid()))
time.sleep(60)
"""
        began = time.monotonic()
        with patch.object(module, 'STOP_GRACE_SECONDS', .15), \
                self.assertRaises(CheckCancelled):
            self.run_child(budget, program)
        self.assertLess(time.monotonic()-began, 3.)
        self.assertEqual(budget.stage.reason, 'effective_time_budget_reached')
        self.assertTrue(pidfile.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        self.assertIsNone(budget.process)
        self.assert_unfrozen(budget, frozen)

    def test_dead_leader_does_not_leave_descendant_and_stdout_reader_alive(self):
        budget, frozen = self.make_budget()
        pidfile = self.base/'grandchild.pid'
        descendant = f"""import os, signal, time
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
Path({str(pidfile)!r}).write_text(str(os.getpid()))
time.sleep(60)
"""
        parent = f"""import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-u', '-c', {descendant!r}])
until = time.monotonic()+2
while not Path({str(pidfile)!r}).exists():
    if time.monotonic()>until: raise RuntimeError('fixture failed to start')
    time.sleep(.01)
"""
        began = time.monotonic()
        with patch.object(module, 'READER_JOIN_SECONDS', .15), \
                patch.object(module, 'STOP_GRACE_SECONDS', .15), \
                self.assertRaisesRegex(RuntimeError, 'open output stream'):
            self.run_child(budget, parent)
        self.assertLess(time.monotonic()-began, 3.)
        # An orphan may briefly remain a zombie until init reaps it. It must
        # already have exited and released stdout, rather than still sleeping.
        pid = int(pidfile.read_text())
        status = Path(f'/proc/{pid}/stat')
        if status.exists():
            self.assertIn(status.read_text().rsplit(')', 1)[1].split()[0], {'Z','X'})
        self.assertIsNone(budget.process)
        self.assert_unfrozen(budget, frozen)

    def test_invalid_and_unclosed_trusted_exclusions_fail_closed(self):
        events = [
            '[regen-event] not-json',
            '[regen-event] '+json.dumps({'event':'generation_exclusion_started',
                                       'phase_id':'p','category':'made_up'}),
            '[regen-event] '+json.dumps({'event':'generation_exclusion_finished',
                                       'phase_id':'missing','category':'runtime_setup'}),
            '[regen-event] '+json.dumps({'event':'generation_exclusion_started',
                                       'phase_id':'unfinished','category':'runtime_setup'}),
        ]
        for event in events:
            with self.subTest(event=event):
                budget, frozen = self.make_budget()
                with self.assertRaises((CheckCancelled, ValueError, KeyError)):
                    self.run_child(budget, 'print('+repr(event)+', flush=True)')
                self.assertTrue(budget.stage.event.is_set())
                self.assertFalse(budget.stage.exclusions)
                self.assert_unfrozen(budget, frozen)

    def test_prose_without_trusted_prefix_cannot_pause_the_clock(self):
        budget, frozen = self.make_budget(seconds=.3)
        prose = 'model says '+json.dumps({'event':'generation_exclusion_started',
                                         'phase_id':'p','category':'runtime_setup'})
        program = 'import time; print('+repr(prose)+', flush=True); time.sleep(60)'
        with self.assertRaises(CheckCancelled):
            self.run_child(budget, program)
        self.assertFalse(budget.stage.exclusions)
        self.assertEqual(budget.stage.reason, 'effective_time_budget_reached')
        self.assert_unfrozen(budget, frozen)

    def test_failed_child_cannot_be_caught_and_later_frozen(self):
        import subprocess
        budget, frozen = self.make_budget()
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_child(budget, 'raise SystemExit(7)')
        self.assertEqual(budget.stage.reason, 'reviewed_planning_child_failed')
        self.assert_unfrozen(budget, frozen)

    def test_log_write_failure_cancels_and_releases_child(self):
        class BrokenLog:
            def write(self, text):
                raise OSError('fixture disk failure')
        budget, frozen = self.make_budget()
        with self.assertRaises((OSError, CheckCancelled)):
            self.run_child(budget, "import time; print('hello', flush=True); time.sleep(60)", BrokenLog())
        self.assertEqual(budget.stage.reason, 'planning_clock_event_error')
        self.assertIsNone(budget.process)
        self.assert_unfrozen(budget, frozen)

    def test_missing_or_mismatched_plan_does_not_publish_freeze_marker(self):
        budget, frozen = self.make_budget()
        with self.assertRaises(FileNotFoundError):
            budget.complete({**frozen, 'plan':str(budget.root/'missing.json')})
        self.assertFalse((budget.root/'frozen-plan.json').exists())
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            budget.complete({**frozen, 'plan_sha256':'wrong'})
        self.assertFalse((budget.root/'frozen-plan.json').exists())
        with self.assertRaisesRegex(ValueError, 'escapes'):
            budget.complete({**frozen, 'plan':str(self.config)})
        self.assertFalse((budget.root/'budget-receipt.json').exists())

    def test_complete_refuses_an_unfinished_exclusion(self):
        budget, frozen = self.make_budget()
        budget.stage.begin_exclusion('runtime_setup')
        with self.assertRaisesRegex(ValueError, 'unfinished exclusions'):
            budget.complete(frozen)
        self.assertFalse((budget.root/'frozen-plan.json').exists())
        self.assertFalse((budget.root/'budget-receipt.json').exists())

    def test_real_complete_receipt_hands_off_without_cost_reset(self):
        now = [0.]
        budget, frozen = self.make_budget(seconds=100, now=now)
        now[0] = 25
        receipt = budget.complete(frozen)
        self.assertEqual(receipt['effective_seconds'], 25)
        listing = json.loads(Path(frozen['file_generation_list']).read_text())
        run = {'run_id':'proof', 'execution_input_kind':'refine-dataset',
            'checker_context_root':str(self.input_path.parent), 'model_config_path':str(self.config),
            'execution_models':self.settings['role_models'], 'session_schedule':{
                'source':frozen['file_generation_list'], 'file_list_sha256':digest(listing),
                'plan_id':'test-plan', 'plan_revision':1}}
        overview = SimpleNamespace(run=run, list_workspace=lambda:{'theories':[
            {'theory':'A', 'contracts':['a'], 'green_count':0}]})
        now[0] = 1000
        def create(ledger, **kwargs):
            return EffectiveTimeBudget(ledger, monotonic=lambda:now[0], **kwargs)
        with patch('regen.infrastructure.audited_llm.model_settings', return_value=self.settings), \
                patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget', side_effect=create):
            stage, bridge = initialize_parallel_budget(self.base/'proof', overview,
                {'model':'test-model'}, seconds=100, planning_budget=budget.root)
        self.addCleanup(stage.close)
        self.addCleanup(bridge.close)
        self.assertEqual(stage.snapshot()['effective_seconds'], 25)
        bridge.handle('theory_generation_started', {'theory':'A'})
        now[0] += 5
        self.assertEqual(stage.snapshot()['effective_seconds'], 30)
        self.assertEqual(stage.snapshot()['remaining_seconds'], 70)

    def test_cli_charges_draft_review_and_revision_to_the_same_budget(self):
        from regen.tools import reviewed_planning
        now = [0.]
        output = self.base/'cli-planning'
        policy = self.base/'policy.json'
        self.write(policy, {'model':{}})
        proposal = {'schema_version':5, 'theories':[
            {'name':'A','contract_ids':['a'],'required_imports':[]}]}
        review = {'theory_reviews':[{'theory':'A','assessment':'fixture',
                                    'consumed_contract_ids':[]}], 'issues':[]}
        calls, budgets = [], []
        def run_command(root, name, args, *, budget=None):
            self.assertIsNotNone(budget)
            budgets.append(budget)
            calls.append(name)
            self.assertTrue(budget.stage.snapshot()['planning_active'])
            self.write(root/'status.json', {'state':'running','stage':name})
            folder = root/name
            if name == 'draft':
                self.write(folder/'planner-proposal.json', proposal)
                self.write(folder/'agent-proposal.json', proposal)
                self.write(folder/'private-contract-map.json', {'C0001':'a'})
            elif name == 'review':
                self.assertEqual(args[args.index('--model')+1], 'gpt-5.6-sol')
                self.assertEqual(args[args.index('--reasoning-effort')+1], 'xhigh')
                self.write(folder/'review.json', review)
            elif name == 'revision':
                self.write(folder/'planner-proposal.json', proposal)
                self.write(folder/'workspace/revision-decisions.json', [])
                self.write(folder/'repository-plan.json',
                           {**proposal, 'plan_id':'test-plan','revision':1})
                self.write(folder/'file-generation-list.json', {
                    'plan_id':'test-plan','plan_revision':1,
                    'files':[{'theory':'A','contract_ids':['a']}]})
            else:
                self.fail('unexpected stage '+name)
            now[0] += {'draft':10, 'review':20, 'revision':30}[name]
        def create(ledger, **kwargs):
            return EffectiveTimeBudget(ledger, monotonic=lambda:now[0], **kwargs)
        argv = ['reviewed_planning', '--input',str(self.input_path), '--output',str(output),
            '--policy',str(policy), '--model','gpt-5.6-sol', '--reasoning-effort','xhigh',
            '--include-planning-time', '--effective-seconds','100', '--allow-external-call']
        with patch('sys.argv', argv), patch.object(module, 'EffectiveTimeBudget', side_effect=create), \
                patch.object(reviewed_planning, 'run_command', side_effect=run_command):
            reviewed_planning.main()
        self.assertEqual(calls, ['draft','review','revision'])
        self.assertEqual(len({id(budget) for budget in budgets}), 1)
        receipt = json.loads((output/'budget-receipt.json').read_text())
        self.assertEqual(receipt['seconds'], 100)
        self.assertEqual(receipt['effective_seconds'], 60)
        self.assertEqual(receipt['planning_seconds'], 60)
        self.assertEqual(receipt['remaining_seconds'], 40)
        self.assertEqual(receipt['model'], 'gpt-5.6-sol')
        self.assertEqual(receipt['reasoning_effort'], 'xhigh')
        self.assertEqual(json.loads((output/'status.json').read_text()),
                         {'state':'complete','stage':'frozen'})


if __name__ == '__main__':
    unittest.main()
