"""Single autonomous Codex, from repository planning to verified integration.

This is a method adapter only. Planning, documents, checks and proof authority
remain the common A harness. No supervisor-generated proof or target advice.
"""
from copy import deepcopy
from pathlib import Path
import argparse,hashlib,json,os,shutil,signal,tempfile,threading,time,uuid
from regen.execution.proof_commits import durable,digest
from regen.infrastructure.experiment_ledger import Ledger,Stage
from regen.execution.check_tasks import CheckCancelled
from regen.execution.check_executor import CURRENT_RESOURCES,NativeResources,CheckExecutor
from regen.planning.proof_workspace_agent import _tool
from regen.planning.parallel_file_planner import TheoryController,tools as document_tools
from regen.planning.workspace_harness import WorkspaceHarness
from regen.planning.workspace_preflight import native_checker
from regen.planning.repository_plan_contract import contracts as planning_contracts, request_schema, proposal_schema, validate_request
from regen.planning.document_interface import observation_instructions,tool_contracts

PROMPT_TEMPLATE = """Prove all fixed contracts in this repository using the supplied Isabelle harness.
You are one Codex agent responsible for theory planning, proof generation and revision.

Before generating proofs, inspect the fixed contracts and submit a complete, valid
contract-to-theory allocation. Assign each contract exactly once; preserve its exact
statement and context. Theory imports must be acyclic. You choose the number of
theories, their grouping, proof order, helper lemmas, batch sizes, proof methods,
retrieval, check frequency and how to use feedback. The original theory attribution
is reference metadata and does not prescribe your grouping.

Use the registered tools and their schemas. repository_plan exposes planning,
validation and preflight operations; proof_action exposes document editing,
checking, observations and finish/seal. Saving a draft is not proof acceptance.
One check can include multiple contracts and helpers. Unproved entries are never
premises. If all active entries of a theory are GREEN, attempt its seal before
further proof generation. A failed seal returns feedback. Once every theory is
sealed, request repository integration; only verified integration establishes
complete success. Once the initial plan is accepted, its theories, contract allocation
and imports are frozen. Do not split, merge, move contracts or replan. Explore and
revise proofs and helper lemmas only within each existing theory.

Only the frozen legal parent context and this experiment's generated material are
available. Imported facts require the appropriate sealed theory context. Other
unsealed generated proofs are references, not automatically available premises.
Do not use removed proofs, other experiments, extra axioms, sorry or raw ML.

There is no token, total wall-time, iteration or per-target attempt cap. The run
stops on verified completion, an external infrastructure blocker, operator stop,
or when any theory accumulates {inactivity_seconds} seconds of effective generation
without a fixed contract becoming verified GREEN for the first time. This pauses
the entire experiment and retains its results and costs. Each theory has its own
clock; switching theories or retrying a provider never resets that clock. Draft
changes, helper success and repeated validation do not reset it. Normal reasoning,
retrieval, editing, helper work and candidate checks (including proof failures and
candidate timeouts) count. Harness-recorded planning, initialization, native context
preparation, seal, integration and confirmed provider recovery are excluded.
Unexplained waiting remains counted. All tokens and total runtime remain charged.
Native-operation and transport timeouts are separate infrastructure limits.

Treat failures according to their evidence, not merely the elapsed time:
- A check_theory result with a concrete failed proof obligation, including a
  candidate-execution timeout, is proof feedback. Inspect its diagnostic and
  source hash; you may revise the proof, helper, or order within this theory.
- A native worker/prefix/provider preparation failure, audit failure, resource
  exhaustion, or incomplete checker result is an infrastructure blocker, not a
  failed contract. Do not rewrite a proof solely to work around it or retry the
  same check unchanged. Preserve the draft and report the exact tool status and
  evidence. The harness pauses or repairs infrastructure outside your proof work.
- Authentication or model-quota exhaustion is not a proof failure. Do not change
  model, credentials or the accepted plan, and do not repeatedly request the
  same work. The supervising harness handles provider connection retries and
  pauses when recovery is unsafe or exhausted. A long-running native tool call
  is not by itself evidence that the model connection failed.
- After a provider recovery, read experiment_status and the current theory
  before continuing. Accepted proofs, helper results, seals, clocks and reported
  token use persist. Check the saved tool result before acting on an interrupted
  request; do not assume its write failed or replay it blindly.
experiment_status reports observed model tokens, not remaining account quota.
Cached input is part of input tokens and reasoning output is part of output;
do not add those subsets again or infer a bill from the token count. There is no
agent-enforced token cap. Use retrieval and diagnostics as needed for the proof.

Use work-scope labels for intended contract/helper work where possible. Labels do
not establish dependencies; shared or unattributed work is valid and charged once.
The effective clock initially follows your accepted plan's first unfinished theory,
then the last selected unfinished theory. A theory-specific tool or a single-theory
work scope selects it; phase labels and reading sealed theories do not exclude time.
Inspect experiment_status.generation_clock for the current attribution. This
accounting default does not constrain your proof order.
Tool errors are not successful actions; inspect returned status/evidence and
apply the failure categories above.

Use proof_tool_help for shared proof/read schemas and examples, and repository_plan action=schema for the planning contract. Retrieval uses request.values, a list of strings.
"""

