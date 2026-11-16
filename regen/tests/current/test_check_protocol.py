"""Shared whole-call cap, cold proof checks and fail-closed sketch disposal."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import contextmanager
import json
import tempfile
import unittest

from regen.execution.check_protocol import (PROTOCOL, FILENAME, NAME, baseline_config,
    configure, descriptor, enabled, limit, remaining, total_call)
from regen.execution.check_tasks import NativeCleanupError
from regen.execution.total_sketch_check import check
from regen.infrastructure.experiment_runtime_budget import native_failure


class CheckProtocolTests(unittest.TestCase):
    def checker(self, root):
        return SimpleNamespace(semantic_workspace=root,work_dir=root,timeout=900,
            batch_incremental=True,persistent=True,imports=lambda:['Main'])

    def test_new_source_sealed_descriptor_selects_cold_backend_and_same_baseline_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);c=self.checker(root)
            (root/FILENAME).write_text(json.dumps(descriptor()))
            configure(c)
            self.assertTrue(enabled(c));self.assertFalse(c.batch_incremental)
            self.assertFalse(c.persistent)
            self.assertEqual(c.timeout,baseline_config()['total_timeout_seconds'])
            self.assertEqual(baseline_config()['native_check_protocol'],c.native_check_protocol)
            self.assertEqual(limit(c,1800),300)

    def test_missing_descriptor_preserves_legacy_checker_and_unknown_descriptor_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);c=self.checker(root);configure(c)
            self.assertTrue(c.batch_incremental);self.assertEqual(c.timeout,900)
            (root/FILENAME).write_text(json.dumps({**descriptor(),'total_timeout_seconds':301}))
            with self.assertRaisesRegex(ValueError,'unsupported'):configure(c)

    def test_prefix_and_candidate_share_one_deadline_not_fresh_300_seconds(self):
        c=SimpleNamespace(native_check_protocol=descriptor(),timeout=300)
        clock=[10.]
        with patch('regen.execution.check_protocol.time.monotonic',side_effect=lambda:clock[0]):
            with total_call(c):
                clock[0]=70.
                self.assertEqual(remaining(300),240)
                with total_call(c):
                    clock[0]=309.
                    self.assertEqual(remaining(),1)
                clock[0]=311.
                with self.assertRaisesRegex(TimeoutError,'total check'):remaining()
            self.assertEqual(remaining(77),77)

    def test_whole_call_timeout_cannot_receive_native_outage_refund(self):
        uid='check';binding=dict(worker='w',prefix_sha256='p',request_sha256='r',imports_sha256='i')
        evidence=dict(policy='native-no-response-after-cleanup-v1',request_id=uid,
            **binding,response_missing=True,worker_discarded=True,native_response_received=False)
        self.assertTrue(native_failure(uid,binding,evidence))
        self.assertFalse(native_failure(uid,{**binding,'check_protocol':NAME},evidence))

    def test_native_admission_wait_spends_same_deadline_and_never_launches_late(self):
        from regen.execution.check_executor import native_operation,CURRENT_RESOURCES
        clock=[0.];launched=[]
        class Pool:
            @contextmanager
            def lease(self,checker):
                clock[0]=301.
                yield
        @native_operation
        def operation(checker,source):
            launched.append(source);return {'verdict':'green'}
        with tempfile.TemporaryDirectory() as temp:
            c=self.checker(Path(temp));c.native_check_protocol=descriptor()
            token=CURRENT_RESOURCES.set(Pool())
            try:
                with patch('regen.execution.check_protocol.time.monotonic',side_effect=lambda:clock[0]):
                    result=operation(c,'theory Queue begin end')
            finally:CURRENT_RESOURCES.reset(token)
        self.assertEqual(launched,[])
        self.assertEqual(result['returncode'],124)
        self.assertFalse(result['native_execution_started'])
        self.assertFalse(result['publishable'])

    def test_total_sketch_reports_startup_time_instead_of_only_delta(self):
        c=SimpleNamespace(native_check_protocol=descriptor(),timeout=300,imports=lambda:['Main'])
        clock=[0.];w=Mock();w.closed=False
        def factory(*args):clock[0]=60.;return w
        def candidate(*args,**kwargs):
            self.assertLessEqual(kwargs['timeout'],240.)
            clock[0]=62.;return dict(verdict='green',returncode=0,wall_ms=2000,native_receipt={})
        def close():clock[0]=64.;w.closed=True
        w.check_sketch.side_effect=candidate;w.close.side_effect=close
        with patch('regen.execution.check_protocol.time.monotonic',side_effect=lambda:clock[0]),patch(
                'regen.execution.total_sketch_check.BatchWorker',side_effect=factory):
            result=check(c,'theory X\nbegin\nend\n','theory X\nbegin\nend\n',[])
        self.assertEqual(result['wall_ms'],64000)
        self.assertEqual(result['prefix_prepare_ms'],60000)
        self.assertEqual(result['native_receipt']['candidate_execution_ms'],2000)
        self.assertFalse(result['publishable'])


class TotalSketchTests(unittest.TestCase):
    def run_case(self, *, startup=False, cleanup=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        checker=SimpleNamespace(work_dir=root,timeout=300,native_check_protocol=descriptor(),
                                imports=lambda:['Main'])
        worker=Mock();worker.root=root;worker.closed=False
        def close():
            if cleanup:raise RuntimeError('container still running')
            worker.closed=True
        worker.close.side_effect=close
        error=TimeoutError('total check expired');error.native_worker=worker
        worker.check_sketch.side_effect=error
        factory=Mock(side_effect=error) if startup else Mock(return_value=worker)
        with patch('regen.execution.total_sketch_check.BatchWorker',factory),patch(
                'regen.execution.total_sketch_check.native_check_receipt',return_value={}):
            result=check(checker,'theory X\nbegin\nlemma x: "True" by simp\nend\n',
                         'theory X\nbegin\n\nend\n',[])
        return result,worker

    def test_startup_timeout_is_undetermined_and_owned_worker_is_disposed(self):
        result,worker=self.run_case(startup=True)
        self.assertEqual(result['returncode'],124)
        self.assertEqual(result['timeout_scope'],'total-check')
        self.assertFalse(result['publishable']);self.assertFalse(result['incremental'])
        self.assertEqual(result['native_check_protocol'],PROTOCOL)
        self.assertTrue(worker.closed);worker.close.assert_called_once()

    def test_candidate_timeout_never_publishes_admissions(self):
        result,worker=self.run_case()
        self.assertEqual(result['verdict'],'red');self.assertFalse(result['publishable'])
        self.assertTrue(worker.closed)

    def test_cleanup_failure_cannot_be_demoted_to_model_timeout_feedback(self):
        with self.assertRaisesRegex(NativeCleanupError,'cleanup failed'):
            self.run_case(cleanup=True)
