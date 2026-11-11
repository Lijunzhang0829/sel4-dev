"""One official invocation, accounting gates, and checker-owned final evaluation.

No provider, proof strategy, or continuation loop is implemented here.
"""
import argparse
import hashlib
import json
import os
import pwd
import subprocess
import tempfile
import time
from pathlib import Path
from official_codex_observer import TimingObserver
from official_runtime_clock import apply_trusted_corrections

from vero.isabelle.benchmark import read, save, extract, prepare, render
from vero.isabelle.active_clock import ActiveClock, initial_state, atomic_state
from vero.isabelle.processes import kill_tree
from vero.isabelle.telemetry import initialize_run, EventLog
from vero.isabelle.response_usage import ResponseUsageCollector

PREFLIGHT_SCHEMA = 'official-codex-owned-context-v3'


class UsageSession:
    """Establish invocation identity before a child can produce any response."""
    def __init__(self, log, cfg, home, events, *, invocation_id=None):
        self.log = log
        self.identity = invocation_id or cfg['run_id'] + ':official-0'
        self.ended = False
        if any(e.get('invocation_id') == self.identity for e in log.events()):
            raise ValueError('fresh invocation identity already exists')
        log.emit('invocation_started', event_id='start:' + self.identity,
                 invocation_id=self.identity, requested_model=cfg['model'],
                 reasoning_effort=cfg['reasoning_effort'], role='repository_agent',
                 accounting_exclusive_home=True)
        self.collector = ResponseUsageCollector(log, codex_home=home, cli_events=events,
            invocation_id=self.identity, model=cfg['model'],
            reasoning_effort=cfg['reasoning_effort'], exclusive_home=True)
        if self.identity not in self.collector.invocations:
            raise RuntimeError('usage collector lacks invocation binding')

    def poll(self):
        self.collector.poll()
        # Auxiliary roots are tracked separately. Root responses must have an owner.
        unbound = [e for e in self.log.events() if e['kind'] == 'response_usage'
                   and e.get('thread_id') in self.collector.threads
                   and e.get('thread_id') not in self.collector.auxiliary_threads
                   and e.get('invocation_id') is None]
        if unbound:
            raise RuntimeError('observed root responses lack invocation ownership')
        return self.collector.summary()

    def finish(self, status, **details):
        if self.ended:
            raise ValueError('invocation already ended')
        self.poll()
        result = self.collector.finish(status)
        self.log.emit('invocation_finished', event_id='end:' + self.identity,
                      invocation_id=self.identity, status=status, **details)
        self.ended = True
        return result


def checker_root(results):
    """Private trusted output shared only by root and the checking account."""
    account = pwd.getpwnam('checker')
    path = Path(results) / 'independent-checker'
    path.mkdir(mode=0o700, exist_ok=False)
    os.chown(path, account.pw_uid, account.pw_gid)
    os.chmod(path, 0o700)
    return path


def evaluation_command(cfg, benchmark, artifact, output, report):
    checker = dict(cfg['final_checker'])
    # The entire evaluator changes UID, including rendering its input files.
    # Running runuser again inside the unprivileged checker would fail.
    prefix = checker.pop('command_prefix', [])
    if prefix != ['runuser', '-u', 'checker', '--']:
        raise ValueError('independent evaluation requires the checker identity')
    checker['env'] = {**checker.get('env', {}),
                      'HOME': '/home/checker', 'USER_HOME': '/home/checker'}
    code = ('from vero.isabelle.benchmark import read,save; '
            'from vero.isabelle.checker import Checker,evaluate; '
            f'save({str(report)!r},evaluate(read({str(benchmark)!r}),'
            f'read({str(artifact)!r}),{str(output)!r},Checker({checker!r})))')
    return prefix + ['python3', '-c', code]


def handoff_artifact(directory, artifact):
    path = Path(directory) / 'candidate-artifact.json'
    save(path, artifact)
    account = pwd.getpwnam('checker')
    os.chown(path, 0, account.pw_gid)
    os.chmod(path, 0o640)
    return path


