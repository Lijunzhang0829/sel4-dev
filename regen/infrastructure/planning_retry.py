"""One identical planning-request resend, with trusted budget boundaries."""
import time
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from regen.infrastructure.progress_events import emit_event, generation_exclusion


class PlanningFeedbackTimeout(RuntimeError):
    """A provider call timed out; its partial response is not a submission."""


def discard_timeout_output(prefix):
    """Keep timing/usage/hashes; discard failed partial text after recovery."""
    from regen.infrastructure.llm_transcript import artifact_paths
    paths = artifact_paths(Path(prefix))
    meta = json.loads(paths['meta'].read_text())
    if not meta.get('timed_out'):
        raise ValueError('only a recorded timeout can discard its partial response')
    usage = []
    for line in paths['raw'].read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('usage'):
            usage.append(event['usage'])
    from regen.execution.proof_commits import durable
    durable(Path(str(prefix)+'.timeout-summary.json'), {'metadata':meta,'usage':usage}, immutable=True)
    for key in ('raw','final','stderr'):
        paths[key].write_text('')
        meta[key+'_sha256'] = hashlib.sha256(b'').hexdigest()
    meta.update(partial_response_discarded=True, sealable_generation=False)
    durable(paths['meta'], meta)


def retry_planning_request(call, *, retry_gap=10):
    failed = None
    for attempt in range(2):
        if attempt:
            with generation_exclusion('provider_recovery'):
                time.sleep(retry_gap)
        request = uuid4().hex
        emit_event('planning_request_started', request_id=request, retry_of=failed)
        started=time.monotonic()
        try:
            result = call(attempt)
        except BaseException as error:
            timeout = isinstance(error, PlanningFeedbackTimeout)
            from regen.infrastructure.provider_http_failure import classify_provider_http_failure
            from regen.infrastructure.provider_recovery import ProviderUnavailable
            from regen.infrastructure.codex_workspace_session import ProviderHTTPError, ProviderFeedbackError
            failure=None
            if isinstance(error,(ProviderHTTPError,ProviderFeedbackError)):
                failure=error.failure
            elif isinstance(error,ProviderUnavailable):
                failure=classify_provider_http_failure(str(error))
            emit_event('planning_request_finished', request_id=request,
                       outcome='timeout' if timeout else 'failed',**(
                           {'failure':failure,'waiting_seconds':max(0.,time.monotonic()-started)}
                           if failure is not None else {}))
            if not timeout:
                raise
            if attempt:
                raise PlanningFeedbackTimeout('planning paused after two consecutive timeouts') from error
            failed = request
        else:
            emit_event('planning_request_finished', request_id=request, outcome='success')
            if failed is not None:
                emit_event('planning_request_retry_succeeded',
                           failed_request_id=failed, successful_request_id=request)
            return result
