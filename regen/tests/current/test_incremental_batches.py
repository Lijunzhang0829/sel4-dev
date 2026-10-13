from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from regen.execution.batch_checker import BatchWorker, body
from regen.execution.proof_batches import prepare_batch, check_batch, validate_batch_commit
from regen.tests.current.test_proof_batches import Checker
from regen.tests.current.test_proof_commits import fixture


class MemoryWorker:
    def __init__(self, checker, source, names, imports):
        self.base, self.pending = body(source), None
        self.imports, self.closed = imports, False
        self.process = Mock();self.process.poll.return_value=None
    def align(self, prefix):
        if prefix != self.base:
            if prefix != self.pending:raise ValueError('different prefix')
            self.base,self.pending=prefix,None
    def check_batch(self,source,names):
        full=body(source);self.pending=full
        log='\n'.join('REGEN_BATCH_CLEAN '+name for name in names)
        return {'verdict':'green','returncode':0,'source_content':source,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'log_content':log,'log_sha256':hashlib.sha256(log.encode()).hexdigest(),
            'incremental':'audited-batch-worker-v1','wall_ms':1,
            'prefix_sha256':hashlib.sha256(self.base.encode()).hexdigest(),
            'delta_sha256':hashlib.sha256(full[len(self.base):].encode()).hexdigest(),
            'audited_facts':names}
    def close(self):self.closed=True


