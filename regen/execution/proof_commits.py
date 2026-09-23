"""Durable acceptance journal. Candidate files and pointers are not authorities."""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile


# Sharing is opt-in: callers identify the complete validation environment and
# validator implementation in cache_namespace. This cache dies on restart.
_VERIFIED_PREFIXES = OrderedDict()
_MAX_VERIFIED_PREFIXES = 8


def _fingerprint(path):
    info = path.stat()
    return (path.name, info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def durable(path: Path, value, *, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode()
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(name, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError(f'immutable artifact collision: {path}')
        else:
            os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


class ProofCommits:
    """Replay validated commits, optionally sharing a process-local prefix cache.

    cache_namespace must identify the validator's implementation and all semantic
    inputs; changing either requires a new namespace. Without it, caches belong
    only to this instance and are invalidated when its validator is replaced.
    The normal single-writer journal lock remains required for append operations.
    """
    def __init__(self, directory: Path, binding: dict, validate, *, cache_namespace=None):
        self.directory = directory
        self.binding = binding
        self.validate = validate
        if cache_namespace is not None and not isinstance(cache_namespace, str):
            raise TypeError('cache_namespace must explicitly identify validation as a string')
        self.cache_namespace = cache_namespace
        self._verified = None
        self._cache_identity = None

    def _cache_key(self):
        return (str(self.directory.resolve()), self.cache_namespace, digest(self.binding))

    def _remember(self, fingerprints, state, previous):
        snapshot = {'fingerprints': fingerprints, 'state': deepcopy(state), 'commit_id': previous}
        self._verified = snapshot
        self._cache_identity = (self._cache_key(), self.validate)
        # Empty theories have no validated evidence to retain and must not
        # evict active histories during a repository-wide progress traversal.
        if self.cache_namespace is not None and previous is not None:
            key = self._cache_key()
            _VERIFIED_PREFIXES[key] = snapshot
            _VERIFIED_PREFIXES.move_to_end(key)
            while len(_VERIFIED_PREFIXES) > _MAX_VERIFIED_PREFIXES:
                _VERIFIED_PREFIXES.popitem(last=False)

    def replay(self):
        paths = sorted((self.directory/'commits').glob('*.json'))
        fingerprints = tuple(_fingerprint(path) for path in paths)
        key = self._cache_key()
        cached = self._verified if self._cache_identity == (key, self.validate) else None
        if self.cache_namespace is not None:
            shared = _VERIFIED_PREFIXES.get(key)
            # LRU eviction does not invalidate this object's verified prefix.
            # Retain the longest known prefix so a shorter shared snapshot
            # cannot hide deletion of previously observed commits.
            if shared is not None and (cached is None or
                    len(shared['fingerprints']) >= len(cached['fingerprints'])):
                cached = shared
        start = 0
        previous = None; state = None
        if cached is not None:
            prefix = cached['fingerprints']
            if len(prefix) > len(fingerprints):
                raise ValueError('committed history is missing from verified prefix')
            if len(prefix) <= len(fingerprints) and fingerprints[:len(prefix)] == prefix:
                start = len(prefix)
                previous = cached['commit_id']
                state = deepcopy(cached['state'])
        for number, path in enumerate(paths[start:], start+1):
            record = json.loads(path.read_text())
            if _fingerprint(path) != fingerprints[number-1]:
                raise ValueError('proof commit changed during replay')
            body = {k:v for k,v in record.items() if k != 'commit_id'}
            if (path.name != f'{number:06d}.json' or record['sequence'] != number
                    or record['parent'] != previous or record['binding'] != self.binding
                    or record['commit_id'] != digest(body)):
                raise ValueError('invalid proof commit chain/binding')
            self.validate(state, record['state'], record['event'], record['evidence'])
            state = record['state']; previous = record['commit_id']
        head_path = self.directory/'HEAD.json'
        head = {}
        if head_path.exists():
            try: head = json.loads(head_path.read_text())
            except (ValueError, OSError): pass
            if head.get('sequence', 0) > len(paths):
                raise ValueError('committed history is missing; cannot recover from pointer alone')
        if previous:
            expected = {'commit_id': previous, 'sequence': len(paths)}
            if head != expected:
                durable(head_path, expected)
        self._remember(fingerprints, state, previous)
        return state

    def append(self, event, state, evidence):
        old = self.replay()
        self.validate(old, state, event, evidence)
        paths = sorted((self.directory/'commits').glob('*.json'))
        parent = self._verified['commit_id']
        body = {'sequence':len(paths)+1, 'parent':parent, 'binding':self.binding,
                'event':event, 'state':state, 'evidence':evidence}
        record = {**body, 'commit_id':digest(body)}
        path = self.directory/'commits'/f'{len(paths)+1:06d}.json'
        durable(path, record, immutable=True)
        durable(self.directory/'HEAD.json', {'commit_id':record['commit_id'], 'sequence':len(paths)+1})
        self._remember(self._verified['fingerprints'] + (_fingerprint(path),), state, record['commit_id'])
        return deepcopy(state)


def freeze_evidence(value):
    """Embed checker source/log bytes before their mutable filenames are reused."""
    if isinstance(value, list): return [freeze_evidence(v) for v in value]
    if not isinstance(value, dict): return value
    result = {key:freeze_evidence(v) for key,v in value.items()}
    for key in ('source', 'log'):
        name = value.get(key)
        if key+'_content' in value:
            raw = value[key+'_content'].encode('utf-8')
            if hashlib.sha256(raw).hexdigest() != value.get(key+'_sha256'):
                raise ValueError('embedded checker evidence bytes mismatch')
            continue  # Mutable paths may now contain a different check's bytes.
        if isinstance(name, str) and Path(name).is_file():
            raw = Path(name).read_bytes()
            result[key+'_content'] = raw.decode('utf-8')
            result[key+'_sha256'] = hashlib.sha256(raw).hexdigest()
    return result
