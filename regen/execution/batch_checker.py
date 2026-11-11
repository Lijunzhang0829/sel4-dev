"""Audited persistent batches. Promotion follows the caller's committed prefix.

Worker failure discards all volatile state; callers may cold-check exact source.
No model-provided admission or arbitrary ML enters this process.
"""
from regen.execution.check_tasks import check_cancelled, CheckCancelled
import hashlib
import shlex
import xml.etree.ElementTree as ET
import time
import uuid
from pathlib import Path

from regen.execution.persistent_checker import PrefixWorker
from regen.execution.batch_audit import PROTOCOL, validate_native_audit
from regen.execution.proof_commits import freeze_evidence


def body(source):
    return source.split('\nbegin\n', 1)[1].rsplit('\nend\n', 1)[0]


def prefix_startup_budget(command_timeout, certified_facts):
    """Certified prefix replay is separately accounted and has no deadline."""
    return None


class BatchWorker(PrefixWorker):
    def __init__(self, checker, source, names, imports):
        self.checker, self.closed, self.index = checker, False, 0
        self.name = 'regen-batch-' + uuid.uuid4().hex[:16]
        self.root = checker.work_dir/self.name
        self.root.mkdir()
        self._progress_tokens = {'startup': uuid.uuid4().hex}
        (self.root/'startup.progress_token').write_text(self._progress_tokens['startup'])
        self.base = body(source)
        self.pending = None
        self.imports = list(imports)
        # Replaying many certified declarations is not one candidate action.
        # No startup deadline: cancellation and worker death remain observable.
        # Candidate limits are unchanged; no proof/context is rewritten.
        self.prefix_timeout = prefix_startup_budget(checker.timeout, len(names))
        self.label = source.splitlines()[0].split()[1]
        self.checkpoints = {}
        for name, value in [('prefix', self.base), ('prefix_facts', '\n'.join(names)), ('worker_id', self.name),
                            ('imports', '\n'.join(imports)), ('theory_name', self.label),
                            ('command_timeout', str(checker.timeout)),
                            ('prefix_timeout', str(self.prefix_timeout)),
                            ('candidate_timeout', str(getattr(checker, 'batch_candidate_timeout', 300)))]:
            (self.root/name).write_text(value)
        self.checkpoints = self._write_checkpoints('initial', source)
        self.checkpoints[hashlib.sha256(self.base.encode()).hexdigest()] = self.base
        (self.root/'prefix_key').write_text(hashlib.sha256(self.base.encode()).hexdigest())
        (self.root/'initial_line').write_text(str(source.partition('\nbegin\n')[0].count('\n') + 2))
        from regen.execution.native_runtime import worker_source
        (self.root/'server.ML').write_text(worker_source(checker, Path(__file__).with_suffix('.ML').read_text()))
        command = checker._command(checker.work_dir/(self.label+'.thy'),
            session='Generated_Refine' if checker.previous_theories else checker.base_session,
            writable_mounts=[(self.root,'/worker')])
        command[2:2] = ['--name', self.name]
        command = command[:-2] + ['-e', 'ML_file "/worker/server.ML"']
        self._launch(command)

    def wait(self, path, *, timeout=None):
        from contextlib import nullcontext
        from regen.infrastructure.progress_events import generation_exclusion
        scope = generation_exclusion('prefix_preparation',
            prefix_sha256=(hashlib.sha256(self.base.encode()).hexdigest()
                           if isinstance(getattr(self,'base',None),str) else None),
            imports_sha256=hashlib.sha256('\n'.join(getattr(self,'imports',[])).encode()).hexdigest()) if path.name == 'ready' else nullcontext()
        with scope:
            return self._wait(path, timeout=timeout)

    def _wait(self, path, *, timeout=None):
        from regen.infrastructure.progress_events import emit_event
        start = time.monotonic()
        limit = (getattr(self, 'prefix_timeout', self.checker.timeout)
                 if path.name == 'ready' and timeout is None
                 else self.checker.timeout if timeout is None else timeout)
        emit_event('isabelle_batch_wait_started', worker=self.name, waiting_for=path.name, timeout_seconds=limit)
        last_progress = None
        while not path.exists():
            from regen.execution.native_progress import read_progress
            stem = 'startup' if path.name == 'ready' else path.stem
            token = getattr(self, '_progress_tokens', {}).get(stem)
            progress = read_progress(self.root, stem, self.name, token) if token else None
            signature = tuple(progress.get(k) for k in ('stage','command','line')) if progress else None
            if signature != last_progress:
                last_progress = signature
                emit_event('native_stage_observed', worker=self.name,
                    request=stem, progress=progress)
            try:
                check_cancelled()
            except CheckCancelled:
                self.close()
                raise
            if self.process.poll() is not None:
                if path.exists():
                    break  # Process exit raced a complete native response.
                from regen.execution.check_tasks import NativeWorkerUnavailable
                raise NativeWorkerUnavailable((self.root/'worker.log').read_text()[-6000:],
                    worker=self, waiting_for=path.name)
            now = time.monotonic()
            if limit is not None and now-start > limit:
                if path.exists():break  # Response publication raced the deadline check.
                self.close()
                # A native TIMEOUT may be published during graceful cleanup. It
                # is a received proof timeout, not evidence of a missing response.
                if path.name != 'ready' and path.is_file() and path.read_text().startswith('TIMEOUT\n'):
                    emit_event('native_response_during_cleanup',worker=self.name,request=path.stem,
                               response_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                               disposition='native_timeout_received_worker_closed')
                    break
                error = TimeoutError('incremental batch worker timeout waiting for ' + path.name)
                error.native_worker = self
                error.timeout_seconds = limit
                error.timeout_scope = 'prefix-startup' if path.name == 'ready' else 'candidate-watchdog'
                error.worker_log = (self.root/'worker.log').read_text()
                error.native_progress = progress
                raise error
            time.sleep(.05)
        emit_event('isabelle_batch_wait_finished', worker=self.name, waiting_for=path.name, elapsed_seconds=round(time.monotonic()-start, 3))

    def transaction(self, action, text='', names=(), line=1, *, fields=None, timeout=None):
        check_cancelled()
        self.index += 1
        stem = str(self.index)
        if not hasattr(self, '_progress_tokens'):
            self._progress_tokens = {}
        token = self._progress_tokens[stem] = uuid.uuid4().hex
        (self.root/(stem+'.progress_token')).write_text(token)
        offset = (self.root/'worker.log').stat().st_size
        for suffix, value in [('action',action), ('facts','\n'.join(names)), ('line',str(line))]:
            (self.root/(stem+'.'+suffix)).write_text(value)
        for suffix, value in (fields or {}).items():
            (self.root/(stem+'.'+suffix)).write_text(str(value))
        temp = self.root/(stem+'.tmp'); temp.write_text(text)
        temp.rename(self.root/(stem+'.request'))
        limit = timeout if timeout is not None else (getattr(self.checker, 'batch_candidate_timeout', 300) + 10 if action == 'check' else self.checker.timeout)
        from regen.infrastructure.progress_events import emit_event
        request_id = self.name + ':' + stem + ':' + token
        binding = dict(prefix_sha256=hashlib.sha256(self.base.encode()).hexdigest(),
                       request_sha256=hashlib.sha256(text.encode()).hexdigest(),
                       worker=self.name, request=stem, action=action,
                       imports_sha256=hashlib.sha256('\n'.join(getattr(self,'imports',[])).encode()).hexdigest())
        emit_event('native_request_started', request_id=request_id, binding=binding)
        outcome = 'interrupted'
        try:
            self.wait(self.root/(stem+'.response'), timeout=limit)
            answer = (self.root/(stem+'.response')).read_text()
            outcome = 'timeout' if answer.startswith('TIMEOUT\n') else 'completed'
        except TimeoutError:
            outcome = 'watchdog'
            raise
        finally:
            response=self.root/(stem+'.response')
            evidence=({'policy':'native-no-response-after-cleanup-v1','request_id':request_id,
                'worker':self.name,'prefix_sha256':binding['prefix_sha256'],
                'request_sha256':binding['request_sha256'],'imports_sha256':binding['imports_sha256'],
                'response_missing':not response.exists(),'native_response_received':response.exists(),
                'worker_discarded':getattr(self,'closed',False) is True}
                if outcome=='watchdog' else None)
            emit_event('native_request_finished', request_id=request_id, outcome=outcome,
                       interruption_evidence=evidence)
        with open(self.root/'worker.log', 'rb') as stream:
            stream.seek(offset); log = stream.read().decode(errors='replace')
        return answer, log

    def observe_source(self, source, label, *, timeout, output_directory=None, output_files=()):
        """Trusted harness observation in an imports-only, disposable theory.

        The exact executed source is saved. This is not a proof certificate and
        cannot promote pending work. Only declared observation outputs are exported.
        """
        header, separator, content = source.partition('\nbegin\n')
        if (not separator or shlex.split(header) != ['theory', label, 'imports', *self.imports]
                or not source.endswith('\nend\n')):
            raise ValueError('observation header must match frozen worker imports')
        stem = str(self.index + 1)
        executed = source
        binding = None
        outputs = {}
        if output_directory is not None:
            from regen.execution.observation_outputs import worker_source
            executed, binding = worker_source(source, output_files, self.index + 1)
            outputs = {name: self.root/Path(path).name
                       for name, path in binding['output_paths'].items()}
            if any(path.exists() for path in outputs.values()):
                raise ValueError('observation request output already exists')
        elif output_files:
            raise ValueError('observation outputs require an output directory')
        started = time.monotonic()
        try:
            answer, log = self.transaction('observe', executed.partition('\nbegin\n')[2],
                fields={'observation_name':label, 'observation_timeout':timeout}, timeout=timeout+10)
            if answer == 'OBSERVED' and output_directory is not None:
                # Missing output is a transport error, not a successful lookup.
                for name, output in outputs.items():
                    (Path(output_directory)/name).write_bytes(output.read_bytes())
        except (TimeoutError, RuntimeError, OSError):
            self.close()
            raise
        path = self.root/(stem+'.observation.thy')
        path.write_text(executed)
        log_path = self.root/(stem+'.observation.log')
        log_path.write_text(log+'\n'+answer)
        result = {'verdict':'green' if answer == 'OBSERVED' else 'red',
            'returncode':0 if answer == 'OBSERVED' else 124 if answer.startswith('TIMEOUT\n') else 1,
            'diagnostic':answer, 'source':str(path), 'log':str(log_path),
            'wall_ms':round((time.monotonic()-started)*1000),
            'check_scope':'isolated-imports-observation', 'proofs_checked':False,
            'worker':self.name, 'worker_preserved':True,
            'timeout_seconds':timeout,
            'timeout_scope':'isolated-imports-observation' if answer.startswith('TIMEOUT\n') else None}
        if binding is not None:
            result['observation_output_binding'] = binding
        from regen.execution.native_check_receipt import native_check_receipt
        result['native_receipt'] = native_check_receipt(
            self.checker, result, source=executed, scope='isolated-imports-observation',
            log_text=log+'\n'+answer, request_id=f'{self.name}:{stem}:observe',
            candidate_execution_ms=result['wall_ms'])
        return result

    def _write_checkpoints(self, stem, source):
        """Declare exact renderer boundaries after complete, closed entries."""
        content = body(source)
        offset = 0
        first = source.partition('\nbegin\n')[0].count('\n') + 2
        keys, lines = [], []
        for number, line in enumerate(content.splitlines(keepends=True), first):
            offset += len(line)
            if line.startswith('ML \\<open>writeln ("REGEN_TIMING_END '):
                prefix = content[:offset]
                key = hashlib.sha256(prefix.encode()).hexdigest()
                keys.append(key); lines.append(str(number))
                (self.root/(stem+'.checkpoint_'+key)).write_text(prefix)
        (self.root/(stem+'.checkpoint_keys')).write_text('\n'.join(keys))
        (self.root/(stem+'.checkpoint_lines')).write_text('\n'.join(lines))
        return {key:(self.root/(stem+'.checkpoint_'+key)).read_text() for key in keys}

    def can_align(self, prefix):
        return prefix in (self.base, self.pending) or hashlib.sha256(prefix.encode()).hexdigest() in getattr(self,'checkpoints',{})

    def align(self, prefix):
        if prefix == self.base:
            return
        if prefix != self.pending:
            key = hashlib.sha256(prefix.encode()).hexdigest()
            if getattr(self, 'checkpoints', {}).get(key) != prefix:
                raise ValueError('worker prefix differs from committed source')
            answer, _ = self.transaction('restore', fields={'checkpoint_key':key})
            if answer != 'RESTORED':
                raise RuntimeError(answer)
            self.base, self.pending = prefix, None
            return
        answer, _ = self.transaction('commit')
        if answer != 'COMMITTED':
            raise RuntimeError(answer)
        self.base, self.pending = prefix, None
        self.checkpoints = {k:v for k,v in getattr(self,'checkpoints',{}).items() if prefix.startswith(v)}

    def check_batch(self, source, names):
        candidate = body(source)
        if not candidate.startswith(self.base):
            raise ValueError('batch is not an append to the certified prefix')
        suffix = candidate[len(self.base):]
        before = source.split('\nbegin\n',1)[0]+'\nbegin\n'+self.base
        started = time.monotonic()
        checkpoints = self._write_checkpoints(str(self.index + 1), source)
        timed_out = False
        timeout_scope = None
        try:
            answer, log = self.transaction('check', suffix, names, before.count('\n')+1,
                fields={'candidate_timeout':getattr(self.checker, 'batch_candidate_timeout', 300)})
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
        if answer == 'GREEN':
            self.checkpoints.update(checkpoints)
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
        from regen.execution.native_reuse_diagnostics import attach_observation
        snapshot = attach_observation(snapshot, self.root/(stem+'.reuse-diagnostics.xml'))
        result = {**audit, 'native_proof_state_full':snapshot,
            'native_proof_state':({'origin':snapshot.get('origin'), 'command':snapshot.get('command'),
                'line':snapshot.get('line'), 'goal_count':len(snapshot.get('goals',[])),
                'available_via':'read_proof_state'} if snapshot else None),
            'verdict':'green' if answer == 'GREEN' else 'red',
            'returncode':0 if answer == 'GREEN' else (124 if timed_out else 1), 'diagnostic':answer,
            'timed_out':timed_out, 'timeout_scope':timeout_scope,
            'timeout_seconds':getattr(self.checker, 'batch_candidate_timeout', 300),
            'watchdog_grace_seconds':10,
            'source':str(path), 'log':str(log_path),
            'wall_ms':round((time.monotonic()-started)*1000),
            'check_scope':'committed-prefix-and-incremental-batch',
            'incremental':PROTOCOL,
            'prefix_sha256':hashlib.sha256(self.base.encode()).hexdigest(),
            'delta_sha256':hashlib.sha256(suffix.encode()).hexdigest(),
            'audited_facts':list(names), 'worker':self.name}
        from regen.execution.native_progress import read_progress
        result['native_progress'] = read_progress(self.root, stem, self.name,
            getattr(self, '_progress_tokens', {}).get(stem))
        result['worker_preserved'] = not self.closed
        from regen.execution.proof_batches import classify_native_failure
        classify_native_failure(result)
        from regen.execution.native_check_receipt import native_check_receipt
        result['native_receipt'] = native_check_receipt(
            self.checker, result, source=source,
            scope='committed-prefix-and-incremental-batch',
            log_text=log+'\n'+answer,
            request_id=f'{self.name}:{self.index}',
            prefix_sha256=result['prefix_sha256'],
            delta_sha256=result['delta_sha256'],
            candidate_execution_ms=result['wall_ms'])
        return freeze_evidence(result)

    def check_sketch(self, source, admitted_names, *, timeout=None):
        """Discard a conditional transaction without changing the proof prefix."""
        candidate = body(source)
        if not candidate.startswith(self.base):
            raise ValueError('sketch is not an append to the certified prefix')
        before = source.split('\nbegin\n', 1)[0] + '\nbegin\n' + self.base
        started = time.monotonic()
        stem = str(self.index + 1)
        path = self.root / (stem + '.sketch.thy')
        path.write_text(source)
        answer, log = self.transaction('sketch', candidate[len(self.base):], admitted_names,
            before.count('\n') + 1,
            fields={} if timeout is None else {'candidate_timeout': timeout},
            timeout=(getattr(self.checker, 'batch_candidate_timeout', 300) if timeout is None else timeout) + 10)
        log_path = self.root / (stem + '.sketch.log')
        log_path.write_text(log + '\n' + answer)
        snapshot = self.root / (stem + '.proof-state.xml')
        state = None
        if snapshot.exists():
            tree=ET.fromstring(snapshot.read_text())
            state={'origin':tree.get('origin'),'command':tree.get('command'),'line':tree.get('line'),
                   'goals':[x.text or '' for x in tree.findall('./goals/goal')],
                   'local_assumptions':[x.text or '' for x in tree.findall('./assumptions/assumption')]}
        from regen.execution.native_reuse_diagnostics import attach_observation
        state = attach_observation(state, self.root/(stem+'.reuse-diagnostics.xml'))
        result = {'verdict': 'green' if answer == 'CONDITIONAL' else 'red',
                'returncode': 0 if answer == 'CONDITIONAL' else 124 if answer.startswith('TIMEOUT\n') else 1,
                'diagnostic': answer, 'source': str(path), 'log': str(log_path),
                'wall_ms': round((time.monotonic() - started) * 1000),
                'check_scope': 'conditional-sketch-with-certified-prefix',
                'publishable': False, 'incremental': True,
                'proof_state_path': str(snapshot) if snapshot.exists() else None,
                'native_proof_state_full':state,
                'timeout_seconds':timeout if timeout is not None else getattr(self.checker,'batch_candidate_timeout',300),
                'timeout_scope':'conditional-sketch-candidate' if answer.startswith('TIMEOUT\n') else None}
        from regen.execution.native_check_receipt import native_check_receipt
        result['native_receipt'] = native_check_receipt(
            self.checker, result, source=source, scope='conditional-sketch-with-certified-prefix',
            log_text=log+'\n'+answer, request_id=f'{self.name}:{stem}:sketch',
            prefix_sha256=hashlib.sha256(self.base.encode()).hexdigest(),
            delta_sha256=hashlib.sha256(candidate[len(self.base):].encode()).hexdigest(),
            candidate_execution_ms=result['wall_ms'])
        return result