class IncrementalBatchTests(unittest.TestCase):
    def test_native_evidence_binds_prefix_suffix_and_audits(self):
        p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
        checker=Checker();checker.batch_incremental=True
        with patch('regen.execution.batch_checker.BatchWorker',MemoryWorker):
            evidence=check_batch(checker,old,new)
            validate_batch_commit(old,new,evidence)
            for field,value in [('prefix_sha256','bad'),('delta_sha256','bad'),('audited_facts',[])]:
                bad=deepcopy(evidence);bad['check'][field]=value
                with self.subTest(field=field),self.assertRaises(ValueError):validate_batch_commit(old,new,bad)
            bad=deepcopy(evidence);bad['check']['log_content']=''
            bad['check']['log_sha256']=hashlib.sha256(b'').hexdigest()
            with self.assertRaises(ValueError):validate_batch_commit(old,new,bad)
            second=prepare_batch(p,new,{'proofs':{'contract':'using helper by simp'}})
            e2=check_batch(checker,new,second)
            validate_batch_commit(new,second,e2)
            self.assertEqual(checker.calls,0)

    def test_worker_failure_preserves_source_without_cold_candidate_or_proof_red(self):
        for error in (RuntimeError('worker died'), OSError('pipe failed'), ValueError('protocol mismatch')):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
                checker=Checker();checker.batch_incremental=True;checker.work_dir=Path(tmp)
                before=deepcopy(old)
                with patch('regen.execution.batch_checker.BatchWorker',side_effect=error):
                    evidence=check_batch(checker,old,new)
                check=evidence['check']
                self.assertEqual(check['verdict'],'infrastructure_error')
                self.assertEqual(check['failure_scope'],'native_preparation')
                self.assertEqual(check['error_type'],type(error).__name__)
                self.assertEqual(check['native_failure_stage'],'prefix-startup-or-alignment')
                self.assertEqual(checker.calls,0)
                self.assertEqual(old,before)
                self.assertIsNone(checker._batch_worker)
                self.assertNotIn('incremental_fallback',evidence)
                self.assertTrue(all(row['status']=='not_checked' for row in evidence['node_results']))
                self.assertIn('by simp',check['source_content'])
                self.assertIn(str(error),check['log_content'])
                receipt=json.loads(next(checker.work_dir.glob('incremental-failure-*/failure.json')).read_text())
                self.assertEqual(receipt['source_content'],check['source_content'])
                self.assertEqual(receipt['source_sha256'],hashlib.sha256(check['source_content'].encode()).hexdigest())
                with self.assertRaises(ValueError):validate_batch_commit(old,new,evidence)

    def test_failed_worker_retains_diagnostic_and_cleanup_failure_owner(self):
        from regen.execution.check_tasks import NativePreparationError
        with tempfile.TemporaryDirectory() as tmp:
            p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
            checker=Checker();checker.batch_incremental=True;checker.work_dir=Path(tmp)
            worker=MemoryWorker(checker,'theory T\nbegin\n\nend\n',[],checker.imports())
            worker.root=checker.work_dir
            (worker.root/'worker.log').write_text('native process ended unexpectedly')
            worker.align=Mock()
            worker.check_batch=Mock(side_effect=RuntimeError('audit transport failed'))
            worker.close=Mock(side_effect=RuntimeError('owner cleanup failed'))
            with patch('regen.execution.batch_checker.BatchWorker',return_value=worker):
                with self.assertRaisesRegex(NativePreparationError,'cleanup failed'):
                    check_batch(checker,old,new)
            self.assertIs(checker._batch_worker,worker)
            self.assertIsNone(worker.pending)
            self.assertEqual(checker.calls,0)
            directory=next(checker.work_dir.glob('incremental-failure-*'))
            self.assertIn('native process ended', (directory/'native.log').read_text())
            self.assertTrue((directory/'cleanup-failure.json').exists())
            receipt=json.loads((directory/'failure.json').read_text())
            self.assertEqual(receipt['verdict'],'infrastructure_error')
            self.assertEqual(receipt['native_failure_stage'],'candidate-execution-or-audit-transport')
            self.assertEqual(receipt['diagnostic'],'audit transport failed')

    def test_receipt_write_failure_still_closes_worker_and_is_infrastructure(self):
        from regen.execution.check_tasks import NativePreparationError
        with tempfile.TemporaryDirectory() as tmp:
            p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
            checker=Checker();checker.batch_incremental=True;checker.work_dir=Path(tmp)
            worker=MemoryWorker(checker,'theory T\n'+'begin\n\nend\n',[],checker.imports())
            worker.align=Mock()
            worker.check_batch=Mock(side_effect=RuntimeError('worker died'))
            with patch('regen.execution.batch_checker.BatchWorker',return_value=worker), \
                    patch('regen.execution.proof_batches.durable',side_effect=OSError('disk full')):
                with self.assertRaisesRegex(NativePreparationError,'cannot save infrastructure evidence'):
                    check_batch(checker,old,new)
            self.assertTrue(worker.closed)
            self.assertIsNone(worker.pending)
            self.assertIsNone(checker._batch_worker)
            self.assertEqual(checker.calls,0)

    def test_unsafe_candidate_is_validation_failure_before_worker_dispatch(self):
        p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
        new['proofs']['helper']='by simp\nML "unsafe"'
        checker=Checker();checker.batch_incremental=True
        with patch('regen.execution.batch_checker.BatchWorker') as worker:
            with self.assertRaisesRegex(ValueError,'unsafe command'):
                check_batch(checker,old,new)
        worker.assert_not_called()
        self.assertEqual(checker.calls,0)

    def test_promotion_requires_matching_committed_prefix(self):
        worker=object.__new__(BatchWorker)
        worker.base='old';worker.pending='checked';worker.transaction=Mock(return_value=('COMMITTED',''))
        worker.align('old');worker.transaction.assert_not_called()
        with self.assertRaises(ValueError):worker.align('unrelated')
        worker.align('checked');worker.transaction.assert_called_once_with('commit')
        self.assertEqual(worker.base,'checked');self.assertIsNone(worker.pending)

    def test_uncommitted_success_is_not_promoted(self):
        p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
        checker=Checker();checker.batch_incremental=True
        with patch('regen.execution.batch_checker.BatchWorker',MemoryWorker):
            check_batch(checker,old,new)
            base=checker._batch_worker.base
            other=prepare_batch(p,old,{'proofs':{'contract':'by simp'}})
            e=check_batch(checker,old,other)
            self.assertEqual(checker._batch_worker.base,base)
            validate_batch_commit(old,other,e)

    def test_use_method_is_not_top_level_file_loading(self):
        from regen.execution.proof_batches import _safe_text
        _safe_text('by (use helper in simp)')
        with self.assertRaises(ValueError):
            _safe_text('by simp\nuse "payload.ML"')
        with self.assertRaises(ValueError):
            _safe_text('by simp (* ( (* nested *) *)\nuse "payload.ML"')
