"""Readable model feedback; original checker receipts remain proof authority.

This module never reads or writes evidence. It projects copies at the tool
boundary, after checking and journalling have completed. Explicit raw reads and
hash-bound history pages retain their original content and coordinate system.
"""
from copy import deepcopy

from regen.execution.proof_commits import digest


DIAGNOSTIC_CHARS = 12000
_EVIDENCE_BODIES = {'source_content', 'log_content', 'audit_content',
                    'native_proof_state_full'}


def readable_diagnostic(text):
    """Remove Isabelle YXML markup, retaining term text, whitespace and symbols.

    YXML markup occupies X/Y-delimited segments. Splitting also discards an
    unfinished markup segment in an older, already-truncated diagnostic. It
    does not reinterpret Isabelle escapes such as ``\\<Longrightarrow>``.
    """
    if '\x05\x06' not in text:
        return text
    return ''.join(part for part in text.split('\x05')
                   if not part.startswith('\x06'))


def diagnostic_fields(value):
    """Return display fields without changing the original diagnostic/hash.

    ``diagnostic_rendering`` distinguishes raw evidence from the displayed text.
    In particular, raw read_diagnostic offsets must never be used as offsets in
    this rendered view. Existing excerpt metadata is retained when only an old
    truncated prefix, rather than the complete original diagnostic, is present.
    """
    raw = value.get('diagnostic')
    if not isinstance(raw, str):
        return {}
    prior_rendering = value.get('diagnostic_rendering')
    if isinstance(prior_rendering, dict) and prior_rendering.get('schema') == 'readable-check-feedback-v1':
        return {}  # The controller may project an already compacted callback.
    text = readable_diagnostic(raw)
    if text == raw and len(text) <= DIAGNOSTIC_CHARS:
        return {}
    old_excerpt = value.get('diagnostic_excerpt')
    source_incomplete = isinstance(old_excerpt, dict) and old_excerpt.get('incomplete', False)
    shown = text[:DIAGNOSTIC_CHARS]
    rendering = {
        'schema': 'readable-check-feedback-v1',
        'format': 'plain-text',
        'raw_sha256': (old_excerpt.get('sha256') if source_incomplete else digest(raw)),
        'raw_chars': (old_excerpt.get('total_chars') if source_incomplete else len(raw)),
        'source_incomplete': bool(source_incomplete),
        'readable_chars': None if source_incomplete else len(text),
        'displayed_chars': len(shown),
        'incomplete': bool(source_incomplete or len(shown) < len(text)),
        'notice': 'Display only. Original evidence and hashes are unchanged. '
                  'read_diagnostic uses original raw offsets; read_sketch/read_failure '
                  'view=raw returns the unprojected receipt.',
    }
    result = {'diagnostic': shown, 'diagnostic_rendering': rendering}
    if len(shown) < len(text):
        result['diagnostic_excerpt'] = {
            'incomplete': True, 'offset': 0, 'total_chars': None if source_incomplete else len(text),
            'sha256': digest(text), 'representation': 'readable-text',
            'notice': 'Readable excerpt; its offsets/hash describe this display, '
                      'not the raw diagnostic pages. See diagnostic_rendering for raw identity.',
        }
    return result


def raw_feedback_requested(name, arguments):
    proposal = arguments.get('proposal', {}) if isinstance(arguments, dict) else {}
    return (name == 'proof_action' and isinstance(proposal, dict)
            and proposal.get('action') in {'read_sketch', 'read_failure'}
            and proposal.get('view') == 'raw')


def model_feedback(value, *, raw=False):
    """Project default tool replies, preserving exact declarations and proofs.

    Only named checker evidence bodies are omitted. Full content remains in
    the receipts addressed by IDs and source/log/audit paths and their hashes.
    Native rule diagnostics are small structured observations and remain visible.
    No verdict, failure classification, acceptance or publication is inferred.
    """
    if raw:
        return deepcopy(value)
    if isinstance(value, list):
        return [model_feedback(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    if value.get('view') == 'full' and 'bindings' in value and 'content_id' in value:
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key in _EVIDENCE_BODIES:
            continue
        # These pages explicitly expose bound raw text; do not change offsets,
        # text, hashes, or an intentionally requested raw YXML fragment.
        if key in {'diagnostic_page', 'diagnostic_pages'}:
            result[key] = deepcopy(item)
        else:
            result[key] = model_feedback(item)
    result.update(diagnostic_fields(value))
    native = value.get('native_proof_state_full')
    if isinstance(native, dict) and isinstance(native.get('reuse_diagnostics'), dict):
        result['reuse_diagnostics'] = deepcopy(native['reuse_diagnostics'])
    return result
