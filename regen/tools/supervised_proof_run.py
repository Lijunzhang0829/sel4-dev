#!/usr/bin/env python3
"""Run a committed continuation under an external service manager.

Launch this supervisor via systemd-run --user, not a conversation tool session.
Supervisor records describe process health; proof commits remain authoritative.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def worker(root, source, theory):
    from regen.execution.proof_session_runner import run_file
    from regen.infrastructure.progress_events import configure_event_monitor, close_event_monitor
    policy = json.loads((source/'theories'/theory/'runtime-policy.json').read_text())
    if policy.get('context_retry_rounds', 1) != 1:
        raise ValueError('public runner currently supports one context reserve')
    models = json.loads((root/'run.json').read_text())['execution_models']
    outcome = {'run_id': root.name, 'source_run': source.name, 'started_at': now(), 'pid': os.getpid()}
    configure_event_monitor(root, run_id=root.name, theory=theory, heartbeat_seconds=30)
    def interrupted(signum, frame):
        outcome['signal'] = signum
        raise InterruptedError(f'runner received signal {signum}')
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        result = run_file(root, theory, provider=policy['provider'],
            planner_model=models['File Planner'], revision_model=models['Graph Revision Agent'],
            prover_model=models['Lemma Prover'], lemma_backend=policy['backend'],
            timeout=policy['timeout'], max_attempts=policy['max_attempts'],
            max_node_rounds=policy['max_node_rounds'], max_node_revisions=policy['max_node_revisions'],
            graph_timeout=policy['graph_timeout'], think_timeout=policy['think_timeout'],
            node_timeout=policy['node_timeout'], salvage_provider=policy['salvage_provider'])
        outcome.update(status=result['status'], result=result)
    except BaseException as error:
        outcome.update(status='infrastructure_failed', error=repr(error))
        (root/'runner-error.txt').write_text(traceback.format_exc())
        raise
    finally:
        outcome['finished_at'] = now()
        save(root/'OUTCOME.json', outcome)
        close_event_monitor(outcome.get('status', 'interrupted'))


def cleanup_containers(root):
    """Only dispose prefix/checker containers mounted from this exact run."""
    result = subprocess.run(['docker','ps','-q'],capture_output=True,text=True,timeout=15)
    removed = []
    for ident in result.stdout.split():
        info = subprocess.run(['docker','inspect',ident],capture_output=True,text=True,timeout=15)
        if info.returncode:
            continue
        item = json.loads(info.stdout)[0]
        if not item['Name'].startswith(('/regen-prefix-', '/regen-isabelle-check-')):
            continue
        if not any(Path(m.get('Source','/')).is_relative_to(root) for m in item['Mounts']):
            continue
        stopped = subprocess.run(['docker','stop','--timeout','5',ident],capture_output=True,text=True,timeout=20)
        removed.append({'id':ident,'returncode':stopped.returncode})
    return removed


def supervise(root, command, *, cleanup=True, interval=30):
    root.mkdir(parents=True, exist_ok=True)
    if (root/'supervisor-status.json').exists():
        raise RuntimeError('refusing to overwrite a supervised invocation; use a new continuation')
    status = {'run_id':root.name, 'started_at':now(), 'supervisor_pid':os.getpid(),
              'supervisor_ppid':os.getppid(), 'unit':os.environ.get('SYSTEMD_UNIT'),
              'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'status':'starting', 'running':False}
    proc = None
    def stop(signum, frame):
        status['supervisor_signal'] = signum
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
    old = {sig:signal.signal(sig,stop) for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP)}
    try:
        with (root/'runner.log').open('x') as log:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, cwd=REPO, start_new_session=True)
            status.update(status='running',running=True,runner_pid=proc.pid)
            stat = Path(f'/proc/{proc.pid}/stat').read_text().rsplit(')',1)[1].split()
            status['runner_start_ticks'] = stat[19]
            while True:
                status['observed_at'] = now()
                heartbeat = root/'runner-status.json'
                if heartbeat.exists():
                    try:
                        h=json.loads(heartbeat.read_text())
                        stamp=h.get('last_heartbeat_at') or h.get('started_at')
                        status['last_runner_heartbeat_at']=stamp
                        age=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()
                        status['heartbeat_stale']=age>120
                    except (ValueError,TypeError):
                        status['heartbeat_stale']=True
                save(root/'supervisor-status.json',status)
                try:
                    code=proc.wait(timeout=interval)
                    break
                except subprocess.TimeoutExpired:
                    if status.get('supervisor_signal'):
                        os.killpg(proc.pid,signal.SIGKILL)
            status.update(running=False,returncode=code,signal=-code if code<0 else None,
                          status='exited' if code==0 else 'process_failed',finished_at=now())
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid,signal.SIGKILL)
            status.update(running=False,returncode=proc.wait(),status='supervisor_failed',finished_at=now())
        save(root/'supervisor-status.json',status)
        if cleanup:
            try: status['container_cleanup']=cleanup_containers(root)
            except Exception as error: status['cleanup_error']=repr(error)
        status['outcome_present']=(root/'OUTCOME.json').exists()
        if not status.get('running') and (root/'runner-status.json').exists():
            h=json.loads((root/'runner-status.json').read_text())
            if h.get('running'):
                h.update(running=False,stop_reason='supervisor_observed_exit',stopped_at=now())
                save(root/'runner-status.json',h)
        save(root/'supervisor-status.json',status)
        for sig, handler in old.items(): signal.signal(sig,handler)
    return status.get('returncode',1)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('run_dir',type=Path)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--theory',default='Kernel_Object_Access')
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args();root=args.run_dir.resolve();source=args.source.resolve()
    if args.worker:
        worker(root,source,args.theory)
        return 0
    return supervise(root,[sys.executable,'-u',str(Path(__file__).resolve()),str(root),
        '--source',str(source),'--theory',args.theory,'--worker'])

if __name__=='__main__':
    sys.exit(main())
