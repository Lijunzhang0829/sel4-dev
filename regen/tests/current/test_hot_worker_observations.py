"""Observations reuse a process, not speculative proof authority."""
import tempfile
from pathlib import Path
import unittest
import threading
from unittest.mock import Mock, patch

from regen.execution.batch_checker import BatchWorker
from regen.execution.isabelle_node_checker import IsabelleChecker


class ObservationTests(unittest.TestCase):
    def test_import_binding_and_pending_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = object.__new__(BatchWorker)
            w.root, w.index, w.name = Path(tmp), 0, 'worker'
            w.imports, w.base, w.pending = ['Main'], 'base', 'pending'
            w.transaction = Mock(return_value=('OBSERVED', 'log'))
            source = 'theory Probe\nimports\n    "Main"\nbegin\nlemma x: "True"\noops\nend\n'
            result = w.observe_source(source, 'Probe', timeout=10)
            self.assertEqual((w.base, w.pending), ('base', 'pending'))
            self.assertFalse(result['proofs_checked'])
            self.assertEqual(result['verdict'], 'green')
            self.assertEqual(w.transaction.call_args.args[0], 'observe')
            self.assertTrue(w.transaction.call_args.args[1].endswith('end\n'))
            with self.assertRaises(ValueError):
                w.observe_source(source.replace('"Main"', '"Other"'), 'Probe', timeout=10)
            with self.assertRaises(ValueError):
                w.observe_source(source, 'Different', timeout=10)

    def test_lease_and_warm_route_do_not_close_worker(self):
        c = object.__new__(IsabelleChecker)
        c.timeout = 90
        c.imports = lambda: ['Main']
        c._batch_worker = Mock(closed=False, imports=['Main'])
        c._batch_worker.process.poll.return_value = None
        c._batch_worker.observe_source.return_value = {'verdict':'green', 'wall_ms':1}
        c._check = Mock()
        c._check_observation('source', 'Probe')
        c._check.assert_not_called()
        c._batch_worker.close.assert_not_called()
        c._batch_worker.imports = ['Other']
        c._check_observation('source', 'Probe')
        c._check.assert_called_once()

    def test_transport_failure_discards_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = object.__new__(BatchWorker)
            w.root, w.index, w.name = Path(tmp), 0, 'worker'
            w.imports = ['Main']
            w.transaction = Mock(side_effect=TimeoutError('deadline'))
            w.close = Mock()
            with self.assertRaises(TimeoutError):
                w.observe_source('theory P\nimports "Main"\nbegin\nend\n', 'P', timeout=1)
            w.close.assert_called_once()

    def test_task_checker_borrows_owner_lease_not_another_resident(self):
        from regen.execution.check_executor import CURRENT_RESOURCES, NativeResources
        owner = object.__new__(IsabelleChecker)
        owner.timeout = 90
        owner.imports = lambda: ['Main']
        owner._batch_worker = Mock(closed=False, imports=['Main'])
        owner._batch_worker.process.poll.return_value = None
        owner._batch_worker.observe_source.return_value = {'verdict':'green', 'wall_ms':1}
        task = object.__new__(IsabelleChecker)
        task._observation_owner, task._observation_lock = owner, threading.RLock()
        task.imports = lambda: ['Main']
        pool=NativeResources(1,4,1)
        token=CURRENT_RESOURCES.set(pool)
        try:
            task._check_observation('source','Probe')
            self.assertEqual(list(pool.residents),[id(owner)])
            task.close_prefix()
            owner._batch_worker.close.assert_not_called()
        finally:
            pool.close()
            CURRENT_RESOURCES.reset(token)

    def test_protocol_keeps_observation_outside_pending_assignments(self):
        text = Path('regen/execution/batch_checker.ML').read_text()
        observe = text.split('else if read (stem ^ ".action") = "observe" then')[1].split('else if read')[0]
        self.assertIn('Theory.begin_theory (name, Position.none) parents', observe)
        self.assertNotIn('base :=', observe)
        self.assertNotIn('pending :=', observe)
        self.assertNotIn('(!base)', observe)

    def test_real_controller_observation_wires_owner_without_closing_it(self):
        from regen.planning.parallel_file_planner import TheoryController
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            generated=directory/'providers'; generated.mkdir()
            original=object.__new__(IsabelleChecker)
            for key in ('run_dir','image','architecture','timeout','heaps_dir','l4v_dir',
                        'semantic_workspace','base_session','base_import','base_catalog',
                        'isabelle_dir','import_all_previous'):
                setattr(original,key,None)
            original.previous_theories=[]
            original.generated_dir=generated
            original.imports=lambda:['Main']
            original.timeout=90
            original._batch_worker=Mock(closed=False, imports=['Main'])
            original._batch_worker.process.poll.return_value=None
            original._batch_worker.observe_source.return_value={'verdict':'green','wall_ms':1}
            task=object.__new__(IsabelleChecker)
            task.imports=lambda:['Main']
            controller=object.__new__(TheoryController)
            controller._action_lock=threading.RLock()
            controller._runtime=lambda:({}, {}, directory, None, original, [])
            def observed(theory_dir,snapshot,proposal,checker,**kwargs):
                self.assertIs(checker,task)
                return checker._check_observation('source','Probe')
            with patch('regen.execution.isabelle_node_checker.IsabelleChecker',return_value=task), \
                 patch('regen.planning.planner_workspace.read_observation',side_effect=observed):
                result=controller._observe('symbol',{}, {}, {}, directory)
            self.assertEqual(result['verdict'],'green')
            self.assertIs(task._observation_owner,original)
            original._batch_worker.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
