"""Reap one planner's model container/private credentials after owner death."""
import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


def owner_identity(pid):
    try:
        tail=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return None if tail[0]=='Z' else tail[19]
    except FileNotFoundError:return None


def validate_private(path):
    path=Path(path)
    if path.is_symlink() or path.parent.resolve()!=Path(tempfile.gettempdir()).resolve() or not path.name.startswith('regen-planner-private-'):
        raise ValueError('not an owned planner temporary directory')
    info=path.stat()
    if info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise ValueError('private directory ownership/permissions mismatch')
    return info.st_dev,info.st_ino


def watch(private,owner,identity,container):
    private=Path(private)
    original=validate_private(private)
    if not re.fullmatch(r'regen-model-[0-9a-f]{32}',container):raise ValueError('invalid owned model name')
    (private/'guardian.ready').write_text(str(os.getpid()))
    while owner_identity(owner)==identity:
        time.sleep(.2)
    from regen.execution.native_container import remove_owned
    remove_owned(container)
    if private.exists():
        if validate_private(private)!=original:raise ValueError('private directory identity changed')
        shutil.rmtree(private)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--private',required=True)
    parser.add_argument('--owner',required=True,type=int)
    parser.add_argument('--identity',required=True)
    parser.add_argument('--container',required=True)
    args=parser.parse_args()
    watch(args.private,args.owner,args.identity,args.container)


if __name__=='__main__':main()
