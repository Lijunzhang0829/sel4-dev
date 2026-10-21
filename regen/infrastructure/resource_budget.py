"""Admission and atomic publication for expensive local heap initialization."""
from contextlib import contextmanager
import ctypes
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from uuid import uuid4


HEAP_COPY_POLICY = {
    'version': 'heap-copy-budget-v1',
    'reserve_bytes': 2 * 1024 ** 3,
    'admission': 'per-user-filesystem-serialized-copy',
    'publication': 'temporary-tree-then-rename',
}


class ResourceBudgetError(RuntimeError):
    """The native initialization cannot fit while preserving disk headroom."""


@contextmanager
def _copy_lock(parent):
    # Separate open descriptions make flock serialize threads as well as
    # processes. Holding it through publication reserves disk capacity without
    # a persistent reservation ledger that could survive its owner incorrectly.
    # Do not key coordination on TMPDIR: different runners may set it differently.
    root = Path('/tmp') / f'regen-heap-copy-{os.getuid()}'
    root.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('heap-copy lock directory must be user-owned and private')
        descriptor = os.open(f'device-{parent.stat().st_dev}.lock',
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077):
                raise PermissionError('heap-copy lock must be a private regular file')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _tree_size(source, block_size):
    """Estimate copytree's dereferenced, non-sparse allocation from actual files."""
    logical = allocated = files = directories = 0

    def walk(path, ancestors):
        nonlocal logical, allocated, files, directories
        info = path.stat()
        if stat.S_ISDIR(info.st_mode):
            identity = (info.st_dev, info.st_ino)
            if identity in ancestors:
                raise ValueError('heap source contains a directory symlink cycle')
            directories += 1
            allocated += block_size
            with os.scandir(path) as entries:
                for entry in entries:
                    walk(Path(entry.path), ancestors | {identity})
        elif stat.S_ISREG(info.st_mode):
            files += 1
            logical += info.st_size
            allocated += ((info.st_size + block_size - 1) // block_size) * block_size
        else:
            raise ValueError(f'heap source contains a non-regular file: {path}')

    walk(source, set())
    return {'source_bytes': logical, 'file_count': files, 'directory_count': directories,
            'copy_bytes': allocated}


def _publish_directory(source, destination):
    # Linux renameat2(RENAME_NOREPLACE) closes the final existence-check race:
    # plain rename can silently replace an existing empty destination directory.
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def clone_heaps(source, destination, *, reserve_bytes=HEAP_COPY_POLICY['reserve_bytes']):
    """Publish a heap clone with explicit, finally-closed preparation timing."""
    from regen.infrastructure.progress_events import generation_exclusion
    with generation_exclusion('heap_preparation'):
        return _clone_heaps(source, destination, reserve_bytes=reserve_bytes)


def _clone_heaps(source, destination, *, reserve_bytes):
    """Copy once with filesystem-wide admission; a failure never publishes heaps.

    Existing targets are never modified. All cooperating callers on the same
    filesystem serialize only their initialization copies, not native proving.
    """
    if type(reserve_bytes) is not int or reserve_bytes < 0:
        raise ValueError('heap disk reserve must be a nonnegative integer')
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    policy = {**HEAP_COPY_POLICY, 'reserve_bytes': reserve_bytes}
    with _copy_lock(destination.parent):
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError('heap destination must be a directory, never a symlink or file')
        if destination.exists():
            return {'status': 'existing', 'policy': policy, 'destination': str(destination)}
        if not source.is_dir():
            raise ValueError('heap source must be an existing directory')
        size = _tree_size(source, os.statvfs(destination.parent).f_frsize or 4096)
        available = shutil.disk_usage(destination.parent).free
        required = size['copy_bytes'] + reserve_bytes
        if available < required:
            raise ResourceBudgetError(
                f'heap copy disk budget insufficient: free={available}, copy={size["copy_bytes"]}, '
                f'reserve={reserve_bytes}, required={required}, destination={destination}')
        with tempfile.TemporaryDirectory(prefix=f'.{destination.name}-copy-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'heaps'
            shutil.copytree(source, staging)
            remaining = shutil.disk_usage(destination.parent).free
            if remaining < reserve_bytes:
                raise ResourceBudgetError(
                    f'heap copy exhausted disk reserve before publication: '
                    f'free={remaining}, reserve={reserve_bytes}, destination={destination}')
            # All cooperating publishers share the filesystem lock. Do not
            # replace a destination introduced by an external writer either.
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f'heap destination appeared during copy: {destination}')
            _publish_directory(staging, destination)
        return {'status': 'copied', 'source': str(source), 'destination': str(destination),
                'policy': policy, **size, 'free_before': available, 'free_after': remaining}


def reclaim_theory_heaps(root, theory):
    """Reclaim one sealed theory's disposable heaps after its controller closed.

    The caller must first confirm a sealed, completed provider session and a
    successful controller close. This helper independently checks the terminal
    commit and restricts deletion to its exact theory-owned heap directory.
    Ordinary failures return warnings and never invalidate the accepted seal.
    """
    from regen.execution.proof_commits import digest, durable
    record = {'theory': theory, 'status': 'warning', 'heap_removed': False}
    audit = None
    try:
        if (not isinstance(theory, str) or not theory or theory in {'.', '..'}
                or '/' in theory or '\\' in theory):
            raise ValueError('heap reclamation requires a single theory directory component')
        root = Path(root)
        if root.is_symlink():
            raise ValueError('heap reclamation root cannot be a symlink')
        root = root.resolve(strict=True)
        directory = root / 'theories' / theory
        heaps = directory / 'heaps'
        for path in (root / 'theories', directory, directory / 'acceptance',
                     directory / 'acceptance' / 'commits'):
            if path.is_symlink() or not path.is_dir():
                raise ValueError(f'heap reclamation requires a real owned directory: {path}')
        if directory.resolve() != directory:
            raise ValueError('theory directory escapes its declared owner')
        owner_identity = directory.stat()
        with _copy_lock(directory):
            commits = sorted((directory / 'acceptance' / 'commits').glob('*.json'))
            if not commits or commits[-1].is_symlink():
                raise ValueError('heap reclamation requires a terminal accepted commit')
            from regen.infrastructure.incremental_log import read_artifact
            last = read_artifact(commits[-1])
            body = {key: value for key, value in last.items() if key != 'commit_id'}
            if (last.get('commit_id') != digest(body)
                    or commits[-1].name != f'{last.get("sequence", 0):06d}.json'
                    or last.get('event') != 'seal' or last.get('state', {}).get('status') != 'sealed'):
                raise ValueError('heap reclamation requires a valid terminal seal commit')
            record.update(commit_id=last['commit_id'], sequence=last['sequence'], directory=str(heaps))
            if heaps.is_symlink():
                raise ValueError('theory heaps cannot be a symlink')
            if not heaps.exists():
                return {**record, 'status': 'absent'}
            if not heaps.is_dir() or heaps.resolve() != heaps:
                raise ValueError('theory heaps escape their declared owner')
            if heaps.stat().st_dev != owner_identity.st_dev:
                raise ValueError('theory heaps cross a filesystem boundary')
            files = []
            for parent, folders, names in os.walk(heaps, followlinks=False):
                for name in folders + names:
                    path = Path(parent) / name
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode):
                        raise ValueError('theory heap tree contains a symlink')
                    if info.st_dev != owner_identity.st_dev:
                        raise ValueError('theory heap tree crosses a filesystem boundary')
                    if stat.S_ISREG(info.st_mode):
                        files.append({'path': str(path.relative_to(heaps)), 'bytes': info.st_size})
                    elif not stat.S_ISDIR(info.st_mode):
                        raise ValueError('theory heap tree contains a non-regular file')
            audit_dir = root / 'heap-reclamation'
            if audit_dir.is_symlink():
                raise ValueError('heap reclamation audit directory cannot be a symlink')
            audit_dir.mkdir(exist_ok=True)
            audit = audit_dir / f'{theory}-{last["commit_id"][:16]}-{uuid4().hex}.json'
            record.update(status='planned', audit_path=str(audit), time=time.time(), files=files,
                          file_bytes=sum(row['bytes'] for row in files),
                          rebuild_from=['run parent heaps', 'sealed generated theory sources'])
            # Audit intent must be durable before removing even rebuildable bytes.
            durable(audit, record)
            if not shutil.rmtree.avoids_symlink_attacks:
                raise RuntimeError('descriptor-relative symlink-safe rmtree is required')
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            root_descriptor = os.open(root, flags)
            try:
                theories_descriptor = os.open('theories', flags, dir_fd=root_descriptor)
                try:
                    descriptor = os.open(theory, flags, dir_fd=theories_descriptor)
                    try:
                        actual = os.fstat(descriptor)
                        if (actual.st_dev, actual.st_ino) != (owner_identity.st_dev, owner_identity.st_ino):
                            raise ValueError('theory owner changed during heap reclamation')
                        shutil.rmtree('heaps', dir_fd=descriptor)
                    finally:
                        os.close(descriptor)
                finally:
                    os.close(theories_descriptor)
            finally:
                os.close(root_descriptor)
            record.update(status='reclaimed', heap_removed=True, finished_at=time.time())
            durable(audit, record)
            return record
    except Exception as error:
        record.update(status='warning', diagnostic=f'{type(error).__name__}: {error}')
        if audit is not None:
            try:
                durable(audit, record)
            except Exception as audit_error:
                record['audit_error'] = f'{type(audit_error).__name__}: {audit_error}'
        logging.getLogger(__name__).warning('Theory heap reclamation warning: %s', record['diagnostic'])
        return record
