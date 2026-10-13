"""Real native cancellation behind a synthetic protocol producer (never a model).

Exercises the exact production session abort hook, TheoryController ownership
filter, CheckExecutor token, native_run cancellation, and acceptance_guard. The
ownership fixture deliberately does NOT initialize a repository controller or
exercise document acceptance/ProofCommits. No existing experiment is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

from regen.execution.check_executor import CheckExecutor, native_run
from regen.execution.check_tasks import CheckCancelled, TERMINAL, acceptance_guard
from regen.execution.proof_commits import durable
from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, SessionProtocolError
from regen.planning.parallel_file_planner import TheoryController
from regen.preparation.checker_context import load_context
from regen.tools.native_backend_acceptance import Limits, catalogs, command


THEORY = 'Protocol_Native_Fixture'
TOOLS = [{'name': 'proof_action', 'description': 'Generic native fixture; no target proofs',
          'inputSchema': {'type': 'object', 'properties': {'action': {'const': 'native_check'}},
                          'required': ['action'], 'additionalProperties': False}}]

SERVER = r'''
import json, pathlib, sys, threading, time
mode, marker, trace_path = sys.argv[1:]
trace = open(trace_path, 'x')
lock = threading.Lock()
def record(direction, message):
    trace.write(json.dumps({'time_unix':time.time(),'direction':direction,'message':message})+'\n')
    trace.flush()
def send(message):
    with lock:
        message['emittedAtMs'] = int(time.time()*1000)
        record('out', message)
        print(json.dumps(message), flush=True)
def attack_when_native_started():
    deadline = time.monotonic()+100
    while not pathlib.Path(marker).exists():
        if time.monotonic()>deadline:
            send({'method':'error','params':{'message':'fixture never saw native start'}})
            return
        time.sleep(.01)
    if mode=='unsafe':
        send({'method':'item/started','params':{'threadId':'fixture-thread','turnId':'fixture-turn',
             'item':{'type':'commandExecution','id':'forbidden-built-in'}}})
    elif mode=='foreign':
        send({'id':101,'method':'item/tool/call','params':{'threadId':'foreign-thread',
             'turnId':'fixture-turn','callId':'foreign-call','namespace':None,'tool':'proof_action',
             'arguments':{'action':'native_check'}}})
for raw in sys.stdin:
    message=json.loads(raw)
    with lock: record('in',message)
    if message.get('method')=='initialize':
        send({'id':message['id'],'result':{'userAgent':'synthetic-native-cancel-producer'}})
    elif message.get('method')=='thread/start':
        send({'id':message['id'],'result':{'thread':{'id':'fixture-thread'}}})
    elif message.get('method')=='turn/start':
        send({'id':message['id'],'result':{'turn':{'id':'fixture-turn'}}})
        send({'id':100,'method':'item/tool/call','params':{'threadId':'fixture-thread',
              'turnId':'fixture-turn','callId':'native-call','namespace':None,
              'tool':'proof_action','arguments':{'action':'native_check'}}})
        if mode!='legal': threading.Thread(target=attack_when_native_started,daemon=True).start()
    elif message.get('id')==100 and 'result' in message:
        send({'method':'turn/completed','params':{'threadId':'fixture-thread',
              'turn':{'id':'fixture-turn','status':'completed'}}})
'''


def native_source(import_name, case, delay):
    if case not in ('unsafe', 'foreign', 'legal') or delay not in (0, 45):
        raise ValueError('only fixed native fixtures are permitted')
    return (f'theory {THEORY}\nimports "{import_name}"\nbegin\n'
            'ML \\<open>\n'
            f'File.write (Path.explode "/acceptance/{case}/native-started.txt") (Time.toString (Time.now ()));\n'
            f'OS.Process.sleep (Time.fromSeconds {delay});\n'
            f'File.write (Path.explode "/acceptance/{case}/native-finished.txt") (Time.toString (Time.now ()))'
            '\n\\<close>\nlemma native_generic_true: "True" by simp\nend\n')


def ownership_bridge(executor, run_id):
    # Only the ownership inputs required by the actual production abort method.
    # This is NOT a fully initialized TheoryController, nor a mocked cancel hook.
    return SimpleNamespace(executor=executor, theory=THEORY, run={'run_id':run_id},
                           _abort_requested=threading.Event())


def pass_conditions(mode, *, error_type, diagnostic, native_started, native_finished,
                    task_status, acceptance_written, result_written, completion_written,
                    late_guard_blocked, response_count, child_exited, abort_count,
                    abort_latency):
    if mode == 'legal':
        return (error_type is None and native_started and native_finished
                and task_status == 'completed' and acceptance_written
                and result_written and completion_written and response_count == 1
                and child_exited and abort_count == 0)
    expected = ('non-allowlisted built-in item' if mode == 'unsafe'
                else 'tool callback belongs to another thread/turn')
    return (error_type == 'SessionProtocolError' and expected in diagnostic
            and native_started and not native_finished and task_status == 'cancelled'
            and not acceptance_written and not result_written and not completion_written
            and late_guard_blocked and response_count == 0 and child_exited
            and abort_count == 1 and abort_latency is not None and 0 <= abort_latency <= 5)


def case_run(ctx, output, mode, timeout, limits):
    root = output / mode
    root.mkdir()
    text = native_source(ctx['base_import'], mode, 0 if mode == 'legal' else 45)
    source_path = root / (THEORY + '.thy')
    source_path.write_text(text)
    executor = CheckExecutor(root / 'tasks', workers=1)
    bridge = ownership_bridge(executor, 'native-protocol-validation-' + mode)
    state = {'callbacks': [], 'abort_calls': [], 'late_guard_blocked': False}
    error = None
    task_id = None
    session = CodexWorkspaceSession(tools=TOOLS, allow_external_call=True,
        command=[sys.executable, '-u', '-c', SERVER, mode, str(root / 'native-started.txt'),
                 str(root / 'producer-stdio.jsonl')], progress_timeout=timeout)

    def abort():
        state['abort_calls'].append({'time_unix': time.time()})
        TheoryController.abort_pending(bridge)

    def execute(directory):
        limits.check()
        name = 'regen-protocol-native-' + uuid.uuid4().hex[:16]
        limits.register(name)
        args = command(ctx, output)
        args[2:2] = ['--name', name]
        args += ['process', '-l', ctx['base_session'], '-o', 'threads=2'] + catalogs(ctx)
        args += ['-T', f'/acceptance/{mode}/{THEORY}']
        durable(directory / 'native-request.json', {'command': args,
                'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'container':name, 'mode':mode}, immutable=True)
        with (directory / 'native.log').open('x') as stream:
            try:
                result = native_run(args, timeout=timeout, text=True,
                                    stdout=stream, stderr=subprocess.STDOUT)
            except CheckCancelled as caught:
                state['native_cancelled_at_unix'] = time.time()
                # Deliberately try a late Python completion after real native
                # cancellation: both the acceptance gate and executor sink must
                # reject it. This return is not an Isabelle GREEN verdict.
                try:
                    with acceptance_guard():
                        durable(directory / 'acceptance-marker.json', {'unexpected':True}, immutable=True)
                except CheckCancelled:
                    state['late_guard_blocked'] = True
                durable(directory / 'late-return-attempt.json', {
                    'native_was_cancelled':True, 'diagnostic':str(caught),
                    'acceptance_guard_blocked':state['late_guard_blocked'],
                    'return_is_fixture_not_proof_verdict':True}, immutable=True)
                return {'late_python_fixture_return': True, 'native_green': False}
        if result.returncode != 0:
            raise RuntimeError(f'native fixture failed with {result.returncode}; see {directory / "native.log"}')
        with acceptance_guard():
            durable(directory / 'acceptance-marker.json', {
                'fixture_only_not_repository_commit':True,
                'source_sha256':hashlib.sha256(text.encode()).hexdigest()}, immutable=True)
        return {'native_green':True, 'returncode':result.returncode,
                'source_sha256':hashlib.sha256(text.encode()).hexdigest()}

    def handler(name, arguments, metadata):
        nonlocal task_id
        TheoryController._check_session_active(bridge)
        state['callbacks'].append({'started_at_unix':time.time(), 'metadata':metadata})
        row = executor.submit(kind='proof_batch', theory=THEORY,
            binding={'run_id':bridge.run['run_id'], 'checker_context_id':ctx['checker_context_id']},
            request={'mode':mode, 'arguments':arguments, 'metadata':metadata}, owner=THEORY,
            execute=execute)
        task_id = row['task_id']
        while row['status'] not in TERMINAL:
            row = executor.wait([task_id], timeout=1)[0]
        state['callbacks'][-1]['finished_at_unix'] = time.time()
        return {'task_id':task_id, 'status':row['status']}

    try:
        with session:
            session.run('Run only the fixed native infrastructure fixture.', handler,
                        artifact_dir=root, model='synthetic-no-model', timeout=timeout, on_abort=abort)
    except Exception as caught:
        error = {'type':type(caught).__name__, 'diagnostic':str(caught)}
    finally:
        executor.close()
    row = executor.get(task_id) if task_id else {}
    task_root = executor.root / task_id if task_id else executor.root / 'not-dispatched'
    trace = [json.loads(line) for line in (root / 'producer-stdio.jsonl').read_text().splitlines()]
    responses = sum(item['direction']=='in' and item['message'].get('id')==100
                    and 'result' in item['message'] for item in trace)
    attacks = [item for item in trace if item['direction']=='out' and (
        item['message'].get('params',{}).get('item',{}).get('type')=='commandExecution'
        or item['message'].get('id')==101)]
    latency = (state.get('native_cancelled_at_unix', float('inf')) - attacks[0]['time_unix']) if attacks else None
    values = dict(error_type=error['type'] if error else None,
                  diagnostic=error['diagnostic'] if error else '',
                  native_started=(root / 'native-started.txt').is_file(),
                  native_finished=(root / 'native-finished.txt').is_file(),
                  task_status=row.get('status'), acceptance_written=(task_root / 'acceptance-marker.json').exists(),
                  result_written=(task_root / 'result.json').exists(),
                  completion_written=(task_root / 'completion.json').exists(),
                  late_guard_blocked=state['late_guard_blocked'], response_count=responses,
                  child_exited=session.process is not None and session.process.poll() is not None,
                  abort_count=len(state['abort_calls']), abort_latency=latency)
    report = {'mode':mode, **values, 'pass':pass_conditions(mode, **values),
              'task':row, 'callback_count':len(state['callbacks']), 'timing':state,
              'source_sha256':hashlib.sha256(text.encode()).hexdigest(),
              'source_unchanged':hashlib.sha256(source_path.read_bytes()).hexdigest()==hashlib.sha256(text.encode()).hexdigest()}
    report['pass'] &= report['source_unchanged'] and len(state['callbacks']) == 1
    durable(root / 'report.json', report, immutable=True)
    return report


def run(dataset, output, timeout=90):
    if not 60 <= timeout <= 180:
        raise ValueError('native fixture timeout must be 60..180 seconds')
    ctx = load_context(Path(dataset))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    limits = Limits(output, 3 * timeout + 30, 128 * 1024 ** 2)
    implementations = [Path(__file__), Path('regen/tools/native_backend_acceptance.py'),
        Path('regen/infrastructure/codex_workspace_session.py'),
        Path('regen/planning/parallel_file_planner.py'), Path('regen/execution/check_executor.py'),
        Path('regen/execution/check_tasks.py')]
    report = {'pass':False, 'model_calls':0, 'benchmark_contracts_proved':0,
              'checker_context_id':ctx['checker_context_id'], 'image':ctx['image'],
              'native_concurrency_limit':1, 'threads':2, 'memory_per_container':'8g',
              'scope':'synthetic-stdio-to-real-native-production-cancellation-bridge',
              'implementation_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in implementations},
              'limitations':['Producer is synthetic, not Codex/model.',
                  'TheoryController repository initializer/submit/document handling not exercised.',
                  'Exact production abort_pending runs on a minimal ownership fixture.',
                  'Acceptance guard and executor receipts exercised, not repository ProofCommits.',
                  'Late Python return is injected after genuine native cancellation, not a GREEN proof.',
                  'No Scheduler 122-node replay or existing experiment restart.']}
    (output / 'driver-snapshot.py').write_bytes(Path(__file__).read_bytes())
    (output / 'producer-snapshot.py').write_text(SERVER)
    durable(output / 'manifest.json', report, immutable=True)
    try:
        limits.check()
        limits.monitor.start()
        rows = [case_run(ctx, output, mode, timeout, limits) for mode in ('unsafe', 'foreign', 'legal')]
        report.update(cases=rows, **{'pass':all(row['pass'] for row in rows)})
    except Exception as caught:
        report['failure'] = f'{type(caught).__name__}: {caught}'
    finally:
        limits.stop.set()
        if limits.monitor.ident is not None:
            limits.monitor.join(timeout=20)
        report['cleanup'] = limits.cleanup()
        report['pass'] &= not limits.failure and all(row['confirmed_absent'] for row in report['cleanup'])
        report['resource_guard_failure'] = limits.failure
        durable(output / 'report.json', report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=90)
    args = parser.parse_args()
    result = run(args.dataset_root, args.output, args.timeout)
    print(json.dumps({'pass':result['pass'], 'failure':result.get('failure'),
                      'cases':[{key:row.get(key) for key in ('mode','pass','task_status','abort_latency')}
                               for row in result.get('cases',[])]}, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