def accounting_probe(directory):
    """Synthetic log fixture, never a model call or a benchmark answer."""
    root = Path(directory)
    log = EventLog(root/'metrics', {'schema_version': 1, 'run_id': 'preflight-only'})
    home = root/'home'
    events = root/'events.jsonl'
    events.write_text(json.dumps({'type': 'thread.started', 'thread_id': 'probe'})+'\n')
    session = UsageSession(log, {'run_id': 'preflight-only', 'model': 'synthetic',
                                'reasoning_effort': 'low'}, home, events)
    path = home/'sessions/probe.jsonl'
    path.parent.mkdir(parents=True)
    row = {'timestamp': time.time(), 'type': 'token_usage_record', 'payload': {
        'thread_id': 'probe', 'turn_id': 'probe-turn', 'response_id': 'probe-response',
        'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}}
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'probe'}})+'\n'
                    +json.dumps(row)+'\n'+json.dumps(row)+'\n')
    session.poll(); session.poll()
    result = session.finish('interrupted')
    if result['reported_responses'] != 1 or result['usage']['total_tokens'] != 12:
        raise RuntimeError('response attribution or deduplication failed')
    if result['usage_complete'] or not result['unreported_usage_possible']:
        raise RuntimeError('interrupted response tail must remain uncertain')
    return {'responses': 1, 'tokens': 12, 'duplicate_counted_once': True,
            'interrupted_tail_unknown': True}


def preflight(cfg, results=Path('/results')):
    """Exercise both identities and the full native evaluation path without a model."""
    # Keep diagnostic outputs separate from final evaluation and all old runs.
    with tempfile.TemporaryDirectory(prefix='codex-preflight-', dir=results) as tmp:
        root = Path(tmp)
        (root/'accounting').mkdir()
        accounting = accounting_probe(root/'accounting')
        work = checker_root(root)
        source = root/'synthetic.jsonl'
        source.write_text(json.dumps({'contract_id': 'probe', 'name': 'probe',
            'exact_statement': 'lemma probe: "True"', 'owner_theory': 'Probe'})+'\n'+json.dumps({'contract_id':'negative-probe','name':'negative_probe','exact_statement':'lemma negative_probe: "False"','owner_theory':'Probe','contract_owned_context':['context fixes n :: nat assumes env: "n = 0" begin']})+'\n')
        # Checker needs to traverse this diagnostic root and read its frozen input.
        account = pwd.getpwnam('checker')
        os.chown(root, 0, account.pw_gid); os.chmod(root, 0o750)
        benchmark = root/'benchmark'
        bench = prepare(source, benchmark, base_import=cfg.get('base_import','Main'), isabelle_api=cfg.get('isabelle_api','2026'))
        for item in [benchmark, *benchmark.rglob('*')]:
            os.chown(item, 0, account.pw_gid)
            os.chmod(item, 0o750 if item.is_dir() else 0o640)
        rendered = root/'rendered'
        render(bench, rendered, {'c0001:prove': 'by simp', 'c0002:disprove':'by auto'})
        artifact = handoff_artifact(work, extract(bench, rendered))
        report = work/'report.json'
        proc = subprocess.run(evaluation_command(cfg, benchmark/'benchmark.json', artifact,
            work/'evaluation', report), capture_output=True, text=True, timeout=cfg.get('checker',{}).get('total_timeout_seconds',300)+60)
        if proc.returncode or not report.exists():
            raise RuntimeError('checker preflight failed: '+proc.stderr[-2000:])
        value = read(report)
        if (value.get('proved_specs') != 1 or value.get('context_disproved_specs') != 1
                or value.get('strict_global_disproved_specs') != 0
                or (value.get('integration') or {}).get('status') != 'ok'):
            save(results/'preflight-failure.json',value)
            raise RuntimeError('native independent evaluator did not accept synthetic proof: '+json.dumps(value)[-6000:])
        declarations=None
        if cfg.get('input_binding'):
            # Check the real frozen render, with every positive/negative slot
            # still oops. This elaborates statements; it proves no benchmark goal.
            checker=dict(cfg['final_checker']);prefix=checker.pop('command_prefix')
            checker['env']={**checker.get('env',{}),'HOME':'/home/checker','USER_HOME':'/home/checker'}
            declaration_report=work/'current-declarations.json'
            declaration_source=work/'current-declarations'
            code=('from vero.isabelle.benchmark import read,render,save; '
                'from vero.isabelle.checker import Checker; '
                'b=read("/benchmark/benchmark.json"); '
                f'render(b,{str(declaration_source)!r}); '
                f'r=Checker({checker!r}).check({str(declaration_source)!r},"Vero_All"); '
                f'save({str(declaration_report)!r},r)')
            checked=subprocess.run(prefix+['python3','-c',code],capture_output=True,text=True,timeout=cfg.get('checker',{}).get('total_timeout_seconds',300)+60)
            if checked.returncode or not declaration_report.exists():
                raise RuntimeError('current declaration preflight failed: '+checked.stderr[-2000:])
            declared=read(declaration_report)
            if declared['status']!='ok':
                save(results/'preflight-declarations-failure.json',declared)
                raise RuntimeError('current declaration preflight rejected: '+declared['log'][-2000:])
            declarations={'status':'pass','targets':len(read('/benchmark/benchmark.json')['contracts']),
                'dataset_binding':cfg['input_binding']['dataset_binding'],
                'native_seconds':declared['elapsed_seconds'],'benchmark_proofs':0,
                'comparison_counts':cfg['comparison_groups']['counts']}
        with_observer = TimingObserver(root/'observer-probe')
        try:
            command=cfg['command']
            cli=command[:command.index('exec')]
            checked=subprocess.run(cli+with_observer.config_args()+['features','list'],
                capture_output=True,text=True,timeout=30,
                env=dict(os.environ,HOME='/home/agent',CODEX_HOME='/home/agent/.codex'))
            if checked.returncode:
                raise RuntimeError('official CLI observation config rejected: '+checked.stderr[-500:])
        finally:with_observer.close()
        return {'schema': PREFLIGHT_SCHEMA, 'status': 'pass', 'model_calls': 0,
                'method': method_identity(cfg),
                'invocation_accounting': accounting,
                'checker_owned_render_and_native_integration': 'pass',
                'default_owned_context_positive_and_negative': 'pass',
                'current_dataset_declarations':declarations,
                'official_cli_observation_config': 'accepted_without_model_call'}