def instructions(inactivity_seconds=3600, *, effective_seconds=None, wall_seconds=None):
    prompt = PROMPT_TEMPLATE.replace('{inactivity_seconds}', str(inactivity_seconds))
    if effective_seconds is not None and wall_seconds is not None:
        raise ValueError('select one experiment time basis')
    if effective_seconds is not None or wall_seconds is not None:
        start = prompt.index('There is no token, total wall-time, iteration')
        end = prompt.index('Native-operation and transport timeouts', start)
        if wall_seconds is not None:
            replacement = (
                f'This experiment has a cumulative active wall-clock budget of {wall_seconds} '
                'seconds across the initial run and authorized resumptions. Planning, model '
                'work, native preparation, checking, seal and integration all count. Only '
                'confirmed provider interruption/recovery and offline operator pauses are '
                'excluded. An unobserved crash tail is unknown and requires reconciliation. '
                'There is no token, per-target attempt or per-theory inactivity cap. '
                'New contracts, helpers and provider retries never reset the wall budget. '
                'All model token usage and total elapsed time remain recorded. A budget '
                'extension requires explicit external authorization and retains prior costs.\n'
            )
        else:
            replacement = (
            f'This experiment has a cumulative effective-generation budget of {effective_seconds} '
            'seconds, shared across all theories, provider threads and authorized resumptions. '
            'There is no token, iteration, per-target attempt or no-GREEN inactivity cap. '
            'Reaching the cumulative time limit pauses the experiment recoverably; it is not '
            'proof failure or completion. Switching theories, new GREEN contracts, helpers '
            'and provider retries never reset or extend the budget. Normal reasoning, '
            'retrieval, editing, helper work and candidate checks (including proof failures '
            'and candidate timeouts) count. Only trusted harness planning, initialization, '
            'native context preparation, seal, integration and confirmed recovery phases '
            'close the clock. Unexplained waiting remains counted. Model phase labels '
            'cannot stop the clock. All token usage and total runtime remain recorded. '
            'A budget extension requires explicit external authorization and retains the '
            'original cutoff and all prior costs.\n'
            )
        prompt = prompt[:start] + replacement + prompt[end:]
    return prompt

INSTRUCTIONS = instructions()

