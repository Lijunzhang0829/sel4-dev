"""Verify inherited heap bytes before starting a native process.

Build databases under log/ are mutable, but inherited session images are not.
Only a manifest recorded at the original copy authorizes restoring a parent.
Generated child images are deliberately outside this manifest.
"""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from regen.execution.check_tasks import NativePreparationError


def require_loadable(check):
    """A failed heap relocation is not a changed contract or a proof failure."""
    diagnostic = check.get('diagnostic', '')
    if (check.get('returncode') != 0 and 'savestate.cpp' in diagnostic
            and 'LoadRelocate::RelocateAddress' in diagnostic
            and 'Assertion' in diagnostic):
        raise NativePreparationError('native heap relocation failed before target checking: '
                                     + diagnostic[-12000:])


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if 'log' in relative.parts:
            continue
        if path.is_symlink():
            raise NativePreparationError('heap image tree contains a symlink: ' + str(relative))
        if path.is_file():
            result[str(relative)] = sha256(path)
    return result


def verify_copy(source, copied, expected):
    if manifest(source) != expected or manifest(copied) != expected:
        raise NativePreparationError('heap copy integrity mismatch; refusing publication')


def verify_parents(source, destination, expected, *, receipt_dir):
    """Restore damaged inherited images only from a hash-bound frozen source.

Call before creating a checker, with no resident using this directory. Leave
generated heaps, proof state and imports untouched. A failed repair is fatal
infrastructure evidence, never feedback to rewrite a mathematical proof.
"""
    from regen.execution.proof_commits import durable
    from regen.infrastructure.resource_budget import _copy_lock
    from uuid import uuid4
    source, destination = Path(source), Path(destination)
    if not expected:
        raise NativePreparationError('parent heap manifest missing; prepare a verified heap copy first')
    with _copy_lock(destination.parent):
        for name, digest in expected.items():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or 'log' in relative.parts:
                raise NativePreparationError('invalid parent heap manifest path')
            for base in (source, destination):
                if base.is_symlink() or any((base / Path(*relative.parts[:i])).is_symlink()
                        for i in range(1, len(relative.parts) + 1)):
                    raise NativePreparationError('parent heap path contains a symlink')
            original = source / relative
            if not original.is_file() or sha256(original) != digest:
                raise NativePreparationError('frozen parent heap integrity mismatch: ' + name)
        repairs = []
        for name, digest in expected.items():
            target = destination / name
            observed = sha256(target) if target.is_file() else None
            if observed == digest:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.heap-repair-') as temp:
                shutil.copyfile(source / name, temp.name)
                if sha256(temp.name) != digest or sha256(source / name) != digest:
                    raise NativePreparationError('parent heap repair copy integrity mismatch: ' + name)
                with open(temp.name, 'rb') as stream:
                    os.fsync(stream.fileno())
                # Preserve the failed image as evidence before replacing it.
                if target.exists():
                    quarantine = Path(receipt_dir) / ('damaged-' + uuid4().hex)
                    quarantine.parent.mkdir(parents=True, exist_ok=True)
                    os.link(target, quarantine)
                else:
                    quarantine = None
                # NamedTemporaryFile owns its path; replace via a separate link.
                staged = Path(temp.name + '.ready')
                os.link(temp.name, staged)
                try:
                    os.replace(staged, target)
                finally:
                    staged.unlink(missing_ok=True)
            repairs.append({'image': name, 'before': observed, 'after': digest,
                            'quarantine': str(quarantine) if quarantine else None})
        if repairs:
            durable(Path(receipt_dir) / ('repair-' + uuid4().hex + '.json'),
                    {'policy': 'verified-parent-heap-repair-v1', 'repairs': repairs}, immutable=True)
        return repairs
