"""Real controller/journal ordering regressions; native evidence is synthetic."""
import unittest


class ControllerMemoryOrderingTests(unittest.TestCase):
    def test_recovery(self):
        import json
        from unittest.mock import patch
        from regen.tests.current.test_parallel_file_planner import ParallelControllerTests
        from regen.planning.batch_file_planner import _record_termination
        case=ParallelControllerTests();case.setUp()
        try:
            ctl=case.controller
            def proof(number,**args):
                return ctl.handle('proof_action',{'theory':'Target','proposal':{'action':'submit_batch',**args}},{**case.metadata,'call_id':str(number)})
            proof(1,proofs={'target_contract':'by simp'},workspace={'summary':'old proof plan'})
            with patch.object(ctl,'_observe',return_value={'status':'available','observations':[]}):
                row=ctl.submit({'theory':'Target','kind':'observe','proposal':{'action':'observe','reads':[{'action':'read_proofs','node_ids':['target_contract']}],'workspace':{'summary':'new observation plan'}}},{**case.metadata,'call_id':'observation'})
                case.wait(row['task_id'])
            proof(3,proofs={})
            _,before=ctl._state('Target')
            run,package,directory,journal,checker,ancestors=ctl._runtime()
            _record_termination(case.root,'Target',directory,journal,before,KeyboardInterrupt('injected external interruption'))
            proof(4,proofs={})
            _,after=ctl._state('Target')
            self.assertEqual(after['batch_workspace'], before['batch_workspace'])
        finally:case.doCleanups()

    def test_observation_completes_before_next_proof_or_read(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from unittest.mock import patch
        from regen.tests.current.test_parallel_file_planner import ParallelControllerTests
        case=ParallelControllerTests();case.setUp()
        release=threading.Event();entered=threading.Event();attempted=threading.Event()
        try:
            ctl=case.controller
            def observed(*args):
                entered.set()
                if not release.wait(5):raise RuntimeError('test release timed out')
                return {'status':'available','observations':[]}
            def next_proof():
                attempted.set()
                return ctl.handle('proof_action',{'theory':'Target','proposal':{
                    'action':'submit_batch','proofs':{'target_contract':'by simp'},
                    'notes':'new proof plan'}},{**case.metadata,'call_id':'proof'})
            with patch.object(ctl,'_observe',side_effect=observed),ThreadPoolExecutor(3) as callers:
                first=callers.submit(ctl.submit,{'theory':'Target','kind':'observe','proposal':{
                    'action':'observe','reads':[],'notes':'old observation plan'}},
                    {**case.metadata,'call_id':'observe'})
                self.assertTrue(entered.wait(3))
                second=callers.submit(next_proof)
                read=callers.submit(ctl.handle,'read_theory',{'theory':'Target'}, {})
                try:
                    self.assertTrue(attempted.wait(3))
                    self.assertFalse(first.done());self.assertFalse(second.done());self.assertFalse(read.done())
                    self.assertEqual(len(ctl.task_ids),1)
                finally:release.set()
                self.assertEqual(first.result(5)['status'],'completed')
                self.assertEqual(second.result(5)['feedback']['status'],'accepted')
                self.assertEqual(read.result(5)['status'],'available')
            self.assertEqual(ctl._state('Target')[1]['batch_notes'],'new proof plan')
        finally:release.set();case.doCleanups()

    def test_two_theory_controllers_still_execute_concurrently(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from unittest.mock import patch
        from regen.tests.current.test_parallel_file_planner import ParallelControllerTests
        from regen.planning.parallel_file_planner import TheoryController
        case=ParallelControllerTests();case.setUp()
        other=None
        try:
            other=TheoryController(case.root,'Source',resources=case.resources,executor=case.executor)
            barrier=threading.Barrier(2)
            def observed(*args):
                barrier.wait(timeout=3)
                return {'status':'available','observations':[]}
            with patch.object(case.controller,'_observe',side_effect=observed),patch.object(other,'_observe',side_effect=observed),ThreadPoolExecutor(2) as callers:
                futures=[callers.submit(ctl.submit,{'theory':ctl.theory,'kind':'observe',
                    'proposal':{'action':'observe','reads':[]}},{**case.metadata,'call_id':ctl.theory})
                    for ctl in (case.controller,other)]
                for future in futures:self.assertEqual(future.result(5)['status'],'completed')
        finally:
            if other is not None:other.close()
            case.doCleanups()
