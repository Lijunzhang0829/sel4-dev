"""Persistent private Codex authentication HOME for fresh VERO runs.

The original VERO agent starts new CLI threads over a shared proof workspace.
Its auth.json may be refreshed during a segment, so the mounted CODEX_HOME is
the private durable directory itself, never a disposable copy. On an explicit
resume the old runtime HOME is archived outside the next model's mount and its
refreshed auth.json is *moved* into a clean HOME. A two-rename staging directory
makes this rotation recoverable after an interrupted launcher operation.

The caller must own the frozen run and hold the common provider_auth_lease for
the entire model segment. This helper does not launch a provider, alter proof
state, replay a tool, or claim independent login chains.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from uuid import uuid4


@dataclass(frozen=True)
class VeroAuthState:
    home: Path
    auth: Path
    lease_sources: tuple[Path, Path]
    resumed: bool


def _private_directory(path, *, create=False):
    path=Path(path)
    if path.is_symlink():
        raise ValueError('provider auth state directory cannot be a symlink')
    if create:path.mkdir(mode=0o700)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)!=0o700):
            raise ValueError('provider auth state directory must be owned and mode 0700')
    finally:os.close(fd)


def _private_auth(path):
    path=Path(path)
    if path.is_symlink():
        raise ValueError('provider auth state cannot be a symlink')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
            raise ValueError('provider auth state must be an owned regular file')
        if stat.S_IMODE(info.st_mode)!=0o600:
            os.fchmod(fd,0o600)
    finally:os.close(fd)


def _fsync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _copy_seed(seed,auth):
    if seed.is_symlink() or not seed.is_file():
        raise ValueError('provider auth seed must be a regular file')
    source=os.open(seed,os.O_RDONLY|os.O_NOFOLLOW)
    temporary=None
    try:
        if not stat.S_ISREG(os.fstat(source).st_mode):
            raise ValueError('provider auth seed must be a regular file')
        target,temporary=tempfile.mkstemp(prefix='.auth-',dir=auth.parent)
        with os.fdopen(target,'wb') as dst,os.fdopen(os.dup(source),'rb') as src:
            os.fchmod(dst.fileno(),0o600)
            shutil.copyfileobj(src,dst)
            dst.flush();os.fsync(dst.fileno())
        os.replace(temporary,auth)
        temporary=None
        _fsync_directory(auth.parent)
    finally:
        os.close(source)
        if temporary is not None and os.path.exists(temporary):os.unlink(temporary)


def _clean_staging(staging):
    _private_directory(staging)
    members=list(staging.iterdir())
    if members:
        raise ValueError('unrecognized VERO auth staging contents')
    staging.rmdir()


def _finish_interrupted_rotation(home,staging,archive):
    """Recover only the two exact states produced by our rename transaction."""
    if staging.is_symlink() or home.is_symlink() or archive.is_symlink():
        raise ValueError('VERO auth rotation path cannot be a symlink')
    if not staging.exists():
        if not home.exists():
            raise ValueError('VERO auth HOME is missing; manual reconciliation required')
        return
    _private_directory(staging)
    members={item.name for item in staging.iterdir()}
    if not members:
        if not home.exists():
            raise ValueError('empty VERO auth staging without HOME')
        _clean_staging(staging)
        return
    if members!={'auth.json'}:
        raise ValueError('unrecognized VERO auth staging contents')
    _private_auth(staging/'auth.json')
    if home.exists():
        _private_directory(home)
        if (home/'auth.json').exists() or (home/'auth.json').is_symlink():
            raise ValueError('ambiguous VERO auth rotation requires reconciliation')
        _private_directory(archive,create=not archive.exists())
        os.rename(home,archive/('segment-'+uuid4().hex))
        _fsync_directory(archive)
    os.rename(staging,home)
    _fsync_directory(home.parent)


def _rotate_home(home,archive):
    staging=home.with_name('home.next')
    _finish_interrupted_rotation(home,staging,archive)
    _private_directory(home)
    _private_auth(home/'auth.json')
    _private_directory(archive,create=not archive.exists())
    _private_directory(staging,create=True)
    _fsync_directory(home.parent)
    os.rename(home/'auth.json',staging/'auth.json')
    _fsync_directory(staging)
    _fsync_directory(home)
    os.rename(home,archive/('segment-'+uuid4().hex))
    _fsync_directory(archive)
    os.rename(staging,home)
    _fsync_directory(home.parent)
    _private_auth(home/'auth.json')


def prepare_vero_auth_state(output,seed_auth,*,resume=False,state_base=None):
    """Return a persistent clean CODEX_HOME and inputs for the common lease.

    Fresh: exclusive state creation and 0600 seed copy. Resume: require the
    same output/seed-parent anchor, rotate old sessions out of the mounted HOME,
    and move the *current refreshed* auth.json without consulting seed content.
    ``state_base`` is optional and must be identical on fresh/resume. It must
    reside outside the experiment output. No secret or token hash is logged.
    """
    output=Path(output).absolute()
    seed=Path(seed_auth).absolute()
    if output.is_symlink():
        raise ValueError('VERO output cannot be a symlink')
    output=output.resolve(strict=False)
    if seed.is_symlink() or not seed.is_file():
        raise ValueError('provider auth seed must be a regular file')
    base=Path(state_base).absolute() if state_base is not None else seed.parent/'.regen-vero-auth-state'
    if base.is_symlink():
        raise ValueError('provider auth state directory cannot be a symlink')
    base=base.resolve(strict=False)
    if base==output or base.is_relative_to(output):
        raise ValueError('provider auth state must remain outside experiment output')
    if resume:
        _private_directory(base)
    else:
        if base.exists():_private_directory(base)
        else:_private_directory(base,create=True)
    run=base/hashlib.sha256(str(output).encode()).hexdigest()
    home=run/'home';auth=home/'auth.json'
    if resume:
        _private_directory(run)
        _rotate_home(home,run/'archived-homes')
    else:
        _private_directory(run,create=True)
        created_home=False
        try:
            _private_directory(home,create=True)
            created_home=True
            _copy_seed(seed,auth)
            _private_directory(home)
            _private_auth(auth)
        except BaseException:
            # A fresh failed initialization has no model process or accepted
            # state. Remove only our known leaf files and empty directories.
            if created_home:
                try:auth.unlink(missing_ok=True)
                except OSError:pass
                try:home.rmdir()
                except OSError:pass
            try:run.rmdir()
            except OSError:pass
            raise
    _private_directory(home)
    _private_auth(auth)
    return VeroAuthState(home=home,auth=auth,lease_sources=(seed,auth),resumed=resume)


def rotate_vero_auth_state(state):
    """Keep refreshed credentials but hide old CLI sessions before a new call.

    The caller must have released the prior model process and hold the run and
    provider-auth leases. A failed rotation is fail-closed; the next explicit
    resume can recover its exact staging transaction without consulting seed.
    """
    if not isinstance(state,VeroAuthState):
        raise TypeError('expected VeroAuthState')
    run=state.home.parent
    _private_directory(run)
    _rotate_home(state.home,run/'archived-homes')
    return state


def existing_vero_auth_source(output,seed_auth,*,state_base=None):
    """Locate refreshed auth for a pre-resume lease without changing state."""
    output=Path(output).resolve(strict=False)
    seed=Path(seed_auth).absolute()
    base=(Path(state_base).absolute() if state_base is not None
          else seed.parent/'.regen-vero-auth-state').resolve(strict=False)
    run=base/hashlib.sha256(str(output).encode()).hexdigest()
    current=run/'home'/'auth.json'
    staged=run/'home.next'/'auth.json'
    if staged.exists() or staged.is_symlink():
        if current.exists() or current.is_symlink():
            raise ValueError('ambiguous VERO auth rotation requires reconciliation')
        return staged
    return current
