"""Conservative provider-failure policy for the original VERO Codex CLI.

Only top-level ``codex exec --json`` error/turn.failed envelopes and anchored
CLI stderr diagnostics are evidence. Proof output and model prose are never
searched for status codes. The result contains enums/status only; raw provider
messages and credential material must remain out of derived experiment events.

This module is pure. The VERO launcher must separately authenticate the frozen
runner checkpoint, stop/clean the old process, persist usage and budget state,
and then start a *new* CLI invocation. No pending tool call may be replayed.
"""
import re

from regen.infrastructure.provider_http_failure import (
    classify_provider_http_failure, recovery_decision,
)


RECOVERY_WINDOW_SECONDS = 1800

_ERROR_PREFIX = re.compile(r'^\s*(?:error|request failed|response failed)\s*:\s*', re.I)
_BARE_STATUS = re.compile(r'^\s*(401|403|429|500|502|503|504)\b', re.I)
_OVERLOAD = re.compile(
    r'^\s*(?:the\s+)?(?:provider|server)\s+(?:is\s+)?overloaded(?:\b|[.!])', re.I)
_DISCONNECT = re.compile(
    r'^\s*(?:response\s+)?stream\s+disconnected(?:\b|[.!])', re.I)
_CONNECTION_FAILED = re.compile(r'^\s*connection\s+failed\s*[.!]?\s*$', re.I)


def _normalize_error(error):
    if not isinstance(error, dict):
        return error
    normalized = {}
    for original, canonical in (
        ('codexErrorInfo', 'codexErrorInfo'),
        ('codex_error_info', 'codexErrorInfo'),
        ('httpStatusCode', 'httpStatusCode'),
        ('http_status_code', 'httpStatusCode'),
        ('message', 'message'),
        ('additionalDetails', 'additionalDetails'),
    ):
        if original in error and canonical not in normalized:
            normalized[canonical] = error[original]
    return normalized


def _failure(error):
    normalized = _normalize_error(error)
    failure = classify_provider_http_failure(normalized)
    if failure is not None:
        return failure
    text = normalized.get('message') if isinstance(normalized, dict) else normalized
    if not isinstance(text, str):
        return None
    text = _ERROR_PREFIX.sub('', text, count=1)
    failure = classify_provider_http_failure(text)
    if failure is not None:
        return failure
    status = _BARE_STATUS.match(text)
    if status:
        return {'kind':'http_status', 'http_status':int(status[1])}
    if _OVERLOAD.match(text):
        return {'kind':'provider_capacity', 'http_status':None}
    if _DISCONNECT.match(text) or _CONNECTION_FAILED.match(text):
        return {'kind':'response_stream', 'http_status':None}
    return None


def _category(failure):
    if failure is None:
        return 'unknown'
    status = failure['http_status']
    if status in (401, 403):
        return 'auth'
    if (status is None or status >= 500 or status in (408, 409, 425, 429)):
        return 'transient'
    return 'unknown'


def classify_vero_cli_failure(events, stderr_text=''):
    """Return ``{category, failure, evidence}`` for one failed CLI invocation.

    A terminal turn.failed supersedes earlier error notifications. Its unknown
    payload is deliberately *not* rescued by an earlier transient notification:
    the CLI may have recovered that notification before failing for another
    reason. Stderr is a fallback only if no top-level error event exists.
    """
    terminal = None
    notification = None
    saw_terminal = False
    saw_notification = False
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'turn.failed':
            terminal = event.get('error')
            saw_terminal = True
        elif event.get('type') == 'error':
            notification = event.get('error', event.get('message'))
            saw_notification = True
    if saw_terminal or saw_notification:
        failure = _failure(terminal if saw_terminal else notification)
        return {'category':_category(failure), 'failure':failure,
                'evidence':'turn.failed' if saw_terminal else 'error'}
    if isinstance(stderr_text, str):
        for line in reversed(stderr_text[-4096:].splitlines()):
            if not _ERROR_PREFIX.match(line):
                continue
            failure = _failure(line)
            if failure is not None:
                return {'category':_category(failure), 'failure':failure,
                        'evidence':'stderr'}
    return {'category':'unknown', 'failure':None, 'evidence':'none'}


def vero_cli_recovery_decision(classification, prior_failures, elapsed_seconds,
                               *, checkpoint_verified, owner_cleaned=True,
                               enabled=True):
    """Authorize only bounded *new-invocation* recovery from a trusted state."""
    if not checkpoint_verified:
        return {'retry':False, 'reason':'provider_checkpoint_unverified'}
    if not owner_cleaned:
        return {'retry':False, 'reason':'provider_owner_cleanup_failed'}
    category = classification.get('category')
    if category == 'unknown':
        return {'retry':False, 'reason':'provider_failure_unclassified'}
    if category not in ('auth', 'transient'):
        raise ValueError('invalid VERO CLI provider failure category')
    failure = classification.get('failure')
    if not isinstance(failure, dict):
        raise ValueError('classified provider failure requires structured evidence')
    return recovery_decision(failure, prior_failures, enabled=enabled,
        elapsed_seconds=elapsed_seconds, window_seconds=RECOVERY_WINDOW_SECONDS,
        pause_auth_immediately=True)
