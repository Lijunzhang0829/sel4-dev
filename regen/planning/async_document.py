"""Task-local document snapshots; concurrent drafts never become check inputs."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import hashlib
import shutil

DOCUMENT_ROOT = ContextVar('regen_document_snapshot', default=None)


def document_root(path):
    path = Path(path)
    binding = DOCUMENT_ROOT.get()
    return binding[1] if binding and path.resolve() == binding[0] else path


class DocumentSnapshot:
    def __init__(self, source, destination):
        self.source = Path(source).resolve()
        self.root = Path(destination).resolve()
        shutil.copytree(self.source, self.root)
        self.original = {p.relative_to(self.root): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in self.root.rglob('*') if p.is_file()}

    @contextmanager
    def activate(self):
        token = DOCUMENT_ROOT.set((self.source, self.root))
        try:
            yield
        finally:
            DOCUMENT_ROOT.reset(token)

    def publish_feedback(self):
        # Drafts and edit pointers belong to the foreground. Only checker-created
        # workflow evidence is copied back, after verifying the old bytes.
        for p in self.root.rglob('*'):
            if not p.is_file():
                continue
            relative = p.relative_to(self.root)
            if relative.parts[0] not in {'workflow', 'sketch-checks', 'sketch-certificates'}:
                continue
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if sha == self.original.get(relative):
                continue
            target = self.source / relative
            old = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
            if old not in (self.original.get(relative), sha):
                raise RuntimeError('concurrent workflow evidence changed: ' + str(relative))
            if old != sha:
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + '.async-tmp')
                shutil.copyfile(p, temp)
                temp.replace(target)
