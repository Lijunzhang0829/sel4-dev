"""Real controller/journal concurrency; synthetic checker proves no benchmark facts."""
import threading
import unittest
from unittest.mock import patch
from regen.tests.current.test_theory_document import DocumentControllerTests
from regen.planning.theory_document import block

class AsyncChecksTests(unittest.TestCase):
    def setUp(self):
        self.f = DocumentControllerTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.c = self.f.controller; self.c.async_checks = True

    def prepare(self):
        draft = self.f.read()['document']
        # One fixed contract plus an independent ordinary helper draft.
        node = {'id':'extra','name':'extra','role':'helper','statement':'lemma extra: "True"','context':[]}
        source = draft['source'].replace('oops','by simp')
        source = source.rsplit('end',1)[0] + block(node,None) + 'end\n'
        return self.f.call({'action':'edit_theory','base_sha256':draft['source_sha256'],'source':source})

    def test_check_returns_while_editing_another_entry_and_preserves_new_draft(self):
        saved = self.prepare()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        from regen.planning import theory_document
        original = theory_document.check_document
        captured=[]
        def blocked(*args, **kw):
            captured.append(args[3]); started.set()
            if not release.wait(5): raise RuntimeError('test release timeout')
            return original(*args,**kw)
        with patch.object(theory_document,'check_document',side_effect=blocked):
            task=self.f.call({'action':'check_theory','source_sha256':saved['source_sha256'], 'entry_ids':['target_contract']})
            self.assertIn(task['status'], ('queued','running'))
            self.assertTrue(started.wait(3))
            # Static retrieval is not queued behind the one native worker.
            with patch.object(self.c, '_observe', return_value={'static_read':True}):
                observed = self.f.call({'action':'observe','reads':[]})
                self.assertTrue(observed['static_read'])
            with self.assertRaisesRegex(ValueError, 'native check in flight'):
                self.f.call({'action':'resolve_symbols','symbols':['True']})
            original_task = self.c.executor.get(task['task_id'])
            duplicate = self.c.submit({'theory':'Target','kind':'proof_batch',
                'proposal':original_task['request']['proposal']},original_task['request']['metadata'])
            self.assertEqual(duplicate['task_id'], task['task_id'])
            draft=self.f.read()['document']
            with self.assertRaisesRegex(ValueError,'reserved'):
                self.f.call({'action':'edit_proof','entry_id':'target_contract','proof_body':'by blast','expected_hash':draft['source_sha256']})
            edited=self.f.call({'action':'edit_proof','entry_id':'extra','proof_body':'by simp','expected_hash':draft['source_sha256']})
            self.assertNotEqual(edited['source_sha256'],saved['source_sha256'])
            release.set()
            result=self.c.handle('wait_checks',{'task_ids':[task['task_id']],'timeout':5},{})['tasks'][0]
            self.assertEqual(result['status'],'completed',result)
        self.assertEqual(self.f.read()['document']['source_sha256'],edited['source_sha256'])
        self.assertIn('target_contract', self.c._state('Target')[1]['proofs'])
        self.assertNotIn('extra',self.c._state('Target')[1]['proofs'])
        self.assertNotIn('lemma extra: "True"\n(* REGEN-PROOF *)\nby simp',captured[0])

    def test_snapshot_feedback_does_not_overwrite_draft_and_detects_conflict(self):
        import tempfile
        from pathlib import Path
        from regen.planning.async_document import DocumentSnapshot, document_root
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'live';root.mkdir();(root/'draft.thy').write_text('old')
            (root/'workflow').mkdir();(root/'workflow/pending.json').write_text('old')
            snap=DocumentSnapshot(root,Path(tmp)/'snapshot')
            (root/'draft.thy').write_text('new')
            (snap.root/'workflow/pending.json').write_text('feedback')
            with snap.activate():self.assertEqual(document_root(root),snap.root)
            snap.publish_feedback()
            self.assertEqual((root/'draft.thy').read_text(),'new')
            self.assertEqual((root/'workflow/pending.json').read_text(),'feedback')
            (root/'workflow/pending.json').write_text('conflict')
            with self.assertRaisesRegex(RuntimeError,'concurrent'):snap.publish_feedback()

    def test_ordinary_helper_authorized_without_sketch_but_not_green(self):
        from tempfile import TemporaryDirectory
        from regen.tests.current.test_sketch_workflow import fixture
        from regen.planning.theory_document import DocumentStore
        from regen.planning.sketch_check import authorize
        package,state=fixture()
        with TemporaryDirectory() as tmp:
            store=DocumentStore(tmp,package,state,[],generation_strategy='sketch-reviewed-v3')
            before=store.read();source=before['source'].replace('oops','by simp')
            store.edit({'action':'edit_theory','base_sha256':before['source_sha256'],'source':source},{})
            self.assertEqual(authorize(store,source,['helper']),[])
            self.assertEqual(state['proofs'],{})

    def test_pending_does_not_rethrow_historical_failure(self):
        self.c.task_ids.add('failed')
        failed = {'task_id':'failed','theory':'Target','kind':'proof_batch',
            'status':'error','error_type':'NativePreparationError','diagnostic':'old failure',
            'request':{'proposal':{'action':'check_theory'}}}
        with patch.object(self.c.executor, 'get', return_value=failed), patch.dict(self.c.executor.records, {'failed':failed}):
            self.assertEqual(self.c._pending(), [])
            view = self.c.handle('list_workspace', {}, {})
            self.assertEqual(view['check_tasks'][0]['status'], 'error')
            from regen.execution.check_tasks import NativePreparationError
            with self.assertRaises(NativePreparationError):self.c._owned('failed')
        self.c.task_ids.remove('failed')
