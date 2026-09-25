"""One app-server process/thread with audited, serial harness tool callbacks.

Local protocol source: `codex app-server generate-json-schema --experimental`.
Dynamic tools are additive, not an exclusive OS sandbox. This adapter reports
`audited-tool-allowlist` assurance, never claims filesystem read isolation, and
requires explicit permission to launch a provider. No provider call is retried.
A callback owns its native/action deadline and worker cleanup. The session
timeout measures inactivity while waiting for protocol events, excluding callback
time; cancellation cleans up the app-server process group.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from jsonschema import Draft202012Validator


class SessionProtocolError(RuntimeError):
    pass


class SessionCancelled(KeyboardInterrupt):
    pass


class CodexWorkspaceSession:
    def __init__(self, *, tools, codex_bin='codex', codex_home=None,
                 allow_external_call=False, command=None):
        self.tools = deepcopy(tools)
        self.validators = {}
        for tool in self.tools:
            if set(tool) - {'name', 'description', 'inputSchema'} or not isinstance(tool.get('name'), str):
                raise ValueError('tools require name, description, inputSchema only')
            if tool['name'] in self.validators:
                raise ValueError('duplicate tool name')
            schema = tool['inputSchema']
            def check_refs(value):
                if isinstance(value, dict):
                    for k, v in value.items():
                        if k in ('$ref', '$dynamicRef') and not v.startswith('#'):
                            raise ValueError('remote schema references are forbidden')
                        check_refs(v)
                elif isinstance(value, list):
                    for v in value: check_refs(v)
            check_refs(schema)
            Draft202012Validator.check_schema(schema)
            self.validators[tool['name']] = Draft202012Validator(schema)
        if not self.tools:
            raise ValueError('at least one explicit harness tool is required')
        self.codex_bin, self.codex_home = str(codex_bin), codex_home
        self.allow_external_call, self.command = allow_external_call, command
        self.process = None
        self.thread_id = self.turn_id = None
        self._serial = 0
        self._buffer = b''
        self._calls = {}
        self._broken = False
        self._closed = False
        self._lock = threading.Lock()
        self._expired = threading.Event()
        self._cancel_event = None
        self._log = None
        self._stderr = None
        self._temporary = None
        self._selector = None
        self._artifact_dir = None
        self._usage = None
        self._deferred = []
        self._request_offsets = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _event(self, direction, message):
        if self._log is None:
            return
        with self._lock:
            if self._log.closed:
                return
            self._log.write(json.dumps({'time_unix':time.time(), 'direction':direction,
                                       'message':message}, ensure_ascii=False)+'\n')
            self._log.flush()
            os.fsync(self._log.fileno())

    def _send(self, message):
        self._event('out', message)
        self.process.stdin.write((json.dumps(message, ensure_ascii=False)+'\n').encode())
        self.process.stdin.flush()

    def _request(self, method, params):
        self._serial += 1
        self._send({'id':self._serial, 'method':method, 'params':params})
        return self._serial

    def _receive(self, deadline):
        # Inactivity budget: callback/native-check time is deliberately excluded.
        deadline = time.monotonic() + self._timeout
        while b'\n' not in self._buffer:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise SessionCancelled('workspace coordinator cancelled')
            remaining = deadline-time.monotonic()
            if self._expired.is_set() or remaining <= 0:
                raise TimeoutError('workspace session deadline expired')
            if not self._selector.select(min(remaining, .2)):
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise SessionProtocolError('app-server closed before terminal response')
            self._buffer += chunk
        raw, self._buffer = self._buffer.split(b'\n', 1)
        value = json.loads(raw)
        self._event('in', {'raw_json':raw.decode(), 'parsed':value})
        if value.get('method') == 'item/tool/call':
            self._request_offsets[id(value)] = os.fstat(self._log.fileno()).st_size
        return value

    def _await(self, request_id, deadline):
        while True:
            message = self._receive(deadline)
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise SessionProtocolError(str(message['error']))
                return message['result']
            if message.get('method') == 'item/tool/call':
                self._deferred.append(message)
            else:
                self._notification(message)

    def _notification(self, message):
        if 'id' in message and 'method' in message:
            # Approval/user-input/unknown server requests are never granted.
            self._send({'id':message['id'], 'error':{'code':-32601, 'message':'Only whitelisted item/tool/call is allowed'}})
            raise SessionProtocolError('unexpected server request: '+message['method'])
        method, params = message.get('method'), message.get('params', {})
        if method in ('item/started', 'item/completed'):
            kind = params.get('item', {}).get('type')
            allowed = {'agentMessage', 'reasoning', 'userMessage', 'dynamicToolCall', 'contextCompaction', 'plan'}
            if kind not in allowed:
                raise SessionProtocolError('non-allowlisted built-in item: '+str(kind))
            if kind == 'dynamicToolCall':
                item = params['item']
                if item.get('tool') not in self.validators or item.get('namespace') is not None:
                    raise SessionProtocolError('non-allowlisted dynamic tool item')
                self.validators[item['tool']].validate(item.get('arguments'))
        if method == 'thread/tokenUsage/updated':
            self._usage = deepcopy(params.get('tokenUsage'))
        if method == 'turn/started':
            self.turn_id = params['turn']['id']
        if method == 'error':
            raise SessionProtocolError(str(params))

    def _start(self, artifact_dir, model, deadline):
        if not self.allow_external_call:
            raise PermissionError('provider launch disabled; explicitly authorize audited-tool-allowlist mode')
        self._artifact_dir = Path(artifact_dir).resolve()
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._log = open(self._artifact_dir/'session-transcript.jsonl', 'x', encoding='utf-8')
        self._stderr = open(self._artifact_dir/'app-server.stderr', 'xb')
        self._temporary = tempfile.TemporaryDirectory(prefix='regen-codex-workspace-')
        base = Path(self._temporary.name)
        self.cwd = base/'cwd'; self.cwd.mkdir()
        home = Path(self.codex_home).resolve() if self.codex_home else base/'codex-home'
        home.mkdir(exist_ok=True)
        if any(p.name != 'auth.json' or p.is_symlink() or not p.is_file() for p in home.iterdir()):
            raise ValueError('clean codex_home may contain only auth.json; do not pass user configuration home')
        env = dict(os.environ)
        env['CODEX_HOME'] = str(home)
        command = self.command or [self.codex_bin, 'app-server', '--stdio',
            '-c', 'web_search="disabled"', '-c', 'approval_policy="never"',
            '-c', 'sandbox_mode="read-only"', '-c', 'project_doc_max_bytes=0']
        if self.command is None:
            for feature in ('shell_tool','unified_exec','apps','plugins','remote_plugin','hooks',
                            'browser_use','computer_use','code_mode','code_mode_host','view_image',
                            'multi_agent','multi_agent_v2','image_generation','skill_search','workspace_dependencies'):
                command += ['--disable', feature]
        executable = Path(shutil.which(command[0]) or command[0]).resolve()
        executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        command = [str(executable), *command[1:]]
        self._event('local', {'event':'launch', 'command':command, 'model':model,
                              'provider':'codex-app-server', 'synthetic':self.command is not None,
                              'executable_sha256':executable_hash,
                              'assurance':'audited-tool-allowlist', 'exclusive_os_isolation':False})
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self._stderr, cwd=self.cwd, env=env, start_new_session=True)
        self._selector = selectors.DefaultSelector()
        self._selector.register(self.process.stdout, selectors.EVENT_READ)
        self._await(self._request('initialize', {'clientInfo':{'name':'regen-proof-workspace','version':'1'},
                    'capabilities':{'experimentalApi':True}}), deadline)
        self._send({'method':'initialized'})
        result = self._await(self._request('thread/start', {'model':model, 'cwd':str(self.cwd),
            'sandbox':'read-only', 'approvalPolicy':'never', 'ephemeral':True,
            'allowProviderModelFallback':False, 'environments':[], 'selectedCapabilityRoots':[],
            'dynamicTools':[{'type':'function', **tool} for tool in self.tools]}), deadline)
        self.thread_id = result['thread']['id']

    def _tool_call(self, message, handler, deadline):
        params = message['params']
        if params.get('threadId') != self.thread_id or params.get('turnId') != self.turn_id:
            raise SessionProtocolError('tool callback belongs to another thread/turn')
        name, arguments, call_id = params['tool'], params['arguments'], params['callId']
        if name not in self.validators or params.get('namespace') is not None:
            raise SessionProtocolError('non-allowlisted dynamic tool: '+name)
        self.validators[name].validate(arguments)
        key = (self.thread_id, self.turn_id, call_id)
        if key in self._calls:
            raise SessionProtocolError('duplicate call ID; callback is never automatically replayed')
        metadata = {'thread_id':self.thread_id, 'turn_id':self.turn_id, 'call_id':call_id,
                    'raw_request':deepcopy(message),
                    'transcript_path':str(self._artifact_dir/'session-transcript.jsonl'),
                    'transcript_offset':os.fstat(self._log.fileno()).st_size,
                    'tool_request_offset':self._request_offsets.pop(id(message)),
                    'callback_timeout_policy':'trusted harness owns native/action deadlines'}
        self._calls[key] = 'started'
        self._event('local', {'event':'tool_started', **{k:v for k,v in metadata.items() if k != 'raw_request'}})
        try:
            value = handler(name, deepcopy(arguments), metadata)
            response = {'success':True, 'contentItems':[{'type':'inputText','text':json.dumps(value, ensure_ascii=False)}]}
        except Exception as error:
            response = {'success':False, 'contentItems':[{'type':'inputText','text':json.dumps(
                {'error_type':type(error).__name__, 'message':str(error)}, ensure_ascii=False)}]}
        self._calls[key] = 'finished'
        self._event('local', {'event':'tool_finished', 'call_id':call_id, 'response':response})
        self._send({'id':message['id'], 'result':response})

    def run(self, prompt, on_tool_call, *, artifact_dir, model, reasoning_effort='medium', timeout=1800, cancel_event=None):
        if self._closed or self._broken:
            raise RuntimeError('session is closed or failed; automatic resume is forbidden')
        if timeout <= 0:
            raise ValueError('timeout must be positive')
        self._timeout = timeout
        self._cancel_event = cancel_event
        deadline = time.monotonic()+timeout
        self._expired.clear()
        old_term = None
        if threading.current_thread() is threading.main_thread():
            old_term = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self._cancel_signal)
        try:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise SessionCancelled('workspace coordinator cancelled')
            if self.process is None:
                self._start(artifact_dir, model, deadline)
            elif Path(artifact_dir).resolve() != self._artifact_dir:
                raise ValueError('a persistent session has one transcript artifact directory')
            self.turn_id = None
            turn = self._await(self._request('turn/start', {'threadId':self.thread_id,
                'input':[{'type':'text','text':prompt}], 'model':model, 'effort':reasoning_effort,
                'approvalPolicy':'never', 'environments':[]}), deadline)
            self.turn_id = turn['turn']['id']
            while True:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise SessionCancelled('workspace coordinator cancelled')
                message = self._deferred.pop(0) if self._deferred else self._receive(deadline)
                if message.get('method') == 'item/tool/call':
                    self._tool_call(message, on_tool_call, deadline)
                    continue
                self._notification(message)
                if message.get('method') == 'turn/completed':
                    params = message['params']
                    if params.get('threadId') != self.thread_id or params['turn']['id'] != self.turn_id:
                        raise SessionProtocolError('terminal turn identity mismatch')
                    if params['turn']['status'] != 'completed':
                        raise SessionProtocolError('turn ended without completion: '+str(params['turn']))
                    result = {'thread_id':self.thread_id, 'turn_id':self.turn_id, 'turn':params['turn'],
                              'status':params['turn']['status'], 'usage':deepcopy(self._usage),
                              'assurance':'audited-tool-allowlist', 'exclusive_os_isolation':False}
                    self._event('local', {'event':'turn_completed', **result})
                    return result
        except BaseException as error:
            self._broken = True
            self._event('local', {'event':'session_failed', 'nonsealable':True,
                                  'exception_type':type(error).__name__, 'message':str(error)})
            self.close()
            raise
        finally:
            if old_term is not None:
                signal.signal(signal.SIGTERM, old_term)

    def _cancel_signal(self, signum, frame):
        raise SessionCancelled('workspace session cancelled by signal '+str(signum))

    def _stop_process(self):
        if self.process is not None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                self.process.wait(timeout=2)
            # The leader can exit before a descendant that ignores SIGTERM.
            try: os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError: pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop_process()
        if self._selector is not None: self._selector.close()
        if self.process is not None:
            self.process.stdin.close(); self.process.stdout.close()
        if self._stderr is not None: self._stderr.close()
        if self._temporary is not None: self._temporary.cleanup()
        if self._log is not None:
            self._event('local', {'event':'session_closed', 'nonsealable':self._broken or self._expired.is_set()})
            self._log.close()
