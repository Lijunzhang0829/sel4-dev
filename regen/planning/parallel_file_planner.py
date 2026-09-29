"""Parallel theory agents with serial tools inside each File Planner.

The task executor persists receipts and shares native capacity across theories.
Every theory tool waits for its result; only the controller owns accepted state.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from contextvars import copy_context
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
from uuid import uuid4

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.check_tasks import TERMINAL, check_cancelled
from regen.execution.proof_commits import digest, durable
from regen.infrastructure.progress_events import event_scope, emit_event
from regen.planning.proof_workspace_agent import WorkspaceController, workspace_tools, _tool

INSTRUCTIONS = '''You are the continuing File Planner for the ONE assigned theory.
Own its proof graph and work normally until a verified finish/seal. Freely explore
contracts, helpers and coherent proof batches inside this theory. Ordinary proof
failure is feedback, not a reason to migrate to another theory or stop exploring.
Other theories have separate agents. Their work is readable as reference only.
No arbitrary shell/files/ML or removed human proofs are available. Preserve fixed
contracts and accepted proofs. Copied helpers require normal target checking.
Use submit_check(theory,kind,proposal) to execute one operation and wait for its result:
  proof_batch: proposal is the existing submit_batch action (including graph/reuse edits).
  seal: proposal is {"action":"finish"}.
  statement: proposal is a submit_batch graph patch with no proofs; it checks declarations only.
  symbol: proposal is a resolve_symbols action.
  observe: proposal is an observe action with independent reads, including query.
A statement success is NOT a proof or a graph commit. All tools within this
File Planner execute serially: submit_check returns a terminal task status and
its result, not a background job to overlap with another action. Proof batches
may contain multiple proofs checked in dependency order. Other theories can run
in parallel. Use read_theory and read_generated for direct reads; prepare_reuse
returns a source receipt, not GREEN.
get_check_result and wait_checks remain available to reread durable task receipts;
completed results do not require another wait. cancel_check remains a compatibility
control; normal actions finish before the next tool call. Accepted commits are
never rolled back. proof_action uses the same serial execution path.
Read failures and continue revising; do not assume unchanged failed candidates work.
Before ending, successfully seal the theory; a final message alone does not seal it.
If genuinely unable to finish, report the exact unresolved issue, never claim success.
'''


DOCUMENT_INSTRUCTIONS = '''You are the continuing File Planner for ONE Isabelle theory, using a persistent source workspace.
Read the assigned theory with read_theory. Its document.source is the editable .thy draft.
Implement the fixed goals by editing this file: batch several proofs, add/remove/reorder
helper declarations, check the current version, then revise using Isabelle feedback.
Do not create a proof DAG, consumer edges, routes, or scheduling JSON. File order determines
which earlier proven declarations are available; oops slots export no theorem.

Use proof_action(theory, proposal):
{"action":"edit_theory","base_sha256":"hash from read_theory","edits":[{"old":"unique exact text","new":"replacement"}]}
Or replace the complete file using source instead of edits. Edits are sequential. Saving
performs boundary/hygiene checks, not Isabelle validation. Failed proof drafts persist.
{"action":"check_theory","source_sha256":"current saved hash","reason":"what changed"}
This checks all supplied proof bodies in file order, atomically accepting a successful
version. Unchanged accepted prefixes may be reused. Editing/reordering/deleting accepted
proofs/helpers triggers full proof revalidation; old evidence remains historical only.
Read_theory returns the draft and last check feedback. You may edit a failed draft directly.
{"action":"finish","source_sha256":"accepted current draft hash"}
Seal requires every fixed goal and retained helper proved, unchanged since successful check,
then complete-theory verification, semantic audit and build. A final message is not a seal.

Preserve header/imports, fixed contract identity markers, exact statements and contexts.
The REGEN-BEGIN metadata has only id, role (contract/helper/theorem), context (an available
context list). REGEN-PROOF separates declaration and proof; REGEN-END closes the entry.
A helper is an ordinary named lemma entry with a unique ID/name. Copy a block layout,
choose its statement and context, and place it before consumers. No intended-consumer list
is required. Context commands must match the marker list and close before REGEN-END.
Use a sole oops for an unfinished proof; never sorry, axioms, ML or hidden declarations.
Isabelle decides candidate validity; uncertainty or lack of a recalled example is not a
reason to prohibit a trial. Keep useful local proofs in the shared file.

All tools within this File Planner are serial; other theories have separate agents.
Read external generated material with list_generated/read_generated. Unsealed/unimported
facts are references only; copy helpers into this draft and validate in the target context.
Retain legal premise search, symbol resolution, diagnostic/attempt reads through proof_action
using the observation formats below. context_entry_id is the stable entry ID of an accepted
or original contract declaration; new unsaved/unaccepted helpers have no accepted retrieval context.
Ordinary proof failure should lead to editing or retrieval, not abandoning the theory.
'''


class TheoryContinuationError(RuntimeError):
    """A provider turn ended repeatedly without advancing or sealing its theory."""


def _cleanup_all(actions, primary=None):
    """Run every cleanup, even when durable diagnostics or one close fails."""
    errors = []
    for action in actions:
        try:
            action()
        except BaseException as error:
            errors.append(error)
    if errors:
        target = primary if primary is not None else errors.pop(0)
        for error in errors:
            target.add_note('cleanup failed: '+type(error).__name__+': '+str(error))
        if primary is None:
            raise target


def _reclaim_completed_heaps(root, result):
    """Only call after future.result(): the controller's finally has completed."""
    if result.get('status') == 'sealed' and result.get('provider', {}).get('status') == 'completed':
        from regen.infrastructure.resource_budget import reclaim_theory_heaps
        result['heap_reclamation'] = reclaim_theory_heaps(root, result['theory'])


