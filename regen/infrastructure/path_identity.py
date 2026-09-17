"""Content identity for immutable source and toolchain directory trees."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_identity(directory: Path, *, allow_links: bool = False) -> str:
    """Bind actual input bytes, including untracked generated specifications."""
    if not directory.is_dir():
        raise ValueError(f"missing source directory: {directory}")
    records = []
    for base, dirs, files in os.walk(directory):
        dirs[:] = sorted(
            name for name in dirs
            if name not in {".git", "heaps", "browser_info", "build", "__pycache__"}
        )
        for name in sorted([*dirs, *files]):
            path = Path(base) / name
            if path.is_symlink():
                if not allow_links:
                    raise ValueError(f"source must be materialized, found symlink: {path}")
                target = path.resolve(strict=True)
                if target.is_dir():
                    raise ValueError(
                        f"toolchain directory symlink must be materialized: {path}"
                    )
                records.append((
                    str(path.relative_to(directory)), "symlink",
                    os.readlink(path), _sha256_path(target),
                ))
            if path.is_file():
                records.append((
                    str(path.relative_to(directory)), _sha256_path(path),
                    path.stat().st_mode & 0o777,
                ))
    return _sha256_json(sorted(records))