def registry():
    tools=deepcopy(document_tools(True))
    proof_action=next(tool for tool in tools if tool['name']=='proof_action')
    source_actions=tool_contracts()['source_action_schemas']
    observation={'type':'object','properties':{
        'action':{'enum':['observe','query','read_facts','read_proofs','read_attempts',
                          'read_queries','read_diagnostic','read_proof_state','resolve_symbols',
                          'read_context_index']}},
        'required':['action'],'additionalProperties':True}
    proof_action['inputSchema']['properties']['proposal']={
        'description':('Source-changing actions use the exact edit_theory/check_theory/finish schemas. '
                       'The enumerated observation actions remain read-only and receive their detailed '
                       'runtime contracts from proof_tool_help.'),
        'oneOf':[deepcopy(source_actions[action]) for action in ('edit_theory','check_theory','finish')]
                +[observation]}
    return tools+[
        _tool('repository_plan','Inspect fixed contracts/parents or validate, preflight and submit one immutable initial plan. action=schema returns operation contracts and proposal format. Retrieval uses values (a list of strings). Inspect valid/errors: a returned result is not necessarily accepted.',{'request':request_schema(allow_revision=False)},('request',)),
        _tool('experiment_status','Read deadline, accounting, plan and previous revision identities; optional notes are retained in this run; launch wrapper is fresh-only.',{'notes':{'type':'string'}}),
        _tool('set_work_scope','Label intended subsequent work; shared labels do not duplicate token costs.',{'target_ids':{'type':'array','items':{'type':'string'}},'theory_ids':{'type':'array','items':{'type':'string'}},'phase':{'enum':['planning','proof','helper','retrieval','seal','integration','analysis']}},('target_ids','theory_ids','phase')),
        _tool('integrate_repository','Build and certify the entire currently sealed repository.',{})]

def read(path):return json.loads(Path(path).read_text())

def node_rows(record):
    state=record['state'];g=state['graph'];out=[]
    from regen.execution.shared_proof_workspace import _actual_dependencies
    dependencies=_actual_dependencies(record.get('evidence',{}),g)
    uids={n['id']:n['id'] if n['role']=='contract' else 'helper:'+g['theory']+':'+n['id'] for n in g['nodes']}
    for n in g['nodes']:
        uid=n['id'] if n['role']=='contract' else 'helper:'+g['theory']+':'+n['id']
        status='sealed' if state['status']=='sealed' else 'green' if n['id'] in state['proofs'] else 'open'
        out.append({'uid':uid,'entry_id':n['id'],'kind':n['role'],'name':n['name'],'theory':g['theory'],
                    'status':status,'checked_dependencies':[uids[k] for k in dependencies.get(n['id'],[]) if k in uids],
                    'dependency_status':'native-audit' if n['id'] in dependencies else 'not-observed','declaration_sha256':digest({k:n.get(k) for k in ('statement','context')})})
    return out

def check_accounting(result, theory, contracts):
    """Batch membership and observed verdicts, never inferred from work-scope labels."""
    feedback = result.get('feedback', {})
    rows = feedback.get('entry_results', feedback.get('node_results', []))
    verdicts = {}
    for row in rows:
        key = row.get('entry_id', row.get('node_id'))
        if not isinstance(key, str):
            raise ValueError('native result has no entry identity')
        uid = key if key in contracts else 'helper:'+theory+':'+key
        verdicts[uid] = row['status']
    return {'target_ids':sorted(verdicts), 'node_verdicts':verdicts,
            'outcome':feedback.get('status', 'unknown'),
            'cached_failure':bool(feedback.get('cached_failure')),
            'cost_seconds':deepcopy(feedback.get('cost_seconds')),
            'cost_basis':'controller mutually exclusive wall intervals; native subphase times may be unavailable',
            'task_id':result.get('task_id'),
            'check_timing':{k:feedback.get('check',{}).get(k) for k in
                            ('prefix_prepare_ms','wall_ms','execution_wall_ms','timeout_scope')},
            'attribution':'native batch membership; not independent per-target execution',
            'coverage':'observed' if rows else 'unavailable-not-zero-work'}