def _continuation_prompt(theory, state, idle_turns):
    open_count = len(state['graph']['nodes']) - len(state['proofs'])
    return (f'Continue the same assigned theory {theory}. The previous model turn ended, '
            f'but checker authority still reports status={state["status"]!r} with '
            f'{open_count} open declarations. A final message is not completion. '
            'Use the current draft and latest feedback to keep editing and '
            'checking until proof_action finish seals the theory. '
            'Do not repeatedly reread unchanged state or wait for another agent to edit this theory; '
            'you own its generation. Repeated reads and sleep are not progress. '
            + ('The preceding turn produced no new tool information or proof work; act on the feedback or report a concrete '
               'infrastructure fault through the tool feedback.' if idle_turns else ''))


def tools(document_mode=False):
    registry = workspace_tools()
    if document_mode:
        registry = [tool for tool in registry if tool['name'] != 'prepare_reuse']
        for tool in registry:
            if tool['name']=='read_theory':
                tool['description']='Read the persistent Isabelle draft, source hash, accepted entries and check feedback.'
            elif tool['name']=='proof_action':
                tool['description']='Edit/check/seal the persistent theory source, or execute a read-only observation.'
        return registry
    text = {'type':'string','minLength':1}
    registry.extend([
        _tool('submit_check','Execute one check serially; wait and return terminal status, result and durable task ID.',
              {'theory':text,'kind':{'enum':['proof_batch','statement','symbol','observe','seal']},
               'proposal':{'type':'object'}},('theory','kind','proposal')),
        _tool('get_check_result','Read a task result without repeating work.', {'task_id':text},('task_id',)),
        _tool('wait_checks','Read terminal task receipts; wait only for an outstanding compatibility task.',
              {'task_ids':{'type':'array','items':text,'minItems':1,'maxItems':16},
               'timeout':{'type':'number','minimum':0,'maximum':60}},('task_ids',)),
        _tool('cancel_check','Cancel this agent task; accepted commits are never rolled back.',
              {'task_id':text},('task_id',)),
    ])
    return registry


