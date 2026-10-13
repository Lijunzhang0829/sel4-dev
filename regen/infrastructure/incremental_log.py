"""Versioned, lossless structural snapshots for append-only experiment logs.

The logical JSON and its existing proof identities do not change. A snapshot is
a Merkle root: unchanged subtrees are shared, so every event is also a random-
access checkpoint without a linear patch chain. Objects precede atomic manifest
publication. This layer never grants proof acceptance or retries a model call.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import zlib

FORMAT = 'regen-merkle-json-v1'
MARKER = 'log-storage.json'
THRESHOLD = 2048
_CACHE = OrderedDict()
_LOCK = threading.RLock()
_MAX_CACHE_BYTES = 32 * 1024 * 1024
_CACHE_BYTES = 0
# Reachability is tiny compared with proof/source payloads. Keep it separately
# so a warm authority check can re-stat every object without decompressing the
# complete history after the payload LRU has evicted it. This is a process-local
# index of already hash-verified bytes, never a persistent trust shortcut.
_REFERENCES = OrderedDict()
_REFERENCE_BYTES = 0
# Paused two-theory 009 reaches 12,591 objects / ~45 MiB conservatively
# accounted metadata. The bound includes string/container overhead, not just
# hash bytes; it remains independent of the unchanged 32 MiB payload LRU.
_MAX_REFERENCE_BYTES = 64 * 1024 * 1024
_MAX_REFERENCE_ENTRIES = 50000
_FINGERPRINT_BATCH = ContextVar('log_fingerprint_batch', default=None)


@contextmanager
def fingerprint_batch():
    """Deduplicate reachable objects within ONE verification, never across reads.

    Each subsequent journal read checks file stamps again. Context-local storage
    avoids sharing an incomplete traversal with concurrent readers/writers.
    """
    if _FINGERPRINT_BATCH.get() is not None:
        yield
        return
    token = _FINGERPRINT_BATCH.set({})
    try:
        yield
    finally:
        _FINGERPRINT_BATCH.reset(token)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def _stamp(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def atomic_bytes(path, raw, *, immutable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temp, path)
            except FileExistsError:
                if path.read_bytes() != raw:
                    raise ValueError('immutable log collision: ' + str(path))
        else:
            os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def enable(root):
    """Only enable explicitly on an empty/new output; never convert in place."""
    root = Path(root).resolve()
    marker = root / MARKER
    value = {'format': FORMAT, 'objects': 'log-objects', 'threshold': THRESHOLD}
    if marker.exists():
        if json.loads(marker.read_text()) != value:
            raise ValueError('logging policy mismatch')
        return
    if root.exists() and any(p.name not in {'.runner-locks', '.planning.lock'} for p in root.iterdir()):
        raise ValueError('incremental logging requires an empty output directory')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'log-objects').mkdir(exist_ok=True)
    atomic_bytes(marker, canonical(value), immutable=True)


def find_root(path):
    for parent in Path(path).resolve().parents:
        marker = parent / MARKER
        if marker.exists():
            policy = json.loads(marker.read_text())
            if policy != {'format': FORMAT, 'objects': 'log-objects', 'threshold': THRESHOLD}:
                raise ValueError('unknown logging policy')
            return parent
    return None


def selected(path):
    """Keep frozen inputs, agent-visible files and mutable control JSON plain."""
    return bool(set(Path(path).parts) & {'commits', 'batch-requests', 'queries', 'snapshots'})


class ObjectStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / 'log-objects'

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('invalid log object identity')
        path = self.directory / (key + '.json.z')
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('log objects cannot be symlinks')
        return path

    def _put(self, raw):
        key = hashlib.sha256(raw).hexdigest()
        path = self.path(key)
        if path.exists():
            # Never bless an existing corrupt object, even on duplicate writes.
            self.references(key)
        else:
            atomic_bytes(path, zlib.compress(raw, 6), immutable=True)
        return key

    def encode(self, value):
        # Tagged internal nodes cannot collide with arbitrary user dictionaries.
        memo = {}
        def pack(v):
            identity = id(v)
            if isinstance(v, (dict, list)) and identity in memo:
                return memo[identity]
            if isinstance(v, dict):
                if any(not isinstance(k, str) for k in v):
                    raise ValueError('JSON object keys must be strings')
                node = ['dict', {k: pack(x) for k, x in v.items()}]
            elif isinstance(v, list):
                node = ['list', [pack(x) for x in v]]
            else:
                node = ['value', v]
            raw = canonical(node)
            result = ['ref', self._put(raw)] if len(raw) >= THRESHOLD else node
            if isinstance(v, (dict, list)):
                memo[identity] = result
            return result
        node = pack(value)
        return node[1] if node[0] == 'ref' else self._put(canonical(node))

    def node(self, key, _retries=3):
        global _CACHE_BYTES
        path = self.path(key)
        stamp = _stamp(path)
        cache_key = str(path)
        with _LOCK:
            item = _CACHE.get(cache_key)
            if item is not None and item[0] == stamp:
                _CACHE.move_to_end(cache_key)
                return item[1], stamp
        try:
            raw = zlib.decompress(path.read_bytes())
        except zlib.error as error:
            raise ValueError('corrupt compressed log object: ' + key) from error
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('log object hash mismatch: ' + key)
        if _stamp(path) != stamp:
            # Publishing uses hardlink + unlink(temp): another reader can see
            # a harmless link-count/ctime change after the manifest is visible.
            # Re-read, never cache bytes under an unobserved fingerprint.
            if _retries == 0:
                raise ValueError('log object changed repeatedly during read')
            return self.node(key, _retries-1)
        value = json.loads(raw)
        with _LOCK:
            previous = _CACHE.pop(cache_key, None)
            if previous:
                _CACHE_BYTES -= previous[2]
            # Bound the cache by decompressed bytes, not just object count.
            if len(raw) <= _MAX_CACHE_BYTES:
                _CACHE[cache_key] = (stamp, value, len(raw))
                _CACHE_BYTES += len(raw)
            while _CACHE_BYTES > _MAX_CACHE_BYTES:
                _, old = _CACHE.popitem(last=False)
                _CACHE_BYTES -= old[2]
        return value, stamp

    def references(self, key):
        """Return verified direct edges; always recheck this object's stamp.

        A cache hit stores no decoded proof/log data. ctime catches same-size
        rewrites even if mtime is restored; inode/device catch replacement.
        Missing files and symlinks still fail before this metadata is consulted.
        A changed object is read, decompressed and hash-checked just as on cold
        startup. Descendants are independently checked by fingerprint().
        """
        global _REFERENCE_BYTES
        path = self.path(key)
        stamp, cache_key = _stamp(path), str(path)
        with _LOCK:
            item = _REFERENCES.get(cache_key)
            if item is not None and item[0] == stamp:
                _REFERENCES.move_to_end(cache_key)
                return item[1], stamp
        node, stamp = self.node(key)
        edges = set()

        def visit(value):
            kind, contents = value
            if kind == 'ref':
                # Validate identities even before visiting the child.
                if not isinstance(contents, str) or not re.fullmatch('[0-9a-f]{64}', contents):
                    raise ValueError('invalid log object identity')
                edges.add(contents)
            elif kind == 'dict':
                for child in contents.values(): visit(child)
            elif kind == 'list':
                for child in contents: visit(child)
            elif kind != 'value':
                raise ValueError('invalid log node kind')

        visit(node)
        refs = tuple(sorted(edges))
        # Conservative accounting includes Python-container/string overhead,
        # unlike the independent payload cache's decompressed-byte accounting.
        size = 384 + 2 * len(cache_key) + sum(160 + 2 * len(ref) for ref in refs)
        with _LOCK:
            previous = _REFERENCES.pop(cache_key, None)
            if previous is not None:
                _REFERENCE_BYTES -= previous[2]
            if size <= _MAX_REFERENCE_BYTES:
                _REFERENCES[cache_key] = (stamp, refs, size)
                _REFERENCE_BYTES += size
            while (_REFERENCE_BYTES > _MAX_REFERENCE_BYTES
                   or len(_REFERENCES) > _MAX_REFERENCE_ENTRIES):
                _, old = _REFERENCES.popitem(last=False)
                _REFERENCE_BYTES -= old[2]
        return refs, stamp

    def decode(self, key):
        active = set()
        def unpack(node):
            kind, value = node
            if kind == 'ref':
                if value in active:
                    raise ValueError('cyclic log object')
                active.add(value)
                try:
                    return unpack(self.node(value)[0])
                finally:
                    active.remove(value)
            if kind == 'dict':
                return {k: unpack(x) for k, x in value.items()}
            if kind == 'list':
                return [unpack(x) for x in value]
            if kind != 'value':
                raise ValueError('invalid log node kind')
            return value
        return unpack(['ref', key])

    def fingerprint(self, key):
        """Track every reachable object, including cache-hit evidence objects."""
        batch = _FINGERPRINT_BATCH.get()
        # Store identity is part of the cache key: equal hashes in independent
        # copied stores must not let a missing/corrupt copy borrow authority.
        cache = ({'closures': {}, 'entries': 0, 'nodes': {}} if batch is None else
                 batch.setdefault(str(self.root), {'closures': {}, 'entries': 0, 'nodes': {}}))
        memo, nodes, active = cache['closures'], cache['nodes'], set()

        def closure(identity):
            if identity in active:
                raise ValueError('cyclic log object')
            if identity in memo:
                return memo[identity]
            active.add(identity)
            if identity in nodes:
                refs, stamp = nodes[identity]
            else:
                refs, stamp = self.references(identity)
                # A batch can reach more objects than the closure memo admits.
                # Deduplicate their stat checks independently of closure size.
                if len(nodes) < _MAX_REFERENCE_ENTRIES:
                    nodes[identity] = (refs, stamp)
            found = {identity: stamp}
            for child in refs:
                found.update(closure(child))
            active.remove(identity)
            # Saturation only loses reuse, never verification coverage.
            if cache['entries'] + len(found) <= 250000:
                memo[identity] = found
                cache['entries'] += len(found)
            return found

        return tuple(sorted(closure(key).items()))


def write_artifact(path, value, *, immutable=False, root=None):
    path = Path(path)
    root = Path(root).resolve() if root is not None else find_root(path)
    if root is None or not path.resolve().is_relative_to(root) or find_root(path) != root:
        raise ValueError('artifact must belong to an enabled log root')
    key = ObjectStore(root).encode(value)
    manifest = {'$log_format': FORMAT, 'root': key}
    # The manifest is the publication point; orphan objects confer no authority.
    atomic_bytes(path, canonical(manifest) + b'\n', immutable=immutable)


def _manifest(path):
    value = json.loads(Path(path).read_text())
    if isinstance(value, dict) and '$log_format' in value:
        if set(value) != {'$log_format', 'root'} or value['$log_format'] != FORMAT:
            raise ValueError('invalid log manifest')
        root = find_root(path)
        if root is None:
            raise ValueError('log manifest without storage policy')
        return value, ObjectStore(root)
    return value, None


def read_artifact(path):
    value, store = _manifest(path)
    return store.decode(value['root']) if store else value


def artifact_fingerprint(path):
    value, store = _manifest(path)
    return store.fingerprint(value['root']) if store else ()


def clear_cache():
    global _CACHE_BYTES, _REFERENCE_BYTES
    with _LOCK:
        _CACHE.clear(); _CACHE_BYTES = 0
        _REFERENCES.clear(); _REFERENCE_BYTES = 0


def copy_logged_file(source, destination):
    """Forks must copy object content, never leave references to another store."""
    import shutil
    if find_root(source) is None:
        return shutil.copy2(source, destination)
    value, store = _manifest(source) if str(source).endswith('.json') else (None, None)
    if store is None:
        return shutil.copy2(source, destination)
    decoded = store.decode(value['root'])
    if find_root(destination) is not None:
        write_artifact(destination, decoded, immutable=True)
    else:
        atomic_bytes(destination, canonical(decoded) + b'\n', immutable=True)
    return str(destination)


def policy_arguments(run):
    value = run.get('log_storage', 'inline-v1')
    if value not in ('inline-v1', FORMAT):
        raise ValueError('unsupported log storage policy')
    return {'log_storage': value} if value != 'inline-v1' else {}
