"""Audited persistent batches. Promotion follows the caller's committed prefix.

Worker failure discards all volatile state; callers may cold-check exact source.
No model-provided admission or arbitrary ML enters this process.
"""
import atexit
import hashlib
import xml.etree.ElementTree as ET
import subprocess
import time
import uuid
from pathlib import Path

from regen.execution.persistent_checker import PrefixWorker
from regen.execution.batch_audit import PROTOCOL, validate_native_audit
from regen.execution.proof_commits import freeze_evidence


def body(source):
    return source.split('\nbegin\n', 1)[1].rsplit('\nend\n', 1)[0]


class BatchWorker(PrefixWorker):
    def __init__(self, checker, source, names, imports):
        self.checker, self.closed, self.index = checker, False, 0
        self.name = 'regen-batch-' + uuid.uuid4().hex[:16]
        self.root = checker.work_dir/self.name
        self.root.mkdir()
        self.base = body(source)
        self.pending = None
        self.imports = list(imports)
        self.label = source.splitlines()[0].split()[1]
        for name, value in [('prefix', self.base), ('prefix_facts', '\n'.join(names)), ('worker_id', self.name),
                            ('imports', '\n'.join(imports)), ('theory_name', self.label),
                            ('command_timeout', str(checker.timeout)),
                            ('candidate_timeout', str(getattr(checker, 'batch_candidate_timeout', 120)))]:
            (self.root/name).write_text(value)
        (self.root/'server.ML').write_bytes(Path(__file__).with_suffix('.ML').read_bytes())
        command = checker._command(checker.work_dir/(self.label+'.thy'),
            session='Generated_Refine' if checker.previous_theories else checker.base_session,
            writable_mounts=[(self.root,'/worker')])
        command[2:2] = ['--name', self.name]
        command = command[:-2] + ['-e', 'ML_file "/worker/server.ML"']
        self.log = open(self.root/'worker.log', 'w')
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT, text=True)
        atexit.register(self.close)
        try:
            self.wait(self.root/'ready')
        except Exception:
            self.close()
            raise

    def wait(self, path, *, timeout=None):
        from regen.infrastructure.progress_events import emit_event
        start = time.monotonic()
        limit = self.checker.timeout if timeout is None else timeout
        emit_event('isabelle_batch_wait_started', worker=self.name, waiting_for=path.name, timeout_seconds=limit)
        while not path.exists():
            if self.process.poll() is not None:
                raise RuntimeError((self.root/'worker.log').read_text()[-6000:])
            now = time.monotonic()
            if now-start > limit:
                self.close()
                error = TimeoutError('incremental batch worker timeout waiting for ' + path.name)
                error.timeout_seconds = limit
                error.timeout_scope = 'prefix-startup' if path.name == 'ready' else 'candidate-watchdog'
                error.worker_log = (self.root/'worker.log').read_text()
                raise error
            time.sleep(.05)
        emit_event('isabelle_batch_wait_finished', worker=self.name, waiting_for=path.name, elapsed_seconds=round(time.monotonic()-start, 3))

    def transaction(self, action, text='', names=(), line=1):
        self.index += 1
        stem = str(self.index)
        offset = (self.root/'worker.log').stat().st_size
        for suffix, value in [('action',action), ('facts','\n'.join(names)), ('line',str(line))]:
            (self.root/(stem+'.'+suffix)).write_text(value)
        temp = self.root/(stem+'.tmp'); temp.write_text(text)
        temp.rename(self.root/(stem+'.request'))
        limit = getattr(self.checker, 'batch_candidate_timeout', 120) + 10 if action == 'check' else self.checker.timeout
        self.wait(self.root/(stem+'.response'), timeout=limit)
        answer = (self.root/(stem+'.response')).read_text()
        with open(self.root/'worker.log', 'rb') as stream:
            stream.seek(offset); log = stream.read().decode(errors='replace')
        return answer, log

    def align(self, prefix):
        if prefix == self.base:
            return
        if prefix != self.pending:
            raise ValueError('worker prefix differs from committed source')
        answer, _ = self.transaction('commit')
        if answer != 'COMMITTED':
            raise RuntimeError(answer)
        self.base, self.pending = prefix, None

    def check_batch(self, source, names):
        candidate = body(source)
        if not candidate.startswith(self.base):
            raise ValueError('batch is not an append to the certified prefix')
        suffix = candidate[len(self.base):]
        before = source.split('\nbegin\n',1)[0]+'\nbegin\n'+self.base
        started = time.monotonic()
        timed_out = False
        timeout_scope = None
        try:
            answer, log = self.transaction('check', suffix, names, before.count('\n')+1)
            timed_out = answer.startswith('TIMEOUT\n')
            timeout_scope = 'candidate-execute-and-audit' if timed_out else None
        except TimeoutError as error:
            self.pending = None
            self.close()
            answer = 'TIMEOUT\n' + str(error)
            log = getattr(error, 'worker_log', '')
            timed_out, timeout_scope = True, 'candidate-watchdog'
        stem = str(self.index)
        audit = {}
        if answer == 'GREEN':
            audit_path = self.root/(stem+'.audit.xml')
            try:
                content = audit_path.read_text()
                audit = {'audit_content':content,
                    'audit_sha256':hashlib.sha256(content.encode()).hexdigest(),
                    'audit':str(audit_path), 'request_id':self.index, 'worker':self.name}
                validate_native_audit(audit, prefix=self.base, delta=suffix,
                    names=names, imports=self.imports, label=self.label)
            except (OSError, ValueError) as error:
                self.pending = None
                raise RuntimeError('incremental audit transport failure: ' + str(error)) from error
        self.pending = candidate if answer == 'GREEN' else None
        path = self.root/(stem+'.thy'); path.write_text(source)
        log_path = self.root/(stem+'.log'); log_path.write_text(log+'\n'+answer)
        snapshot = None
        snapshot_path = self.root/(stem+'.proof-state.xml')
        if snapshot_path.is_file():
            try:
                tree = ET.fromstring(snapshot_path.read_text())
                snapshot = {'origin':tree.get('origin'), 'command':tree.get('command'),
                    'line':tree.get('line'), 'goals':[x.text or '' for x in tree.findall('./goals/goal')],
                    'local_assumptions':[x.text or '' for x in tree.findall('./assumptions/assumption')],
                    'executed_commands':[dict(x.attrib) for x in tree.findall('./executed_commands/command')]}
            except (ET.ParseError, OSError) as error:
                snapshot = {'origin':'unavailable', 'error':str(error)}
        return freeze_evidence({**audit, 'native_proof_state_full':snapshot,
            'native_proof_state':({'origin':snapshot.get('origin'), 'command':snapshot.get('command'),
                'line':snapshot.get('line'), 'goal_count':len(snapshot.get('goals',[])),
                'available_via':'read_proof_state'} if snapshot else None),
            'verdict':'green' if answer == 'GREEN' else 'red',
            'returncode':0 if answer == 'GREEN' else (124 if timed_out else 1), 'diagnostic':answer,
            'timed_out':timed_out, 'timeout_scope':timeout_scope,
            'timeout_seconds':getattr(self.checker, 'batch_candidate_timeout', 120),
            'watchdog_grace_seconds':10,
            'source':str(path), 'log':str(log_path),
            'wall_ms':round((time.monotonic()-started)*1000),
            'check_scope':'committed-prefix-and-incremental-batch',
            'incremental':PROTOCOL,
            'prefix_sha256':hashlib.sha256(self.base.encode()).hexdigest(),
            'delta_sha256':hashlib.sha256(suffix.encode()).hexdigest(),
            'audited_facts':list(names), 'worker':self.name})
