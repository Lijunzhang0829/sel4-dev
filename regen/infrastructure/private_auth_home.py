"""Disposable model HOME with opt-in refresh persistence to a private store.

Only auth.json leaves the disposable directory. The caller must first copy its
external login seed into a run-specific private directory; this helper never
initializes that store or writes to the user's normal Codex HOME. A nonblocking
source lock covers copy, transport lifetime and writeback, so two transports
cannot refresh the same state concurrently.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


PERSIST_ENV = 'REGEN_PERSIST_PRIVATE_AUTH'


class PrivateAuthPersistenceError(RuntimeError):
    """Private provider state could not be safely retained; never retry silently."""


class PrivateAuthHome:
    def __init__(self, base, home):
        self.base = Path(base)
        self.home = Path(home)
        self._stop_checks = []

    def require_stopped(self, check):
        """Register trusted cleanup evidence before starting a model transport."""
        self._stop_checks.append(check)

    def stopped(self):
        return all(check() is True for check in self._stop_checks)


def _private_store(directory, forbidden_roots):
    directory = Path(directory).absolute()
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError('persistent provider HOME must not use symlink paths')
    if directory == (Path.home() / '.codex').resolve():
        raise ValueError('persistent provider HOME must be run-private, not the user Codex HOME')
    if {'reports', 'release', 'logs', 'transcript', 'transcripts'} & set(directory.parts):
        raise ValueError('persistent provider HOME must be outside application and result artifacts')
    roots = [Path(__file__).resolve().parents[2], *map(Path, forbidden_roots)]
    if any(directory.is_relative_to(root.resolve()) for root in roots):
        raise ValueError('persistent provider HOME must be outside application and result artifacts')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('persistent provider HOME must be owned and mode 0700')
    finally:
        os.close(fd)
    return directory


def _auth_source(path, *, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size < 1 or info.st_size > 16 * 1024 * 1024
                or private and stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('provider authentication must be an owned regular file with private store mode 0600')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _copy_auth(source, destination, *, private_source=False):
    """Atomically publish only a regular credential file, never its contents."""
    source = Path(source)
    destination = Path(destination)
    source_fd = _auth_source(source, private=private_source)
    temporary = None
    try:
        target_fd, temporary = tempfile.mkstemp(prefix='.auth-', dir=destination.parent)
        with os.fdopen(target_fd, 'wb') as target, os.fdopen(os.dup(source_fd), 'rb') as original:
            os.fchmod(target.fileno(), 0o600)
            shutil.copyfileobj(original, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        temporary = None
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(source_fd)
        if temporary is not None:
            os.unlink(temporary)


@contextmanager
def clean_codex_home(source_home=None, *, prefix='regen-private-auth-',
                     require_auth=False, forbidden_roots=()):
    """Yield a clean HOME; retain refreshed auth only under explicit opt-in.

    Default callers keep the historical copy-and-discard behavior. Persistence
    requires an explicit CODEX_HOME or source_home, an existing 0700 private
    store, and its existing 0600 auth.json. No user config, session or log file
    is copied in either direction. Stop checks run after the caller's transport
    cleanup; failed cleanup never authorizes a writeback.
    """
    explicit = source_home is not None or bool(os.environ.get('CODEX_HOME'))
    source_home = Path(source_home or os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    persist = os.environ.get(PERSIST_ENV) == '1'
    lock = None
    if persist:
        if not explicit:
            raise ValueError('persistent provider authentication requires explicit private CODEX_HOME')
        source_home = _private_store(source_home, forbidden_roots)
        lock = os.open(source_home / '.auth-refresh.lock',
                       os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('provider authentication lock must be an owned private file')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PrivateAuthPersistenceError('private provider authentication is already in use') from None
        except BaseException:
            os.close(lock)
            raise
    try:
        auth = source_home / 'auth.json'
        if persist:
            fd = _auth_source(auth, private=True)
            os.close(fd)
        elif require_auth and not auth.is_file():
            raise ValueError('Codex authentication unavailable.')
        with tempfile.TemporaryDirectory(prefix=prefix) as base:
            home = Path(base) / 'home'
            home.mkdir(mode=0o700)
            state = PrivateAuthHome(base, home)
            if auth.is_file():
                # Preserve permissive legacy seed handling without writeback.
                if persist:
                    _copy_auth(auth, home / 'auth.json', private_source=True)
                else:
                    shutil.copyfile(auth, home / 'auth.json')
                    (home / 'auth.json').chmod(0o600)
            try:
                yield state
            finally:
                if persist:
                    error = sys.exc_info()[1]
                    try:
                        if not state.stopped():
                            raise PrivateAuthPersistenceError('model cleanup was not confirmed; private authentication was not overwritten')
                        _private_store(source_home, forbidden_roots)
                        fd = _auth_source(auth, private=True)
                        os.close(fd)
                        _copy_auth(home / 'auth.json', auth)
                    except BaseException as persistence_error:
                        if error is None:
                            raise
                        error.add_note('private authentication persistence failed: ' + type(persistence_error).__name__)
    finally:
        if lock is not None:
            os.close(lock)
