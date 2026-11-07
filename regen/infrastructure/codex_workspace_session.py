"""One app-server process/thread with audited, serial harness tool callbacks.

Local protocol source: `codex app-server generate-json-schema --experimental`.
Dynamic tools are additive, not an exclusive OS sandbox. Real providers require
a pinned minimal OS-isolation container; synthetic protocol tests retain the
audited-tool-allowlist label. Explicit provider authorization is still required.
The session never replays an executed callback. Its coordinator may resend a
failed provider request from retained workspace state; app-server may reconnect a response stream when it explicitly
reports that the same turn will be retried. A callback owns its native/action
deadline and worker cleanup. The session timeout measures inactivity while
waiting for protocol events, excluding callback time; cancellation cleans up the
app-server process group. A callback-local reader continues receiving and
validating protocol events without moving the serial callback off its owner
thread. The optional abort hook cancels that owner's in-flight check tasks.
"""
from copy import deepcopy
from contextvars import copy_context
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from jsonschema import Draft202012Validator, ValidationError
from regen.infrastructure.codex_runtime import CodexRuntimeError


class SessionProtocolError(RuntimeError):
    pass


class SessionCancelled(KeyboardInterrupt):
    pass


class SessionStalled(SessionProtocolError):
    """Protocol is live but the planner is not obtaining new information."""


class SessionProgressDeadline(SessionStalled):
    """No complete useful response before the progress deadline (not a read loop)."""


class ProviderFeedbackError(SessionProtocolError):
    """A trusted local/transport interruption eligible for one fresh resend."""
    def __init__(self, failure, failed_request_id):
        self.failure = deepcopy(failure)
        self.failed_request_id = failed_request_id
        super().__init__('provider feedback interrupted: '+json.dumps(failure, sort_keys=True))


class ProviderOverloaded(SessionProtocolError):
    """Explicit provider capacity rejection; not an unknown protocol failure."""


class ProviderHTTPError(SessionProtocolError):
    """Bound provider HTTP/stream failure; coordinator owns the limited retry."""
    def __init__(self, failure):
        self.failure = deepcopy(failure)
        super().__init__('provider HTTP/transport failure: '+json.dumps(failure, sort_keys=True))


SLEEP_POLICY_VERSION = 2
MAX_DEFERRED_TOOL_CALLS = 64
TRUSTED_FAILURE_INTERRUPT_SECONDS = 2.0


def tool_result_confirmed(params, response, *, owner, tool):
    """A server display receipt confirms delivery, never a proof or model reading.

    Notifications may precede the RPC or arrive while another callback runs.
    An incomplete display item is not an acknowledgement. Do not infer one
    from subsequent model output, a terminal turn, or an elapsed deadline.
    """
    item = params.get('item', {})
    if ((params.get('threadId'), params.get('turnId'), item.get('id')) != owner
            or item.get('tool') != tool or item.get('namespace') is not None):
        raise ValueError('dynamic tool result confirmation identity mismatch')
    if (item.get('status') not in {'completed', 'failed'}
            or type(item.get('success')) is not bool
            or not isinstance(item.get('contentItems'), list)):
        return False
    if (item['success'] != response['success']
            or item['contentItems'] != response['contentItems']):
        raise ValueError('dynamic tool result confirmation differs from sent result')
    return True


def validate_sleep_event(params, method, known_turns, state, *, policy_version=1):
    """Shared runtime/audit policy for clock.sleep display events, never execution."""
    if type(policy_version) is not int or policy_version not in (1, 2):
        raise ValueError('unsupported sleep policy version')
    item = params.get('item', {})
    owner = (params.get('threadId'), params.get('turnId'))
    duration = item.get('durationMs')
    if (owner not in known_turns or not all(isinstance(x, str) and x for x in owner)
            or set(item) != {'type', 'id', 'durationMs'} or item['type'] != 'sleep'
            or not isinstance(item['id'], str) or not item['id']
            or type(duration) is not int or not 0 <= duration <= 60000):
        raise ValueError('invalid or unbound sleep item (maximum 60000 ms)')
    key = (*owner, item['id'])
    pending = state.setdefault('pending', {})
    seen = state.setdefault('seen', set())
    totals = state.setdefault('totals', {})
    if method == 'item/started':
        if key in seen or (policy_version == 1 and totals.get(owner, 0) + duration > 120000):
            raise ValueError('duplicate sleep item or per-turn sleep budget exceeded')
        seen.add(key)
        totals[owner] = totals.get(owner, 0) + duration
        pending[key] = duration
    elif method == 'item/completed':
        if pending.get(key) != duration:
            raise ValueError('unmatched or changed sleep completion')
        del pending[key]
    else:
        raise ValueError('invalid sleep lifecycle event')


