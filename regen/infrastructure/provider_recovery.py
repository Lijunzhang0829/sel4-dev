"""Classify saved provider failures without interpreting model reasoning."""
import json
from pathlib import Path


class ProviderUnavailable(RuntimeError):
    pass


def transient_failure(prefix):
    prefix = Path(prefix)
    try:
        meta = json.loads(prefix.with_suffix('.meta.json').read_text())
    except (OSError, ValueError):
        return False
    if meta.get('interrupted') or meta.get('returncode') == 0:
        return False
    if meta.get('timed_out'):
        return True
    try:
        stderr = prefix.with_suffix('.stderr.txt').read_text()
    except OSError:
        return False
    return any(s in stderr for s in ('idle timeout waiting for websocket',
        'stream disconnected before completion', 'Connection reset by peer',
        'temporarily unavailable', 'HTTP 429', 'HTTP 503'))