class TheoryController(WorkspaceController):
    def __init__(self, run_dir, theory, *, resources, executor, document_mode=False, **options):
        self.document_mode = document_mode
        super().__init__(run_dir,theories=[theory],**options)
        self.theory, self.resources, self.executor = theory, resources, executor
        with executor.lock:
            self.task_ids = {key for key,row in executor.records.items()
                             if row['theory']==theory and row['binding'].get('run_id')==self.run['run_id']}
        self._runtime_lock = threading.RLock()
        self._action_lock = threading.RLock()
        self._tool_lock = threading.RLock()

    def _observation_state(self, state):
        # Completed receipts are observation authority only, never proof authority.
        from regen.planning.observation_memory import merge, memory_value
        applied = set(memory_value(state,2,[]))
        with self.executor.lock:
            rows = [self.executor.get(key) for key in self.task_ids
                    if key not in applied and self.executor.records[key]['status']=='completed'
                    and self.executor.records[key]['kind'] in {'action','symbol'}]
        return merge(state,rows,self.executor.root)

    def _state(self, theory):
        package,state = super()._state(theory)
        if theory == self.theory:
            state = self._observation_state(state)
        return package,state

    def _runtime(self):
        from regen.execution.repository_execution import setup
        key = (str(self.root),self.theory,self.options['check_timeout'])
        with self._runtime_lock:
            if key not in self.runtime_cache:
                self.runtime_cache[key] = setup(self.root,self.theory,self.options['check_timeout'])
            self.runtime_cache[key][4].document_mode = self.document_mode
            return self.runtime_cache[key]

    @staticmethod
    def _public(row):
        from regen.planning.batch_file_planner import compact_feedback
        result = {key:value for key,value in row.items() if key not in {"request"}}
        if "result" in result:
            result["result"] = compact_feedback(result["result"])
        return result

    def _owned(self, task_id):
        row = self.executor.get(task_id)
        if row['theory'] != self.theory or task_id not in self.task_ids:
            raise ValueError('task does not belong to this File Planner session')
        return self._public(row)

    def _pending(self):
        return [self._owned(key) for key in self.task_ids if self._owned(key)['status'] not in TERMINAL]

    def handle(self, name, arguments, metadata):
        # Receipt inspection/cancellation may be used by an external supervisor.
        # All mathematical/read tools share one theory-local serial boundary.
        if name in {'get_check_result','wait_checks','cancel_check'}:
            return self._handle(name, arguments, metadata)
        with self._tool_lock:
            result = self._handle(name, arguments, metadata)
            if self.document_mode:
                from regen.planning.document_interface import public_view
                result = public_view(result)
            return result

    def _document(self):
        from regen.planning.theory_document import DocumentStore
        package,state = self._state(self.theory)
        runtime = self._runtime()
        return DocumentStore(runtime[2]/'document',package,state,runtime[4].imports()),state

    def _handle(self, name, arguments, metadata):
        if self.document_mode and name=='read_theory' and arguments.get('theory')==self.theory:
            store,state = self._document()
            from regen.planning.batch_file_planner import compact_feedback, planner_input
            context = planner_input(self.shared._package(self.theory),state)
            return {'theory':self.theory,'document':store.read(),
                    'accepted_ids':list(state['accepted_order']),
                    'accepted_source_sha256':state.get('document_source_sha256'),
                    'status':state['status'],'feedback':compact_feedback(state.get('batch_feedback',{})),
                    'observations':{k:compact_feedback(context[k]) for k in (
                        'query_index','query_memory','requested_attempts','next_proof_observation',
                        'requested_proof_state','resolved_symbols') if k in context}}
        if self.document_mode and name=='read_theory':
            package,state = self._state(arguments['theory'])
            return {'theory':arguments['theory'], 'status':state['status'],
                    'reference_only':True, 'repository_projection':package['repository_projection'],
                    'declarations':state['graph']['nodes'], 'proofs':state['proofs'],
                    'accepted_ids':state['accepted_order'],
                    'notice':'External accepted snapshot; copy/adapt source and validate in the assigned theory. No external draft is created.'}
        if self.document_mode and name == 'read_generated':
            from regen.planning.document_interface import internal_request
            return super().handle(name, internal_request(arguments), metadata)
        if self.document_mode and name=='proof_action':
            if arguments.get('theory')!=self.theory:
                raise ValueError('only the assigned theory may be edited')
            proposal=arguments['proposal']
            if not isinstance(proposal,dict):
                raise ValueError('proposal must be an object')
            if proposal.get('action') not in {'edit_theory','check_theory','finish'}:
                from regen.planning.document_interface import internal_request
                proposal = internal_request(proposal)
                arguments = {**arguments, 'proposal':proposal}
            allowed = {'edit_theory','check_theory','finish','observe','query','read_facts',
                       'read_proofs','read_attempts','read_queries','read_diagnostic',
                       'read_proof_state','resolve_symbols'}
            if proposal.get('action') not in allowed:
                raise ValueError('document mode requires source edits/checks, not legacy graph actions')
            if proposal.get('action')=='edit_theory':
                from regen.infrastructure.workspace_transcript import record_workspace_action
                store,state=self._document()
                if state['status'] in {'sealed','retired'}:
                    raise ValueError('terminal theory is immutable')
                prefix=store.root/'edits'/uuid4().hex/'call'
                record_workspace_action(prefix,'Persistent theory source edit',proposal,metadata,
                                        theory=self.theory,model=self.model)
                return store.edit(proposal,{'transcript_prefix':str(prefix)})
            if proposal.get('action')=='finish':
                store,state=self._document()
                current=store.read()
                if (proposal.get('source_sha256')!=current['source_sha256']
                        or state.get('document_source_sha256')!=current['source_sha256']
                        or current['open_ids']):
                    raise ValueError('seal requires the current draft checked successfully with no open proof slots')
        if self.document_mode and name == 'prepare_reuse':
            raise ValueError('read_generated and copy source entries into the document for fresh checking')
        if name == 'list_workspace':
            result = super().handle(name,arguments,metadata)
            result.pop('worker_count', None)
            result['assigned_theory'] = self.theory
            result['assigned_writer_count'] = 1
            result['theory_tool_execution'] = 'serial'
            result['max_parallel_theories'] = self.executor.worker_capacity
            result.update(self.resources.policy)
            result['check_tasks'] = [{key:row[key] for key in ('task_id','kind','status')}
                                     for row in (self._owned(task) for task in sorted(self.task_ids))]
            return result
        if name == 'submit_check':
            if self.document_mode:
                raise ValueError('document mode uses check_theory through proof_action')
            return self.submit(arguments, metadata)
        if name == 'get_check_result':
            return self._owned(arguments['task_id'])
        if name == 'wait_checks':
            for key in arguments['task_ids']:
                self._owned(key)
            return {'tasks':[self._public(row) for row in self.executor.wait(arguments['task_ids'],timeout=arguments.get('timeout',30))]}
        if name == 'cancel_check':
            self._owned(arguments['task_id'])
            return self._public(self.executor.cancel(arguments['task_id']))
        if name == 'proof_action':
            if arguments.get('proposal',{}).get('action') == 'pause':
                raise ValueError('continue local exploration; report an external blocker or unresolved outcome in the final response')
            action = arguments['proposal'].get('action')
            kind = {'submit_batch':'proof_batch','check_theory':'proof_batch','finish':'seal','resolve_symbols':'symbol'}.get(action,'observe')
            proposal = arguments['proposal']
            if kind == 'observe' and action != 'observe':
                proposal = {'action':'observe','reads':[proposal]}
            row = self.submit({'theory':arguments['theory'],'kind':kind,'proposal':proposal},metadata)
            if row.get('error_type') == 'NativePreparationError':
                from regen.execution.check_tasks import NativePreparationError
                raise NativePreparationError(row.get('diagnostic','native prefix preparation failed'))
            if row['status'] != 'completed':
                return {'status':row['status'],'task_id':row['task_id'],
                        'diagnostic':row.get('diagnostic','')}
            return {**self._public(row)['result'],'task_id':row['task_id']}
        return super().handle(name, arguments, metadata)

    def submit(self, arguments, metadata):
        """Complete one operation before admitting another for this theory."""
        with self._tool_lock:
            row = self._submit_task(arguments, metadata)
            while row['status'] not in TERMINAL:
                row = self.executor.wait([row['task_id']], timeout=30)[0]
            return self._public(row)

    def _submit_task(self, arguments, metadata):
        if arguments['theory'] != self.theory:
            raise ValueError('only the assigned theory may be checked or modified')
        kind, proposal = arguments['kind'], deepcopy(arguments['proposal'])
        expected = {'proof_batch':'submit_batch','seal':'finish','statement':'submit_batch',
                    'symbol':'resolve_symbols','observe':'observe'}
        if kind not in expected or (proposal.get('action') != expected[kind] and not (kind=='proof_batch' and proposal.get('action')=='check_theory')):
            raise ValueError('check kind and proposal action disagree')
        if 'notes' in proposal and not isinstance(proposal['notes'],str):
            raise ValueError('notes must be a string')
        if kind == 'statement' and proposal.get('proofs'):
            raise ValueError('statement task must not contain proof bodies')
        if kind == 'seal' and self._pending():
            raise ValueError('wait for outstanding checks before seal changes build outputs')
        if any(row['kind']=='seal' for row in self._pending()):
            raise ValueError('seal is in flight; its frozen build inputs are exclusive')
        row = next(row for row in self.list_workspace()['theories'] if row['theory']==self.theory)
        if not row['ready_for_proof_action']:
            raise ValueError('theory is not ready for checks: '+json.dumps(row['unsealed_providers']))
        package, snapshot = self._state(self.theory)
        binding = {key:snapshot[key] for key in ('graph','proofs','accepted_order')}
        binding = {'state_sha256':digest(binding),'run_id':self.run['run_id'],
                   'package_sha256':package['package_payload_sha256']}
        metadata = deepcopy(metadata)
        task_id = digest({'thread':metadata['thread_id'],'turn':metadata['turn_id'],
                          'call':metadata['call_id'],'theory':self.theory})

        def execute(directory):
            marker = CURRENT_RESOURCES.set(self.resources)
            try:
                with event_scope(directory,run_id=self.run['run_id'],theory=self.theory,task_id=task_id):
                    emit_event('check_task_started',kind=kind)
                    runtime = self._runtime()
                    check_cancelled()
                    if kind in {'proof_batch','seal'}:
                        with self._action_lock:
                            # Read-only observations never mutate this accepted snapshot.
                            _, now = self._state(self.theory)
                            actual = digest({key:now[key] for key in ('graph','proofs','accepted_order')})
                            # Initial scaffold creation happens inside proof_action below.
                            if actual != binding['state_sha256']:
                                raise ValueError('submitted candidate context is stale')
                            runtime[4].work_dir = directory/'isabelle'
                            runtime[4].work_dir.mkdir(exist_ok=True)
                            from regen.planning.observation_memory import task_order
                            self._action_observation_order = task_order(self.executor.get(task_id))
                            result = super(TheoryController,self).proof_action(
                                {'theory':self.theory,'proposal':proposal},metadata)
                    else:
                        result = self._observe(kind,proposal,snapshot,package,directory)
                    emit_event('check_task_result_ready',kind=kind)
                    return result
            finally:
                CURRENT_RESOURCES.reset(marker)

        result = self.executor.submit(kind='action' if kind=='observe' else kind,
            theory=self.theory,binding=binding,request={'proposal':proposal,'metadata':metadata},
            execute=execute,owner=self.theory,task_id=task_id)
        with self.executor.lock:
            self.task_ids.add(result['task_id'])
        return self._public(result)

    def _observe(self, kind, proposal, snapshot, package, directory):
        from regen.execution.isabelle_node_checker import IsabelleChecker
        from regen.execution.proof_batches import prepare_batch
        from regen.planning.batch_file_planner import execute_query
        from regen.planning.planner_workspace import read_observation, observe
        run, _, theory_dir, _, original, _ = self._runtime()
        # Read-only tasks get their own checker/cache/output; immutable providers are copied,
        # heaps are mounted read-only. Seal is barred until these tasks finish.
        generated = directory/'generated'
        generated.mkdir(exist_ok=True)
        for path in original.generated_dir.iterdir():
            if path.suffix == '.thy' or path.name == 'ROOT':
                shutil.copy2(path,generated/path.name)
        checker = IsabelleChecker(original.run_dir,directory/'isabelle',
            image=original.image,architecture=original.architecture,timeout=original.timeout,
            previous_theories=list(original.previous_theories),generated_dir=generated,
            heaps_dir=original.heaps_dir,l4v_dir=original.l4v_dir,
            semantic_workspace=original.semantic_workspace,base_session=original.base_session,
            base_import=original.base_import,base_catalog=original.base_catalog,
            isabelle_dir=original.isabelle_dir,import_all_previous=original.import_all_previous)
        checker.declaration_environment = digest(run)
        command = checker._command
        def readonly(*args, **kwargs):
            return [value+',readonly' if value.startswith('type=bind,') and value.endswith('dst=/root/.isabelle/heaps')
                    else value for value in command(*args,**kwargs)]
        checker._command = readonly
        try:
            if kind == 'statement':
                candidate = prepare_batch(package,snapshot,{key:value for key,value in proposal.items() if key not in {'action','notes','workspace'}})
                if 'workspace' in proposal:
                    from regen.planning.planner_workspace import validate_workspace
                    validate_workspace(snapshot,proposal['workspace'],candidate_nodes=candidate['graph']['nodes'])
                return {'check':checker.check_graph(candidate['graph']), 'proofs_checked':False,
                        'committed':False,'context_sha256':digest(snapshot['graph'])}
            # Retrieval resolves failure evidence and sealed imports relative to the
            # owning theory; only its audit output belongs in the task directory.
            query_context = theory_dir/'batch-requests'/directory.name
            query = lambda request,index: execute_query(run,package,snapshot,checker,query_context,
                request,index,audit_directory=directory)
            if kind == 'observe':
                return observe(theory_dir,snapshot,proposal,checker,query_handler=query)
            return read_observation(theory_dir,snapshot,proposal,checker,query_handler=query)
        finally:
            checker.close_prefix()

    def close(self):
        def drain():
            for key in list(self.task_ids):
                while self.executor.get(key)['status'] not in TERMINAL:
                    self.executor.wait([key],timeout=1)
        _cleanup_all([*(lambda key=key: self.executor.cancel(key) for key in list(self.task_ids)),
                      drain, super().close])