class ProgressWatch:
    """Bound duplicate reads and event-only stalls; native callbacks own their timeouts."""
    def __init__(self, timeout, *, clock=time.monotonic):
        self.clock, self.timeout = clock, timeout
        self.last_progress = clock()
        self.generation = 0
        self.seen = set()
        self.repeats = 0

    def check(self):
        if self.clock() - self.last_progress >= self.timeout:
            raise SessionProgressDeadline('no new tool information or proof action within progress deadline')

    def observe(self, name, arguments, value, elapsed):
        # Waiting inside an authorized callback is governed by the native/action
        # timeout, not by provider inactivity or the progress clock.
        self.last_progress += elapsed
        from regen.infrastructure.information_progress import information_payload, proof_work
        fingerprint = hashlib.sha256(json.dumps(information_payload(name, arguments, value),
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        work = proof_work(name, arguments, value)
        if work or fingerprint not in self.seen:
            self.seen.add(fingerprint)
            self.repeats = 0
            self.generation += 1
            self.last_progress = self.clock()
        else:
            self.repeats += 1
            if self.repeats >= 8:
                raise SessionStalled('eight repeated tool results without new information or proof work')


class CodexWorkspaceSession:
    def __init__(self, *, tools, codex_bin='codex', codex_home=None,
                 allow_external_call=False, command=None, progress_timeout=None,
                 http_retry_mode=False, feedback_retry_mode=False,
                 retry_failed_request_id=None):
        if type(http_retry_mode) is not bool:
            raise ValueError('http_retry_mode must be boolean')
        self._http_retry_mode = http_retry_mode
        if type(feedback_retry_mode) is not bool:
            raise ValueError('feedback_retry_mode must be boolean')
        if retry_failed_request_id is not None and (
                not feedback_retry_mode or not isinstance(retry_failed_request_id, str)
                or not retry_failed_request_id):
            raise ValueError('a failed request identity requires feedback retry mode')
        self._feedback_retry_mode = feedback_retry_mode
        self._retry_failed_request_id = retry_failed_request_id
        self._provider_request_id = None
        self._provider_request_since = None
        self._provider_feedback_at = None
        self._feedback_interrupted = False
        self._http_interrupted = False
        self._overload_interrupted = False
        self._http_failure = None
        self.tools = deepcopy(tools)
        self.validators = {}
        for tool in self.tools:
            if set(tool) - {'name', 'description', 'inputSchema'} or not isinstance(tool.get('name'), str):
                raise ValueError('tools require name, description, inputSchema only')
            if tool['name'] in self.validators:
                raise ValueError('duplicate tool name')
            schema = tool['inputSchema']
            def check_refs(value):
                if isinstance(value, dict):
                    for k, v in value.items():
                        if k in ('$ref', '$dynamicRef') and not v.startswith('#'):
                            raise ValueError('remote schema references are forbidden')
                        check_refs(v)
                elif isinstance(value, list):
                    for v in value: check_refs(v)
            check_refs(schema)
            Draft202012Validator.check_schema(schema)
            self.validators[tool['name']] = Draft202012Validator(schema)
        if not self.tools:
            raise ValueError('at least one explicit harness tool is required')
        self.codex_bin, self.codex_home = str(codex_bin), codex_home
        self.allow_external_call, self.command = allow_external_call, command
        self.process = None
        self.thread_id = self.turn_id = None
        self._serial = 0
        self._buffer = b''
        self._calls = {}
        self._tool_results = {}
        self._tool_completions = {}
        self._model_item_activity = {}
        self._model_item_diagnostics = set()
        self._broken = False
        self._closed = False
        self._lock = threading.Lock()
        self._expired = threading.Event()
        self._cancel_event = None
        self._log = None
        self._stderr = None
        self._temporary = None
        self._isolation = None
        self._selector = None
        self._artifact_dir = None
        self._usage = None
        self._deferred = []
        self._awaiting_turn_response = False
        self._pending_turn_notifications = []
        self._deferred_terminal = None
        self._callback_executing = False
        self._request_offsets = {}
        self._notification_receipts = {}
        self.last_notification_receipt = None
        # Zero disables both elapsed-progress and duplicate-result termination.
        # Global time-budget experiments own their complete stopping policy.
        self._progress_timeout = progress_timeout
        self._progress = None
        self._sleep_state = {}
        self._on_abort = None
        self._on_notification = None
        self._abort_lock = threading.Lock()
        self._abort_notified = False
        self._abort_failure = None
        self._callback_reader = None
        self._callback_reader_stop = None
        self._callback_reader_error = None
        self._active_request_id = None
        self._generation_id = uuid4().hex
        self._generation_active = False
        self._stream_retry_phase = None
        self._stream_retry_phase_since = None
        self._stream_retry_since = None
        self._stream_retry_error = None
        self._transport_progress_reported = False
        self._model_work_observed = False
        self._model_work_items = set()
        self._model_work_closed_items = set()
        self._clock_untrusted_reasons = set()

    def _generation_start(self):
        if not self._generation_active:
            from regen.infrastructure.progress_events import emit_event
            emit_event('theory_generation_started', generation_id=self._generation_id,
                       clock_schema='theory_effective_v1', model_lifecycle_policy=3)
            self._generation_active = True

    def _runtime_failed(self, error):
        if isinstance(error, CodexRuntimeError) and not getattr(error, '_regen_runtime_failure_emitted', False):
            from regen.infrastructure.progress_events import emit_event
            try:
                emit_event('generation_runtime_integrity_failure',
                           generation_id=self._generation_id, error_type=type(error).__name__,
                           reason=str(error), failure=error.failure)
                error._regen_runtime_failure_emitted = True
            except BaseException as timing_error:
                error.add_note('runtime failure publication failed: ' + str(timing_error))

    def _generation_stop(self, reason, *, handler_stop=False):
        if self._generation_active:
            self._generation_active = False
            self._finish_stream_retry()
            from regen.infrastructure.progress_events import emit_event
            if self._provider_request_id is not None:
                # Cancellation/protocol errors do not authorize a refund. Close
                # the pending attribution interval as retained work instead.
                self._provider_request_finish('retained', reason=reason)
            if (handler_stop or self._http_interrupted or self._overload_interrupted or self._feedback_interrupted
                    or (self._cancel_event is not None and self._cancel_event.is_set())):
                # A trusted handler stop or explicit pause is a known interruption, not a claim
                # that the provider completed its open items. A later successful
                # resend may exclude only its explicitly failed request interval.
                emit_event('generation_items_interrupted', generation_id=self._generation_id,
                           thread_id=self.thread_id, turn_id=self.turn_id, reason=reason,
                           interruption_origin='handler' if handler_stop else 'session_interrupt',
                           item_ids=sorted(str(key[-1]) for key in self._model_work_items))
                self._finish_model_work(clear_items=True)
            else:
                self._model_work_boundary('session_stopped_with_open_model_item')
            emit_event('theory_generation_stopped', generation_id=self._generation_id,
                       clock_schema='theory_effective_v1', reason=reason)

    def _provider_request_begin(self, source):
        if not self._feedback_retry_mode or self._provider_request_id is not None:
            return
        from regen.infrastructure.progress_events import emit_event
        self._provider_request_id = uuid4().hex
        self._provider_request_since = time.monotonic()
        self._provider_feedback_at = self._provider_request_since
        fields = dict(request_id=self._provider_request_id, source=source,
            generation_id=self._generation_id, thread_id=self.thread_id, turn_id=self.turn_id)
        self._event('local', {'event':'provider_request_started', **fields})
        emit_event('generation_provider_request_started', **fields)

    def _provider_request_finish(self, outcome, **evidence):
        if self._provider_request_id is None:
            return None
        from regen.infrastructure.progress_events import emit_event
        request_id, self._provider_request_id = self._provider_request_id, None
        self._provider_request_since = None
        self._provider_feedback_at = None
        fields = dict(request_id=request_id, generation_id=self._generation_id,
            thread_id=self.thread_id, turn_id=self.turn_id, outcome=outcome, **evidence)
        self._event('local', {'event':'provider_request_finished', **fields})
        emit_event('generation_provider_request_finished', **fields)
        if outcome == 'success' and self._retry_failed_request_id is not None:
            old, self._retry_failed_request_id = self._retry_failed_request_id, None
            emit_event('provider_feedback_retry_succeeded', failed_request_id=old,
                successful_request_id=request_id, generation_id=self._generation_id)
        return request_id

    def _raise_feedback_failure(self, failure, *, source):
        # Only trusted call sites invoke this: a local response deadline, EOF,
        # or an ownership-checked provider transport error. Never classify a
        # model's prose or a mathematical tool failure as infrastructure.
        self._feedback_interrupted = True
        self._generation_start()
        if self._provider_request_id is None:
            # A failure during an executing callback cannot refund its work.
            # Attribute only the detection boundary, not the whole session.
            self._provider_request_begin('failure_without_pending_response')
        request_id = self._provider_request_finish('failed', failure=deepcopy(failure),
            protocol_source=source, active_request_id=self._active_request_id,
            callback_executing=self._callback_executing)
        from regen.infrastructure.progress_events import emit_event
        emit_event('generation_provider_request_failed', request_id=request_id,
            generation_id=self._generation_id, thread_id=self.thread_id, turn_id=self.turn_id,
            failure=deepcopy(failure), callback_executing=self._callback_executing,
            active_request_id=self._active_request_id, protocol_source=source)
        raise ProviderFeedbackError(failure, request_id)

    def _finish_stream_retry(self):
        self._stream_retry_since = None
        self._stream_retry_error = None
        if self._stream_retry_phase is not None:
            if self._progress is not None and self._stream_retry_phase_since is not None:
                self._progress.last_progress += time.monotonic() - self._stream_retry_phase_since
            self._stream_retry_phase_since = None
            phase_id, self._stream_retry_phase = self._stream_retry_phase, None
            from regen.infrastructure.progress_events import emit_event
            emit_event('generation_exclusion_finished', phase_id=phase_id,
                       category='provider_stream_retry')

    def _begin_stream_retry(self, error):
        # A notification announcing a retry is NOT a terminal provider failure.
        if self._stream_retry_since is None:
            self._stream_retry_since = time.monotonic()
        self._stream_retry_error = deepcopy(error)
        if not self._callback_executing and self._stream_retry_phase is None:
            from regen.infrastructure.progress_events import emit_event
            self._stream_retry_phase = uuid4().hex
            self._stream_retry_phase_since = time.monotonic()
            emit_event('generation_exclusion_started', phase_id=self._stream_retry_phase,
                       category='provider_stream_retry',confirmed_interruption=True,
                       evidence_id='trusted-stream-retry:'+self._stream_retry_phase)

    def _observe_model_work(self):
        self._finish_stream_retry()
        if self._callback_executing and not self._model_work_observed:
            from regen.infrastructure.progress_events import emit_event
            emit_event('model_work_observed', thread_id=self.thread_id, turn_id=self.turn_id,
                       generation_id=self._generation_id, model_lifecycle_policy=3)
            self._model_work_observed = True

    def _finish_model_work(self, *, clear_items=False):
        if clear_items:
            self._model_work_closed_items.update(self._model_work_items)
            self._model_work_items.clear()
        if self._model_work_observed:
            self._model_work_observed = False
            from regen.infrastructure.progress_events import emit_event
            emit_event('model_work_observation_finished', thread_id=self.thread_id,
                       turn_id=self.turn_id, generation_id=self._generation_id,
                       model_lifecycle_policy=3)

    def _untrusted_model_clock(self, reason, items=()):
        """Retain protocol/proof work, but never certify an ambiguous budget."""
        identity = (self.turn_id, reason, tuple(sorted(str(item[-1]) for item in items)))
        if identity in self._clock_untrusted_reasons:
            return
        self._clock_untrusted_reasons.add(identity)
        from regen.infrastructure.progress_events import emit_event
        emit_event('generation_clock_untrusted', reason=reason,
                   generation_id=self._generation_id, thread_id=self.thread_id,
                   turn_id=self.turn_id, item_ids=list(identity[-1]),
                   model_lifecycle_policy=3)

    def _model_work_boundary(self, reason):
        """An unclosed item is uncertainty, never authority for the next tool."""
        unfinished = tuple(self._model_work_items)
        self._finish_model_work(clear_items=True)
        if unfinished:
            self._untrusted_model_clock(reason, unfinished)

    def _begin_model_callback(self):
        # Callback boundaries are local, not provider item completion events.
        # A reasoning item may begin before/during one tool and finish later.
        self._callback_executing = True
        if self._model_work_items:
            self._observe_model_work()

    def _end_model_callback(self):
        # End only the preparation-overlap override. Preserve item identities
        # for their real completion (or validated turn terminal). Outside a
        # callback ordinary model waiting is already charged by the base gate.
        self._callback_executing = False
        self._finish_model_work()
        if self._stream_retry_since is not None:
            self._begin_stream_retry(self._stream_retry_error)

    def _start_model_item(self, key):
        if not isinstance(key[-1], str) or not key[-1]:
            self._untrusted_model_clock('model_item_missing_identity', (key,))
        if key in self._model_work_closed_items:
            self._untrusted_model_clock('late_model_activity_after_completion', (key,))
            return
        self._model_work_items.add(key)
        now = time.time()
        activity = self._model_item_activity.setdefault(key, {'started_at_unix': now})
        activity['last_activity_at_unix'] = now
        self._observe_model_work()

    def _diagnose_open_messages(self, boundary, *, except_key=None):
        # A missing completion can be legitimate during an asynchronous tool.
        # Diagnose it early, without changing clocks, interrupting, or claiming
        # that the message completed. In particular, no age/proof-progress cap.
        for key in sorted(self._model_work_items, key=lambda value: tuple(map(str, value))):
            if key[2] != 'agentMessage' or key == except_key:
                continue
            identity = (key, boundary)
            if identity in self._model_item_diagnostics:
                continue
            self._model_item_diagnostics.add(identity)
            self._event('local', {'event': 'model_message_completion_pending',
                'thread_id': key[0], 'turn_id': key[1], 'item_id': key[3],
                'boundary': boundary, 'diagnostic_only': True, 'clock_effect': 'none',
                **self._model_item_activity.get(key, {})})

    def _confirm_tool_result(self, key):
        result, completion = self._tool_results.get(key), self._tool_completions.get(key)
        if result is None or result['state'] not in {'sent', 'server_confirmed'} or completion is None:
            return
        try:
            confirmed = tool_result_confirmed(completion, result['response'],
                                              owner=key, tool=result['tool'])
        except ValueError as error:
            raise SessionProtocolError(str(error)) from error
        if confirmed and result['state'] != 'server_confirmed':
            result['state'] = 'server_confirmed'
            self._event('local', {'event': 'tool_result_server_confirmed',
                'thread_id': key[0], 'turn_id': key[1], 'call_id': key[2],
                'rpc_id': result['rpc_id'], 'response_sha256': result['response_sha256'],
                'authority': 'server_result_receipt_only'})

    def _result_confirmation_summary(self):
        return {'policy_version': 1,
            'sent': sum(value['state'] in {'sent', 'server_confirmed'}
                        for value in self._tool_results.values()),
            'server_confirmed': sum(value['state'] == 'server_confirmed'
                                    for value in self._tool_results.values()),
            'unconfirmed_call_ids': [key[2] for key, value in self._tool_results.items()
                                     if value['state'] != 'server_confirmed'],
            'notice': 'Delivery receipts do not establish that the model understood a result.'}

    def _model_work_notification(self, method, params):
        """Bound preparation overrides to observed model-item lifecycles.

        A tool's execution lifecycle is not a model-generation lifecycle.
        Open items may span callbacks in the same bound turn. Completion closes
        the preparation override; callback return alone does not close an item.
        Session failure with missing closure still invalidates measurement, not
        accepted proofs; ordinary silence outside preparation remains charged.
        """
        if params.get('threadId') != self.thread_id or params.get('turnId') != self.turn_id:
            return
        item = params.get('item', {})
        kinds = {'reasoning', 'agentMessage'}
        deltas = {'item/reasoning/textDelta':'reasoning',
                  'item/reasoning/summaryTextDelta':'reasoning',
                  'item/agentMessage/delta':'agentMessage'}
        if self._provider_request_id is not None and (
                method in deltas or (method in ('item/started', 'item/completed')
                                    and item.get('type') in kinds)):
            # Partial model feedback keeps a long reasoning response alive.
            # Quota/token metadata alone must not extend a silent connection.
            self._provider_feedback_at = time.monotonic()
        if method in ('item/started', 'item/completed') and item.get('type') in kinds:
            key = (self.thread_id, self.turn_id, item['type'], item.get('id'))
            if method == 'item/started':
                self._start_model_item(key)
                if item['type'] == 'agentMessage':
                    self._diagnose_open_messages('later_agent_message_started', except_key=key)
            else:
                self._finish_stream_retry()
                self._model_work_closed_items.add(key)
                self._model_work_items.discard(key)
                if not self._model_work_items:
                    self._finish_model_work()
        elif method in deltas:
            self._start_model_item((self.thread_id, self.turn_id, deltas[method], params.get('itemId')))
        elif method in ('item/started', 'item/completed') and item.get('type') == 'dynamicToolCall':
            self._finish_stream_retry()
            if method == 'item/completed' and isinstance(item.get('id'), str):
                key = (self.thread_id, self.turn_id, item['id'])
                self._tool_completions[key] = deepcopy(params)
                self._confirm_tool_result(key)

    def __enter__(self):
        return self

    def __exit__(self, exception_type, error, traceback):
        self._close_preserving_error(error)

    def _close_preserving_error(self, error):
        try:
            self.close()
        except BaseException as cleanup_error:
            if error is None:
                raise
            # The coordinator must still recognize the original provider error
            # and forbid a replacement owner when cleanup could not finish.
            error.add_note('workspace session cleanup failed: '+str(cleanup_error))

    def _raise_http_failure(self, error, *, source, request_id=None):
        """Classify only after the caller validates notification/RPC ownership."""
        if not (self._http_retry_mode or self._feedback_retry_mode):
            return
        from regen.infrastructure.provider_http_failure import classify_provider_http_failure
        failure = classify_provider_http_failure(error)
        if failure is None and isinstance(error, dict):
            # JSON-RPC errors may carry structured provider details under data.
            failure = classify_provider_http_failure(error.get('data'))
        if failure is None:
            return
        status = failure.get('http_status')
        if self._feedback_retry_mode and (
                status is None or status >= 500 or status in (408, 409, 425, 429)):
            self._raise_feedback_failure(failure, source=source)
        if not self._http_retry_mode:
            return
        if self._http_failure is None:
            from regen.infrastructure.progress_events import emit_event
            self._http_interrupted = True
            self._http_failure = failure
            emit_event('generation_provider_http_failure',
                generation_id=self._generation_id, thread_id=self.thread_id,
                turn_id=self.turn_id, failure=failure,
                active_request_id=self._active_request_id,
                callback_executing=self._callback_executing,
                protocol_source=source, provider_request_id=request_id,
                retry_policy='provider-recovery-v2')
        # A broken session never internally retries callbacks or provider calls.
        raise ProviderHTTPError(self._http_failure)

    @property
    def tool_call_count(self):
        return len(self._calls)

    def _event(self, direction, message):
        if self._log is None:
            return
        with self._lock:
            if self._log.closed:
                return
            self._log.write(json.dumps({'time_unix':time.time(), 'direction':direction,
                                       'message':message}, ensure_ascii=False)+'\n')
            self._log.flush()
            os.fsync(self._log.fileno())
            return self._log.tell()

    def _send(self, message):
        self._event('out', message)
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False)+'\n').encode())
            self.process.stdin.flush()
        except (BrokenPipeError, ConnectionResetError):
            if self._feedback_retry_mode:
                self._raise_feedback_failure({'kind':'app_server_pipe_closed', 'http_status':None},
                    source='stdin_write')
            raise

    def _request(self, method, params):
        self._serial += 1
        self._send({'id':self._serial, 'method':method, 'params':params})
        return self._serial

    def _receive(self, deadline):
        # Inactivity budget: callback/native-check time is deliberately excluded.
        deadline = time.monotonic() + self._timeout
        return self._read_message(deadline=deadline)

    def _read_message(self, *, deadline=None, callback_stop=None, available_only=False,
                      shutdown=False):
        """Exactly one reader owns stdout/buffer at a time, including callbacks."""
        while True:
            if callback_stop is not None and callback_stop.is_set():
                return None
            if not shutdown and self._cancel_event is not None and self._cancel_event.is_set():
                raise SessionCancelled('workspace coordinator cancelled')
            if (not shutdown and callback_stop is None and self._feedback_retry_mode
                    and self._provider_feedback_at is not None
                    and time.monotonic() - self._provider_feedback_at >= self._timeout):
                self._raise_feedback_failure({'kind':'no_complete_response', 'http_status':None},
                    source='provider_feedback_deadline')
            if not shutdown and self._stream_retry_since is not None and (self._http_retry_mode or self._feedback_retry_mode):
                # Bound a stuck reconnect even if it keeps sending heartbeats.
                # Native callbacks retain their own deadline and are not aborted
                # merely because the provider is reconnecting in parallel.
                if callback_stop is None and time.monotonic() - self._stream_retry_since >= self._timeout:
                    self._raise_http_failure(self._stream_retry_error, source='reconnect_deadline')
            if not shutdown and callback_stop is None and self._progress is not None and self._stream_retry_since is None:
                try:
                    self._progress.check()
                except SessionProgressDeadline as error:
                    if self._feedback_retry_mode and self._provider_request_id is not None:
                        self._raise_feedback_failure({'kind':'no_complete_response', 'http_status':None},
                            source='progress_deadline')
                    if self._progress_timeout is None:
                        raise TimeoutError('workspace session deadline expired without tool progress') from error
                    raise
            if b'\n' in self._buffer:
                break
            remaining = deadline-time.monotonic() if deadline is not None else .05
            if (not shutdown and self._expired.is_set()) or remaining <= 0:
                if shutdown:
                    raise TimeoutError('trusted failure interrupt acknowledgement deadline expired')
                if self._stream_retry_since is not None:
                    self._raise_http_failure(self._stream_retry_error, source='reconnect_timeout')
                if self._feedback_retry_mode:
                    self._raise_feedback_failure({'kind':'no_complete_response', 'http_status':None},
                        source='response_deadline')
                raise TimeoutError('workspace session deadline expired')
            if not self._selector.select(0 if available_only else min(remaining, .05)):
                if available_only:
                    return None
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                if shutdown:
                    raise SessionProtocolError('app-server closed before interrupted turn was confirmed')
                if self._stream_retry_since is not None:
                    self._raise_http_failure(self._stream_retry_error, source='reconnect_eof')
                if self._feedback_retry_mode:
                    self._raise_feedback_failure({'kind':'app_server_eof', 'http_status':None},
                        source='stdout_eof')
                raise SessionProtocolError('app-server closed before terminal response')
            self._buffer += chunk
        raw, self._buffer = self._buffer.split(b'\n', 1)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise SessionProtocolError('app-server message must be an object')
        offset = self._event('in', {'raw_json':raw.decode(), 'parsed':value})
        if value.get('method') and 'id' not in value:
            self._notification_receipts[id(value)]={'transcript_path':str(self._artifact_dir/'session-transcript.jsonl'),
                'transcript_offset':offset,'raw_message_sha256':hashlib.sha256(raw).hexdigest()}
        if value.get('method') == 'item/tool/call':
            self._request_offsets[id(value)] = offset
        return value

    def _notify_abort(self):
        # Never close/drain the controller in the reader thread. Its hook only
        # latches cancellation and cancels owned tokens, including registration
        # races; the original owner remains responsible for cleanup and joining.
        with self._abort_lock:
            if self._abort_notified:
                return
            self._abort_notified = True
        if self._on_abort is not None:
            try:
                self._on_abort()
            except BaseException as error:
                self._abort_failure = error
                self._event('local', {'event':'callback_abort_failed', 'nonsealable':True,
                                      'exception_type':type(error).__name__, 'message':str(error)})

    def _validate_tool_request(self, message):
        params = message.get('params')
        if (not isinstance(params, dict)
                or not {'tool', 'arguments', 'callId'} <= params.keys()):
            raise SessionProtocolError('malformed dynamic tool request envelope')
        if params.get('threadId') != self.thread_id or params.get('turnId') != self.turn_id:
            raise SessionProtocolError('tool callback belongs to another thread/turn')
        name, arguments, call_id = params['tool'], params['arguments'], params['callId']
        if not isinstance(name, str) or name not in self.validators or params.get('namespace') is not None:
            raise SessionProtocolError('non-allowlisted dynamic tool: '+str(name))
        if not isinstance(call_id, str) or not call_id:
            raise SessionProtocolError('invalid dynamic tool call ID')
        # Envelope validation is fatal; argument schema errors belong to the
        # actual RPC response, including requests queued during a callback.
        return self.thread_id, self.turn_id, call_id

    def _queue_callback_tool(self, message, *, previously_received=False):
        if self._deferred_terminal is not None and not previously_received:
            raise SessionProtocolError("tool request arrived after turn completion")
        key = self._validate_tool_request(message)
        queued = [self._validate_tool_request(item) for item in self._deferred]
        if key in self._calls or key in queued:
            raise SessionProtocolError('duplicate call ID; callback is never automatically replayed')
        if (message['id'] == self._active_request_id
                or any(item['id'] == message['id'] for item in self._deferred)):
            raise SessionProtocolError('duplicate pending server request ID')
        if len(self._deferred) >= MAX_DEFERRED_TOOL_CALLS:
            raise SessionProtocolError('too many queued serial tool requests')
        self._finish_stream_retry()
        self._provider_request_finish('success', response_kind='tool_request',
            call_id=message['params']['callId'])
        self._deferred.append(message)

    def _pump_callback_events(self):
        try:
            while not self._callback_reader_stop.is_set():
                message = self._read_message(callback_stop=self._callback_reader_stop)
                if message is None:
                    break
                self._callback_notification(message)
        except BaseException as error:
            self._callback_reader_error = error
            self._notify_abort()
            self._event('local', {'event':'callback_protocol_failed', 'nonsealable':True,
                                  'exception_type':type(error).__name__, 'message':str(error)})

    def _callback_notification(self, message):
        method = message.get('method')
        if method == 'item/tool/call':
            self._queue_callback_tool(message)
        elif method == 'turn/completed':
            params = message.get('params', {})
            turn = params.get('turn', {})
            if (not self._callback_executing or self._deferred_terminal is not None
                    or params.get('threadId') != self.thread_id
                    or turn.get('id') != self.turn_id):
                raise SessionProtocolError('invalid terminal while tool callback is pending')
            if turn.get('status') != 'completed':
                self._raise_http_failure(turn.get('error'), source='callback_failed_turn')
                raise SessionProtocolError('invalid terminal while tool callback is pending')
            if any(key[:2] == (self.thread_id, self.turn_id)
                   for key in self._sleep_state.get('pending', {})):
                raise SessionProtocolError('turn completed with unfinished sleep item')
            # The provider may finish without awaiting an asynchronous dynamic
            # call. Preserve the raw ordering, finish already accepted callbacks
            # exactly once, and only then let the owner consume this terminal.
            self._deferred_terminal = message
            self._provider_request_finish('success', response_kind='turn_completed')
            self._finish_stream_retry()
            self._finish_model_work(clear_items=True)
            self._event('local', {'event':'terminal_deferred_until_tool_results',
                'thread_id':self.thread_id, 'turn_id':self.turn_id})
        elif method == 'turn/started':
            raise SessionProtocolError('turn changed while tool callback is pending')
        elif method is None:
            raise SessionProtocolError('unsolicited server response while tool callback is pending')
        else:
            self._notification(message)

    def _preflight_callback(self):
        # Requests may have been queued before turn/start's response or at the
        # previous reader's stop boundary. Validate all already received/ready
        # events before dispatching another mathematical operation. Never block
        # here for an incomplete future message.
        queued, self._deferred = self._deferred, []
        for message in queued:
            self._queue_callback_tool(message, previously_received=True)
        while True:
            message = self._read_message(deadline=time.monotonic()+self._timeout,
                                         available_only=True)
            if message is None:
                return
            self._callback_notification(message)

    def _stop_callback_reader(self):
        if self._callback_reader is not None:
            self._callback_reader_stop.set()
            if self._callback_reader.ident is not None:
                self._callback_reader.join()
            self._callback_reader = None

    def _check_callback_abort(self):
        if self._callback_reader_error is not None:
            raise self._callback_reader_error
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise SessionCancelled('workspace coordinator cancelled')

    def _interrupt_native_failure(self, error):
        """Bound a trusted local stop to a real provider interruption terminal.

        The callback reader is already joined. This owner is the only reader;
        it drains notifications but never executes/replays queued callbacks.
        Keep the original exception and all charged time if acknowledgement
        fails. An RPC response alone does not establish a stopped turn.
        """
        if (self.process is None or self.process.poll() is not None
                or not self.thread_id or not self.turn_id or not self._generation_active):
            return False
        if self._callback_reader is not None:
            raise SessionProtocolError('cannot interrupt with an active callback reader')
        owner = self.thread_id, self.turn_id
        request_id = self._request('turn/interrupt',
            {'threadId': owner[0], 'turnId': owner[1]})
        self._event('local', {'event': 'trusted_failure_interrupt_requested',
            'thread_id': owner[0], 'turn_id': owner[1], 'rpc_id': request_id,
            'exception_type': type(error).__name__, 'refund_seconds': 0})
        deadline = time.monotonic() + TRUSTED_FAILURE_INTERRUPT_SECONDS
        acknowledged = interrupted = False
        while not (acknowledged and interrupted):
            message = self._read_message(deadline=deadline, shutdown=True)
            method, params = message.get('method'), message.get('params', {})
            if method is None:
                if message.get('id') != request_id or 'error' in message or 'result' not in message:
                    raise SessionProtocolError('invalid trusted failure interrupt response')
                acknowledged = True
            elif method == 'turn/completed':
                turn = params.get('turn', {})
                if ((params.get('threadId'), turn.get('id')) != owner
                        or turn.get('status') != 'interrupted' or interrupted):
                    raise SessionProtocolError('trusted failure lacks matching interrupted terminal')
                interrupted = True
            elif method == 'item/tool/call':
                # A request can have been queued while the failed tool ran.
                # The interrupt cancels it; receiving it is not execution.
                self._queue_callback_tool(message)
            elif method == 'turn/started':
                raise SessionProtocolError('turn changed during trusted failure interrupt')
            else:
                self._notification(message)
        self._event('local', {'event': 'trusted_failure_interrupt_confirmed',
            'thread_id': owner[0], 'turn_id': owner[1], 'rpc_id': request_id,
            'refund_seconds': 0, 'queued_tools_not_executed': len(self._deferred)})
        return True

    def _await(self, request_id, deadline):
        while True:
            message = self._receive(deadline)
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    self._raise_http_failure(message['error'], source='rpc_error',
                                             request_id=request_id)
                    raise SessionProtocolError(str(message['error']))
                return message['result']
            if message.get('method') == 'item/tool/call':
                if len(self._deferred) >= MAX_DEFERRED_TOOL_CALLS:
                    raise SessionProtocolError('too many queued serial tool requests')
                self._deferred.append(message)
            elif message.get('method') == 'turn/completed':
                # A terminal consumed before its start response must never be
                # discarded and followed by dispatch of deferred proof work.
                raise SessionProtocolError('turn completed before start response was confirmed')
            else:
                self._notification(message)

    def _notification(self, message):
        params = message.get('params', {})
        item = params.get('item', {})
        key = (params.get('threadId'), params.get('turnId'), item.get('id'))
        if (message.get('method') == 'item/completed' and item.get('type') == 'dynamicToolCall'
                and key in self._tool_results and key[:2] != (self.thread_id, self.turn_id)):
            # A display receipt for an already executed call may be delivered
            # after the next turn starts. It cannot execute a callback or close
            # any model item in the current turn.
            self.last_notification_receipt = self._notification_receipts.pop(id(message), None)
            self._tool_completions[key] = deepcopy(params)
            self._confirm_tool_result(key)
            if self._on_notification is not None:
                self._on_notification(message['method'], deepcopy(params))
            return
        if (self._awaiting_turn_response
                and 'id' not in message
                and message.get('method') in ('item/started', 'item/completed')
                and params.get('item', {}).get('type') == 'dynamicToolCall'):
            item = params['item']
            if (params.get('threadId') != self.thread_id
                    or not isinstance(params.get('turnId'), str) or not params['turnId']):
                raise SessionProtocolError('unbound dynamic tool item before turn response')
            if (not isinstance(item.get('tool'), str)
                    or item['tool'] not in self.validators or item.get('namespace') is not None):
                raise SessionProtocolError('non-allowlisted dynamic tool item')
            if len(self._pending_turn_notifications) >= 2 * MAX_DEFERRED_TOOL_CALLS:
                raise SessionProtocolError('too many dynamic tool items before turn response')
            # Keep the original receipt and do not notify application observers
            # until turn/start's response confirms the claimed owner.
            self._pending_turn_notifications.append(message)
            return
        self.last_notification_receipt=self._notification_receipts.pop(id(message),None)
        if 'id' in message and 'method' in message:
            # Approval/user-input/unknown server requests are never granted.
            self._send({'id':message['id'], 'error':{'code':-32601, 'message':'Only whitelisted item/tool/call is allowed'}})
            raise SessionProtocolError('unexpected server request: '+message['method'])
        method, params = message.get('method'), message.get('params', {})
        self._model_work_notification(method, params)
        if method in ('item/started', 'item/completed'):
            kind = params.get('item', {}).get('type')
            if kind == 'sleep':
                try:
                    validate_sleep_event(params, method, {(self.thread_id, self.turn_id)},
                                         self._sleep_state, policy_version=SLEEP_POLICY_VERSION)
                except ValueError as error:
                    raise SessionProtocolError(str(error)) from error
                return
            allowed = {'agentMessage', 'reasoning', 'userMessage', 'dynamicToolCall', 'contextCompaction', 'plan'}
            if kind not in allowed:
                raise SessionProtocolError('non-allowlisted built-in item: '+str(kind))
            if kind == 'dynamicToolCall':
                item = params['item']
                if (not isinstance(item.get('tool'), str)
                        or item.get('tool') not in self.validators or item.get('namespace') is not None):
                    raise SessionProtocolError('non-allowlisted dynamic tool item')
                if (params.get('threadId') != self.thread_id
                        or params.get('turnId') != self.turn_id):
                    raise SessionProtocolError('dynamic tool item belongs to another thread/turn')
                # Notifications do not execute tools. Reject invalid arguments
                # with corrective feedback when the actual RPC arrives.
        if method == 'thread/tokenUsage/updated':
            self._usage = deepcopy(params.get('tokenUsage'))
        if method == 'turn/started':
            self.turn_id = params['turn']['id']
        if self._on_notification is not None:
            self._on_notification(method, deepcopy(params))
        if method == 'error':
            error = params.get('error', {})
            if (params.get('willRetry') is True and self.thread_id is not None
                    and self.turn_id is not None and params.get('threadId') == self.thread_id
                    and params.get('turnId') == self.turn_id):
                from regen.infrastructure.provider_http_failure import classify_provider_http_failure
                self._event('local', {'event':'provider_stream_retrying',
                    'thread_id':self.thread_id, 'turn_id':self.turn_id,
                    'failure':classify_provider_http_failure(error)})
                self._begin_stream_retry(error)
                return
            if (params.get('threadId') == self.thread_id
                    and params.get('turnId') == self.turn_id
                    and isinstance(error, dict)
                    and error.get('codexErrorInfo') == 'serverOverloaded'):
                # A terminal capacity rejection can leave an unfinished model
                # item in the stream. Charge work to this boundary and retry a
                # new thread; the missing item/completed is expected here.
                if self._feedback_retry_mode:
                    self._raise_feedback_failure({'kind':'provider_capacity', 'http_status':None},
                        source='error_notification')
                self._overload_interrupted = True
                # Keep provider diagnostics in the private transcript. The
                # coordinator's events/results must never serialize raw error
                # objects, which may contain credential or endpoint details.
                raise ProviderOverloaded('provider capacity rejection')
            if ((self._http_retry_mode or self._feedback_retry_mode) and params.get('threadId') == self.thread_id
                    and params.get('turnId') == self.turn_id
                    and self.thread_id is not None and self.turn_id is not None):
                self._raise_http_failure(error, source='error_notification')
            info = error.get('codexErrorInfo') if isinstance(error, dict) else None
            network_info = {'responseStreamDisconnected', 'responseStreamConnectionFailed'}
            details = str(error).lower()
            network_failure = (isinstance(info, str) and info in network_info) or (
                isinstance(info, dict) and bool(network_info & set(info))) or any(
                    text in details for text in ('tls handshake eof',
                    'stream disconnected before completion', 'error sending request',
                    'transport error: network error', 'waiting for network'))
            if (network_failure and params.get('threadId') == self.thread_id
                    and params.get('turnId') == self.turn_id):
                from regen.infrastructure.progress_events import emit_event
                # The parallel budget observer checkpoints and cancels the
                # experiment here. Standalone clients retain their retry policy.
                emit_event('generation_network_pause_requested',
                    generation_id=self._generation_id, thread_id=self.thread_id,
                    turn_id=self.turn_id, error=deepcopy(error),
                    active_request_id=self._active_request_id,
                    callback_executing=self._callback_executing,
                    transcript_path=str(self._artifact_dir/'session-transcript.jsonl')
                        if self._artifact_dir else None, network_pause_policy=1)
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise SessionCancelled('provider network failure; experiment checkpointed for resume')
            raise SessionProtocolError(str(params))

    def _start(self, artifact_dir, model, deadline):
        if not self.allow_external_call:
            raise PermissionError('provider launch disabled; explicitly authorize audited-tool-allowlist mode')
        self._artifact_dir = Path(artifact_dir).resolve()
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._log = open(self._artifact_dir/'session-transcript.jsonl', 'x', encoding='utf-8')
        self._stderr = open(self._artifact_dir/'app-server.stderr', 'xb')
        self._temporary = tempfile.TemporaryDirectory(prefix='regen-codex-workspace-')
        base = Path(self._temporary.name)
        self.cwd = base/'cwd'; self.cwd.mkdir()
        home = Path(self.codex_home).resolve() if self.codex_home else base/'codex-home'
        home.mkdir(exist_ok=True)
        if any(p.name != 'auth.json' or p.is_symlink() or not p.is_file() for p in home.iterdir()):
            raise ValueError('clean codex_home may contain only auth.json; do not pass user configuration home')
        env = dict(os.environ)
        env['CODEX_HOME'] = str(home)
        command = self.command or [self.codex_bin, 'app-server', '--stdio',
            '-c', 'web_search="disabled"', '-c', 'approval_policy="never"',
            '-c', 'sandbox_mode="read-only"', '-c', 'project_doc_max_bytes=0']
        if self.command is None:
            for feature in ('shell_tool','unified_exec','apps','plugins','remote_plugin','hooks',
                            'browser_use','computer_use','code_mode','view_image',
                            'multi_agent','multi_agent_v2','image_generation','skill_search','workspace_dependencies'):
                command += ['--disable', feature]
            # The model may route registered dynamic tools through this host even
            # when optional code_mode is off. It does not grant shell access;
            # callbacks and observable items still pass the existing allowlist.
            command += ['--enable', 'code_mode_host']
        executable = Path(shutil.which(command[0]) or command[0]).resolve()
        if self.command is None:
            from regen.infrastructure.audited_llm import model_settings
            from regen.infrastructure.codex_runtime import runtime_verified_settings
            settings = model_settings()
            pinned = runtime_verified_settings(settings)
            allowed = {Path(settings['codex_bin']).resolve(), Path(pinned['codex_bin'])}
            if executable not in allowed:
                raise CodexRuntimeError('workspace launcher executable differs from frozen configuration',
                                        path=executable)
            executable = Path(pinned['codex_bin'])
        executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        command = [str(executable), *command[1:]]
        isolation = None
        transport_command = command
        if self.command is None:
            from regen.infrastructure.audited_llm import model_settings
            from regen.infrastructure.model_isolation import ModelIsolation
            policy = model_settings().get('model_isolation', {})
            if policy.get('required') is not True:
                raise SessionProtocolError('real workspace provider requires configured OS isolation')
            self._isolation = ModelIsolation(base=base,client=executable,cwd=self.cwd,
                home=home,image=policy['image'],proxy=policy.get('transport_proxy'))
            isolation = self._isolation.evidence()
            transport_command = self._isolation.command(command)
        self._event('local', {'event':'launch', 'command':command, 'model':model,
                              'provider':'codex-app-server', 'synthetic':self.command is not None,
                              'executable_sha256':executable_hash,
                              'sleep_policy_version':SLEEP_POLICY_VERSION,
                              'callback_event_reader':'serial-owner-with-reader-v1',
                              'pending_terminal_policy_version':2,
                              'progress_watch_timeout':self._progress_timeout,
                              'tool_argument_policy_version':2,
                              'tool_result_confirmation_policy_version':1,
                              'trusted_failure_interrupt_policy_version':1,
                              'model_item_diagnostics_policy_version':1,
                              'assurance':'os-isolated-tool-allowlist' if isolation else 'audited-tool-allowlist',
                              'exclusive_os_isolation':bool(isolation), 'isolation':isolation})
        self.process = subprocess.Popen(transport_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self._stderr, cwd=self.cwd, env=env, start_new_session=True)
        self._selector = selectors.DefaultSelector()
        self._selector.register(self.process.stdout, selectors.EVENT_READ)
        self._await(self._request('initialize', {'clientInfo':{'name':'regen-proof-workspace','version':'1'},
                    'capabilities':{'experimentalApi':True}}), deadline)
        self._send({'method':'initialized'})
        result = self._await(self._request('thread/start', {'model':model, 'cwd':str(self.cwd),
            'sandbox':'read-only', 'approvalPolicy':'never', 'ephemeral':True,
            'allowProviderModelFallback':False, 'environments':[], 'selectedCapabilityRoots':[],
            'dynamicTools':[{'type':'function', **tool} for tool in self.tools]}), deadline)
        self.thread_id = result['thread']['id']

    def _tool_call(self, message, handler, deadline):
        params = message['params']
        key = self._validate_tool_request(message)
        self._finish_stream_retry()
        name, arguments, call_id = params['tool'], params['arguments'], params['callId']
        if key in self._calls:
            raise SessionProtocolError('duplicate call ID; callback is never automatically replayed')
        self._provider_request_finish('success', response_kind='tool_request', call_id=call_id)
        metadata = {'thread_id':self.thread_id, 'turn_id':self.turn_id, 'call_id':call_id,
                    'raw_request':deepcopy(message),
                    'transcript_path':str(self._artifact_dir/'session-transcript.jsonl'),
                    'transcript_offset':os.fstat(self._log.fileno()).st_size,
                    'tool_request_offset':self._request_offsets.pop(id(message)),
                    'callback_timeout_policy':'trusted harness owns native/action deadlines'}
        self._calls[key] = 'started'
        self._event('local', {'event':'tool_started', **{k:v for k,v in metadata.items() if k != 'raw_request'}})
        started = time.monotonic()
        succeeded = False
        value = None
        self._active_request_id = message['id']
        self._callback_reader_error = None
        self._callback_reader_stop = threading.Event()
        callback_context = copy_context()
        self._callback_reader = threading.Thread(target=callback_context.run,
            args=(self._pump_callback_events,),
            name='workspace-protocol-reader', daemon=True)
        try:
            self._preflight_callback()
            self._begin_model_callback()
            self._callback_reader.start()
            self._check_callback_abort()
            try:
                try:
                    self.validators[name].validate(arguments)
                except ValidationError as error:
                    rejection = {'error_type':'ToolArgumentValidationError',
                        'code':'invalid_tool_arguments', 'message':error.message,
                        'path':list(error.absolute_path),
                        'schema_path':list(error.absolute_schema_path)}
                    self._event('local', {'event':'tool_arguments_rejected',
                        'thread_id':self.thread_id, 'turn_id':self.turn_id,
                        'call_id':call_id, 'tool':name, 'error':rejection})
                    response = {'success':False, 'contentItems':[{'type':'inputText',
                        'text':json.dumps(rejection, ensure_ascii=False)}]}
                else:
                    value = handler(name, deepcopy(arguments), metadata)
                    succeeded = True
                    response = {'success':True, 'contentItems':[{'type':'inputText','text':json.dumps(value, ensure_ascii=False)}]}
            except Exception as error:
                from regen.execution.check_tasks import NativePreparationError
                from regen.infrastructure.resource_budget import ResourceBudgetError
                if isinstance(error, (NativePreparationError, CodexRuntimeError, ResourceBudgetError)):
                    raise  # Preparation failure is not a candidate proof failure.
                rejection = {'error_type':type(error).__name__, 'message':str(error)}
                from regen.planning.theory_document import DocumentEditError
                if isinstance(error, DocumentEditError):
                    rejection.update(code=error.code, details=deepcopy(error.details))
                response = {'success':False, 'contentItems':[{'type':'inputText','text':json.dumps(
                    rejection, ensure_ascii=False)}]}
        except BaseException as error:
            self._runtime_failed(error)
            self._notify_abort()
            raise
        finally:
            self._stop_callback_reader()
            self._end_model_callback()
            self._active_request_id = None
        self._check_callback_abort()
        self._calls[key] = 'finished'
        self._event('local', {'event':'tool_finished', 'call_id':call_id, 'response':response})
        response_hash = hashlib.sha256(json.dumps(response, sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self._tool_results[key] = {'state': 'persisted', 'response': deepcopy(response),
            'response_sha256': response_hash, 'rpc_id': message['id'], 'tool': name}
        self._event('local', {'event': 'tool_result_persisted', 'thread_id': key[0],
            'turn_id': key[1], 'call_id': key[2], 'rpc_id': message['id'],
            'response_sha256': response_hash})
        self._send({'id':message['id'], 'result':response})
        self._tool_results[key]['state'] = 'sent'
        self._event('local', {'event': 'tool_result_sent', 'thread_id': key[0],
            'turn_id': key[1], 'call_id': key[2], 'rpc_id': message['id'],
            'response_sha256': response_hash})
        self._confirm_tool_result(key)
        self._diagnose_open_messages('tool_result_sent')
        if self._deferred_terminal is None:
            self._provider_request_begin('tool_result_sent')
        if self._http_retry_mode and not self._transport_progress_reported:
            from regen.infrastructure.progress_events import emit_event
            emit_event('provider_transport_progress', generation_id=self._generation_id,
                       thread_id=self.thread_id, turn_id=self.turn_id)
            self._transport_progress_reported = True
        if self._progress is not None:
            if succeeded:
                self._progress.observe(name, arguments, value, time.monotonic() - started)
            else:
                self._progress.last_progress += time.monotonic() - started
        return value if succeeded else None

    def run(self, prompt, on_tool_call, *, artifact_dir, model, reasoning_effort='medium',
            timeout=1800, cancel_event=None, on_abort=None, on_notification=None,
            stop_when=None):
        if self._closed or self._broken:
            raise RuntimeError('session is closed or failed; automatic resume is forbidden')
        if timeout <= 0:
            raise ValueError('timeout must be positive')
        self._timeout = timeout
        if self._progress is None and self._progress_timeout != 0:
            progress_timeout = self._progress_timeout if self._progress_timeout is not None else timeout
            if progress_timeout <= 0:
                raise ValueError('progress timeout must be positive')
            self._progress = ProgressWatch(progress_timeout)
        progress_before = self._progress.generation if self._progress else None
        self._cancel_event = cancel_event
        self._on_abort = on_abort
        self._on_notification = on_notification
        deadline = time.monotonic()+timeout
        self._expired.clear()
        old_term = None
        if threading.current_thread() is threading.main_thread():
            old_term = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self._cancel_signal)
        try:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise SessionCancelled('workspace coordinator cancelled')
            if self.process is None:
                self._start(artifact_dir, model, deadline)
            elif Path(artifact_dir).resolve() != self._artifact_dir:
                raise ValueError('a persistent session has one transcript artifact directory')
            self.turn_id = None
            self._awaiting_turn_response = True
            try:
                self._generation_start()
                self._provider_request_begin('turn_start')
                turn = self._await(self._request('turn/start', {'threadId':self.thread_id,
                    'input':[{'type':'text','text':prompt}], 'model':model, 'effort':reasoning_effort,
                    'approvalPolicy':'never', 'environments':[]}), deadline)
            finally:
                self._awaiting_turn_response = False
            if self.turn_id is not None and self.turn_id != turn['turn']['id']:
                raise SessionProtocolError('turn start notification and response identity mismatch')
            self.turn_id = turn['turn']['id']
            pending, self._pending_turn_notifications = self._pending_turn_notifications, []
            if any(message['params']['turnId'] != self.turn_id for message in pending):
                raise SessionProtocolError('dynamic tool item belongs to another thread/turn')
            for message in pending:
                self._notification(message)
            while True:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise SessionCancelled('workspace coordinator cancelled')
                if self._deferred:
                    message = self._deferred.pop(0)
                elif self._deferred_terminal is not None:
                    message, self._deferred_terminal = self._deferred_terminal, None
                else:
                    message = self._receive(deadline)
                if message.get('method') == 'item/tool/call':
                    value = self._tool_call(message, on_tool_call, deadline)
                    stop_reason = stop_when(value) if stop_when is not None else None
                    if stop_reason:
                        if not isinstance(stop_reason, str):
                            raise ValueError(
                                'stop_when must return a string reason or false')
                        self._generation_stop(stop_reason, handler_stop=True)
                        result = {
                            'thread_id': self.thread_id,
                            'turn_id': self.turn_id,
                            'status': 'stopped_by_handler',
                            'stop_reason': stop_reason,
                            'usage': deepcopy(self._usage),
                            'assurance': (
                                'os-isolated-tool-allowlist'
                                if self._isolation else
                                'audited-tool-allowlist'),
                            'exclusive_os_isolation': bool(self._isolation),
                            'tool_result_confirmation': self._result_confirmation_summary(),
                        }
                        if self._progress is not None:
                            result['progress_made'] = (
                                self._progress.generation != progress_before)
                        self._event('local', {
                            'event': 'turn_stopped_by_handler', **result})
                        return result
                    continue
                self._notification(message)
                if message.get('method') == 'turn/completed':
                    params = message['params']
                    if params.get('threadId') != self.thread_id or params['turn']['id'] != self.turn_id:
                        raise SessionProtocolError('terminal turn identity mismatch')
                    if params['turn']['status'] != 'completed':
                        self._raise_http_failure(params['turn'].get('error'), source='failed_turn')
                        raise SessionProtocolError('turn ended without completion: '+str(params['turn']))
                    self._finish_stream_retry()
                    self._finish_model_work(clear_items=True)
                    if any(key[:2] == (self.thread_id, self.turn_id)
                           for key in self._sleep_state.get('pending', {})):
                        raise SessionProtocolError('turn completed with unfinished sleep item')
                    self._provider_request_finish('success', response_kind='turn_completed')
                    result = {'thread_id':self.thread_id, 'turn_id':self.turn_id, 'turn':params['turn'],
                              'status':params['turn']['status'], 'usage':deepcopy(self._usage),
                              'assurance':'os-isolated-tool-allowlist' if self._isolation else 'audited-tool-allowlist',
                              'exclusive_os_isolation':bool(self._isolation)}
                    result['tool_result_confirmation'] = self._result_confirmation_summary()
                    if self._progress is not None:
                        result['progress_made'] = self._progress.generation != progress_before
                    self._event('local', {'event':'turn_completed', **result})
                    return result
        except BaseException as error:
            self._runtime_failed(error)
            interrupted = False
            from regen.execution.check_tasks import NativePreparationError
            if isinstance(error, NativePreparationError):
                try:
                    interrupted = self._interrupt_native_failure(error)
                except BaseException as interrupt_error:
                    error.add_note('trusted failure interruption was not confirmed: ' + str(interrupt_error))
                    self._event('local', {'event': 'trusted_failure_interrupt_unconfirmed',
                        'exception_type': type(interrupt_error).__name__,
                        'thread_id': self.thread_id, 'turn_id': self.turn_id,
                        'message': str(interrupt_error), 'refund_seconds': 0})
            try:
                self._generation_stop(type(error).__name__, handler_stop=interrupted)
            except BaseException as timing_error:
                error.add_note('generation timing close failed: '+str(timing_error))
            self._broken = True
            self._notify_abort()
            if self._abort_failure is not None:
                error.add_note('callback abort failed: '+str(self._abort_failure))
            self._event('local', {'event':'session_failed', 'nonsealable':True,
                                  'exception_type':type(error).__name__, 'message':str(error)})
            self._close_preserving_error(error)
            raise
        finally:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)

    def _cancel_signal(self, signum, frame):
        raise SessionCancelled('workspace session cancelled by signal '+str(signum))

    def _stop_process(self):
        if self.process is not None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                self.process.wait(timeout=2)
            # The leader can exit before a descendant that ignores SIGTERM.
            try: os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError: pass

    def close(self):
        if self._closed:
            return
        timing_error = None
        try:
            self._generation_stop('session_closed')
        except BaseException as error:
            timing_error = error
        self._stop_callback_reader()
        try:
            self._stop_process()
        finally:
            if self._isolation is not None:
                self._isolation.close()
        if self._selector is not None: self._selector.close()
        if self.process is not None:
            self.process.stdin.close(); self.process.stdout.close()
        if self._stderr is not None: self._stderr.close()
        if self._temporary is not None: self._temporary.cleanup()
        if self._log is not None:
            self._event('local', {'event':'session_closed', 'nonsealable':self._broken or self._expired.is_set()})
            self._log.close()
        self._closed = True
        if timing_error is not None:
            raise timing_error
