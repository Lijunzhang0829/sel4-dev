"""Disposable conditional diagnostic under the shared whole-call deadline."""
import hashlib
from pathlib import Path
import time
from uuid import uuid4

from regen.execution.batch_checker import BatchWorker, body
from regen.execution.check_protocol import descriptor, limit, remaining, total_call
from regen.execution.check_tasks import NativeCleanupError
from regen.execution.native_check_receipt import native_check_receipt


def check(checker, source, prefix_source, admitted_names, *, timeout=None):
    started = time.monotonic()
    seconds = limit(checker, timeout)
    worker = None
    result = None
    prefix_ms = None
    candidate_started = None
    candidate_ms = None
    try:
        with total_call(checker, seconds):
            try:
                worker = BatchWorker(checker, prefix_source,
                    getattr(checker, '_sketch_prefix_names', []), checker.imports())
                prefix_ms = round((time.monotonic()-started)*1000)
                candidate_started = time.monotonic()
                result = worker.check_sketch(source, admitted_names,
                                            timeout=remaining(seconds))
                remaining()  # Native success arriving after cutoff is rejected.
            except TimeoutError as error:
                worker = worker or getattr(error, 'native_worker', None)
                path = Path(checker.work_dir)/('Total_Sketch_'+uuid4().hex+'.thy')
                path.write_text(source)
                log = path.with_suffix('.log')
                text = getattr(error, 'worker_log', '')
                if not text and worker is not None and (worker.root/'worker.log').exists():
                    text = (worker.root/'worker.log').read_text(errors='replace')
                text += '\nTIMEOUT: shared total check deadline reached\n'
                log.write_text(text)
                result = dict(verdict='red', returncode=124, timed_out=True,
                    diagnostic=text[-12000:], source=str(path), log=str(log),
                    timeout_seconds=seconds, timeout_scope='total-check',
                    check_scope='conditional-sketch-with-certified-prefix',
                    publishable=False, failure_scope='candidate',
                    wall_ms=round((time.monotonic()-started)*1000))
                result['native_receipt'] = native_check_receipt(checker, result,
                    source=source, scope=result['check_scope'], log_text=text,
                    prefix_sha256=hashlib.sha256(body(prefix_source).encode()).hexdigest(),
                    candidate_execution_ms=result['wall_ms'])
            result.update(publishable=False, incremental=False, worker_preserved=False,
                          native_check_protocol=descriptor(), total_timeout_seconds=seconds,
                          timeout_seconds=seconds, prefix_prepare_ms=prefix_ms)
            if candidate_started is not None:
                candidate_ms = round((time.monotonic()-candidate_started)*1000)
            if result.get('returncode') == 124:
                result.update(timeout_scope='total-check',timed_out=True)
            return result
    finally:
        if worker is not None:
            try:
                if not worker.closed:
                    worker.close()
                if worker.closed is not True:
                    raise RuntimeError('worker disposal was not confirmed')
            except Exception as error:
                raise NativeCleanupError('total-check sketch worker cleanup failed: '+str(error)) from error
        if result is not None:
            result['wall_ms']=round((time.monotonic()-started)*1000)
            if isinstance(result.get('native_receipt'),dict):
                result['native_receipt'].update(wall_ms=result['wall_ms'],
                    prefix_prepare_ms=prefix_ms,candidate_execution_ms=candidate_ms,
                    timeout_seconds=seconds,timeout_scope=result.get('timeout_scope'))
