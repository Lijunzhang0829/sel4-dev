"""Isolated, model-free replay of saved Scheduler_Queues request 000121.

Uses the real seal_theory sequence/checker/executor behind a synthetic stdio
producer. No proof edit, provider eligibility override, journal append or source
run restart. Parent heaps are read-only; build outputs have a separate store.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from regen.execution import isabelle_node_checker as native
from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.check_tasks import TERMINAL
from regen.execution.proof_commits import durable, digest
from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
from regen.infrastructure.progress_events import event_scope
from regen.infrastructure.workspace_transcript import audit_workspace_session
from regen.planning.batch_file_planner import seal_theory
from regen.planning.parallel_file_planner import TheoryController
from regen.preparation.checker_context import load_context
from regen.tools.harness_acceptance_baseline import protected_snapshot, production_hashes
from regen.tools.native_backend_acceptance import Limits
from regen.tools.native_protocol_repair_validation import ownership_bridge

THEORY = 'Scheduler_Queues'
TASK = '7b8b670c3e9bd3c9ef48a41c1a751a0e2580ee3d4c7a8d9779ea808df610e997'
SOURCE_SHA = 'd1954a21a477b4dd95cda109dbaa934f10011f37d1e756ee8e56f3bd72fd156c'
DOCUMENT_SHA = 'ec416a90c04c967c5b47b42af0799e8b3988e3cea026a8bec2db8c894c1f3d19'
STATE_SHA = '804a391f85d428c3d2da5d7de290e19ed6ae5fc276e21be367b9d99b37453b24'
TIMEOUT = 900
THREADS = 4

SERVER = r'''
import json,sys,threading,time
request=json.load(open(sys.argv[1])); trace=open(sys.argv[2],'x')
lock=threading.Lock(); stopped=threading.Event(); producer=None
def record(direction,message):
 trace.write(json.dumps({'time_unix':time.time(),'direction':direction,'message':message})+'\n');trace.flush()
def send(message):
 with lock:
  message['emittedAtMs']=int(time.time()*1000);record('out',message)
  print(json.dumps(message),flush=True)
def sleep_events():
 number=0
 while not stopped.wait(.1):
  number+=1
  params={'threadId':'replay-thread','turnId':'replay-turn','item':{
   'type':'sleep','id':'replay-sleep-'+str(number),'durationMs':40000}}
  send({'method':'item/started','params':params})
  stopped.wait(40)
  send({'method':'item/completed','params':params})
for raw in sys.stdin:
 message=json.loads(raw)
 with lock:record('in',message)
 method=message.get('method')
 if method=='initialize':send({'id':message['id'],'result':{}})
 elif method=='thread/start':send({'id':message['id'],'result':{'thread':{'id':'replay-thread'}}})
 elif method=='turn/start':
  send({'id':message['id'],'result':{'turn':{'id':'replay-turn'}}})
  send({'id':100,'method':'item/tool/call','params':{'threadId':'replay-thread',
   'turnId':'replay-turn','callId':'saved-finish-replay','namespace':None,
   'tool':'proof_action','arguments':request}})
  producer=threading.Thread(target=sleep_events,daemon=True);producer.start()
 elif message.get('id')==100 and 'result' in message:
  stopped.set();producer.join()
  send({'method':'turn/completed','params':{'threadId':'replay-thread',
   'turn':{'id':'replay-turn','status':'completed'}}})
'''


def read(path):
    from regen.infrastructure.incremental_log import read_artifact
    return read_artifact(path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_destination(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('replay output must be separate from the protected source run')
    if output.exists():
        raise FileExistsError('replay output must be new')
    return source, output


def load_inputs(source):
    directory = source/'theories'/THEORY
    request = directory/'batch-requests/000121'
    run = read(source/'run.json')
    payload, proposal = read(request/'input.json'), read(request/'proposal.json')
    graph = payload['graph']
    candidate = payload['last_proof_feedback']['candidate']
    proofs = candidate['proofs']
    complete = source/'check-tasks'/TASK/'isabelle'/('Complete_'+THEORY+'.thy')
    doc = directory/'document/versions'/DOCUMENT_SHA/'source.json'
    if (proposal != {'action':'finish', 'source_sha256':DOCUMENT_SHA}
            or sha(complete) != SOURCE_SHA
            or hashlib.sha256(read(doc)['source'].encode()).hexdigest() != DOCUMENT_SHA
            or hashlib.sha256(candidate['document_source'].encode()).hexdigest() != DOCUMENT_SHA
            or set(proofs) != {node['id'] for node in graph['nodes']}
            or len(proofs) != 122 or len(payload['unproved_node_ids']) != 0
            or graph['accepted_order'] != payload['green_node_ids']):
        raise ValueError('saved request/source/graph/proof binding mismatch')
    ancestors = read(directory/'provider-session.json')['imports']
    renderer = object.__new__(native.IsabelleChecker)
    renderer.previous_theories, renderer.import_all_previous = ancestors, True
    if renderer._source(graph, proofs, theory_name='Complete_'+THEORY,
                        diagnostic_markers=True).encode() != complete.read_bytes():
        raise ValueError('current renderer differs from exact failed source')
    return run, directory, graph, proofs, ancestors, proposal, complete, doc


def validate_frozen_context(source, inputs):
    run, directory, graph, proofs, ancestors, _, _, _ = inputs
    task = read(source/'check-tasks'/TASK/'task.json')
    package = read(source/'packages'/(THEORY+'.thy.json'))
    state = {'graph':graph, 'proofs':proofs, 'accepted_order':graph['accepted_order']}
    if (digest(state) != STATE_SHA or task['binding']['state_sha256'] != STATE_SHA
            or task['binding']['package_sha256'] != package['package_payload_sha256']
            or task['binding']['run_id'] != run['run_id']):
        raise ValueError('frozen state/package/task binding mismatch')
    context = load_context(Path(run['checker_context_root']))
    if context['checker_context_id'] != run['checker_context_id'] or context['image'] != run['image']:
        raise ValueError('checker environment identity mismatch')
    cache_path = source.parents[1]/'replay-resume-20260929/Scheduler_Queues-provider-cache.json'
    cached = read(cache_path)
    if ancestors != cached['imports']:
        raise ValueError('provider closure differs from verified cache receipt')
    for name, expected in cached['hashes'].items():
        path = (directory/'heaps'/name).resolve()
        if not path.is_relative_to((directory/'heaps').resolve()) or sha(path) != expected:
            raise ValueError('frozen provider heap differs from verified cache: '+name)
    return context, cache_path


def readonly_command(command, output):
    """Transport/resource instrumentation only; keep Isabelle arguments intact."""
    args = list(command)
    if args[:2] != ['docker','run'] or '--name' not in args:
        raise ValueError('replay only permits explicitly owned Docker commands')
    if [value for value in args if value.startswith('threads=')] != ['threads=4']:
        raise ValueError('replay must retain exactly four Isabelle threads')
    mounts = [i for i, value in enumerate(args) if value.startswith('type=bind,')
              and value.endswith('dst=/root/.isabelle/heaps')]
    if len(mounts) != 1:
        raise ValueError('expected exactly one frozen heap mount')
    args[mounts[0]] = args[mounts[0]].replace('dst=/root/.isabelle/heaps', 'dst=/parent-heaps,readonly')
    args[2:2] = ['--cpus','4','--memory','16g','--memory-swap','16g','--pids-limit','512',
                  '--mount',f'type=bind,src={output / "build-heaps"},dst=/replay-heaps']
    executable = '/workspace/verification/isabelle/bin/isabelle'
    at = args.index(executable)
    args[at:at+1] = [executable, 'env', 'ISABELLE_HEAPS_SYSTEM=/parent-heaps',
                     'ISABELLE_HEAPS=/replay-heaps', executable]
    return args


class NativeObserver:
    """Read-only snapshots of OWNED Docker logs/stats; no injected ML/source edits."""
    def __init__(self, root):
        self.root, self.stop = root, threading.Event()
        self.active, self.lock = None, threading.Lock()
        self.rows = []
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def watch(self):
        while not self.stop.wait(5):
            with self.lock:
                current = deepcopy(self.active)
            if current is None:
                continue
            sample = {'time_unix':time.time(), **current}
            for label, command in (
                ('logs', ['docker','logs','--timestamps','--tail','300',current['container']]),
                ('stats', ['docker','stats','--no-stream','--format','{{json .}}',current['container']])):
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=4)
                    sample[label] = {'returncode':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr}
                except subprocess.TimeoutExpired:
                    sample[label] = {'observer_timeout':True}
            with (self.root/'native-observations.jsonl').open('a') as stream:
                stream.write(json.dumps(sample)+'\n')
            durable(self.root/'latest-native-observation.json', sample)


def protocol_metrics(path):
    delays, count = [], 0
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if event['direction'] != 'in':
            continue
        message = event['message']['parsed']
        if message.get('params',{}).get('item',{}).get('type') == 'sleep':
            count += 1
            delays.append(event['time_unix']-message['emittedAtMs']/1000)
    return {'sleep_lifecycle_events':count, 'max_receive_delay_seconds':max(delays, default=0),
            'latency_limit_seconds':.5, 'within_latency_limit':max(delays, default=0) <= .5,
            'sleep_is_synthetic_and_interruptible':True}


def run(source, output, native_timeout_seconds=TIMEOUT, *, reuse_arch_context=False):
    # This is an isolated diagnostic override, never a production policy edit.
    # Validate before any input replay or output creation.
    if type(native_timeout_seconds) is not int or not 900 <= native_timeout_seconds <= 3600:
        raise ValueError('native timeout must be an integer from 900 to 3600 seconds')
    source, output = validate_destination(source, output)
    inputs = load_inputs(source)
    context, cache_path = validate_frozen_context(source, inputs)
    run_config, directory, graph, proofs, ancestors, proposal, complete, doc = inputs
    output.mkdir(parents=True)
    before = protected_snapshot(source)
    implementation = production_hashes(Path(__file__).resolve().parents[2])
    frozen = [source/'run.json', directory/'batch-requests/000121/input.json',
              directory/'batch-requests/000121/proposal.json', complete, doc,
              directory/'provider-session.json', directory/'generated/ROOT',
              Path(run_config['premise_snapshot']),cache_path,
              source/'check-tasks'/TASK/'task.json',source/'packages'/(THEORY+'.thy.json')]
    frozen += sorted((directory/'generated').glob('*.thy'))
    frozen += sorted(path for path in (directory/'heaps').rglob('*') if path.is_file())
    hashes = {str(path):sha(path) for path in frozen}
    shutil.copytree(directory/'generated', output/'generated')
    (output/'build-heaps').mkdir()
    (output/'driver-snapshot.py').write_bytes(Path(__file__).read_bytes())
    shutil.copyfile(Path(native.__file__), output/'checker-snapshot.py')
    (output/'producer-snapshot.py').write_text(SERVER)
    arguments = {'theory':THEORY, 'proposal':proposal}
    durable(output/'saved-tool-request.json', arguments, immutable=True)
    durable(output/'manifest.json', {'source_run':str(source),'request':'000121','action':'finish',
        'exact_source_sha256':SOURCE_SHA, 'document_sha256':DOCUMENT_SHA,
        'reuse_arch_context':reuse_arch_context,
        'rendering_notice':('Experimental scaffolding optimization; exact_source_sha256 binds '
            'the original saved source, not the optimized candidate. Statements and proofs remain frozen.'
            if reuse_arch_context else 'Original frozen rendering'),
        'timeout_per_native_phase':native_timeout_seconds,
        'source_timeout_per_native_phase':TIMEOUT,
        'protocol_progress_timeout_seconds':TIMEOUT,
        'total_wall_budget_seconds':3*native_timeout_seconds+180,
        'threads':THREADS, 'native_slots':1,
        'memory_limit':'16g','cpu_limit':4,'source_native_slots':2,
        'model_calls':0, 'real_model':False, 'protected_before':before,
        'frozen_input_sha256':hashes,'production_sha256':implementation,
        'architecture':context['architecture'],'state_sha256':STATE_SHA,
        'scope':'read-only saved-step replay, not an experiment continuation or accepted seal'}, immutable=True)
    limits = Limits(output, 3*native_timeout_seconds+180, 2*1024**3)
    observer = NativeObserver(output)
    executor = CheckExecutor(output/'tasks', workers=1)
    resources = NativeResources(slots=1, threads=THREADS, resident_limit=1)
    bridge = ownership_bridge(executor, 'isolated-finish-'+output.name)
    bridge.theory = THEORY
    checker = native.IsabelleChecker(output, output/'isabelle', timeout=native_timeout_seconds,
        architecture=context['architecture'],
        previous_theories=ancestors, import_all_previous=True, generated_dir=output/'generated',
        heaps_dir=directory/'heaps', image=run_config['image'],
        l4v_dir=Path(run_config['l4v']), isabelle_dir=Path(run_config['isabelle_dir']),
        semantic_workspace=Path(run_config['semantic_workspace']),
        base_session=run_config['base_session'], base_import=run_config['base_import'],
        base_catalog=run_config['base_catalog'],
        complete_context_policy='arch-reuse-v1' if reuse_arch_context else 'isolated-v1')
    original_native_run = native.native_run
    result = {'step_pass':False,'protocol_pass':False,'model_calls':0,'experiment_resumed':False,
              'reuse_arch_context':reuse_arch_context,
              'seal_published':False,'request':'000121','native_executions':observer.rows}

    def instrumented_native(command, *, timeout, **kwargs):
        if timeout != native_timeout_seconds:
            raise ValueError('native call differs from the explicitly selected replay budget')
        args = readonly_command(command, output)
        name = args[args.index('--name')+1]
        limits.register(name)
        phase = 'build' if 'build' in args else 'audit' if any('Complete_Audit_' in v for v in args) else 'complete'
        row = {'phase':phase, 'container':name, 'started_at_unix':time.time(), 'command':args}
        observer.rows.append(row)
        with observer.lock:
            observer.active = {k:row[k] for k in ('phase','container','started_at_unix')}
        durable(output/(phase+'-native-command.json'), row, immutable=True)
        try:
            completed = original_native_run(args, timeout=timeout, **kwargs)
            row['returncode'] = completed.returncode
            (output/(phase+'-stdout.log')).write_text(completed.stdout or '')
            (output/(phase+'-stderr.log')).write_text(completed.stderr or '')
            return completed
        except BaseException as error:
            row['exception_type'] = type(error).__name__
            if isinstance(error, subprocess.TimeoutExpired):
                for channel in ('stdout','stderr'):
                    content = getattr(error,channel) or ''
                    if isinstance(content,bytes): content = content.decode(errors='replace')
                    (output/(phase+'-'+channel+'.log')).write_text(content)
            raise
        finally:
            row['finished_at_unix'] = time.time()
            row['wall_seconds'] = row['finished_at_unix']-row['started_at_unix']
            with observer.lock:
                observer.active = None
            durable(output/(phase+'-native-timing.json'), row, immutable=True)

    def execute(task_directory):
        token = CURRENT_RESOURCES.set(resources)
        try:
            with event_scope(task_directory, run_id=bridge.run['run_id'], theory=THEORY):
                state = {'graph':graph, 'proofs':proofs, 'accepted_order':graph['accepted_order']}
                # Exact production gate: audit/build only follow GREEN complete.
                # Returned candidate state is NEVER appended to any journal.
                new, evidence = seal_theory(output, run_config, {'file_slot':THEORY+'.thy'},
                    output, checker, ancestors, state)
                durable(output/'step-evidence.json', evidence, immutable=True)
                return {'native_step_pass':new is not None, 'failed_stage':evidence.get('stage'),
                        'evidence_path':str(output/'step-evidence.json'), 'seal_published':False}
        finally:
            CURRENT_RESOURCES.reset(token)

    def handler(name, received, metadata):
        if name != 'proof_action' or received != arguments:
            raise ValueError('replay request differs from the original finish request')
        TheoryController._check_session_active(bridge)
        durable(output/'callback-binding.json', metadata, immutable=True)
        row = executor.submit(kind='seal', theory=THEORY, binding={'run_id':bridge.run['run_id'],
            'source_sha256':SOURCE_SHA}, request={'arguments':received,'metadata':metadata},
            owner=THEORY, execute=execute)
        durable(output/'task-reference.json', {'task_id':row['task_id']}, immutable=True)
        while row['status'] not in TERMINAL:
            row = executor.wait([row['task_id']], timeout=1)[0]
        result['task'] = executor.get(row['task_id'])
        if row['status'] != 'completed':
            raise RuntimeError('replay task failed: '+str(row))
        value = read(executor.root/row['task_id']/'result.json')
        result['step_pass'] = value['native_step_pass']
        result['failed_stage'] = value['failed_stage']
        return value

    try:
        limits.check(); limits.monitor.start(); observer.thread.start()
        with patch.object(native, 'native_run', side_effect=instrumented_native), CodexWorkspaceSession(
                tools=[{'name':'proof_action','description':'Exact saved finish replay; no generation',
                        'inputSchema':{'const':arguments}}], allow_external_call=True,
                command=[sys.executable,'-u','-c',SERVER,str(output/'saved-tool-request.json'),
                         str(output/'producer-stdio.jsonl')], progress_timeout=TIMEOUT) as session:
            result['protocol_result'] = session.run('Replay exactly the saved finish request.', handler,
                artifact_dir=output/'protocol', model='synthetic-no-model', timeout=TIMEOUT,
                on_abort=lambda: TheoryController.abort_pending(bridge))
        result['protocol_audit'] = audit_workspace_session(output/'protocol/session-transcript.jsonl', require_real=False)
        result['protocol_metrics'] = protocol_metrics(output/'protocol/session-transcript.jsonl')
        result['protocol_pass'] = result['protocol_metrics']['within_latency_limit']
    except BaseException as error:
        result['failure'] = {'type':type(error).__name__,'message':str(error)}
    finally:
        cleanup_errors = []
        for owner in (executor,resources):
            try:
                owner.close()
            except BaseException as error:
                cleanup_errors.append({'type':type(error).__name__,'message':str(error)})
        observer.stop.set(); limits.stop.set()
        if observer.thread.ident is not None: observer.thread.join(timeout=15)
        if limits.monitor.ident is not None: limits.monitor.join(timeout=20)
        result['cleanup'] = limits.cleanup()
        result['cleanup_errors'] = cleanup_errors
        result['guard_failure'] = limits.failure
        result['protected_after'] = protected_snapshot(source)
        result['protected_unchanged'] = before == result['protected_after']
        result['frozen_inputs_unchanged'] = all(sha(Path(name)) == value for name,value in hashes.items())
        result['production_unchanged'] = implementation == production_hashes(Path(__file__).resolve().parents[2])
        result['limitations'] = ['Synthetic protocol producer, not a model request.',
            'Calls production seal sequence/checker/executor but not full TheoryController submission or provenance gate.',
            'No journal acceptance or experiment seal; all results are diagnostic.',
            'One native slot, 4 CPU quota and 16 GiB cap; not a controlled two-worker throughput comparison.',
            'Docker log/stats samples are observations, not prover state or proof authority.']
        durable(output/'report.json', result, immutable=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-timeout-seconds', type=int, default=TIMEOUT,
                        help='Isolated native phase budget, 900..3600 seconds; protocol remains 900')
    parser.add_argument('--reuse-arch-context', action='store_true',
                        help='Opt-in experimental Arch scaffolding; never resumes the source experiment')
    args = parser.parse_args()
    result = run(args.source_run, args.output, native_timeout_seconds=args.native_timeout_seconds,
                 reuse_arch_context=args.reuse_arch_context)
    print(json.dumps({key:result.get(key) for key in ('step_pass','protocol_pass','failed_stage',
        'failure','protected_unchanged','frozen_inputs_unchanged','production_unchanged')}, indent=2))
    return 0 if (result['step_pass'] and result['protocol_pass'] and result['protected_unchanged']
                 and result['frozen_inputs_unchanged'] and result['production_unchanged']
                 and not result['guard_failure'] and not result['cleanup_errors']
                 and all(row['confirmed_absent'] for row in result['cleanup'])) else 1


if __name__ == '__main__':
    raise SystemExit(main())
