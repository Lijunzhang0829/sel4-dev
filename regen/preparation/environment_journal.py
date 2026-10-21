"""Trusted preparation journal and local, verified artifact checkpoints.

Recovery requires the journal AND its checkpoint files, never hashes alone.
Only the preparation orchestrator may use this private directory.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Callable

from regen.preparation.refine_campaign import read_json, sha256_json, write_immutable_json
from regen.preparation.environment_gate import sha256_path


def safe_path(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or '..' in value.parts or not value.parts:
        raise ValueError(f"unsafe artifact path: {relative}")
    path = root / value
    # Never traverse an existing symlink while writing or restoring.
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"symlink in artifact path: {parent}")
    return path


def inventory_tree(root: Path, outputs: list[str]) -> list[dict]:
    records = []
    def visit(path: Path):
        relative = str(path.relative_to(root))
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            # Checkpoints intentionally reject links: restored runtime must not
            # re-expose raw sources through an external or dangling alias.
            raise ValueError(f"checkpoint requires materialized files, found symlink: {relative}")
        if path.is_dir():
            records.append({'path': relative, 'kind': 'directory', 'mode': mode})
            for child in sorted(path.iterdir()):
                visit(child)
        elif path.is_file():
            records.append({'path': relative, 'kind': 'file', 'mode': mode,
                            'bytes': path.stat().st_size, 'sha256': sha256_path(path)})
        else:
            raise ValueError(f"unsupported checkpoint artifact: {relative}")
    for relative in outputs:
        path = safe_path(root, relative)
        if not path.exists():
            raise ValueError(f"missing stage output: {relative}")
        visit(path)
    return records


class EnvironmentJournal:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.private = self.root / '.preparation'
        if self.private.is_symlink():
            raise ValueError('private preparation directory cannot be a symlink')
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.private.chmod(0o700)
        self.log = self.private / 'events.jsonl'

    @contextmanager
    def lock(self):
        with (self.private / 'lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('another environment preparation process holds the lock') from error
            try:
                yield self
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def events(self) -> list[dict]:
        if not self.log.exists():
            return []
        data = self.log.read_bytes()
        if data and not data.endswith(b'\n'):
            raise ValueError('incomplete journal tail; use restore to preserve and trim it')
        result = []
        previous = None
        for line in data.splitlines():
            event = json.loads(line)
            body = {k: v for k, v in event.items() if k != 'event_sha256'}
            if (event.get('sequence') != len(result) + 1 or
                event.get('previous_sha256') != previous or
                event.get('event_sha256') != sha256_json(body)):
                raise ValueError('preparation journal identity/sequence mismatch')
            previous = event['event_sha256']
            result.append(event)
        return result

    def trim_incomplete_tail(self):
        if self.log.exists():
            data = self.log.read_bytes()
            if data and not data.endswith(b'\n'):
                prefix = data[:data.rfind(b'\n') + 1]
                tail = self.private / f'torn-tail-{len(data)}.bin'
                if tail.exists() and tail.read_bytes() != data[len(prefix):]:
                    raise ValueError('conflicting saved journal tail')
                tail.write_bytes(data[len(prefix):])
                self.log.write_bytes(prefix)
                self.events()  # Validate every complete record before proceeding.
                self.append('journal-tail-recovered', saved_tail=tail.name)

    def append(self, event: str, **fields) -> dict:
        events = self.events()
        body = {'schema_version': 1, 'sequence': len(events) + 1,
                'previous_sha256': events[-1]['event_sha256'] if events else None,
                'time': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
        record = {**body, 'event_sha256': sha256_json(body)}
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def completed(self) -> dict[str, dict]:
        return {event['stage']: event for event in self.events()
                if event['event'] == 'stage-completed'}

    def checkpoint(self, stage: str, outputs: list[str]) -> dict:
        records = inventory_tree(self.root, outputs)
        destination = self.private / 'checkpoints' / f'{len(self.events()) + 1:06d}-{stage}'
        destination.mkdir(parents=True)
        for relative in outputs:
            source = self.root / relative
            target = destination / 'files' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        if inventory_tree(destination / 'files', outputs) != records:
            raise ValueError(f'checkpoint copy changed during snapshot: {stage}')
        manifest = {'stage': stage, 'outputs': outputs, 'records': records}
        path = destination / 'snapshot.json'
        write_immutable_json(path, manifest)
        return {'path': str(path.relative_to(self.private)), 'sha256': sha256_path(path)}

    def snapshot(self, event: dict) -> tuple[Path, dict]:
        ref = event['checkpoint']
        path = safe_path(self.private, ref['path'])
        if sha256_path(path) != ref['sha256']:
            raise ValueError('checkpoint manifest hash mismatch')
        snapshot = read_json(path)
        if snapshot['stage'] != event['stage']:
            raise ValueError('checkpoint stage mismatch')
        # Validate all references and backup bytes before any recovery writes.
        if inventory_tree(path.parent / 'files', snapshot['outputs']) != snapshot['records']:
            raise ValueError(f"checkpoint content drift: {event['stage']}")
        return path.parent / 'files', snapshot

    def restore_stage(self, event: dict, *, repair: bool = False):
        backup, snapshot = self.snapshot(event)
        outputs = snapshot['outputs']
        for relative in outputs:
            path = safe_path(self.root, relative)
            expected = [item for item in snapshot['records']
                        if item['path'] == relative or item['path'].startswith(relative + '/')]
            if path.exists():
                current = inventory_tree(self.root, [relative])
                if current == expected:
                    continue
                expected_by_path = {item['path']: item for item in expected}
                only_missing = all(expected_by_path.get(item['path']) == item for item in current)
                if only_missing:
                    # Restore missing children without moving an intact large
                    # runtime/heap directory or overwriting existing bytes.
                    for item in expected:
                        destination = safe_path(self.root, item['path'])
                        if destination.exists():
                            continue
                        if item['kind'] == 'directory':
                            destination.mkdir(parents=True, exist_ok=True)
                            destination.chmod(item['mode'])
                        else:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(backup / item['path'], destination)
                    continue
                if not repair:
                    raise ValueError(f'artifact drift: {relative}; use restore for explicit recovery')
                self.quarantine([relative])
            source = backup / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, path)
            else:
                shutil.copy2(source, path)
        if inventory_tree(self.root, outputs) != snapshot['records']:
            raise ValueError(f"restored artifacts differ: {event['stage']}")

    def quarantine(self, outputs: list[str]):
        existing = [relative for relative in outputs if safe_path(self.root, relative).exists()]
        if not existing:
            return
        target = self.private / 'incomplete' / f'{len(self.events()) + 1:06d}'
        # Record intent before moving interrupted files; retain raw diagnostics.
        self.append('outputs-quarantined', paths=existing,
                    destination=str(target.relative_to(self.private)))
        for relative in existing:
            source = self.root / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))

    def run_stage(self, stage: str, outputs: list[str], action: Callable[[], None]):
        prior = self.completed().get(stage)
        if prior:
            self.restore_stage(prior)
            self.append('stage-reused', stage=stage, checkpoint=prior['checkpoint'])
            return
        # A new attempt never promotes remnants of an interrupted stage to PASS.
        self.quarantine(outputs)
        self.append('stage-started', stage=stage, outputs=outputs)
        try:
            action()
            checkpoint = self.checkpoint(stage, outputs)
            self.append('stage-completed', stage=stage, checkpoint=checkpoint)
        except BaseException as error:
            self.append('stage-failed', stage=stage, error_type=type(error).__name__,
                        diagnostic=str(error))
            raise
