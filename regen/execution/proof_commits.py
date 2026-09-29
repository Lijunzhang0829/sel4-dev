"""Durable acceptance journal. Candidate files and pointers are not authorities."""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import traceback
from functools import wraps

_CACHE_LOCK = threading.RLock()
# A cold continuation record can contain gigabytes of embedded predecessor
# evidence. Serialize decoding/validation, not hot prefix reads, across journals.
_COLD_REPLAY_LOCK = threading.RLock()
_JOURNAL_LOCKS = {}
ACCEPTANCE_EVENTS = frozenset({'batch', 'document', 'green', 'seal'})


def journal_lock(directory):
    key = str(Path(directory).resolve())
    with _CACHE_LOCK:
        return _JOURNAL_LOCKS.setdefault(key, threading.RLock())


def locked_journal(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        with journal_lock(self.directory):
            return function(self, *args, **kwargs)
    return call


def commit_paths(directory):
    """Observe HEAD before listing append-only commits, never newer HEAD afterward."""
    directory = Path(directory)
    head = {}
    try:
        head = json.loads((directory/"HEAD.json").read_text())
    except (OSError, ValueError):
        pass
    paths = sorted((directory/"commits").glob("*.json"))
    if head.get("sequence", 0) > len(paths):
        raise ValueError("committed history is missing; cannot recover from pointer alone")
    return paths, head


# Sharing is opt-in: callers identify the complete validation environment and
# validator implementation in cache_namespace. This cache dies on restart.
_VERIFIED_PREFIXES = OrderedDict()
_MAX_VERIFIED_PREFIXES = 128
_HISTORICAL_STATES = OrderedDict()
_MAX_HISTORICAL_STATES = 128


def _clear_validation_tracebacks(error):
    """A saved Future exception must not retain the decoded origin evidence."""
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        if isinstance(current, json.JSONDecodeError):
            # JSONDecodeError keeps its entire input in .doc independently of
            # traceback frames. Its message/position/line/column remain intact.
            current.doc = ''
        pending.extend(value for value in (current.__cause__, current.__context__)
                       if value is not None)
        pending.extend(getattr(current, 'exceptions', ()))


def actual_dependencies(evidence, graph):
    """Extract only the small dependency projection of a validated seal."""
    result = {}
    check = evidence.get('dependencies', {})
    audit = check.get('complete_theory_audit', {})
    native = check.get('source_content', '')
    label = re.search(r'^\s*theory\s+([A-Za-z0-9_]+)', native)
    if not label or check.get('verdict') != 'green':
        return result
    names = {label[1] + '.' + n['name']: n['id'] for n in graph['nodes']}
    for key, row in audit.get('proof_dependencies', {}).items():
        result[key] = sorted({names[d['canonical_name']] for d in row.get('dependencies', [])
                              if d.get('canonical_name') in names and names[d['canonical_name']] != key})
    return result


def _record_summary(record):
    """Retain identity/dependency metadata, never source records or raw logs."""
    state = record['state']
    graph = state.get('graph') if isinstance(state, dict) else None
    identity = ({key: graph.get(key) for key in ('run_id', 'theory')}
                if isinstance(graph, dict) else None)
    return {'commit_id': record['commit_id'], 'event': record['event'],
            'graph_identity': identity,
            'inherited': record['event'] == 'initial' and any(
                key in record['evidence'] for key in ('sealed_reuse', 'continuation')),
            'actual_dependencies': (actual_dependencies(record['evidence'], graph)
                if record['event'] == 'seal' and isinstance(graph, dict) else {})}


def _fingerprint(path):
    info = path.stat()
    return (path.name, info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def digest(value):
    # Preserve canonical identities without a multi-GB string and byte copy.
    result = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    for chunk in encoder.iterencode(value):
        result.update(chunk.encode('utf-8'))
    return result.hexdigest()


def _same_file_bytes(left, right):
    """Compare a published artifact with its pending file using bounded buffers."""
    with open(left, 'rb') as first, open(right, 'rb') as second:
        while True:
            chunk = first.read(1024 * 1024)
            if chunk != second.read(1024 * 1024):
                return False
            if not chunk:
                return True


def durable(path: Path, value, *, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, indent=2)
            for chunk in encoder.iterencode(value):
                stream.write(chunk.encode('utf-8'))
            stream.write(b'\n'); stream.flush(); os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(name, path)
            except FileExistsError:
                if not _same_file_bytes(path, name):
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
        self._historical = OrderedDict()
        self._historical_identity = None

    def _cache_key(self):
        return (str(self.directory.resolve()), self.cache_namespace, digest(self.binding))

    def _historical_cache(self):
        # Caller holds _CACHE_LOCK. Unscoped validators must never share state
        # with another journal instance or survive a validator replacement.
        if self.cache_namespace is not None:
            return _HISTORICAL_STATES
        identity = (self._cache_key(), self.validate)
        if self._historical_identity != identity:
            self._historical.clear()
            self._historical_identity = identity
        return self._historical

    def _history_get(self, commit_id, fingerprint):
        key = (self._cache_key(), commit_id)
        with _CACHE_LOCK:
            cache = self._historical_cache()
            cached = cache.get(key)
            if cached is not None and cached['fingerprint'] == fingerprint:
                cache.move_to_end(key)
                return True, deepcopy(cached['state'])
        return False, None

    def _history_remember(self, commit_id, fingerprint, state):
        key = (self._cache_key(), commit_id)
        with _CACHE_LOCK:
            cache = self._historical_cache()
            cache[key] = {'fingerprint': fingerprint, 'state': deepcopy(state)}
            cache.move_to_end(key)
            while len(cache) > _MAX_HISTORICAL_STATES:
                cache.popitem(last=False)

    def _remember(self, fingerprints, state, previous, records):
        snapshot = {'fingerprints': fingerprints, 'state': deepcopy(state),
                    'commit_id': previous, 'records': tuple(records)}
        self._verified = snapshot
        self._cache_identity = (self._cache_key(), self.validate)
        # Empty theories have no validated evidence to retain and must not
        # evict active histories during a repository-wide progress traversal.
        if self.cache_namespace is not None and previous is not None:
            key = self._cache_key()
            with _CACHE_LOCK:
                _VERIFIED_PREFIXES[key] = snapshot
                _VERIFIED_PREFIXES.move_to_end(key)
                while len(_VERIFIED_PREFIXES) > _MAX_VERIFIED_PREFIXES:
                    _VERIFIED_PREFIXES.popitem(last=False)

    @locked_journal
    def replay(self, *, repair=True):
        paths, head = commit_paths(self.directory)
        fingerprints = tuple(_fingerprint(path) for path in paths)
        key = self._cache_key()
        cached = self._verified if self._cache_identity == (key, self.validate) else None
        if self.cache_namespace is not None:
            with _CACHE_LOCK:
                shared = _VERIFIED_PREFIXES.get(key)
            # LRU eviction does not invalidate this object's verified prefix.
            # Retain the longest known prefix so a shorter shared snapshot
            # cannot hide deletion of previously observed commits.
            if shared is not None and (cached is None or
                    len(shared['fingerprints']) >= len(cached['fingerprints'])):
                cached = shared
        start = 0
        previous = None; state = None; records = []
        if cached is not None:
            prefix = cached['fingerprints']
            if len(prefix) > len(fingerprints):
                raise ValueError('committed history is missing from verified prefix')
            if len(prefix) <= len(fingerprints) and fingerprints[:len(prefix)] == prefix:
                start = len(prefix)
                previous = cached['commit_id']
                state = deepcopy(cached['state'])
                records = list(cached['records'])
        for number, path in enumerate(paths[start:], start+1):
            with _COLD_REPLAY_LOCK:
                record = body = None
                try:
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
                    records.append(_record_summary(record))
                except BaseException as error:
                    _clear_validation_tracebacks(error)
                    raise
                finally:
                    # Drop giant evidence before releasing the memory gate or
                    # evaluating the next json.loads, including error paths.
                    del record, body
        if previous and repair:
            expected = {'commit_id': previous, 'sequence': len(paths)}
            # Repair only as the owning writer; readers use repair=False.
            if head != expected:
                durable(self.directory/'HEAD.json', expected)
        self._remember(fingerprints, state, previous, records)
        return state

    @locked_journal
    def replay_snapshot(self, *, commit_id=None):
        """Read an authoritative current/historical snapshot without HEAD repair.

        The full observed prefix remains checked even for an older reference.
        Only the requested historical state is reread; validated metadata and
        unchanged evidence share the ordinary writer's prefix cache.
        """
        state = self.replay(repair=False)
        prefix = self._verified
        records = prefix['records']
        if state is None:
            if commit_id is not None:
                raise ValueError('unknown immutable source commit in this theory')
            return None
        index = len(records) - 1
        if commit_id is not None:
            index = next((i for i, row in enumerate(records)
                          if row['commit_id'] == commit_id), -1)
            if index < 0:
                raise ValueError('unknown immutable source commit in this theory')
        if index != len(records) - 1:
            previous_id = records[index]['commit_id']
            fingerprint = prefix['fingerprints'][index]
            found, historical = self._history_get(previous_id, fingerprint)
            if found:
                state = historical
            else:
                path = self.directory/'commits'/f'{index+1:06d}.json'
                with _COLD_REPLAY_LOCK:
                    record = body = None
                    try:
                        record = json.loads(path.read_text())
                        body = {k:v for k,v in record.items() if k != 'commit_id'}
                        if (_fingerprint(path) != fingerprint
                                or record['commit_id'] != previous_id
                                or digest(body) != previous_id
                                or _record_summary(record) != records[index]):
                            raise ValueError('historical proof commit changed during snapshot read')
                        state = record['state']
                        self._history_remember(previous_id, fingerprint, state)
                    except BaseException as error:
                        _clear_validation_tracebacks(error)
                        raise
                    finally:
                        del record, body
        return {'state': state, 'commit_id': records[index]['commit_id'],
                'records': deepcopy(records[:index+1])}

    @locked_journal
    def append(self, event, state, evidence):
        from regen.execution.check_tasks import acceptance_guard, check_cancelled
        check_cancelled()
        if event in ACCEPTANCE_EVENTS:
            with acceptance_guard():
                return self._append(event, state, evidence)
        return self._append(event, state, evidence)

    def _append(self, event, state, evidence):
        old = self.replay()
        self.validate(old, state, event, evidence)
        paths = sorted((self.directory/'commits').glob('*.json'))
        parent = self._verified['commit_id']
        body = {'sequence':len(paths)+1, 'parent':parent, 'binding':self.binding,
                'event':event, 'state':state, 'evidence':evidence}
        record = {**body, 'commit_id':digest(body)}
        path = self.directory/'commits'/f'{len(paths)+1:06d}.json'
        from regen.execution.check_tasks import CURRENT_TASK
        token = CURRENT_TASK.get() if event in ACCEPTANCE_EVENTS else None
        try:
            durable(path, record, immutable=True)
        except BaseException:
            # link() can publish the complete record before directory fsync or
            # temporary-link cleanup fails. A visible accepted commit must not
            # be cancelled; keep the I/O error and let replay establish recovery.
            if token is not None:
                try:
                    visible = json.loads(path.read_text()) == record
                except (OSError, ValueError):
                    visible = False
                if visible:
                    token.published = True
            raise
        # The commit is authoritative even if a later HEAD/cache projection
        # fails. append holds the acceptance lock until this flag is visible.
        if token is not None:
            token.published = True
        durable(self.directory/'HEAD.json', {'commit_id':record['commit_id'], 'sequence':len(paths)+1})
        self._remember(self._verified['fingerprints'] + (_fingerprint(path),), state,
                       record['commit_id'], (*self._verified['records'], _record_summary(record)))
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
