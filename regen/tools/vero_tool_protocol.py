"""Bounded diagnostic views; full native evidence remains in its original file.

Offsets always count bytes in the original UTF-8 log.  Preview truncation is
explicit and callers can retrieve every byte range using ``next_offset``.
These helpers accept trusted paths; the host service owns path authorization.
"""
from __future__ import annotations

import codecs
from contextlib import contextmanager
import os
from pathlib import Path
import stat


DIAGNOSTIC_PREVIEW_BYTES = 64 * 1024
MAX_DIAGNOSTIC_CHUNK_BYTES = 1024 * 1024


def _integer(value, name, *, minimum, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(name + ' must be an integer')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(name + ' outside permitted range')
    return value


@contextmanager
def _diagnostic_stream(path):
    """Refuse symlinks and special files without a blocking or check/open race."""
    fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('diagnostic must be a regular file')
        yield stream


def preview_diagnostic(path, limit=DIAGNOSTIC_PREVIEW_BYTES):
    """Read only a bounded head and tail, returning text and retrieval metadata."""
    _integer(limit, 'limit', minimum=2, maximum=MAX_DIAGNOSTIC_CHUNK_BYTES)
    with _diagnostic_stream(path) as stream:
        total = os.fstat(stream.fileno()).st_size
        head_bytes = min(total, limit if total <= limit else limit // 2)
        head = stream.read(head_bytes)
        tail_offset = total
        tail = b''
        if total > limit:
            tail_offset = total - (limit - len(head))
            stream.seek(tail_offset)
            tail = stream.read(limit - len(head))
    truncated = total > limit
    text = head.decode('utf-8', errors='replace')
    if truncated:
        omitted = tail_offset - len(head)
        text += ('\n[VERO diagnostic preview: ' + str(omitted)
                 + ' bytes omitted; use tool.py diagnostic for the full log]\n')
        text += tail.decode('utf-8', errors='replace')
    metadata = {
        'encoding': 'utf-8', 'decode_errors': 'replace',
        'total_bytes': total, 'truncated': truncated,
        'head_bytes': len(head), 'tail_offset': tail_offset,
        'tail_bytes': len(tail), 'next_offset': len(head),
    }
    return text, metadata


def read_diagnostic_chunk(path, offset=0, limit=DIAGNOSTIC_PREVIEW_BYTES):
    """Read a bounded page without splitting valid UTF-8 during sequential reads.

``offset`` is an original-file byte offset.  Follow ``next_offset`` starting
at zero to recover the complete text.  Up to three extra bytes may be consumed
when the requested limit is too small for the first UTF-8 character.  Arbitrary
offsets inside a character and malformed UTF-8 use explicit replacement decoding.
"""
    _integer(offset, 'offset', minimum=0)
    _integer(limit, 'limit', minimum=1, maximum=MAX_DIAGNOSTIC_CHUNK_BYTES)
    with _diagnostic_stream(path) as stream:
        total = os.fstat(stream.fileno()).st_size
        if offset > total:
            raise ValueError('offset exceeds diagnostic size')
        stream.seek(offset)
        data = stream.read(min(limit, total - offset))
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        text = decoder.decode(data, final=offset + len(data) >= total)
        consumed = len(data) - len(decoder.getstate()[0])
        # A one-byte page must still advance through a multibyte character.
        while data and consumed == 0:
            extra = stream.read(1)
            if not extra:
                text += decoder.decode(b'', final=True)
                consumed = len(data)
                break
            data += extra
            text += decoder.decode(extra, final=offset + len(data) >= total)
            consumed = len(data) - len(decoder.getstate()[0])
    next_offset = offset + consumed
    return {
        'text': text, 'offset': offset, 'next_offset': next_offset,
        'returned_bytes': consumed, 'total_bytes': total,
        'eof': next_offset >= total,
        'encoding': 'utf-8', 'decode_errors': 'replace',
    }
