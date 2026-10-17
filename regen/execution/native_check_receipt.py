"""Comparable, non-authoritative metadata for native Isabelle invocations.

Proof acceptance continues to use the original checker evidence and audit.  This
receipt only binds timing and failure classification to the exact native input.
"""
import hashlib
import json
from pathlib import Path
from uuid import uuid4


def _sha(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def checker_binding(checker):
    """Hash stable prover configuration, never mutable worker or log paths."""
    imports = getattr(checker, 'imports', None)
    value = {
        'image': getattr(checker, 'image', None),
        'base_session': getattr(checker, 'base_session', None),
        'base_import': getattr(checker, 'base_import', None),
        'previous_theories': list(getattr(checker, 'previous_theories', ())),
        'imports': imports() if callable(imports) else None,
        'architecture': getattr(checker, 'architecture', None),
        'checker_context_id': getattr(checker, 'checker_context_id', None),
    }
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':')))


def native_check_receipt(checker, result, *, source, scope, log_text=None,
                         request_id=None, prefix_sha256=None, delta_sha256=None,
                         prefix_prepare_ms=None, candidate_execution_ms=None):
    """Return a normalized receipt without changing a native verdict.

    ``scope`` distinguishes a cold full-theory check from an incremental suffix
    and from a session build.  A timed-out prefix is infrastructure preparation;
    a timed-out candidate is proof feedback.  Unknown timing stays null.
    """
    if scope not in {'cold-full-theory', 'committed-prefix-and-incremental-batch',
                     'generated-session-build', 'isolated-imports-observation',
                     'conditional-sketch-with-certified-prefix'}:
        raise ValueError('unknown native check scope')
    if log_text is None:
        log_text = Path(result['log']).read_bytes()
    if request_id is None:
        request_id = uuid4().hex
    if not isinstance(request_id, str) or not request_id:
        raise ValueError('missing native request identity')
    if isinstance(source, dict):
        source_hashes = {name: _sha(body) for name, body in sorted(source.items())}
        source_sha256 = _sha(json.dumps(source_hashes, sort_keys=True,
                                        separators=(',', ':')))
    else:
        source_hashes = None
        source_sha256 = _sha(source)
    return {
        'schema': 'native-check-receipt-v1',
        'request_id': request_id,
        'checker_binding_sha256': checker_binding(checker),
        'source_sha256': source_sha256,
        'source_files_sha256': source_hashes,
        'check_scope': scope,
        'prefix_sha256': prefix_sha256,
        'delta_sha256': delta_sha256,
        'prefix_prepare_ms': prefix_prepare_ms,
        'candidate_execution_ms': candidate_execution_ms,
        'wall_ms': result.get('wall_ms'),
        'timeout_seconds': result.get('timeout_seconds'),
        'timeout_scope': result.get('timeout_scope'),
        'returncode': result.get('returncode'),
        'verdict': result.get('verdict'),
        'log_sha256': _sha(log_text),
    }
