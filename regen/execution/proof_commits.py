"""Durable acceptance journal. Candidate files and pointers are not authorities."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile


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
    def __init__(self, directory: Path, binding: dict, validate):
        self.directory = directory
        self.binding = binding
        self.validate = validate

    def replay(self):
        previous = None; state = None
        for number, path in enumerate(sorted((self.directory/'commits').glob('*.json')), 1):
            record = json.loads(path.read_text())
            body = {k:v for k,v in record.items() if k != 'commit_id'}
            if (path.name != f'{number:06d}.json' or record['sequence'] != number
                    or record['parent'] != previous or record['binding'] != self.binding
                    or record['commit_id'] != digest(body)):
                raise ValueError('invalid proof commit chain/binding')
            self.validate(state, record['state'], record['event'], record['evidence'])
            state = record['state']; previous = record['commit_id']
        head_path=self.directory/'HEAD.json'
        if head_path.exists():
            try: head=json.loads(head_path.read_text())
            except (ValueError,OSError): head={}
            if head.get('sequence',0)>len(list((self.directory/'commits').glob('*.json'))):
                raise ValueError('committed history is missing; cannot recover from pointer alone')
        if previous:
            durable(self.directory/'HEAD.json', {'commit_id':previous, 'sequence':number})
        return state

    def append(self, event, state, evidence):
        old = self.replay()
        self.validate(old, state, event, evidence)
        paths = sorted((self.directory/'commits').glob('*.json'))
        parent = json.loads(paths[-1].read_text())['commit_id'] if paths else None
        body = {'sequence':len(paths)+1, 'parent':parent, 'binding':self.binding,
                'event':event, 'state':state, 'evidence':evidence}
        record = {**body, 'commit_id':digest(body)}
        durable(self.directory/'commits'/f'{len(paths)+1:06d}.json', record, immutable=True)
        durable(self.directory/'HEAD.json', {'commit_id':record['commit_id'], 'sequence':len(paths)+1})
        return state


def freeze_evidence(value):
    """Embed checker source/log bytes before their mutable filenames are reused."""
    if isinstance(value, list): return [freeze_evidence(v) for v in value]
    if not isinstance(value, dict): return value
    result = {key:freeze_evidence(v) for key,v in value.items()}
    for key in ('source', 'log'):
        name = value.get(key)
        if isinstance(name, str) and Path(name).is_file():
            raw = Path(name).read_bytes()
            result[key+'_content'] = raw.decode('utf-8')
            result[key+'_sha256'] = hashlib.sha256(raw).hexdigest()
    return result
