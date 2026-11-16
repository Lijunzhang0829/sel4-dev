from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.planning.publication_manager import PublicationManager
from regen.planning.publication_admission import publication_ready


class PublicationManagerTests(unittest.TestCase):
    def test_owner_retained_until_join_and_receipts_do_not_restart_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=PublicationManager(Path(tmp))
            row={'task_id':'seal-a','binding':{'state_sha256':'frozen'}}
            manager.adopt('A',row)
            self.assertEqual(manager.generating(['A','B']),{'A','B'})
            manager.release_model('A')
            self.assertEqual(manager.generating(['A','B']),{'B'})
            with self.assertRaises(ValueError):manager.adopt('A',row)
            self.assertTrue((Path(tmp)/'publication-tasks/seal-a.json').exists())
            self.assertEqual(PublicationManager(Path(tmp)).theories(),set())
            manager.joined('A')
            self.assertEqual(manager.generating(['B']),{'B'})

    def test_mixed_positive_refuted_ready_but_pending_or_tampered_not_ready(self):
        from regen.tests.current.test_contract_dispositions import ContractDispositionsTests
        from regen.planning.declaration_index import initial_index, accepted_declarations
        f=ContractDispositionsTests();f.setUp();self.addCleanup(f.doCleanups)
        f.package['file_slot']='T.thy'
        for c in f.package['contracts']:c['name']=c['contract_id']
        f.state['graph']=accepted_declarations(initial_index(f.package,run_id=f.c.run['run_id']),['a'])
        f.refute()  # Real journal/certificate validation; synthetic native transport only.
        self.assertFalse(publication_ready(f.c))  # c is still pending.
        f.state['proofs']['c']='by simp';f.state['accepted_order'].append('c')
        f.state['graph']=accepted_declarations(f.state['graph'],f.state['accepted_order'])
        self.assertTrue(publication_ready(f.c))
        from regen.planning.parallel_file_planner import TheoryController
        f.c._publication_tasks={'mixed-seal'}
        f.c._record_proof_work=lambda *args:None
        f.c._public=lambda row:row
        result=TheoryController._consume_task(f.c,
            {'status':'completed','task_id':'mixed-seal','result':{'status':'seal_failed'}},
            {'kind':'seal'})
        self.assertEqual(result['result']['publication_deferred']['reason'],'publication_check_failed')
        self.assertEqual(set(f.state['proofs']),{'a','c'})
        before=deepcopy(f.state)
        self.assertIn('b',{n['id'] for n in before['graph']['nodes']})
        f.state['graph']['nodes'][0]['dependencies']=['b']
        with self.assertRaisesRegex(ValueError,'unproved prerequisite'):publication_ready(f.c)
        f.state=before
        from regen.planning.theory_outcomes import current_dispositions
        bad=current_dispositions(f.c);bad['b']['details']['certificate']={}
        with patch('regen.planning.theory_outcomes.current_dispositions',return_value=bad):
            with self.assertRaises(ValueError):publication_ready(f.c)

    def test_real_controller_keeps_seal_alive_and_returns_completion_to_manager(self):
        import threading,time
        from concurrent.futures import ThreadPoolExecutor
        from regen.tests.current import test_theory_document
        f=test_theory_document.DocumentControllerTests();f.setUp();self.addCleanup(f.doCleanups)
        draft=f.read()['document']
        saved=f.call({'action':'edit_theory','base_sha256':draft['source_sha256'],
                      'source':draft['source'].replace('oops','by simp')})
        f.call({'action':'check_theory','source_sha256':saved['source_sha256']})
        manager=PublicationManager(f.controller.root);f.controller.publication_manager=manager
        entered=threading.Event();release=threading.Event()
        def seal(*args, **kwargs):
            entered.set()
            if not release.wait(5):raise AssertionError('publication owner was not released')
            return None,{'stage':'complete','check':{'verdict':'red','diagnostic':'fixture timeout'}}
        with patch('regen.planning.batch_file_planner.seal_theory',side_effect=seal):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(f.call,{'action':'finish','source_sha256':saved['source_sha256']})
                try:
                    self.assertTrue(entered.wait(3))
                    deadline=time.monotonic()+3
                    while not future.done() and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(future.done())
                    self.assertEqual(manager.theories(),set())
                    self.assertIn('publication_handoff',future.result())
                    manager.release_model('Target')
                    self.assertEqual(manager.generating(['Target','Other']),{'Other'})
                finally:release.set()
                result=future.result(timeout=3)
        self.assertIn('publication_handoff',result)
        completed=f.controller.complete_publication()
        self.assertIn('publication_deferred',completed['result'])
        self.assertEqual(manager.theories(),{'Target'})
        manager.joined('Target')
        self.assertEqual(manager.theories(),set())

    def test_manager_completion_never_starts_another_model_turn(self):
        from regen.tests.current.test_parallel_file_planner import SchedulerTests
        f=SchedulerTests()
        def completed(turn):
            f.empty_controller.managed_publication={'task_id':'seal-task'}
        with tempfile.TemporaryDirectory() as tmp:
            result=f._empty_turn_scenario(tmp,on_turn=completed)
        self.assertEqual(f.empty_session.turns,1)
        self.assertEqual(result['theory_status'],'generating')
        self.assertTrue(f.empty_session.closed)

    def test_private_auth_is_released_before_next_model_slot(self):
        import os,fcntl
        from regen.tests.current.test_parallel_file_planner import SchedulerTests
        f=SchedulerTests()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as auth:
            home=Path(auth);home.chmod(0o700)
            (home/'auth.json').write_text('{}');(home/'auth.json').chmod(0o600)
            class Manager(PublicationManager):
                def release_model(manager, theory):
                    self.assertTrue(f.empty_session.closed)
                    with (home/'.auth-refresh.lock').open('r+') as lock:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        fcntl.flock(lock,fcntl.LOCK_UN)
                    super().release_model(theory)
            manager=Manager(Path(tmp))
            def handoff(turn):
                f.empty_controller.managed_publication=manager.adopt('Target',{'task_id':'auth-test','binding':{}})
                def finish():f.empty_controller.state['status']='sealed'
                f.empty_controller.complete_publication=finish
            with patch.dict(os.environ,{'CODEX_HOME':auth,'REGEN_PERSIST_PRIVATE_AUTH':'1'}):
                result=f._empty_turn_scenario(tmp,on_turn=handoff,publication_manager=manager)
            self.assertEqual(result['status'],'sealed')
            self.assertEqual(f.empty_session.turns,1)
