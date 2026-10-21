"""One persistent repository workspace agent; one active native proof worker.

The agent may switch between ready theories without sealing its previous target.
All mutations go through the batch File Planner's existing journal and checker.
Shared proof reads carry no theorem authority; copied helpers are checked again.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from regen.execution.proof_commits import durable
from regen.execution.shared_proof_workspace import SharedProofWorkspace
from regen.planning.batch_file_planner import INSTRUCTIONS, _run_batch_file, compact_feedback, planner_input


WORKSPACE_INSTRUCTIONS = '''You are the continuing File Planner for a SHARED repository proof workspace.
Use the supplied dynamic tools throughout this session. You may switch theories,
inspect another theory's accepted helpers or failed drafts, and submit coherent
batches. There is ONE serial native proof worker; batching inside a theory is allowed.
The repository's collaboration groups and reuse suggestions guide exploration;
they are not mandatory imports or a required proof order. Only required_imports
are hard build dependencies. A blocked theory's contracts remain readable.
Read a theory's current state with read_theory before changing it. Do not wait for
an entire provider to seal merely to inspect/adapt its partial results. Use
list_generated/read_generated and read_attempt for source examples. These are
reference-only, including GREEN results from another theory. To copy a helper use
prepare_reuse, inspect the complete returned closure, then include its reuse_request
in proposal.reuse_helpers. The harness rechecks every copied proof in the target.
Contexts and contracts may not be silently moved or weakened. If a helper depends
on an unmapped contract, adapt the approach or prove a local helper yourself.
Unsealed theories are not direct imports. Failed drafts can inform new candidates
but are never accepted premises. No arbitrary filesystem, shell, browser, or removed
human target proofs are available. Use proof_action(theory, proposal) for all
existing File Planner actions, including retrieval, graph edits, batches and finish.
The action formats below describe proposal. You are not limited to one action in
this turn: continue using tools and feedback. Keep a short explanation of the next
useful trial. Finish each selected theory with a successful full seal; final text
alone is never success. End early only for a concrete unresolved blocker and report it.
'''


def _tool(name, description, properties, required=()):
    return {'name':name, 'description':description, 'inputSchema':{
        'type':'object', 'properties':properties, 'required':list(required), 'additionalProperties':False}}


def workspace_tools():
    text = {'type':'string', 'minLength':1}
    obj = {'type':'object'}
    return [
        _tool('list_workspace', 'Read contract distribution, hard dependencies, advisory groups and current progress.', {}),
        _tool('read_theory', 'Read fixed contracts, local graph, GREEN index and recent feedback; no proof check.', {'theory':text}, ('theory',)),
        _tool('list_generated', 'Search accepted generated examples across this run, including unsealed theories.',
              {'theory':text, 'query':{'type':'string'}, 'offset':{'type':'integer','minimum':0},
               'limit':{'type':'integer','minimum':1,'maximum':100}}),
        _tool('read_generated', 'Read exact declarations, contexts and proof bodies from immutable source references.',
              {'source_refs':{'type':'array','items':obj,'minItems':1,'maxItems':8}}, ('source_refs',)),
        _tool('list_attempts', 'Page verified failed history; filter explicitly by entry ID/status before reading details. Submitted does not mean failed.',
              {'theory':text, 'entry_id':text,
               'status':{'enum':['rejected_batch','rejected_request','checked_not_committed']},
               'offset':{'type':'integer','minimum':0}, 'limit':{'type':'integer','minimum':1,'maximum':100},
               'snapshot_commit_id':text}, ('theory',)),
        _tool('read_attempt', 'Read a verified failed-attempt summary by default. Use view=full for exact original candidate, context and feedback. Reference only.',
              {'theory':text, 'request_number':{'type':'integer','minimum':1},
               'view':{'enum':['summary','full']}}, ('theory','request_number')),
        _tool('prepare_reuse', 'Prepare a complete helper closure and receipt for fresh target-native validation.',
              {'source_ref':obj,'target_theory':text,'target_node_id':text,'mapping':obj},
              ('source_ref','target_theory','target_node_id')),
        _tool('proof_action', 'Execute one existing File Planner action and return verified feedback; never auto-retried.',
              {'theory':text,'proposal':obj}, ('theory','proposal')),
    ]


class WorkspaceController:
    """Caller holds the run mutation lock for the full provider session."""
    def __init__(self, run_dir, *, theories=None, model=None, call_timeout=600,
                 check_timeout=900, candidate_timeout=120):
        from regen.execution.proof_session_runner import frozen_role_model
        self.root = Path(run_dir).resolve()
        self.shared = SharedProofWorkspace(self.root)
        self.run = self.shared.run
        self.selected = list(theories) if theories is not None else list(self.shared.entries)
        if not self.selected or len(set(self.selected)) != len(self.selected) or any(
                t not in self.shared.entries for t in self.selected):
            raise ValueError('select distinct theories belonging to this run')
        self.model = model or frozen_role_model(self.run, 'File Planner')
        if self.model != frozen_role_model(self.run, 'File Planner'):
            raise ValueError('workspace model differs from run-frozen File Planner model')
        if min(call_timeout, check_timeout, candidate_timeout) <= 0:
            raise ValueError('workspace timeouts must be positive')
        self.options = dict(model=self.model, call_timeout=call_timeout,
                            check_timeout=check_timeout, candidate_timeout=candidate_timeout)
        self.runtime_cache = {}
        self.verify_implementation()

    def verify_implementation(self):
        repo = Path(__file__).resolve().parents[2]
        if any(hashlib.sha256((repo/path).read_bytes()).hexdigest() != sha
               for path,sha in self.run['implementation'].items()):
            from regen.execution.check_tasks import ImplementationChangedError
            raise ImplementationChangedError('execution code changed after run initialization; use a new proof run')

    def _state(self, theory):
        from regen.planning.declaration_index import accepted_declarations
        from regen.planning.proof_graph import build_graph
        package = self.shared._package(theory)
        snapshot = self.shared._snapshot(theory)
        if snapshot:
            return package, deepcopy(snapshot['state'])
        if getattr(self, 'document_mode', False):
            from regen.planning.declaration_index import initial_index
            graph = initial_index(package, run_id=self.run['run_id']+'--'+theory)
        else:
            graph = build_graph(package, {'generated_nodes':[], 'contract_dependencies':{}},
                                run_id=self.run['run_id']+'--'+theory, revision=1)
        graph.update(initial_graph_owner='File Planner', runtime_graph_owner='File Planner',
                     execution_mode='file-planner-batch-v1')
        return package, {'graph':accepted_declarations(graph, []), 'proofs':{}, 'accepted_order':[],
                         'rounds':{},'revisions':{},'status':'not_started','seal_id':None}

    def list_workspace(self):
        from regen.planning.import_contract import required_imports
        rows, states = [], {}
        for theory in self.shared.entries:
            package, state = self._state(theory)
            states[theory] = state['status']
            projection = package['repository_projection']
            rows.append({'theory':theory, 'selected':theory in self.selected,
                         'status':state['status'], 'green_count':len(state['proofs']),
                         'contracts':[{'id':c['contract_id'], 'name':c.get('name', c['contract_id'])}
                                      for c in package['contracts']],
                         'required_imports':[p['theory'] for p in required_imports(projection)],
                         **{k:deepcopy(projection[k]) for k in ('collaboration_groups','reuse_suggestions') if k in projection}})
        for row in rows:
            row['unsealed_providers'] = [t for t in row['required_imports'] if states.get(t) != 'sealed']
            row['ready_for_proof_action'] = row['selected'] and not row['unsealed_providers'] and row['status'] not in {'sealed','retired'}
        return {'run_id':self.run['run_id'], 'worker_count':1, 'theories':rows,
                'all_selected_sealed':all(states[t] in {'sealed','retired'} for t in self.selected)}

    def _theory_readiness(self, theory, *, package=None, state=None):
        """Read only a target and its hard providers, not the whole repository."""
        from regen.planning.import_contract import required_imports
        if theory not in self.selected:
            raise ValueError('proof target is outside selected theories')
        if package is None or state is None:
            package, state = self._state(theory)
        providers = [p['theory'] for p in required_imports(package['repository_projection'])]
        unsealed = [provider for provider in providers
                    if provider not in self.shared.entries or self._state(provider)[1]['status'] != 'sealed']
        return {'theory':theory, 'status':state['status'], 'unsealed_providers':unsealed,
                'ready_for_proof_action':not unsealed and state['status'] not in {'sealed','retired'}}

    def _all_selected_sealed(self, theory, state):
        # Most actions leave their target generating; that alone proves false.
        if state['status'] not in {'sealed','retired'}:
            return False
        return all(self._state(other)[1]['status'] in {'sealed','retired'}
                   for other in self.selected if other != theory)

    def handle(self, name, arguments, metadata):
        from regen.infrastructure.progress_events import emit_event
        emit_event('workspace_tool_started', tool=name, theory=arguments.get('theory', arguments.get('target_theory')),
                   call_id=metadata.get('call_id'))
        try:
            if name == 'list_workspace':
                return self.list_workspace()
            if name == 'read_theory':
                package,state = self._state(arguments['theory'])
                from regen.planning.planner_context import project_context
                projection = package['repository_projection']
                context = {key:deepcopy(projection[key]) for key in (
                    'theory','file_slot','purpose','grouping_reason','context_profile_ids','context_profiles',
                    'required_imports','required_providers','suggested_predecessors','suggested_providers',
                    'collaboration_groups','reuse_suggestions','auxiliary_intents','import_semantics') if key in projection}
                return {'status':'available', 'input':project_context(planner_input(package,state)),
                        'repository_projection':context}
            if name == 'list_generated':
                return self.shared.index(**arguments)
            if name == 'read_generated':
                return self.shared.read(arguments['source_refs'])
            if name == 'read_attempt':
                return self.shared.read_attempt(**arguments)
            if name == 'list_attempts':
                return self.shared.list_attempts(**arguments)
            if name == 'prepare_reuse':
                _,state = self._state(arguments['target_theory'])
                if arguments['target_theory'] not in self.selected:
                    raise ValueError('reuse target is outside selected theories')
                return self.shared.prepare_reuse(arguments['source_ref'],target_state=state,
                    target_node_id=arguments['target_node_id'],mapping=arguments.get('mapping'))
            if name == 'proof_action':
                return self.proof_action(arguments, metadata)
            raise ValueError('unknown workspace tool')
        finally:
            emit_event('workspace_tool_finished', tool=name, call_id=metadata.get('call_id'))

    def proof_action(self, arguments, metadata, *, state_snapshot=None, helper_attempt=None):
        from regen.infrastructure.workspace_transcript import record_workspace_action, audit_workspace_session
        theory, proposal = arguments['theory'], arguments['proposal']
        if not isinstance(proposal, dict):
            raise ValueError('proposal must be an object')
        row = (self._theory_readiness(theory) if state_snapshot is None else
               self._theory_readiness(theory, package=self.shared._package(theory), state=state_snapshot))
        if row['status'] in {'sealed','retired'}:
            return {'status':'already_'+row['status'],'theory':theory}
        if row['unsealed_providers']:
            return {'status':'not_ready','theory':theory,'unsealed_providers':row['unsealed_providers'],
                    'notice':'You may inspect its contracts and source helpers, or work on an available provider.'}
        self.verify_implementation()
        # Never keep multiple native prefixes executing while switching theories.
        for key, runtime in self.runtime_cache.items():
            if key[1] != theory:
                runtime[4].close_prefix()
        if proposal.get('action') == 'finish':
            # The callback reader continues appending notifications while a
            # seal runs. Audit the already durable callback boundary, never an
            # unconstrained read that can end in its next partially written line.
            offset = metadata.get('transcript_offset')
            if type(offset) is not int or offset <= 0:
                raise ValueError('finish requires a durable workspace transcript offset')
            audit_workspace_session(metadata['transcript_path'], require_terminal=False,
                                    require_real=True, offset=offset)
        def decision_source(prompt, payload, prefix, **identity):
            return record_workspace_action(prefix,prompt,proposal,metadata,**identity)
        committed = []
        _run_batch_file(self.root, theory, provider='codex', max_requests=1,
                        decision_source=decision_source,runtime_cache=self.runtime_cache,
                        observation_source=getattr(self,'_observation_state',None),
                        observation_order=getattr(self,'_action_observation_order',None),
                        state_sink=committed.append, incremental_progress=True,
                        helper_attempt=helper_attempt, **self.options)
        if committed:
            state = committed[-1]
        else:
            _, state = self._state(theory)
        self._completed_action_state = state
        from regen.planning.planner_efficiency import feedback_semantics, next_proof_observation
        feedback = state.get('batch_feedback', {})
        result = {'theory':theory,'status':state['status'],'request_number':state.get('batch_requests',0),
                'green_node_ids':list(state['accepted_order']),
                'feedback':compact_feedback(feedback),
                **feedback_semantics(feedback),
                'all_selected_sealed':self._all_selected_sealed(theory, state)}
        if proposal.get('action') == 'finish' and state['status'] == 'sealed':
            result.update(tool_status='completed', proof_verdict='green', publication='sealed')
        if proposal.get('action') in {'check_theory', 'submit_batch'}:
            observation = next_proof_observation(state, feedback=feedback)
            if observation is not None:
                result['next_proof_observation'] = observation
        return result

    def close(self):
        errors = []
        for runtime in self.runtime_cache.values():
            try:
                runtime[4].close_prefix()
            except Exception as error:
                errors.append(error)
        self.runtime_cache.clear()
        if errors:
            raise RuntimeError('workspace native worker cleanup failed') from errors[0]


def run_workspace(run_dir, *, theories=None, model=None, call_timeout=600, check_timeout=900,
                  candidate_timeout=120, allow_external_call=False, codex_bin=None, codex_home=None):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.infrastructure.audited_llm import model_settings
    from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
    from regen.infrastructure.progress_events import configure_event_monitor, emit_event, close_event_monitor
    from regen.infrastructure.workspace_transcript import audit_workspace_session
    if not allow_external_call:
        raise PermissionError('workspace provider is opt-in: use --allow-external-call')
    root = Path(run_dir).resolve()
    token = acquire_run_mutation_lock(root, operation='workspace')
    controller = None
    outcome = 'failed'
    directory = root/'workspace-sessions'/uuid4().hex
    try:
        controller = WorkspaceController(root,theories=theories,model=model,call_timeout=call_timeout,
            check_timeout=check_timeout,candidate_timeout=candidate_timeout)
        configure_event_monitor(root,run_id=controller.run['run_id'])
        overview = controller.list_workspace()
        if overview['all_selected_sealed']:
            outcome = 'complete'
            return overview
        settings = model_settings()
        binary = codex_bin or settings['codex_bin']
        if hashlib.sha256(Path(binary).read_bytes()).hexdigest() != settings['codex_sha256']:
            raise ValueError('Codex binary differs from frozen model configuration')
        policy = {'mode':'file-planner-workspace-v1','selected_theories':controller.selected,
                  **controller.options,'reasoning_effort':settings['reasoning_effort'],'worker_count':1,
                  'assurance':'audited-tool-allowlist','exclusive_os_isolation':False}
        durable(root/'workspace-policy.json',policy,immutable=True)
        durable(directory/'policy.json',policy,immutable=True)
        # Copy only authentication into an ephemeral configuration home. Its path
        # and contents never enter the model prompt or the committed run artifacts.
        with tempfile.TemporaryDirectory(prefix='regen-workspace-auth-') as temp:
            home = Path(temp)
            auth_home = Path(codex_home or os.environ.get('CODEX_HOME',Path.home()/'.codex'))
            auth = auth_home/'auth.json'
            if auth.is_file():
                shutil.copyfile(auth,home/'auth.json')
                (home/'auth.json').chmod(0o600)
            instructions = INSTRUCTIONS.replace(
                'You are the continuing File Planner for ONE Isabelle theory. Plan and revise its\nlocal graph, manage helpers, generate proofs, and use Isabelle feedback to continue.\nReturn one JSON action per call. No shell, arbitrary files or external tools.',
                'Within each selected theory, use these proposal formats through proof_action.')
            prompt = WORKSPACE_INSTRUCTIONS+'\n'+instructions+'\nWORKSPACE:\n'+json.dumps(overview,ensure_ascii=False)
            emit_event('workspace_session_started',session_id=directory.name,model=controller.model,stage='model')
            with CodexWorkspaceSession(tools=workspace_tools(),codex_bin=binary,codex_home=home,
                                       allow_external_call=True) as session:
                provider = session.run(prompt,controller.handle,artifact_dir=directory,model=controller.model,
                    reasoning_effort=settings['reasoning_effort'],timeout=call_timeout)
            audit = audit_workspace_session(directory/'session-transcript.jsonl',require_terminal=True,require_real=True)
        final = controller.list_workspace()
        outcome = 'complete' if final['all_selected_sealed'] else 'partial'
        result = {'status':outcome,'workspace':final,'provider':provider,'session_audit':audit,
                  'session_artifact_dir':str(directory)}
        durable(directory/'result.json',result,immutable=True)
        emit_event('workspace_session_finished',status=outcome,session_id=directory.name)
        return result
    except BaseException as error:
        durable(directory/'failure.json',{'error_type':type(error).__name__,'diagnostic':str(error)},immutable=True)
        raise
    finally:
        try:
            if controller is not None:
                controller.close()
        finally:
            try:
                close_event_monitor('workspace-'+outcome)
            finally:
                release_run_mutation_lock(token)
