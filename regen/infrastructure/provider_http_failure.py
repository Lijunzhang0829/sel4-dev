"""Conservative classification of provider error notifications, without secrets.

This helper does not authorize retries or bind a notification to a session. Its
caller must validate thread/turn ownership and apply the frozen retry policy.
Returned fields contain only an enumerated kind and an optional HTTP status;
raw diagnostics, endpoint URLs and credentials never enter derived metadata.
"""
import re


_VARIANTS = {
    'serverOverloaded': 'provider_capacity',
    'httpConnectionFailed': 'http_connection',
    'responseStreamDisconnected': 'response_stream',
    'responseStreamConnectionFailed': 'response_stream',
}
_TRANSPORT_PHRASES = (
    'tls handshake eof',
    'stream disconnected before completion',
    'error sending request',
    'transport error: network error',
    'waiting for network',
    'idle timeout waiting for websocket',
    'connection reset by peer',
    'connection refused',
    'temporary failure in name resolution',
)
# Status recognition is deliberately not a search for any bare HTTP/code pair:
# proof diagnostics can quote URLs or discuss HTTP without a provider failure.
_HTTP_STATUS = re.compile(
    r'^\s*(?:(?:request|response)\s+failed\s*(?:with\s+)?)?'
    r'(?:http(?:/\d(?:\.\d)?)?(?:\s+(?:status(?:\s+code)?|error))?'
    r'|unexpected\s+(?:http\s+)?status(?:\s+code)?)\s*[:=]?\s*([45]\d\d)\b',
    re.IGNORECASE,
)


def _status(value):
    # JSON booleans are ints in Python, but are not status codes. Do not silently
    # coerce arbitrary strings from model/provider diagnostics into authority.
    return value if type(value) is int and 400 <= value <= 599 else None


def classify_provider_http_failure(error):
    """Return ``{kind, http_status}`` for a recognized HTTP/transport error.

    Structured app-server error variants have precedence over text. A null
    ``httpStatusCode`` still denotes transport failure for a recognized variant.
    Both 4xx and 5xx qualify: retryability and the single-retry bound are policy,
    not this classifier's decision. Generic timeouts, overload messages and
    arbitrary proof/protocol failures without HTTP evidence remain unclassified.
    """
    if isinstance(error, dict):
        info = error.get('codexErrorInfo')
        status = _status(error.get('httpStatusCode'))
        if isinstance(info, str) and info in _VARIANTS:
            return {'kind': _VARIANTS[info], 'http_status': status}
        if isinstance(info, dict):
            status = _status(info.get('httpStatusCode')) or status
            for variant, kind in _VARIANTS.items():
                if variant in info:
                    details = info[variant]
                    if isinstance(details, dict):
                        status = _status(details.get('httpStatusCode')) or status
                    return {'kind': kind, 'http_status': status}
            # Other structured variants (e.g. a capacity error) can carry a
            # concrete HTTP status. Inspect one variant level, never recurse
            # into arbitrary proof payloads or stringify an entire dictionary.
            for details in info.values():
                if isinstance(details, dict):
                    status = _status(details.get('httpStatusCode')) or status
        if status is not None:
            return {'kind': 'http_status', 'http_status': status}
        texts = [error.get('message'), error.get('additionalDetails')]
    elif isinstance(error, str):
        texts = [error]
    else:
        return None
    for text in texts:
        if not isinstance(text, str):
            continue
        match = _HTTP_STATUS.match(text)
        if match:
            return {'kind': 'http_status', 'http_status': int(match[1])}
        if any(phrase in text.lower() for phrase in _TRANSPORT_PHRASES):
            return {'kind': 'transport', 'http_status': None}
    return None


RECOVERY_POLICY = 'provider-recovery-v2'


def recovery_streak(rows, theory, failure=None):
    """Only consecutive *current-policy* failures are backoff state.

    Historical lifetime allowances remain evidence, never authorization limits.
    A completed tool exchange establishes a recovered transport, not a proof.
    """
    count = 0
    last_failure = None
    for row in rows:
        data = row['data']
        if data.get('theory') != theory:
            continue
        if row['kind'] == 'provider_transport_progress':
            count = 0
            last_failure = None
        elif (row['kind'] == 'provider_http_retry_scheduled'
              and data.get('retry_policy') == RECOVERY_POLICY):
            count = data['consecutive_failures']
            last_failure = data.get('failure')
    # A newly observed permanent status must itself receive the promised retry;
    # prior timeouts/503s do not spend a later 401's diagnostic retry.
    if (failure is not None and failure.get('http_status') is not None
            and 400 <= failure['http_status'] < 500
            and failure['http_status'] not in (408, 409, 425, 429)
            and failure != last_failure):
        return 0
    return count


def recovery_decision(failure, prior_failures, *, enabled, cleanup_failed=False,
                      elapsed_seconds=None, window_seconds=None,
                      pause_auth_immediately=False):
    """Choose a fresh transport retry without replaying a harness callback.

    Existing callers retain the unbounded transient policy unless they freeze a
    recovery window. A bounded caller measures the *same consecutive incident*
    with a monotonic clock and resets it only after transport progress. Explicit
    authentication failures may be paused immediately instead of spending a
    diagnostic retry on credentials that require operator action.
    """
    if cleanup_failed:
        return {'retry': False, 'reason': 'provider_http_cleanup_failed'}
    status = failure.get('http_status')
    if pause_auth_immediately and status in (401, 403):
        return {'retry': False, 'reason': 'provider_authentication_required'}
    if not enabled:
        return {'retry': False, 'reason': 'provider_http_retry_disabled'}
    transient = status is None or status >= 500 or status in (408, 409, 425, 429)
    if not transient and prior_failures >= 1:
        return {'retry': False, 'reason': 'provider_http_requires_intervention'}
    delay = min(300, 30 * 2 ** min(prior_failures, 4))
    if window_seconds is not None:
        import math
        if (type(window_seconds) not in (int, float) or not math.isfinite(window_seconds)
                or window_seconds <= 0 or type(elapsed_seconds) not in (int, float)
                or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0):
            raise ValueError('recovery window and elapsed time must be finite and positive')
        # Do not schedule a wait that would consume the last instant available
        # for a fresh transport attempt. A native callback is never interrupted
        # by this administrative retry window.
        if elapsed_seconds + delay >= window_seconds:
            return {'retry': False, 'reason': 'provider_recovery_window_exhausted'}
    return {'retry': True, 'consecutive_failures': prior_failures + 1,
            'delay_seconds': delay,
            'retry_policy': RECOVERY_POLICY}
