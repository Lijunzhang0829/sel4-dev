"""Transport for the original VERO shell agent; no host shell is exposed."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
from regen.infrastructure.model_isolation import ModelIsolation


def run(config, logical):
    if not logical or Path(logical[0]).resolve() != Path(config['client']).resolve():
        raise ValueError('unexpected model executable')
    logical=list(logical)
    # Inner CLI sandbox is redundant inside the dedicated restricted container.
    # Never apply this translation to a host subprocess.
    index=logical.index('--sandbox')
    if logical[index+1]!='workspace-write':
        raise ValueError('expected workspace-write launcher configuration')
    logical[index+1]='danger-full-access'
    owner=os.getppid()
    child=None
    isolation=ModelIsolation(base=config['transport_base'],client=config['client'],
        cwd=config['workspace'],home=config['auth_home'],image=config['image'],proxy=config.get('proxy'))
    try:
        command=isolation.command(logical)
        index=command.index(config['image'])
        command[index:index]=[
            '--mount',f"type=bind,src={config['socket_dir']},dst=/vero-tools,readonly",
            '--mount',f"type=bind,src={Path(__file__).with_name('vero_application_tool.py').resolve()},dst=/backend/tool.py,readonly"]
        def stop(signum,frame):
            raise KeyboardInterrupt('model launcher interrupted')
        signal.signal(signal.SIGTERM,stop)
        child=subprocess.Popen(command)
        while True:
            if os.getppid()!=owner:
                raise RuntimeError('VERO supervisor exited')
            try:
                result=child.wait(timeout=1)
                if result:raise subprocess.CalledProcessError(result,command)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        try:isolation.close()
        finally:
            if child is not None:
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    logical=args.command[1:] if args.command[:1]==['--'] else args.command
    run(json.loads(args.config.read_text()),logical)


if __name__=='__main__':main()