class Controller:
    def __init__(self,root,dataset,ledger,stage,config):
        self.root=Path(root);self.dataset=Path(dataset);self.ledger=ledger;self.stage=stage;self.config=config
        self.contracts=read(self.dataset/'repository-planning-input.json')['contracts']
        self.resources=NativeResources(1,config['threads'],config.get('resident_limit',2),
            cpus=config.get('native_cpus',4),memory_mb=config.get('native_memory_mb',16384),
            pids_limit=config.get('native_pids_limit',1024),
            provider_build_timeout=config.get('provider_build_timeout',1800))
        self.executor=CheckExecutor(self.root/'native-tasks',workers=1);self.controllers={}
        self.current_path=self.root/'current-plan.json';self.current=read(self.current_path) if self.current_path.exists() else None
        self.planning_path=self.root/'planning-draft.json'
        self.draft=read(self.planning_path) if self.planning_path.exists() else {'epoch':1,'reason':'initial plan'}
        self.planning_root=self.root/'plans'/f"{self.draft['epoch']:04d}"
        self.planner=self._planner();self.scope={'target_ids':[],'theory_ids':[],'phase':'planning' if not self.current else 'analysis','basis':'unattributed'}
        self.observed_commits={r['data']['commit_id'] for r in ledger.rows if r['kind']=='proof_commit'}
        self.observed_drafts={}
        self.observed_commit_paths=set()
        self.observed_draft_nodes={}
        self.register_activity_plan()
    def register_activity_plan(self):
        if not self.current or not hasattr(self.stage,'register'):return
        # Only accepted ledger evidence seeds inherited proof identities. Never
        # refresh an idle allowance by reconstructing a controller after retry.
        baseline={n['uid'] for r in self.ledger.rows
            if r['kind']=='proof_commit' and r['data']['epoch']==self.current['epoch']
            for n in r['data']['nodes'] if n.get('kind')=='contract' and n.get('status') in ('green','sealed')}
        self.stage.register(self.current['allocation'],baseline_green=baseline)
        if hasattr(self.stage,'mark_sealed'):
            for r in self.ledger.rows:
                if r['kind']=='proof_commit' and r['data']['epoch']==self.current['epoch'] and r['data']['event']=='seal':
                    self.stage.mark_sealed(r['data']['theory'])
        states=self.stage.snapshot()['theories']
        if self.stage.active_theory is None or states[self.stage.active_theory]['sealed']:
            prior=[]
            for row in self.ledger.rows:
                data=row['data']
                if row['kind']=='theory_activity_state':
                    selected=data['state'].get('active_theory')
                elif (row['kind']=='time_budget_checkpoint' and
                      data.get('boundary')=='worker_selected' and data.get('worker_id')=='default'):
                    selected=data.get('theory')
                else:
                    continue
                if selected:prior.append(selected)
            planned=[r['theory'] for r in read(Path(self.current['run'])/'execution-input.json')['files']]
            selected=next((t for t in prior[-1:]+planned if t in states and not states[t]['sealed']),None)
            if selected is not None:
                self.stage.select(selected)
                self.ledger.append('generation_attribution_default',theory=selected,
                    basis='restored selection or accepted plan execution order; agent may select another theory')
    def select_activity_theory(self,theory):
        if not hasattr(self.stage,'select'):return
        states=self.stage.snapshot()['theories']
        if theory not in states:raise ValueError('unknown theory scope')
        if not states[theory]['sealed']:self.stage.select(theory)
    def _planner(self):
        return WorkspaceHarness(self.dataset/'repository-planning-input.json',self.planning_root,
            native_checker(self.dataset,self.config.get('preflight_seconds',900),threads=self.config['threads']),visibility='source-theory-visible-v1')
    def committed(self,record,*,recovered=False,evidence_path=None):
        if self.current is None:raise ValueError('proof commit before accepted repository plan')
        self.ledger.append('proof_commit',epoch=self.current['epoch'],theory=record['state']['graph']['theory'],
            commit_id=record['commit_id'],event=record['event'],nodes=node_rows(record),recovered=recovered,
            evidence_sha256=digest(record['evidence']),journal_artifact=str(evidence_path) if evidence_path else None)
        if hasattr(self.stage,'observe'):
            for row in node_rows(record):
                self.stage.observe(row['uid'],row,kind='validation',theory=row['theory'],
                    baseline=record['event'] not in ('green','batch','document','seal'))
            if record['state']['status']=='sealed' and hasattr(self.stage,'mark_sealed'):
                self.stage.mark_sealed(record['state']['graph']['theory'])
                self.register_activity_plan()
    def observe_updates(self):
        if not self.current:return
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        from regen.infrastructure.incremental_log import read_artifact
        workspace=SharedProofWorkspace(Path(self.current['run']))
        for theory in self.current['allocation']:
            snap=workspace._snapshot(theory)
            if snap is None:continue
            for path in sorted((Path(snap['directory'])/'acceptance'/'commits').glob('*.json')):
                if path in self.observed_commit_paths:continue
                record=read_artifact(path)
                if record['commit_id'] not in self.observed_commits:
                    self.committed(record,evidence_path=path)
                    self.observed_commits.add(record['commit_id'])
                self.observed_commit_paths.add(path)
                if record['commit_id']==snap['commit_id']:break
        for theory,c in self.controllers.items():
            store,_=c._document()
            draft=store.read()
            if self.observed_drafts.get(theory)==draft['source_sha256']:continue
            from regen.planning.theory_document import parse
            entries,proofs=parse(draft['source'],store.package,store.imports)
            current_ids={e['id'] if e['role']=='contract' else 'helper:'+theory+':'+e['id'] for e in entries}
            previous_ids=getattr(self,'observed_draft_nodes',{}).get(theory,set())
            for uid in previous_ids-current_ids:
                if hasattr(self.stage,'observe'):self.stage.observe(uid,{'removed':True},kind='draft',theory=theory)
            if not hasattr(self,'observed_draft_nodes'):self.observed_draft_nodes={}
            self.observed_draft_nodes[theory]=current_ids
            for entry in entries:
                uid=entry['id'] if entry['role']=='contract' else 'helper:'+theory+':'+entry['id']
                value={'statement':entry['statement'],'context':entry['context'],'proof':proofs.get(entry['id'])}
                if hasattr(self.stage,'observe'):
                    self.stage.observe(uid,value,kind='draft',theory=theory,
                        baseline=entry['role']=='contract' and entry['id'] not in proofs)
            self.observed_drafts[theory]=draft['source_sha256']
    def controller(self,theory=None):
        if not self.current:raise ValueError('submit a complete validated theory plan before proof generation')
        run=Path(self.current['run'])
        if theory is None:theory=read(run/'execution-input.json')['files'][0]['theory']
        if theory not in self.controllers:
            self.controllers[theory]=TheoryController(run,theory,resources=self.resources,executor=self.executor,
                document_mode=True,model=self.config['model'],call_timeout=self.config['transport_idle_timeout'],
                check_timeout=self.config['check_timeout'],candidate_timeout=self.config['candidate_timeout'])
        return self.controllers[theory]
    def _publish(self,metadata):
        from regen.planning.plan_revisions import start_plan,append_revision
        from regen.planning.repository_planner import _bind_direct_artifacts
        from regen.execution.repository_execution import init_run
        proposal,bundle=self.planner.artifacts();epoch=self.draft['epoch'];out=self.planning_root
        identity=start_plan(out/'planning',self.planner.view.payload['benchmark']['id'])
        current=append_revision(out/'planning',{**bundle['repository-plan.json'],'plan_id':identity['plan_id']})
        artifacts=_bind_direct_artifacts(bundle,current,out)
        for name,value in artifacts.items():
            if name!='repository-plan.json':durable(out/name,value,immutable=True)
        run=self.root/'proof-runs'/f'{epoch:04d}'
        with self.stage.lock:
            self.stage.check()
            from regen.infrastructure.incremental_log import FORMAT
            init_run(run,self.dataset,self.config['run_id']+f'--plan-{epoch}',out/'file-generation-list.json',
                complete_context_policy='arch-reuse-v1',log_storage=FORMAT,
                execution_models={'File Planner':self.config['model']})
            allocation={t['name']:list(t['contract_ids']) for t in current['theories']}
            info={'epoch':epoch,'run':str(run),'plan_id':current['plan_id'],'allocation':allocation}
            previous=[r for r in self.ledger.rows if r['kind']=='plan_accepted' and r['data']['epoch']==epoch]
            if not previous:self.ledger.append('plan_accepted',**info,reason=self.draft['reason'],provenance=metadata)
            for c in self.controllers.values():c.close()
            self.controllers={};durable(self.current_path,info);self.current=info
            self.scope={'target_ids':[],'theory_ids':[],'phase':'analysis','basis':'unattributed'}
            self.register_activity_plan()
        return {'accepted':True,**info,'notice':'All previous revision proofs are reference-only until rechecked here.'}
    def plan(self,request,metadata):
        action=request.get('action')
        if action=='begin_revision':
            raise ValueError('generic repository plan accepts one initial plan only and is locked after acceptance')
        validate_request(request,allow_revision=False)
        if getattr(self,'current',None) and action in ('validate','preflight','submit'):
            raise ValueError('repository plan is locked after acceptance; only proof/helper exploration within existing theories is allowed')
        if action=='schema':
            from regen.planning.repository_plan_contract import schema_help
            return {'tool_contract':planning_contracts(allow_revision=False), 'proposal_schema':proposal_schema(),
                    'schema_help':schema_help(allow_revision=False),
                    'contract_aliases':self.planner.view.to_original,'context_profiles':self.planner.view.payload['context_profiles']}
        if action=='read_prior':
            epoch=request['epoch'];theory=request['theory']
            if type(epoch) is not int or epoch<1 or not self.current or epoch>=self.current['epoch']:raise ValueError('only an earlier epoch of this experiment is readable')
            prior=next((r['data'] for r in self.ledger.rows if r['kind']=='plan_accepted' and r['data']['epoch']==epoch),None)
            if prior is None or theory not in prior['allocation']:
                raise ValueError('request.epoch/theory must identify a prior accepted plan allocation; read experiment_status.prior_plans')
            from regen.execution.shared_proof_workspace import SharedProofWorkspace
            from regen.planning.document_interface import public_view
            reference=self.root/'proof-runs'/f'{epoch:04d}'
            if not reference.exists() and (self.root/'continuation-origin.json').exists():
                reference=Path(read(self.root/'continuation-origin.json')['prior_proof_runs'][str(epoch)])
            ws=SharedProofWorkspace(reference);snap=ws._snapshot(theory)
            return {'reference_only':True,'state':None if snap is None else public_view(snap['state'])}
        self.planner.active_call=metadata.get('call_id')
        result=self.planner.dispatch(request)
        if action=='submit' and result.get('submitted'):
            try:
                return self._publish(metadata)
            except Exception as error:
                # A valid submitted plan has already been persisted. The model
                # cannot repair a publication/configuration failure by changing
                # the proposal; continuing would create an expensive submit loop.
                self.infrastructure_blocker={'error_type':type(error).__name__,
                    'message':str(error),'tool':'repository_plan','phase':'publish'}
                self.ledger.append('infrastructure_blocked',**self.infrastructure_blocker)
                self.stage.cancel('repository_plan_publication_error')
                raise
        return result
    def _visible_event(self,row):
        path=self.root/'continuation-origin.json'
        ranges=read(path).get('hidden_event_ranges',[]) if path.exists() else []
        return not any(lo<=row['sequence']<=hi for lo,hi in ranges)
    def status(self):
        from regen.infrastructure.experiment_activity import observed_usage
        usage=observed_usage(self.ledger)
        deadline=self.stage.deadline
        return {'run_id':self.config['run_id'],'model':self.config['model'],'effort':self.config['effort'],
            'deadline_unix':deadline,
            'remaining_seconds':None if deadline is None else max(0,deadline-time.time()),
            'generation_clock':self.stage.snapshot() if hasattr(self.stage,'snapshot') else None,
            'plan_locked':self.current is not None,'plan':self.current,'draft_plan':self.draft,'reported_tokens':usage['total_tokens'],
            'token_accounting':usage,
            'fresh_only':not (self.root/'continuation-origin.json').exists(),
            'prior_plans':[{'epoch':r['data']['epoch'],'allocation':r['data']['allocation']}
                           for r in self.ledger.rows if r['kind']=='plan_accepted'
                           and self.current and r['data']['epoch']<self.current['epoch']],
            'scope':self.scope,'notes':[r['data']['notes'] for r in self.ledger.rows if r['kind']=='notes' and self._visible_event(r)][-20:]}
    def handle(self,name,args,metadata):
        started=time.monotonic()
        try:
            result=self._handle_recorded(name,args,metadata)
        except Exception as error:
            self.ledger.append('tool_outcome',tool=name,call_id=metadata.get('call_id'),
                thread_id=metadata.get('thread_id'),turn_id=metadata.get('turn_id'),
                outcome='exception',error_type=type(error).__name__,message=str(error),
                elapsed_seconds=time.monotonic()-started)
            raise
        self.ledger.append('tool_outcome',tool=name,call_id=metadata.get('call_id'),
            thread_id=metadata.get('thread_id'),turn_id=metadata.get('turn_id'),
            outcome='returned',result_sha256=digest(result),
            result_status=result.get('status') if isinstance(result,dict) else None,
            valid=result.get('valid') if isinstance(result,dict) else None,
            accepted=result.get('accepted') if isinstance(result,dict) else None,
            elapsed_seconds=time.monotonic()-started)
        if name=='proof_action' and args.get('proposal',{}).get('action')=='finish':
            self.ledger.append('seal_result',theory=args.get('theory'),call_id=metadata.get('call_id'),
                task_id=result.get('task_id'),result_status=result.get('status'),
                feedback_status=result.get('feedback',{}).get('status'),result_sha256=digest(result),
                authority='consult proof_commit seal evidence; tool return alone is not seal acceptance')
        return result
    def _handle_recorded(self,name,args,metadata):
        self.stage.check();start=time.monotonic();ids=list(self.scope['target_ids'])
        self.ledger.append('tool_started',tool=name,arguments=args,metadata=metadata,scope=self.scope)
        try:
            if name=='repository_plan':return self.plan(args['request'],metadata)
            if name=='proof_tool_help':
                from regen.planning.document_interface import tool_contracts
                return tool_contracts(**args)
            if name=='experiment_status':
                if args.get('notes'):self.ledger.append('notes',notes=args['notes'])
                return self.status()
            if name=='set_work_scope':
                allowed={c['contract_id'] for c in self.contracts};aliases=self.planner.view.to_original
                ids=[aliases.get(k,k) for k in args['target_ids']]
                for row in self.ledger.rows:
                    if row['kind']=='proof_commit' and self.current and row['data']['epoch']==self.current['epoch']:
                        allowed.update(n['uid'] for n in row['data']['nodes'])
                theories=set(self.current['allocation']) if self.current else set()
                def known_scope(key):
                    if key in allowed:return True
                    parts=key.split(':',2)
                    return len(parts)==3 and parts[0]=='helper' and parts[1] in theories and bool(parts[2])
                if any(not known_scope(k) for k in ids):raise ValueError('unknown target scope')
                if not set(args['theory_ids'])<=theories:raise ValueError('unknown theory scope')
                self.scope={**args,'target_ids':sorted(set(ids)),'basis':'agent-declared-intended-work','confidence':'declared-not-causal'}
                if len(args['theory_ids'])==1 and hasattr(self.stage,'select'):
                    self.select_activity_theory(args['theory_ids'][0])
                self.ledger.append('scope',**self.scope);return self.scope
            if name=='integrate_repository':
                if not self.current:raise ValueError('no accepted plan')
                from regen.execution.repository_integration import integrate_repository
                for c in self.controllers.values():c.close()
                self.controllers={}
                from contextlib import nullcontext
                with self.stage.excluded('integration') if hasattr(self.stage,'excluded') else nullcontext():
                    result=integrate_repository(Path(self.current['run']),timeout=self.config['check_timeout'])
                self.ledger.append('integrated',verified=result['repository_verified'],evidence=result.get('artifact_dir'),epoch=self.current['epoch'])
                return result
            theory=args.get('theory')
            if theory and hasattr(self.stage,'select'):
                self.select_activity_theory(theory)
            c=self.controller(theory)
            if name=='list_workspace':
                from regen.planning.proof_workspace_agent import WorkspaceController
                selected=c.selected
                try:
                    c.selected=list(self.current['allocation'])
                    return {**WorkspaceController.list_workspace(c),'native_concurrency':1,'agent_count':1}
                finally:c.selected=selected
            if name=='proof_action':
                due=self.seal_due()
                if due and args['proposal'].get('action') not in ('finish','observe','query','read_facts','read_diagnostic','read_attempts','read_proofs','read_queries','read_proof_state','resolve_symbols'):
                    raise ValueError('Complete GREEN drafts must attempt seal first: '+', '.join(due))
            from contextlib import nullcontext
            sealing=name=='proof_action' and args['proposal'].get('action')=='finish'
            with self.stage.excluded('seal') if sealing and hasattr(self.stage,'excluded') else nullcontext():
                result=c.handle(name,args,metadata)
            if name=='proof_action' and args['proposal'].get('action')=='finish' and result.get('task_id'):
                self.ledger.append('seal_attempt',epoch=self.current['epoch'],theory=theory,source_sha256=args['proposal'].get('source_sha256'))
            if name=='proof_action' and args['proposal'].get('action')=='check_theory':
                self.ledger.append('check_completed',epoch=self.current['epoch'],theory=theory,
                    **check_accounting(result, theory, {c['contract_id'] for c in self.contracts}),
                    declared_target_ids=ids,elapsed_seconds=time.monotonic()-start,result_sha256=digest(result))
            return result
        except Exception as error:
            from regen.execution.check_tasks import NativePreparationError
            if isinstance(error, NativePreparationError) or (isinstance(error, ValueError) and str(error) == 'invalid tool input lacks its exact nonexecution rejection'):
                self.infrastructure_blocker = {'error_type':type(error).__name__, 'message':str(error), 'tool':name}
                self.ledger.append('infrastructure_blocked', **self.infrastructure_blocker)
            raise
        finally:
            self.ledger.append('tool_finished',tool=name,call_id=metadata.get('call_id'),elapsed_seconds=time.monotonic()-start)
            if not self.stage.event.is_set():self.observe_updates()
    def seal_due(self):
        due=[]
        if not self.current:return due
        from regen.execution.shared_proof_workspace import SharedProofWorkspace
        workspace=SharedProofWorkspace(Path(self.current['run']))
        for theory in self.current['allocation']:
            snapshot=workspace._snapshot(theory)
            if snapshot is None:continue
            s=snapshot['state'];sha=s.get('document_source_sha256')
            if s['status'] in ('sealed','retired') or not sha or len(s['proofs'])!=len(s['graph']['nodes']):continue
            if not any(r['kind']=='seal_attempt' and r['data']=={'epoch':self.current['epoch'],'theory':theory,'source_sha256':sha} for r in self.ledger.rows):due.append(theory)
        return due
    def close(self):
        from contextlib import ExitStack
        with ExitStack() as cleanup:
            cleanup.callback(self.resources.close)
            cleanup.callback(self.executor.close)
            for c in self.controllers.values():cleanup.callback(c.close)
            cleanup.callback(self.planner.native_check.abort)