def run_theory_session(root, theory, *, resources, executor, settings, directory,
                       model=None, call_timeout=600, check_timeout=900, candidate_timeout=120,
                       codex_home=None, cancel_event=None, document_mode=True):
    from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
    from regen.infrastructure.workspace_transcript import audit_workspace_session
    from regen.planning.batch_file_planner import INSTRUCTIONS as FORMATS
    controller = TheoryController(root,theory,resources=resources,executor=executor,document_mode=document_mode,
        model=model,call_timeout=call_timeout,check_timeout=check_timeout,candidate_timeout=candidate_timeout)
    try:
        with event_scope(directory,run_id=controller.run['run_id'],theory=theory):
            durable(directory/'policy.json',{'theory':theory,**controller.options,
                    **resources.policy,'mode':'parallel-file-planner-v1','theory_tool_execution':'serial',
                    'progress_policy':{'idle_turn_limit':2,'repeated_result_limit':8,
                        'no_information_timeout_seconds':call_timeout,'callback_time_excluded':True,
                        'sleep_item_max_ms':60000,'sleep_per_turn_max_ms':120000},
                    'file_planner_interface':'document' if document_mode else 'graph'},immutable=True)
            with tempfile.TemporaryDirectory(prefix='regen-theory-auth-') as temp:
                home = Path(temp)
                auth = Path(codex_home or os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'auth.json'
                if auth.is_file():
                    shutil.copyfile(auth,home/'auth.json'); (home/'auth.json').chmod(0o600)
                formats = FORMATS.replace('Return one JSON action per call. No shell, arbitrary files or external tools.',
                    'Use the action formats below inside the approved tools; no shell or external tools.')
                formats = formats.split('{"action":"pause"',1)[0]
                if document_mode:
                    from regen.planning.document_interface import observation_instructions
                    formats = observation_instructions()
                prompt = (DOCUMENT_INSTRUCTIONS if document_mode else INSTRUCTIONS)+'\n'+formats+'\nASSIGNED THEORY: '+theory+'\n'+json.dumps(controller.handle('list_workspace',{},{}),ensure_ascii=False)
                providers = []
                idle_turns = 0
                with CodexWorkspaceSession(tools=tools(document_mode),codex_bin=settings['codex_bin'],codex_home=home,
                                           allow_external_call=True,progress_timeout=call_timeout) as session:
                    next_prompt = prompt
                    while True:
                        calls_before = session.tool_call_count
                        provider = session.run(next_prompt,controller.handle,artifact_dir=directory,model=controller.model,
                                               reasoning_effort=settings['reasoning_effort'],timeout=call_timeout,
                                               cancel_event=cancel_event)
                        providers.append(provider)
                        _, state = controller._state(theory)
                        if state['status'] in {'sealed','retired'}:
                            break
                        made_progress = provider.get('progress_made', session.tool_call_count != calls_before)
                        idle_turns = 0 if made_progress else idle_turns + 1
                        if idle_turns >= 2:
                            raise TheoryContinuationError(
                                f'{theory} ended two consecutive turns without new information or proof work; '
                                f'checker status remains {state["status"]}')
                        next_prompt = _continuation_prompt(theory,state,idle_turns)
                audit = audit_workspace_session(directory/'session-transcript.jsonl',require_terminal=True,require_real=True)
            # A model ending a turn never upgrades pending work to success.
            _, state = controller._state(theory)
            result = {'theory':theory,'status':state['status'],'provider':providers[-1],
                      'provider_turns':providers,'continuation_turns':len(providers)-1,'audit':audit,
                      'task_ids':sorted(controller.task_ids),'artifact_dir':str(directory)}
            durable(directory/'result.json',result,immutable=True)
            return result
    finally:
        _cleanup_all([controller.close], primary=sys.exc_info()[1])


def _execution_policy(value, selected):
    value = {} if value is None else deepcopy(value)
    if not isinstance(value, dict) or set(value) - {'stages', 'session_stall_retries'}:
        raise ValueError('invalid execution policy keys')
    stages = value.get('stages', [])
    retries = value.get('session_stall_retries', 0)
    if type(retries) is not int or not 0 <= retries <= 3:
        raise ValueError('session_stall_retries must be an integer from 0 to 3')
    if not isinstance(stages, list):
        raise ValueError('stages must be a list')
    seen = set()
    for stage in stages:
        if not isinstance(stage, list) or not stage:
            raise ValueError('each stage must be a nonempty list')
        for theory in stage:
            if not isinstance(theory, str) or theory not in selected or theory in seen:
                raise ValueError('stage contains an unknown or repeated theory')
            seen.add(theory)
    return {'stages':stages, 'session_stall_retries':retries}


def _stage_eligible(rows, policy, active=()):
    states = {row['theory']:row['status'] for row in rows}
    for stage in policy['stages']:
        if any(states.get(theory) not in {'sealed','retired'} or theory in active for theory in stage):
            return set(stage)
    return {row['theory'] for row in rows if row['selected']}


def _retry_stalled(error, attempt, retries):
    from regen.infrastructure.codex_workspace_session import SessionStalled
    # A cleanup failure may leave a live owner; never overlap it with a retry.
    return (isinstance(error, SessionStalled) and attempt <= retries
            and not getattr(error, '__notes__', ()))


def run_parallel_workspace(run_dir, *, theories=None, max_parallel_theories=2, native_slots=1,
                           native_threads=4, resident_limit=2, model=None, call_timeout=600,
                           check_timeout=900, candidate_timeout=120, allow_external_call=False,
                           codex_home=None, integrate=True, file_planner_interface='document',
                           execution_policy=None):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.infrastructure.audited_llm import model_settings
    if not allow_external_call:
        raise PermissionError('parallel workspace requires --allow-external-call')
    if type(max_parallel_theories) is not int or max_parallel_theories < 1:
        raise ValueError('max_parallel_theories must be positive')
    if file_planner_interface not in {'document','graph'}:
        raise ValueError('file_planner_interface must be document or graph')
    root = Path(run_dir).resolve()
    # This is the sole coordinator/legacy-runner exclusion lock. Worker actions do
    # not acquire it; their mutations are theory-owned and short journal transactions.
    resources = NativeResources(native_slots,native_threads,resident_limit)
    lock = acquire_run_mutation_lock(root,operation='parallel-workspace')
    executor = None
    old_signals = {}
    terminating = False
    if threading.current_thread() is threading.main_thread():
        def terminate(signum, frame):
            nonlocal terminating
            # The service manager bounds shutdown; repeated soft signals must
            # not interrupt cancellation halfway through resource cleanup.
            if terminating:
                return
            terminating = True
            raise KeyboardInterrupt('parallel workspace terminated by signal '+str(signum))
        for signum in (signal.SIGTERM, signal.SIGHUP):
            old_signals[signum] = signal.signal(signum, terminate)
    try:
        overview = WorkspaceController(root,theories=theories,model=model,
            call_timeout=call_timeout,check_timeout=check_timeout,candidate_timeout=candidate_timeout)
        overview.document_mode = file_planner_interface == 'document'
        selected = overview.selected
        policy = _execution_policy(execution_policy, selected)
        if overview.run.get("execution_input_kind") != "refine-dataset":
            raise ValueError("parallel workspace requires isolated refine-dataset input; legacy shared build directories are unsupported")
        if overview.run.get('execution_input_kind')=='refine-dataset':
            from regen.preparation.checker_context import load_context
            context = load_context(Path(overview.run['checker_context_root']))
            if context['checker_context_id'] != overview.run['checker_context_id']:
                raise ValueError('checker context differs from frozen run')
        settings = model_settings()
        if hashlib.sha256(Path(settings['codex_bin']).read_bytes()).hexdigest()!=settings['codex_sha256']:
            raise ValueError('Codex executable differs from frozen configuration')
        durable(root/'parallel-workspace-policy.json',{'mode':'parallel-file-planner-v1',
            'selected_theories':selected,'max_parallel_theories':max_parallel_theories,
            'theory_tool_execution':'serial','file_planner_interface':file_planner_interface,
            **resources.policy,**overview.options,'integration_required':integrate,
            'execution_policy':policy},immutable=True)
        executor = CheckExecutor(root/'check-tasks',workers=max_parallel_theories)
        results, active, failures = [], {}, {}
        attempts, directories = {}, {}
        cancel_event = threading.Event()
        sessions = root/'parallel-sessions'/uuid4().hex
        with event_scope(sessions,run_id=overview.run['run_id']):
            with ThreadPoolExecutor(max_workers=max_parallel_theories,thread_name_prefix='file-planner') as agents:
                try:
                    while True:
                        rows = overview.list_workspace()['theories']
                        eligible = _stage_eligible(rows, policy, active.values())
                        for row in rows:
                            if len(active)>=max_parallel_theories:
                                break
                            theory = row['theory']
                            if (theory not in eligible or theory in failures or theory in active.values()
                                    or not row['ready_for_proof_action']):
                                continue
                            attempt = attempts.get(theory, 0) + 1
                            attempts[theory] = attempt
                            directory = sessions/theory
                            if attempt > 1:
                                directory = directory/f'retry-{attempt-1:03d}'
                            directories[theory] = directory
                            durable(directory/'attempt.json', {'theory':theory,'attempt':attempt,
                                'call_timeout':call_timeout}, immutable=True)
                            context = copy_context()
                            future = agents.submit(context.run,run_theory_session,root,theory,
                                resources=resources,executor=executor,settings=settings,directory=directory,
                                codex_home=codex_home,cancel_event=cancel_event,document_mode=file_planner_interface=='document',**overview.options)
                            active[future] = theory
                        if not active:
                            break
                        done,_ = wait(active,timeout=1,return_when=FIRST_COMPLETED)
                        for future in done:
                            theory = active.pop(future)
                            try:
                                completed = future.result()
                                # A persisted result.json alone is too early:
                                # run_theory_session must finish controller.close
                                # before removing any worker-mounted heap cache.
                                _reclaim_completed_heaps(root, completed)
                                results.append(completed)
                                if completed.get('status') not in {'sealed','retired'}:
                                    failures[theory] = {'reason':'session_returned_unsealed',
                                                        'status':completed.get('status')}
                            except Exception as error:
                                result = {'theory':theory,'status':'infrastructure_error',
                                          'error_type':type(error).__name__,'diagnostic':str(error)}
                                retry = _retry_stalled(error, attempts[theory], policy['session_stall_retries'])
                                result.update(attempt=attempts[theory], retry_scheduled=retry)
                                durable(directories[theory]/'failure.json',result,immutable=True)
                                results.append(result)
                                if retry:
                                    emit_event('session_stall_retry_scheduled',theory=theory,
                                               next_attempt=attempts[theory]+1)
                                    continue
                                failures[theory] = {'reason':'infrastructure_error',
                                                    'error_type':type(error).__name__,
                                                    'diagnostic':str(error)}
                        # Refill immediately; never wait for an entire wave of theories.
                except BaseException as error:
                    cancel_event.set()
                    with executor.lock:
                        executor.closed = True
                        pending = list(executor.records)
                    # Cancel before diagnostics: ENOSPC must never leave a
                    # callback running while the agent pool waits to join it.
                    _cleanup_all([
                        *(lambda task=task: executor.cancel(task) for task in pending),
                        lambda: emit_event('parallel_workspace_interrupted',
                            exception_type=type(error).__name__,reason=str(error)),
                        lambda: durable(sessions/'interruption.json',{'status':'interrupted',
                            'error_type':type(error).__name__,'diagnostic':str(error),
                            'active_theories':list(active.values())}),
                    ], primary=error)
                    raise
            final = overview.list_workspace()
            final['worker_count'] = max_parallel_theories
            incomplete = [row for row in final['theories']
                          if row['selected'] and row['status'] not in {'sealed','retired'}]
            eligible = _stage_eligible(final['theories'], policy)
            runnable = [row['theory'] for row in incomplete
                        if row['ready_for_proof_action'] and row['theory'] not in failures
                        and row['theory'] in eligible]
            if runnable:
                raise RuntimeError('scheduler exited while runnable theories remain: '+', '.join(runnable))
            blocked = {row['theory']:{'reason':('priority_stage_incomplete'
                       if row['theory'] not in eligible else 'dependency_blocked'),
                       'unsealed_providers':row.get('unsealed_providers',[])}
                       for row in incomplete if row['theory'] not in failures}
            result = {'status':'partial','workspace':final,'results':results,'repository_verified':False,
                      'integration_required':integrate,'failures':failures,'blocked':blocked,
                      'session_attempts':attempts,'execution_policy':policy}
            all_sealed = all(row['status']=='sealed' or (row['status']=='retired' and not row['contracts'])
                             for row in final['theories'])
            if integrate and all_sealed:
                from regen.execution.repository_integration import integrate_repository
                marker = CURRENT_RESOURCES.set(resources)
                try:
                    result['integration'] = integrate_repository(root,timeout=check_timeout)
                finally:
                    CURRENT_RESOURCES.reset(marker)
                result['repository_verified'] = result['integration']['repository_verified']
                result['status'] = 'repository_verified' if result['repository_verified'] else 'integration_failed'
            elif all(row['status']=='sealed' for row in final['theories'] if row['selected']):
                result['status'] = 'selected_theories_sealed'
            durable(sessions/'result.json',result,immutable=True)
            return result
    finally:
        try:
            _cleanup_all(([executor.close] if executor is not None else []) + [resources.close],
                         primary=sys.exc_info()[1])
        finally:
            _cleanup_all([
                *(lambda signum=signum, handler=handler: signal.signal(signum, handler)
                  for signum, handler in old_signals.items()),
                # A failed join may leave callbacks able to mutate this run.
                # Keep ownership until they stop or the service exits.
                *([lambda: release_run_mutation_lock(lock)]
                  if executor is None or executor.shutdown_complete else []),
            ], primary=sys.exc_info()[1])
