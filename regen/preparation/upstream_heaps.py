"""Allowlisted cache inputs for the current Refine/ARM Semantic Base.

Cache bytes are optimization inputs, never evidence that current sources passed.
The ordinary Isabelle build still validates source and parent fingerprints.
"""
from pathlib import Path
import shutil
from regen.preparation.environment_gate import sha256_path

# Transitive parent/import sessions of BaseRefine, Lib, CorresK and AInvs.
# Deliberately excludes target sessions and every descendant of them.
SESSIONS = frozenset(('Pure', 'HOL', 'HOL-Library', 'HOL-Eisbach', 'Word_Lib',
    'Basics', 'ML_Utils', 'Eisbach_Tools', 'Monads', 'Lib', 'ExecSpec',
    'ASpec', 'AInvs', 'CorresK', 'BaseRefine'))
PLATFORM = 'polyml-5.9.1_x86_64_32-linux'


def inventory(root: Path) -> list[dict]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError('upstream cache must be a materialized directory')
    records = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f'upstream cache symlink: {relative}')
        parts = relative.parts
        allowed_dir = parts in {(PLATFORM,), (PLATFORM, 'log')}
        if path.is_dir() and allowed_dir:
            continue
        allowed_heap = len(parts) == 2 and parts[0] == PLATFORM and parts[1] in SESSIONS
        allowed_db = (len(parts) == 3 and parts[:2] == (PLATFORM, 'log')
                      and path.suffix == '.db' and path.stem in SESSIONS)
        if not path.is_file() or not (allowed_heap or allowed_db):
            raise ValueError(f'not an allowlisted upstream heap/database: {relative}')
        records.append({'path': str(relative), 'sha256': sha256_path(path), 'size': path.stat().st_size})
    if not records:
        raise ValueError('upstream cache is empty')
    paths = {item['path'] for item in records}
    for item in records:
        path = Path(item['path'])
        if len(path.parts) == 2 and f'{PLATFORM}/log/{path.name}.db' not in paths:
            raise ValueError(f'upstream heap has no build database: {path.name}')
    return records


def copy_cache(source: Path, destination: Path, expected: list[dict]):
    if inventory(source) != expected:
        raise ValueError('upstream cache input drifted')
    if destination.exists():
        raise ValueError('cache destination must be empty/new')
    shutil.copytree(source, destination)
    if inventory(destination) != expected:
        raise ValueError('upstream cache copy mismatch')
