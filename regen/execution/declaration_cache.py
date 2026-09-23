"""Process-local GREEN declaration certificates, never proof certificates.

Only enabled by a frozen repository runtime. No disk certificate is trusted on
restart. File metadata includes ctime/inode, so replacing or rewriting a frozen
input invalidates the entry even when its mtime is restored. This assumes the
same immutable-input contract as native execution; it is not a hostile-filesystem
security boundary.
"""
from collections import OrderedDict
from copy import deepcopy
import os
from pathlib import Path

from regen.execution.proof_commits import digest, freeze_evidence


def tree_stamp(root):
    root = Path(root)
    records = []
    if not root.exists():
        raise ValueError('missing declaration context: '+str(root))
    visited = set()
    for directory, dirs, files in os.walk(root, followlinks=True):
        resolved = Path(directory).resolve()
        if resolved in visited:
            dirs[:] = []
            continue
        visited.add(resolved)
        dirs.sort(); files.sort()
        for name in ['.', *dirs, *files]:
            path = Path(directory)/name
            info = path.stat()
            # Directory mtimes detect additions/removals, symlink target metadata
            # detects replacement; include link text as well as resolved target.
            records.append((str(path.relative_to(root)), str(path.resolve()),
                            info.st_dev, info.st_ino, info.st_size,
                            info.st_mtime_ns, info.st_ctime_ns))
    return records


def context_key(checker, source):
    binding = getattr(checker, 'declaration_environment', None)
    if not binding:
        return None
    fields = ('image', 'architecture', 'base_session', 'base_import', 'base_catalog')
    roots = ('heaps_dir', 'l4v_dir', 'semantic_workspace', 'isabelle_dir', 'generated_dir')
    return digest({'binding':binding, 'source':source,
                   'runtime':{k:getattr(checker, k) for k in fields},
                   'imports':checker.imports(),
                   'inputs':{k:tree_stamp(getattr(checker, k)) for k in roots},
                   'implementation':id(getattr(checker._check, '__func__', checker._check))})


def checked_declarations(checker, source, name):
    key = context_key(checker, source)
    cache = getattr(checker, '_declaration_certificates', None)
    if cache is None:
        cache = checker._declaration_certificates = OrderedDict()
    if key is not None and key in cache:
        result = deepcopy(cache[key])
        cache.move_to_end(key)
        result['declaration_cache'] = {'hit':True, 'identity':key,
                                       'original_wall_ms':result.get('wall_ms', 0)}
        result['wall_ms'] = 0
        return result
    result = checker._check(source, name)
    if key is not None and result.get('verdict') == 'green' and result.get('returncode') == 0:
        frozen = freeze_evidence(result)
        # Cache only complete native evidence, and only if inputs stayed stable.
        if (frozen.get('source_content') == source and 'log_content' in frozen
                and context_key(checker, source) == key):
            cache[key] = deepcopy(frozen)
            while len(cache) > 32:
                cache.popitem(last=False)
            result = frozen
    result['declaration_cache'] = {'hit':False, 'identity':key}
    return result
