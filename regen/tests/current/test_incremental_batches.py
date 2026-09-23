from copy import deepcopy
import hashlib
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

    def test_worker_failure_falls_back_to_real_cold_source(self):
        p,old=fixture();new=prepare_batch(p,old,{'proofs':{'helper':'by simp'}})
        checker=Checker();checker.batch_incremental=True
        with patch('regen.execution.batch_checker.BatchWorker',side_effect=RuntimeError('worker died')):
            evidence=check_batch(checker,old,new)
        self.assertFalse(evidence['check']['incremental'])
        self.assertEqual(evidence['incremental_fallback'],'worker died')
        validate_batch_commit(old,new,evidence)

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