def method_identity(cfg):
    method=cfg.get('method','official-codex')
    if method not in ('vero','official-codex'):
        raise ValueError('unsupported frozen baseline method')
    return method


def cleanup_abandoned_agent_processes(results):
    """After agent termination, reap its remaining native descendants, not root/checker."""
    import signal
    uid=pwd.getpwnam('agent').pw_uid
    killed=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if proc.stat().st_uid!=uid:continue
            pid=int(proc.name)
            os.kill(pid,signal.SIGKILL);killed.append(pid)
        except (FileNotFoundError,ProcessLookupError):pass
    save(Path(results)/'agent-descendant-cleanup.json',{'at_unix':time.time(),'pids':killed})


def main(cfg, results=Path('/results')):
    if cfg.get('validation_only'):
        raise ValueError('validation-only deployment cannot run generation')
    method=method_identity(cfg)
    if cfg.get('observation_profile')!='official-otel-metadata-v1':
        raise ValueError('new runtime requires a frozen operation-observation profile')
    if (results/'started.json').exists():
        raise ValueError('fresh run cannot be launched twice')
    for path, expected in read('/control/frozen-files.json').items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError('frozen source mismatch: '+path)
    receipt = read('/control/runtime-preflight.json')
    if receipt.get('schema') != PREFLIGHT_SCHEMA or receipt.get('status') != 'pass':
        raise ValueError('accounting/independent-checker preflight required')
    # Verify before starting a model, rather than discovering bad ownership at the end.
    independent = checker_root(results)
    subprocess.run(['runuser', '-u', 'checker', '--', 'python3', '-c',
        f'from pathlib import Path; p=Path({str(independent / "permission-probe")!r}); '
        'p.write_text("ok"); assert p.read_text()=="ok"; p.unlink()'], check=True)
    path = Path('/run/experiment-budget.json')
    atomic_state(path, initial_state(cfg['run_id'], cfg['budget_seconds'], time.time()))
    clock = ActiveClock(path); clock.resume()
    log = initialize_run(results/'metrics', {**cfg, 'agent': {
        'model': cfg['model'], 'reasoning_effort': cfg['reasoning_effort']}},
        read('/benchmark/benchmark.json'), method, 'frozen-deployment')
    session = UsageSession(log, cfg, '/home/agent/.codex', results/'agent-events.jsonl')
    reserve=cfg.get('final_evaluation_reserve_seconds',0)
    if type(reserve) not in (int,float) or not 0<=reserve<cfg['budget_seconds']:
        raise ValueError('final evaluation reserve must fit total experiment budget')
    reserved_cutoff=False
    proc = evaluator = None
    observer = None
    try:
        observer = TimingObserver(results/'operation-observation')
        actual_command=observer.command(cfg['command'])
        env = dict(os.environ, HOME='/home/agent', USER='agent', CODEX_HOME='/home/agent/.codex')
        with open('/control/PROMPT.txt', 'rb') as prompt, open(results/'agent-events.jsonl', 'wb') as out, open(results/'agent-stderr.log', 'wb') as err:
            proc = subprocess.Popen(actual_command, stdin=prompt, stdout=out, stderr=err,
                                    cwd='/sandbox', env=env, start_new_session=True)
            save(results/'started.json', {'at_unix': time.time(), 'pid': proc.pid,
                 'command': actual_command, 'task_command': cfg['command'],
                 'observation_profile': cfg['observation_profile'], 'agent_invocations': 1})
            while proc.poll() is None:
                usage = session.poll()  # Accounting corruption is an explicit blocker.
                apply_trusted_corrections(clock,results)
                state = clock.snapshot()
                save(results/'health.json', {'status': 'running', 'at_unix': time.time(),
                    'clock': state, 'pid': proc.pid, 'usage': usage,
                    'operation_observation': observer.snapshot()})
                if state['remaining_seconds'] <= reserve:
                    reserved_cutoff=reserve>0
                    kill_tree(proc.pid); break
                time.sleep(2)
            code = proc.wait()
        cleanup_abandoned_agent_processes(results)
        usage = session.finish('completed' if code == 0 else 'interrupted', returncode=code)
        save(results/'usage.json', {'usage': usage})
        save(results/'agent-finished.json', {'at_unix': time.time(), 'returncode': code,
                                            'clock': clock.snapshot(), 'agent_invocations': 1})
        bench = read('/benchmark/benchmark.json')
        artifact = extract(bench, Path('/sandbox'))
        save(results/'candidate-artifact.json', artifact)
        handoff = handoff_artifact(independent, artifact)
        report_path = independent/'final-report.json'
        evaluation_status = 'budget_exhausted_before_evaluation'
        if clock.snapshot()['remaining_seconds'] > 0:
            with open(results/'final-evaluator.log', 'wb') as stderr:
                evaluator = subprocess.Popen(evaluation_command(cfg, '/benchmark/benchmark.json',
                    handoff, independent/'final-eval', report_path), stdout=stderr, stderr=stderr,
                    start_new_session=True)
                while evaluator.poll() is None:
                    apply_trusted_corrections(clock,results)
                    state = clock.snapshot()
                    save(results/'health.json', {'status': 'independent_final_evaluation',
                                                'at_unix': time.time(), 'clock': state})
                    if state['remaining_seconds'] <= 0:
                        kill_tree(evaluator.pid); break
                    time.sleep(2)
                evaluation_status = 'completed' if evaluator.wait() == 0 and report_path.exists() else 'failed_or_interrupted'
        report = read(report_path) if report_path.exists() else None
        if report is not None:save(results/'final-report.json', report)
        observer.close()
        reason = ('budget_exhausted' if clock.snapshot()['remaining_seconds'] <= 0 else
                  'generation_allowance_exhausted' if reserved_cutoff else
                  'agent_error' if code else 'final_evaluation_error' if evaluation_status != 'completed'
                  else 'official_agent_returned')
        clock.finish(reason)
        summary = {'status': 'finished', 'method': method, 'reason': reason, 'agent_returncode': code,
                   'evaluation_status': evaluation_status, 'generation_stopped_for_final_evaluation':reserved_cutoff,
                   'final_evaluation_reserve_seconds':reserve, 'clock': clock.snapshot(),
                   'report': report, 'operation_observation': {**observer.snapshot(),
                       'export_tail_may_be_missing': code != 0}, 'agent_invocations': 1, 'automatic_restart': False}
        save(results/'final-summary.json', summary)
        save(results/'health.json', {**summary, 'at_unix': time.time()})
        return 0 if (code == 0 or reserved_cutoff) and evaluation_status == 'completed' else 1
    except BaseException as exc:
        for child in (proc, evaluator):
            if child is not None and child.poll() is None:kill_tree(child.pid); child.wait()
        cleanup_abandoned_agent_processes(results)
        if not session.ended:
            log.emit('invocation_finished', event_id='end:'+session.identity,
                     invocation_id=session.identity, status='interrupted',
                     reason='supervisor_error')
        save(results/'controller-error.json', {'type': type(exc).__name__,
              'message': str(exc), 'at_unix': time.time()})
        if observer is not None:observer.close()
        clock.finish('supervisor_error')
        save(results/'health.json', {'status': 'failed', 'clock': clock.snapshot(),
                                    'at_unix': time.time()})
        raise
    finally:
        if observer is not None:observer.close()
        clock.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--preflight', action='store_true')
    a = p.parse_args()
    cfg = read('/control/config.json')
    if a.preflight:
        print(json.dumps(preflight(cfg), indent=2))
    else:
        raise SystemExit(main(cfg))
