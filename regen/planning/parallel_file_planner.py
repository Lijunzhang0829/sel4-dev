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
from time import monotonic
from uuid import uuid4

from regen.execution.check_executor import CheckExecutor, NativeResources, CURRENT_RESOURCES
from regen.execution.check_tasks import TERMINAL, CheckCancelled, check_cancelled
from regen.execution.proof_commits import digest, durable
from regen.infrastructure.codex_runtime import CodexRuntimeError, runtime_verified_settings
from regen.infrastructure.progress_events import event_scope, emit_event, generation_exclusion, observe_events
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
Use explicit sketches groups, without legacy proof-DAG, route or scheduling JSON. File order determines
which earlier proven declarations are available; oops slots export no theorem.

Use proof_action(theory, proposal):
{"action":"edit_theory","base_sha256":"hash from read_theory","edits":[{"old":"unique exact text","new":"replacement"}]}
Or replace the complete file using source instead of edits. Edits are sequential. Saving
performs boundary/hygiene checks, not Isabelle validation. Failed proof drafts persist.
{"action":"check_theory","source_sha256":"current saved hash","reason":"what changed"}
Optional entry_ids selects several proof updates while retaining every accepted proof.
{"action":"check_sketch","source_sha256":"current saved hash","target_entry_ids":["parent ID"]}
Explicit sketches groups on edit_theory contain target_entry_id and helper_ids.
This diagnostic checks the consumer with precisely those helpers temporarily admitted;
conditional_closed never proves a helper or the parent and never publishes a fact.
This checks all supplied proof bodies in file order. A successful version is atomically
accepted only if it retains every previously accepted entry. A GREEN diagnostic draft
with removed proofs/helpers or oops replacing accepted bodies returns draft_checked;
it does not publish proofs or replace the accepted version. Restore the missing entries
(read_proofs retrieves accepted bodies), then check the complete draft. Unchanged accepted
prefixes are reused. Editing/reordering accepted bodies rechecks the affected suffix.
Read_theory returns the draft and last check feedback. You may edit a failed draft directly.
Pass if_source_sha256 to read_theory when you already hold that exact source; an unchanged
draft then returns source_unchanged=true without repeating its text. Omit the hash to
retrieve the complete source. Observation indexes are paginated; use the returned
read_context_index actions for older fact/query addresses, not repeated whole-file reads.
Historical reads default to summaries. Use list_attempts with an entry_id to navigate
failed history, then read_attempt or read_attempts with view="full" for exact evidence.
Do not scan request numbers blindly. read_theory restores notes, recent actions and
target navigation, not all selected historical draft bodies. A batch containing an
entry does not prove that entry failed; follow checker_failed_entry_ids when available.
{"action":"finish","source_sha256":"accepted current draft hash"}
Seal requires every fixed goal and retained helper proved, unchanged since successful check,
then complete-theory verification, semantic audit and build. A final message is not a seal.

Preserve header/imports, fixed contract identity markers, exact statements and contexts.
The REGEN-BEGIN metadata has only id, role (contract/helper/theorem), context (an available
context list). REGEN-PROOF separates declaration and proof; REGEN-END closes the entry.
A helper is an ordinary named lemma entry with a unique ID/name. Copy a block layout,
choose its statement and context, and place it before consumers. No intended-consumer list
is required in source markers. Optional helper_purposes notes may be attached to
edit_theory, outside the .thy source: {"helper_id":{"contract_id":"fixed goal ID",
"gap":"specific condition or proof step","expected_use":"where in the target proof",
"remaining_gap":"what remains; write none if nothing"}}. Update these notes when
the purpose changes or feedback reveals a remaining gap. To update notes alone,
submit the unchanged source with the current base_sha256. After trying the helper
in a non-oops target body, optionally add used_in_target with the exact use location.
This is a reported attempt, not native dependency evidence or target success.
read_theory returns these notes; accepted status is derived from committed proofs.
Missing notes are reminders, never a reason to stop proving. Use checked sketches to revisit
the target after useful helper progress; do not build a scheduling DAG.
Context commands must match the marker list and close before REGEN-END.
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


GENERATION_STRATEGIES = {
    'baseline': '',
    'sketch-groups-v1': '''\nENFORCED HELPER GROUPS: SKETCH GROUPS V1
When adding helpers, supply sketches=[{"target_entry_id":"parent ID",
"helper_ids":["h1","h2"]}] on edit_theory and include the executable parent
consumer proof in the same draft. A group may contain one or many helpers; its
complete interface must close the parent. Multiple parent groups may share one
edit. Call check_sketch with source_sha256 (optional target_entry_ids) before
proving a group. The harness rejects orphan or unchecked helpers regardless of
your explanation. Recursively add a checked group for a difficult helper.
Use check_theory with entry_ids to check several selected proof updates while
other failed or sketch-only bodies stay in the draft. After the helpers are
proved, check their parent using its actual proof. Conditional results are never
GREEN, never premises for helper proofs, and never replenish generation budget.
''',
    'bounded-audited-prefix-v1': '''\nONLINE CHECK POLICY: BOUNDED AUDITED PREFIX V1
For an append-only check_theory draft, the harness checks your new proof bodies
one declaration at a time in source order, independently auditing and committing
each GREEN declaration. It stops at the first rejected declaration or the first
accepted fixed contract. The full draft, including all untested proof bodies,
remains saved and readable. Inspect accepted_ids and bounded_prefix feedback;
an untested body is never a premise. Non-append rewrites use an explicitly marked
whole-document fallback. No proof bodies or new mathematical content are supplied
by this policy. Keep existing fixed-contract and legal-fact restrictions.
''',
    'helper-closure-v1': '''\nEXPERIMENTAL WORKFLOW: HELPER CLOSURE V1
Keep the original unproved fixed contract as the objective of each small helper batch.
Before expanding that batch, state which observed obligation of that contract it addresses.
After a helper batch is accepted, return to the original contract: inspect its current
obligations and check a revised contract proof before starting another helper batch.
If that check is not yet meaningful, state the exact remaining prerequisite and keep the
next batch focused on it. Record remaining obligations in your ordinary check reason or
analysis, not new formal declarations. Helpers do not renew the effective generation
budget. Do not invent numeric remaining time: only harness-reported measurements count.
This changes workflow only: preserve all contract statements, legal-fact restrictions,
accepted-proof boundaries and complete native proof validation.
''',
}


GENERATION_STRATEGIES['sketch-local-demands-v1'] = GENERATION_STRATEGIES['sketch-groups-v1'] + '''
LOCAL HELPER DEMANDS: inspect requirements before generalizing
Every pending group must include use_sites covering every helper. Each selector
has helper_id, consumer_line (1-based line of the parent's proof body), and fact
(the exact helper fact expression, e.g. h[where x=x]). Put its using/from/with/
apply/by command on its own line. check_sketch observes the native fact after
attributes, the pending goals and local assumptions at that position. Read the
full receipt's helper_uses before proving helpers. Schematic variables still in
the fact are unresolved; neither the selector nor its observation proves actual
dependency, minimality, truth or difficulty. Keep selectors current after edits.
Default to the instance, available hypotheses and conclusion required at the
consumer. Avoid dropping useful premises, broadening objects or adding stronger
conclusions merely for generality. If a general helper stalls, specialize it or
restore legal local premises, then edit the consumer and rerun check_sketch.
The consumer must prove all restored premises. Generalization remains allowed
when useful for induction or measured reuse. A conditional receipt does not
prove a complexity reduction. Briefly probe all leaves before committing the
main budget; repair a script/known-fact gap before creating more helpers.
'''

GENERATION_STRATEGIES['sketch-reviewed-v1'] = GENERATION_STRATEGIES['sketch-local-demands-v1'] + '''
ENFORCED DECOMPOSITION REVIEW
After check_sketch closes, use probe_helpers to briefly check each pending leaf
in its own legal GREEN prefix (10 candidate seconds; preparation is separate).
An oops leaf receives a basic simp probe. Probes never publish GREEN or assume
siblings. Missing dependencies remain diagnostic proof failures, not admissions.
Call review_sketch bottom-up, referencing the exact receipt and native use
indices. Review domain_scope, premises and conclusion as local/generalized;
generalization needs induction/reuse justification. Reject unchanged burden;
explain the isolated reasoning, or delegate to a reviewed child sketch. Every
pending leaf needs a native probe; its failure/timeout does not prove hardness.
Native failure IDs appear in read_theory.workflow. Use diagnose_failure before
revising related decompositions. For script/known_fact failures, change and
check the existing proof first. Unrelated parents may continue independently.
Reviews are accountable model judgments, not kernel difficulty certificates.
'''

GENERATION_STRATEGIES['sketch-guided-v1'] = GENERATION_STRATEGIES['sketch-local-demands-v1'].replace(
    'Briefly probe all leaves before committing the\nmain budget;',
    'Assess the current sketch before committing the main budget;') + '''
NATIVE STRUCTURAL SCREENING AND LOCAL REPAIR
After conditional closure call assess_sketch. It observes the parent and helpers
in legal native contexts without assuming siblings, ranks archived alternatives
by worst remaining obligation, and recommends at most two diagnostic probes.
Scores are untrained structural heuristics, not solvability probabilities.
review_sketch bottom-up remains required. A repeated parent, retained multi-operation
burden, iteration, or unknown profile needs a successful native probe or a reviewed
child decomposition. Merely labeling it isolated cannot override these signals.
Other leaves can proceed without mandatory probes. Do not expand definitions or
generalize just to manipulate a score; the final parent must still be proved.
For generalized demands add generalization_evidence: induction needs variable,
invariant, base_case, step_case; reuse needs consumers and shared_pattern.
These explanations are auditable judgments, not proofs of induction or reuse.
Use read_failure for the exact failed entry, saved proof, native diagnostic and
proof state. Diagnose script/known_fact errors and check a changed local proof
before revising related interfaces. Preserve accepted siblings and recheck every
changed consumer; no temporary admission or successful prefix publishes GREEN.
'''


GENERATION_STRATEGIES['sketch-phased-v1'] = GENERATION_STRATEGIES['sketch-guided-v1'] + '''
PHASED WORKFLOW (overrides probe-success-only rules above)
At most two short probes per leaf: initial attempt plus one changed-script repair.
Then stop probing. Use transition_proof with current source_sha256, failure_id,
decision and concrete plan. formal_leaf keeps the interface, requires a closed
consumer and prior probe, and permits risk-reviewed formal proof via check_theory
without first solving the leaf in a probe. This is not GREEN or a difficulty certificate.
Continue with the existing script and diagnostics; budgets never reset.
After an initial failed check and one failed local repair, a transition is required.
revise_consumer preserves the entry interface and requires a changed consumer proof;
revise_interface requires changing a helper declaration (contracts stay fixed);
decompose_helper requires a saved child group. Diagnose failures before interface
edits; recheck modified sketches and review bottom-up. Transitions bind the exact
current candidate; execute it before editing again. Formal proof work may continue
while making native progress; repeated residual obligations should be decomposed.
These actions gate entries, not arbitrary tactic checkpoints: do not claim a kernel
verified unchanged prefix when no native checkpoint evidence exists.
'''

def generation_strategy(value):
    if value == 'and-or-helper-selection-v1':
        raise ValueError('and-or-helper-selection-v1 was removed from the current document workflow; '
                         'use baseline for free helper/batch generation, or the original frozen '
                         'application to replay historical AND/OR runs')
    if value not in GENERATION_STRATEGIES:
        raise ValueError('unknown generation strategy')
    text = GENERATION_STRATEGIES[value]
    return {'name':value, 'instructions':text,
            'instructions_sha256':hashlib.sha256(text.encode()).hexdigest()}


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


def _continuation_prompt(theory, state):
    open_count = len(state['graph']['nodes']) - len(state['proofs'])
    return (f'Continue the same assigned theory {theory}. The previous model turn ended, '
            f'but checker authority still reports status={state["status"]!r} with '
            f'{open_count} open declarations. A final message is not completion. '
            'Use the current draft and latest feedback to keep editing and '
            'checking until proof_action finish seals the theory. '
            'Do not repeatedly reread unchanged state or wait for another agent to edit this theory; '
            'you own its generation. Repeated reads and sleep are not progress.')


def tools(document_mode=False):
    registry = workspace_tools()
    if document_mode:
        registry = [tool for tool in registry if tool['name'] != 'prepare_reuse']
        for tool in registry:
            if tool['name']=='read_theory':
                tool['description']='Read the persistent Isabelle draft, source hash, accepted entries and check feedback.'
                tool['inputSchema']['properties']['if_source_sha256'] = {'type':'string','minLength':1}
            elif tool['name']=='proof_action':
                tool['description']='Edit/check/seal the persistent theory source, or execute a read-only observation.'
        registry.append(_tool('proof_tool_help','Read shared document tool schemas, examples and result semantics without opening a theory or calling Isabelle.',{}))
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
    def __init__(self, run_dir, theory, *, resources, executor, document_mode=False,
                 generation_strategy_name='baseline', focus_contract_id=None,
                 stop_at_focus_contract=False, **options):
        if type(stop_at_focus_contract) is not bool:
            raise ValueError('stop_at_focus_contract must be boolean')
        if stop_at_focus_contract and focus_contract_id is None:
            raise ValueError('stop_at_focus_contract requires focus_contract_id')
        self.document_mode = document_mode
        self.generation_strategy = generation_strategy(generation_strategy_name)
        if generation_strategy_name in {'bounded-audited-prefix-v1', 'sketch-groups-v1', 'sketch-local-demands-v1', 'sketch-reviewed-v1', 'sketch-guided-v1', 'sketch-phased-v1'} and not document_mode:
            raise ValueError(generation_strategy_name + ' requires document mode')
        super().__init__(run_dir,theories=[theory],**options)
        self.theory, self.resources, self.executor = theory, resources, executor
        with executor.lock:
            self.task_ids = {key for key,row in executor.records.items()
                             if row['theory']==theory and row['binding'].get('run_id')==self.run['run_id']}
        self._runtime_lock = threading.RLock()
        self._action_lock = threading.RLock()
        self._tool_lock = threading.RLock()
        self._abort_requested = threading.Event()
        self._provider_preparation_error = None
        self.focus_contract_id = focus_contract_id
        self.stop_at_focus_contract = stop_at_focus_contract
        if focus_contract_id is not None:
            package, state = self._state(theory)
            if (not isinstance(focus_contract_id, str)
                    or focus_contract_id not in {c['contract_id'] for c in package['contracts']}
                    or (focus_contract_id in state['proofs']
                        and not stop_at_focus_contract)):
                raise ValueError('focus_contract_id must identify an unproved fixed contract of the assigned theory')

    def target_contract_green(self):
        if not self.stop_at_focus_contract:
            return False
        _, state = self._state(self.theory)
        return self.focus_contract_id in state.get('proofs', {})

    def _check_session_active(self):
        if self._abort_requested.is_set():
            raise CheckCancelled('File Planner session aborted; no new action may be dispatched')

    def abort_pending(self):
        """Cooperative session-only abort; never wait for the theory tool lock.

        The executor owns cancellation/acceptance linearization. An accepted
        commit is retained even when its tool response can no longer be sent.
        Inspect executor records, not just task_ids: submit may have published a
        task before its caller has registered the returned ID on this controller.
        """
        self._abort_requested.set()
        with self.executor.lock:
            pending = [key for key, row in self.executor.records.items()
                       if row['theory'] == self.theory
                       and row['binding'].get('run_id') == self.run['run_id']
                       and row['status'] not in TERMINAL]
        # Never hold executor.lock while taking a token's acceptance lock.
        _cleanup_all([lambda key=key: self.executor.cancel(key) for key in pending])

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
        from regen.execution.repository_execution import setup, ProviderPreparationError
        key = (str(self.root),self.theory,self.options['check_timeout'])
        with self._runtime_lock:
            if key not in self.runtime_cache:
                if self._provider_preparation_error is not None:
                    raise self._provider_preparation_error
                # Document reads initialize providers too, outside CheckExecutor.
                # Bind policy here, before checker construction, so every entry
                # path receives the same admission, thread and build budgets.
                marker = CURRENT_RESOURCES.set(self.resources)
                try:
                    with generation_exclusion('runtime_setup', theory=self.theory):
                        self.runtime_cache[key] = setup(self.root,self.theory,self.options['check_timeout'])
                except ProviderPreparationError as error:
                    # Repeated reads must not silently rebuild a failed provider.
                    # A fresh controller is the explicit retry boundary; neither
                    # cancellation nor candidate RED feedback is latched here.
                    self._provider_preparation_error = error
                    raise
                finally:
                    CURRENT_RESOURCES.reset(marker)
            self.runtime_cache[key][4].document_mode = self.document_mode
            self.runtime_cache[key][4].generation_strategy_name = self.generation_strategy['name']
            self.runtime_cache[key][4].bounded_prefix_cancel_check = self._check_session_active
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
        mathematical = name in {'proof_action', 'submit_check'}
        if self.stop_at_focus_contract and mathematical:
            if self.target_contract_green():
                raise ValueError(
                    'target contract is already journal-accepted; further '
                    'mathematical actions are blocked')
            if arguments.get('proposal', {}).get('action') == 'finish':
                raise ValueError(
                    'target-contract mode does not seal the whole theory')
        if name in {'get_check_result','wait_checks','cancel_check'}:
            return self._handle(name, arguments, metadata)
        with self._tool_lock:
            self._check_session_active()
            self._completed_action_state = None
            self._helper_attempt = None
            result = self._handle(name, arguments, metadata)
            if self.document_mode:
                from regen.planning.document_interface import public_view
                result = public_view(result)
            if self.target_contract_green():
                if not isinstance(result, dict):
                    result = {'result': result}
                result = {**result,
                    'target_contract_green': True,
                    'target_contract_id': self.focus_contract_id}
            return result

    def _document(self, *, state=None):
        from regen.planning.theory_document import DocumentStore
        if state is None:
            package,state = self._state(self.theory)
        else:
            package = self.shared._package(self.theory)
        runtime = self._runtime()
        from regen.planning.sketch_check import strategy_options
        return DocumentStore(runtime[2]/'document',package,state,runtime[4].imports(),
            **strategy_options(self.generation_strategy['name'])),state

    def _handle(self, name, arguments, metadata):
        if self.document_mode and name=='proof_tool_help':
            from regen.planning.document_interface import tool_contracts
            return tool_contracts(**arguments)
        if self.document_mode and name=='read_theory' and arguments.get('theory')==self.theory:
            store,state = self._document()
            from regen.planning.batch_file_planner import planner_input
            from regen.planning.document_recall import compact_history_feedback, document_observations
            context = planner_input(self.shared._package(self.theory),state)
            document = store.read()
            known_source = arguments.get('if_source_sha256')
            if known_source is not None and (not isinstance(known_source,str) or not known_source):
                raise ValueError('if_source_sha256 must be a nonempty source hash')
            if known_source == document.get('source_sha256'):
                document = {key:value for key,value in document.items() if key != 'source'}
                document['source_unchanged'] = True
            return {'theory':self.theory,'document':document,
                    'accepted_ids':list(state['accepted_order']),
                    'accepted_source_sha256':state.get('document_source_sha256'),
                    'status':state['status'],'feedback':compact_history_feedback(state.get('batch_feedback',{}),state),
                    'observations':document_observations(context,state)}
        if self.document_mode and name=='read_theory':
            if 'if_source_sha256' in arguments:
                raise ValueError('if_source_sha256 applies only to the assigned draft')
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
            if proposal.get('action') == 'select_helpers':
                raise ValueError('select_helpers was removed; edit any needed helpers and check '
                                 'one or more proof bodies directly with check_theory')
            if proposal.get('action') == 'read_sketch':
                from regen.planning.sketch_check import read_receipt
                if set(proposal)-{'action','receipt_id','view'}:
                    raise ValueError('read_sketch accepts receipt_id and view only')
                store, _ = self._document()
                return read_receipt(store,proposal.get('receipt_id'),proposal.get('view','summary'))
            if proposal.get('action') == 'read_failure':
                from regen.planning.sketch_assessment import read_failure
                if set(proposal) != {'action','failure_id'}:
                    raise ValueError('read_failure accepts failure_id only')
                store, _ = self._document()
                return read_failure(store, proposal['failure_id'])
            if proposal.get('action') == 'transition_proof':
                from regen.planning.sketch_phases import transition_proof
                from regen.infrastructure.workspace_transcript import record_workspace_action
                if set(proposal) != {'action','source_sha256','failure_id','decision','plan'}:
                    raise ValueError('invalid transition_proof fields')
                store,state = self._document()
                if state['status'] in {'sealed','retired'}: raise ValueError('terminal theory is immutable')
                record_workspace_action(store.root/'workflow'/'calls'/uuid4().hex/'call',
                    'transition_proof',proposal,metadata,theory=self.theory,model=self.model)
                return transition_proof(store,proposal['source_sha256'],proposal['failure_id'],proposal['decision'],proposal['plan'])
            if proposal.get('action') in {'review_sketch','diagnose_failure'}:
                from regen.planning.sketch_workflow import review_sketch, diagnose_failure
                from regen.infrastructure.workspace_transcript import record_workspace_action
                action = proposal['action']
                fields = ({'action','source_sha256','target_entry_id','receipt_id','reviews'}
                          if action == 'review_sketch' else {'action','failure_id','classification','plan'})
                if set(proposal) != fields:
                    raise ValueError('invalid ' + action + ' fields')
                store,state = self._document()
                if state['status'] in {'sealed','retired'}:
                    raise ValueError('terminal theory is immutable')
                record_workspace_action(store.root/'workflow'/'calls'/uuid4().hex/'call',
                    action,proposal,metadata,theory=self.theory,model=self.model)
                return (review_sketch(store,proposal['source_sha256'],proposal['target_entry_id'],
                        proposal['receipt_id'],proposal['reviews']) if action == 'review_sketch' else
                        diagnose_failure(store,proposal['failure_id'],proposal['classification'],proposal['plan']))
            if proposal.get('action') == 'read_context_index':
                from regen.planning.batch_file_planner import planner_input
                from regen.planning.document_recall import context_index_page
                package,state = self._state(self.theory)
                return context_index_page(planner_input(package,state),proposal)
            if proposal.get('action') not in {'edit_theory','check_theory','check_sketch','probe_helpers','assess_sketch','finish'}:
                from regen.planning.document_interface import internal_request
                proposal = internal_request(proposal)
                arguments = {**arguments, 'proposal':proposal}
            allowed = {'edit_theory','check_theory','check_sketch','probe_helpers','assess_sketch','finish','observe','query','read_facts',
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
            if arguments.get('assigned_only') is True:
                package,state = self._state(self.theory)
                ready = self._theory_readiness(self.theory,package=package,state=state)
                from regen.planning.import_contract import required_imports
                return {'run_id':self.run['run_id'],'assigned_theory':self.theory,
                    'status':state['status'],'contract_count':len(package['contracts']),
                    'accepted_entry_count':len(state['proofs']),
                    'required_imports':[p['theory'] for p in required_imports(package['repository_projection'])],
                    'ready_for_proof_action':ready['ready_for_proof_action'],
                    'unsealed_providers':ready['unsealed_providers'],
                    'assigned_writer_count':1,'theory_tool_execution':'serial',
                    'notice':'Read this theory for exact goals/draft and feedback. list_workspace '
                             'lists other theories on demand; list_generated/read_generated expose '
                             'reference-only material. Current source imports govern legal facts.'}
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
            kind = {'submit_batch':'proof_batch','check_theory':'proof_batch','check_sketch':'sketch','probe_helpers':'sketch','assess_sketch':'sketch',
                    'finish':'seal','resolve_symbols':'symbol'}.get(action,'observe')
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
            if row.get('error_type') == 'ImplementationChangedError':
                from regen.execution.check_tasks import ImplementationChangedError
                raise ImplementationChangedError(row.get('diagnostic', 'frozen execution code changed'))
            if row.get('error_type') == 'ProviderPreparationError':
                from regen.execution.repository_execution import ProviderPreparationError
                raise ProviderPreparationError(row.get('diagnostic', 'provider preparation failed'))
            if row.get('error_type') == 'CodexRuntimeError':
                # Executor receipts retain only the exception type and message.
                # Restore the infrastructure type before the session boundary:
                # an unavailable/untrusted runtime is not candidate feedback.
                raise CodexRuntimeError(row.get('diagnostic', 'trusted Codex runtime unavailable'))
            return self._public(row)

    def _submit_task(self, arguments, metadata):
        self._check_session_active()
        if arguments['theory'] != self.theory:
            raise ValueError('only the assigned theory may be checked or modified')
        kind, proposal = arguments['kind'], deepcopy(arguments['proposal'])
        helper_attempt = deepcopy(getattr(self, '_helper_attempt', None))
        expected = {'proof_batch':'submit_batch','seal':'finish','statement':'submit_batch','sketch':'check_sketch',
                    'symbol':'resolve_symbols','observe':'observe'}
        if kind not in expected or (proposal.get('action') != expected[kind]
                and not (kind=='proof_batch' and proposal.get('action')=='check_theory')
                and not (kind=='sketch' and proposal.get('action') in {'probe_helpers','assess_sketch'})):
            raise ValueError('check kind and proposal action disagree')
        if 'notes' in proposal and not isinstance(proposal['notes'],str):
            raise ValueError('notes must be a string')
        if kind == 'statement' and proposal.get('proofs'):
            raise ValueError('statement task must not contain proof bodies')
        if kind == 'seal' and self._pending():
            raise ValueError('wait for outstanding checks before seal changes build outputs')
        if any(row['kind']=='seal' for row in self._pending()):
            raise ValueError('seal is in flight; its frozen build inputs are exclusive')
        package, snapshot = self._state(self.theory)
        row = self._theory_readiness(self.theory, package=package, state=snapshot)
        if not row['ready_for_proof_action']:
            raise ValueError('theory is not ready for checks: '+json.dumps(row['unsealed_providers']))
        binding = {key:snapshot[key] for key in ('graph','proofs','accepted_order')}
        binding = {'state_sha256':digest(binding),'run_id':self.run['run_id'],
                   'package_sha256':package['package_payload_sha256']}
        metadata = deepcopy(metadata)
        task_id = digest({'thread':metadata['thread_id'],'turn':metadata['turn_id'],
                          'call':metadata['call_id'],'theory':self.theory})

        def execute(directory):
            marker = CURRENT_RESOURCES.set(self.resources)
            try:
                self._check_session_active()
                with event_scope(directory,run_id=self.run['run_id'],theory=self.theory,task_id=task_id):
                    emit_event('check_task_started',kind=kind)
                    runtime = self._runtime()
                    check_cancelled()
                    if kind in {'proof_batch','seal','sketch'}:
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
                            if kind == 'sketch':
                                from regen.planning.sketch_check import check_groups
                                from regen.infrastructure.workspace_transcript import record_workspace_action
                                probing = proposal['action'] == 'probe_helpers'
                                selectors = 'entry_ids' if probing else 'target_entry_ids'
                                if set(proposal)-{'action','source_sha256',selectors}:
                                    raise ValueError('check_sketch accepts source_sha256 and target_entry_ids only')
                                store, _ = self._document(state=now)
                                label = ('Native sketch structural assessment' if proposal['action']=='assess_sketch'
                                         else 'Native helper probes' if probing else 'Conditional sketch check')
                                record_workspace_action(directory/'call', label,
                                    proposal, metadata, theory=self.theory, model=self.model)
                                if proposal['action'] == 'assess_sketch':
                                    from regen.planning.sketch_assessment import assess_sketch
                                    result = assess_sketch(runtime[4],store,proposal.get('source_sha256'),proposal.get('target_entry_ids'))
                                elif probing:
                                    from regen.planning.sketch_workflow import probe_helpers
                                    result = probe_helpers(runtime[4],store,proposal.get('source_sha256'),proposal.get('entry_ids'))
                                else:
                                    result = check_groups(runtime[4], store, proposal.get('source_sha256'),
                                        proposal.get('target_entry_ids'))
                            else:
                                result = super(TheoryController,self).proof_action(
                                    {'theory':self.theory,'proposal':proposal},metadata,
                                    state_snapshot=now, helper_attempt=helper_attempt)
                    else:
                        result = self._observe(kind,proposal,snapshot,package,directory)
                    emit_event('check_task_result_ready',kind=kind)
                    return result
            finally:
                try:
                    emit_event('check_task_stopped', kind=kind, theory=self.theory,
                               task_id=task_id, run_id=self.run['run_id'])
                finally:
                    CURRENT_RESOURCES.reset(marker)

        self._check_session_active()
        result = self.executor.submit(kind='action' if kind=='observe' else kind,
            theory=self.theory,binding=binding,request={'proposal':proposal,'metadata':metadata},
            execute=execute,owner=self.theory,task_id=task_id)
        with self.executor.lock:
            self.task_ids.add(result['task_id'])
        # Cover an abort between the final pre-submit check and registration.
        # An already accepted task may decline cancellation; never roll it back.
        if self._abort_requested.is_set():
            result = self.executor.cancel(result['task_id'])
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
            isabelle_dir=original.isabelle_dir,import_all_previous=original.import_all_previous,
            complete_context_policy=getattr(original, 'complete_context_policy', 'isolated-v1'))
        checker.declaration_environment = digest(run)
        # Keep task-local outputs and caches, but route supported observations
        # through the original process under its action lock and native lease.
        # Closing this task-local checker must never close the borrowed owner.
        checker._observation_owner = original
        checker._observation_lock = self._action_lock
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
                       codex_home=None, cancel_event=None, document_mode=True,
                       generation_strategy_name='baseline', focus_contract_id=None,
                       stop_at_focus_contract=False, and_or_control=None, retry_context=None,
                       http_retry_mode=False):
    from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
    from regen.infrastructure.workspace_transcript import audit_workspace_session
    from regen.planning.batch_file_planner import INSTRUCTIONS as FORMATS
    strategy = generation_strategy(generation_strategy_name)
    controller_class = TheoryController
    controller_options = {}
    if and_or_control is not None:
        from regen.planning.and_or_file_planner_supervisor import \
            AndOrTheoryController
        controller_class = AndOrTheoryController
        controller_options = {
            'and_or_problem': and_or_control['problem'],
            'and_or_state': and_or_control['state'],
            'execution_identity': and_or_control['execution_identity'],
            'arm_attestation_path': and_or_control.get(
                'arm_attestation_path'),
            'supervisor_directory': and_or_control.get(
                'supervisor_directory'),
        }
    controller = controller_class(root,theory,resources=resources,executor=executor,document_mode=document_mode,
        generation_strategy_name=generation_strategy_name, focus_contract_id=focus_contract_id,
        stop_at_focus_contract=stop_at_focus_contract,
        model=model,call_timeout=call_timeout,check_timeout=check_timeout,
        candidate_timeout=candidate_timeout, **controller_options)
    try:
        with event_scope(directory,run_id=controller.run['run_id'],theory=theory):
            durable(directory/'policy.json',{'theory':theory,**controller.options,
                    'generation_strategy':strategy,
                    'focus_contract_id':focus_contract_id,
                    'stop_at_focus_contract':stop_at_focus_contract,
                    'http_retry_mode':http_retry_mode,
                    **resources.policy,'mode':'parallel-file-planner-v1','theory_tool_execution':'serial',
                    'progress_policy':{'repeated_result_limit':8,
                        'no_information_timeout_seconds':call_timeout,'callback_time_excluded':True,
                        'sleep_policy_version':2,'sleep_item_max_ms':60000,
                        'sleep_semantics':'protocol-display-only; not proof progress',
                        'deadline_authority':'no-information deadline and repeated-result limit; native/action deadlines govern callbacks'},
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
                workspace_view = controller.handle('list_workspace',{'assigned_only':True} if document_mode else {}, {})
                prompt = (DOCUMENT_INSTRUCTIONS if document_mode else INSTRUCTIONS)+strategy['instructions']+'\n'+formats+'\nASSIGNED THEORY: '+theory+'\n'+json.dumps(workspace_view,ensure_ascii=False)
                if retry_context is not None:
                    prompt += '\nRECOVERY RECEIPT (not mathematical advice):\n'+json.dumps(retry_context,ensure_ascii=False)
                if focus_contract_id is not None:
                    prompt += ('\nFOCUS FIXED CONTRACT ID: '+focus_contract_id+
                        '\nPrioritize this fixed contract and its supporting helpers. '
                        'All ordinary proof, legal-fact and checker-acceptance rules remain unchanged.')
                providers = []
                transport_anomaly = None
                target_completed = controller.target_contract_green()
                if not target_completed:
                    with CodexWorkspaceSession(tools=tools(document_mode),codex_bin=settings['codex_bin'],codex_home=home,
                                               allow_external_call=True,progress_timeout=call_timeout,
                                               http_retry_mode=http_retry_mode) as session:
                        next_prompt = prompt
                        while True:
                            try:
                                provider = session.run(next_prompt,controller.handle,artifact_dir=directory,model=controller.model,
                                                       reasoning_effort=settings['reasoning_effort'],timeout=call_timeout,
                                                       cancel_event=cancel_event,
                                                       on_abort=getattr(controller,'abort_pending',None),
                                                       stop_when=(
                                                           (lambda value: 'target_contract_green'
                                                            if isinstance(value,dict) and value.get('target_contract_green')
                                                            else None)
                                                           if stop_at_focus_contract else None))
                            except BaseException as error:
                                # The proof journal is the authority even if
                                # transport fails after the accepting callback.
                                # A global runtime-integrity fault must still
                                # stop peers even if this target just became GREEN.
                                if isinstance(error, CodexRuntimeError) or not controller.target_contract_green():
                                    raise
                                transport_anomaly = {
                                    'error_type':type(error).__name__,
                                    'diagnostic':str(error)}
                                provider = {
                                    'status':'target_green_post_acceptance_transport_anomaly',
                                    'post_acceptance_transport_anomaly':transport_anomaly}
                            providers.append(provider)
                            _, state = controller._state(theory)
                            target_completed = controller.target_contract_green()
                            if target_completed or state['status'] in {'sealed','retired'}:
                                break
                            # A model turn is not a proof or budget boundary.
                            # The persistent session keeps its independent
                            # inactivity/duplicate-result guards; the coordinator
                            # owns cancellation and effective-generation limits.
                            next_prompt = _continuation_prompt(theory,state)
                    if transport_anomaly is None:
                        audit = audit_workspace_session(
                            directory/'session-transcript.jsonl',
                            require_terminal=not target_completed,
                            require_real=True)
                    else:
                        audit = {
                            'terminal':False,
                            'post_acceptance_transport_anomaly':transport_anomaly}
                else:
                    audit = {
                        'terminal':False,
                        'provider_not_started':'target_already_journal_green'}
            # A model ending a turn never upgrades pending work to success.
            _, state = controller._state(theory)
            target_completed = controller.target_contract_green()
            status = ('target_green_completed'
                      if target_completed else state['status'])
            provider = (providers[-1] if providers else
                        {'status':'not_started_target_already_green'})
            result = {'theory':theory,'status':status,
                      'theory_status':state['status'],'provider':provider,
                      'provider_turns':providers,'continuation_turns':len(providers)-1,'audit':audit,
                      'task_ids':sorted(controller.task_ids),'artifact_dir':str(directory)}
            if target_completed:
                snapshot = controller._runtime()[3].replay_snapshot()
                result['target_authority'] = {
                    'focus_contract_id':focus_contract_id,
                    'journal_commit_id':snapshot['commit_id'],
                    'journal_sequence':len(snapshot['records']),
                    'proof_body_sha256':digest(
                        state['proofs'][focus_contract_id]),
                }
            if transport_anomaly is not None:
                result['post_acceptance_transport_anomaly'] = \
                    transport_anomaly
            durable(directory/'result.json',result,immutable=True)
            return result
    except CodexRuntimeError as error:
        # Cover launcher/final-audit faults outside session.run as well. Stop
        # charging before controller.close can wait for native worker cleanup.
        # The session already reports callback failures at their first boundary.
        if not getattr(error, '_regen_runtime_failure_emitted', False):
            try:
                emit_event('generation_runtime_integrity_failure', theory=theory,
                    error_type=type(error).__name__, reason=str(error))
                error._regen_runtime_failure_emitted = True
            except BaseException as timing_error:
                error.add_note('runtime integrity timing close failed: '+str(timing_error))
        raise
    finally:
        _cleanup_all([controller.close], primary=sys.exc_info()[1])


def _execution_policy(value, selected):
    value = {} if value is None else deepcopy(value)
    if not isinstance(value, dict) or set(value) - {'stages', 'session_stall_retries', 'provider_http_retries'}:
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
    out = {'stages':stages, 'session_stall_retries':retries}
    if 'provider_http_retries' in value:
        http_retries = value['provider_http_retries']
        if type(http_retries) is not int or http_retries not in (0, 1):
            raise ValueError('provider_http_retries must be 0 or 1')
        out['provider_http_retries'] = http_retries
    return out


def _stage_eligible(rows, policy, active=()):
    states = {row['theory']:row['status'] for row in rows}
    for stage in policy['stages']:
        if any(states.get(theory) not in {'sealed','retired'} or theory in active for theory in stage):
            return set(stage)
    return {row['theory'] for row in rows if row['selected']}


def _retry_stalled(error, attempt, retries):
    from regen.infrastructure.codex_workspace_session import SessionStalled, ProviderOverloaded
    # A cleanup failure may leave a live owner; never overlap it with a retry.
    return (isinstance(error, (SessionStalled, ProviderOverloaded)) and attempt <= retries
            and not getattr(error, '__notes__', ()))


def _run_budgeted_theory_session(bridge, *args, **kwargs):
    with bridge.worker(args[1]):
        return run_theory_session(*args, **kwargs)


def _pause_budget_for_exit(budget, error=None):
    if budget is not None and not budget.finished:
        reason = ('operator_pause' if isinstance(error, (KeyboardInterrupt, SystemExit))
                  else 'infrastructure_error' if error is not None else 'runner_paused')
        # Settle while the gate is still open. Setting event directly first loses
        # the known generation interval since the last monotonic observation.
        budget.cancel(budget.reason or reason)


def _attach_budget_pause(error, budget, active=()):
    if budget is None or budget.finished or not budget.event.is_set():
        return
    error.regen_pause_result = {
        'status':'paused', 'disposition':'pause',
        'reason':budget.reason or 'runner_paused',
        'error_type':type(error).__name__, 'diagnostic':str(error),
        'generation_budget':budget.snapshot(), 'active_theories':list(active),
        'repository_verified':False, 'resume_requires_authorization':True}


def run_parallel_workspace(run_dir, *, theories=None, max_parallel_theories=2, native_slots=1,
                           native_threads=4, resident_limit=2, model=None, call_timeout=600,
                           check_timeout=900, candidate_timeout=120, allow_external_call=False,
                           codex_home=None, integrate=True, file_planner_interface='document',
                           execution_policy=None, native_cpus=4, native_memory_mb=16384,
                           native_pids_limit=1024, provider_build_timeout=1800,
                           build_ml_heap_mb=None, generation_strategy_name='sketch-reviewed-v1', focus_contract_id=None,
                           effective_seconds=172800, resume_authorization=None,
                           extend_effective_seconds=None):
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    from regen.infrastructure.audited_llm import model_settings
    if not allow_external_call:
        raise PermissionError('parallel workspace requires --allow-external-call')
    if type(max_parallel_theories) is not int or max_parallel_theories < 1:
        raise ValueError('max_parallel_theories must be positive')
    if file_planner_interface not in {'document','graph'}:
        raise ValueError('file_planner_interface must be document or graph')
    strategy = generation_strategy(generation_strategy_name)
    root = Path(run_dir).resolve()
    # This is the sole coordinator/legacy-runner exclusion lock. Worker actions do
    # not acquire it; their mutations are theory-owned and short journal transactions.
    resources = NativeResources(native_slots,native_threads,resident_limit,
        cpus=native_cpus,memory_mb=native_memory_mb,pids_limit=native_pids_limit,
        provider_build_timeout=provider_build_timeout,build_ml_heap_mb=build_ml_heap_mb)
    lock = acquire_run_mutation_lock(root,operation='parallel-workspace')
    executor = None
    budget = bridge = None
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
        # Publish before controller/context recovery can emit native events.
        # This is a schema marker, not the start of effective generation.
        emit_event('parallel_generation_clock_started', clock_schema='theory_effective_v1',
                   run_dir=str(root))
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
        settings = runtime_verified_settings(model_settings())
        workspace_policy = {'mode':'parallel-file-planner-v1',
            'generation_strategy':strategy,
            'focus_contract_id':focus_contract_id,
            'selected_theories':selected,'max_parallel_theories':max_parallel_theories,
            'theory_tool_execution':'serial','file_planner_interface':file_planner_interface,
            **resources.policy,**overview.options,'integration_required':integrate,
            'execution_policy':policy}
        from regen.infrastructure.parallel_generation_budget import initialize_parallel_budget
        rows = overview.list_workspace()['theories']
        budget, bridge = initialize_parallel_budget(root, overview, workspace_policy,
            seconds=effective_seconds, resume_authorization=resume_authorization,
            extension_seconds=extend_effective_seconds, rows=rows)
        budget.ledger.append('parallel_budget_invocation',
            service_invocation_id=os.environ.get('INVOCATION_ID'), pid=os.getpid())
        durable(root/'parallel-workspace-policy.json', workspace_policy, immutable=True)
        executor = CheckExecutor(root/'check-tasks',workers=max_parallel_theories)
        results, active, failures = [], {}, {}
        attempts, directories = {}, {}
        stall_attempts = {}
        from regen.infrastructure.provider_http_failure import recovery_streak, recovery_decision
        retry_not_before = {}
        cancel_event = budget.event
        budget.start(abort=cancel_event.set)
        sessions = root/'parallel-sessions'/uuid4().hex
        with event_scope(sessions,run_id=overview.run['run_id']), \
                observe_events(bridge.handle, publication_guard=bridge.acceptance):
            with ThreadPoolExecutor(max_workers=max_parallel_theories,thread_name_prefix='file-planner') as agents:
                try:
                    refresh = False
                    while True:
                        budget.check()
                        if refresh:
                            rows = overview.list_workspace()['theories']
                        eligible = _stage_eligible(rows, policy, active.values())
                        for row in rows:
                            if len(active)>=max_parallel_theories:
                                break
                            theory = row['theory']
                            if (theory not in eligible or theory in failures or theory in active.values()
                                    or not row['ready_for_proof_action']
                                    or retry_not_before.get(theory, 0) > monotonic()):
                                continue
                            retry_not_before.pop(theory, None)
                            attempt = attempts.get(theory, 0) + 1
                            attempts[theory] = attempt
                            directory = sessions/theory
                            if attempt > 1:
                                directory = directory/f'retry-{attempt-1:03d}'
                            directories[theory] = directory
                            durable(directory/'attempt.json', {'theory':theory,'attempt':attempt,
                                'call_timeout':call_timeout}, immutable=True)
                            context = copy_context()
                            from regen.planning.document_recall import retry_summary
                            future = agents.submit(context.run,_run_budgeted_theory_session,bridge,root,theory,
                                resources=resources,executor=executor,settings=settings,directory=directory,
                                generation_strategy_name=generation_strategy_name,
                                focus_contract_id=focus_contract_id,
                                retry_context=retry_summary(results,theory),
                                http_retry_mode=bool(policy.get('provider_http_retries', 0)),
                                codex_home=codex_home,cancel_event=cancel_event,document_mode=file_planner_interface=='document',**overview.options)
                            active[future] = theory
                        if not active:
                            deferred = [retry_not_before[row['theory']] for row in rows
                                if row['theory'] in retry_not_before and row['theory'] in eligible
                                and row['theory'] not in failures and row['ready_for_proof_action']]
                            if deferred:
                                # A provider cooldown belongs to its theory, not
                                # to the coordinator. Only wait when no other
                                # ready work can fill a slot, and keep budget /
                                # cancellation checks responsive while idle.
                                cancel_event.wait(min(1, max(0, min(deferred) - monotonic())))
                                # No live writers can change these rows; avoid
                                # replaying the workspace every idle second.
                                refresh = False
                                continue
                            break
                        done,_ = wait(active,timeout=1,return_when=FIRST_COMPLETED)
                        budget.check()
                        # Full slots need no new scheduling decision. With a
                        # free slot, a still-active writer may have sealed a
                        # provider before returning from its session; continue
                        # polling then so its hard dependants can start.
                        refresh = bool(done) or len(active) < max_parallel_theories
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
                                from regen.infrastructure.codex_workspace_session import ProviderHTTPError
                                result = {'theory':theory,'status':'infrastructure_error',
                                          'error_type':type(error).__name__,'diagnostic':str(error)}
                                http_error = isinstance(error, ProviderHTTPError)
                                if not http_error:
                                    stall_attempts[theory] = stall_attempts.get(theory, 0) + 1
                                decision = (recovery_decision(error.failure,
                                    recovery_streak(budget.ledger.rows, theory, error.failure),
                                    enabled=bool(policy.get('provider_http_retries', 0)),
                                    cleanup_failed=bool(getattr(error, '__notes__', ()))) if http_error else {})
                                retry = (decision['retry'] if http_error else
                                         _retry_stalled(error, stall_attempts[theory], policy['session_stall_retries']))
                                result.update(attempt=attempts[theory], retry_scheduled=retry)
                                durable(directories[theory]/'failure.json',result,immutable=True)
                                results.append(result)
                                from regen.execution.check_tasks import ImplementationChangedError
                                if isinstance(error, ImplementationChangedError):
                                    raise  # Global frozen-code drift invalidates every worker.
                                if isinstance(error, CodexRuntimeError):
                                    # Session events normally close the clock at
                                    # detection. This also covers bootstrap/audit
                                    # failures outside session.run; never consume
                                    # HTTP allowance or keep healthy peers running
                                    # against the same broken trust boundary.
                                    budget.cancel('runtime_integrity_failure')
                                    raise
                                if retry:
                                    if http_error:
                                        # This point is reached only after session/controller close.
                                        # Reserve durably before scheduling a new provider request.
                                        used = decision['consecutive_failures']
                                        delay = decision['delay_seconds']
                                        budget.ledger.append('provider_http_retry_scheduled', theory=theory,
                                            retry_number=used, failure=error.failure,
                                            consecutive_failures=used, retry_policy=decision['retry_policy'],
                                            prior_attempt_directory=str(directories[theory]),
                                            delay_seconds=delay, accepted_state_preserved=True)
                                        retry_not_before[theory] = monotonic() + delay
                                        emit_event('provider_http_retry_scheduled', theory=theory,
                                            retry_number=used, delay_seconds=delay,
                                            next_attempt=attempts[theory]+1)
                                        continue
                                    from regen.infrastructure.codex_workspace_session import ProviderOverloaded
                                    if isinstance(error, ProviderOverloaded):
                                        delay = min(120, 30 * 2 ** min(stall_attempts[theory] - 1, 2))
                                        retry_not_before[theory] = monotonic() + delay
                                        emit_event('provider_overload_backoff', theory=theory,
                                                   delay_seconds=delay, next_attempt=attempts[theory]+1)
                                    emit_event('session_stall_retry_scheduled',theory=theory,
                                               next_attempt=attempts[theory]+1)
                                    continue
                                if http_error:
                                    reason = decision['reason']
                                    budget.ledger.append('provider_http_retry_stopped', theory=theory,
                                        reason=reason, retries_used=recovery_streak(budget.ledger.rows, theory),
                                        failure=error.failure)
                                    budget.cancel(reason)
                                    raise
                                failures[theory] = {'reason':'infrastructure_error',
                                                    'error_type':type(error).__name__,
                                                    'diagnostic':str(error)}
                        # Refill immediately; never wait for an entire wave of theories.
                except BaseException as error:
                    _cleanup_all([lambda: _pause_budget_for_exit(budget, error)], primary=error)
                    # Persistence failure also closes the budget gate. Preserve
                    # the original exception while still stopping every worker.
                    cancel_event.set()
                    budget_paused = budget.event.is_set()
                    _attach_budget_pause(error, budget, active.values())
                    with executor.lock:
                        executor.closed = True
                        pending = list(executor.records)
                    # Cancel before diagnostics: ENOSPC must never leave a
                    # callback running while the agent pool waits to join it.
                    _cleanup_all([
                        *(lambda task=task: executor.cancel(task) for task in pending),
                        lambda: emit_event('parallel_workspace_paused' if budget_paused else 'parallel_workspace_interrupted',
                            exception_type=type(error).__name__,reason=str(error)),
                        lambda: durable(sessions/'interruption.json',{'status':'paused' if budget_paused else 'interrupted',
                            'error_type':type(error).__name__,'diagnostic':str(error),
                            'active_theories':list(active.values()),
                            'generation_budget':budget.snapshot()}),
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
                      'session_attempts':attempts,'execution_policy':policy,
                      'generation_budget':budget.snapshot()}
            all_sealed = all(row['status']=='sealed' or (row['status']=='retired' and not row['contracts'])
                             for row in final['theories'])
            if integrate and all_sealed:
                from regen.execution.repository_integration import integrate_repository
                marker = CURRENT_RESOURCES.set(resources)
                try:
                    with bridge.integration():
                        result['integration'] = integrate_repository(root,timeout=check_timeout)
                finally:
                    CURRENT_RESOURCES.reset(marker)
                result['repository_verified'] = result['integration']['repository_verified']
                result['status'] = 'repository_verified' if result['repository_verified'] else 'integration_failed'
            elif all(row['status']=='sealed' for row in final['theories'] if row['selected']):
                result['status'] = 'selected_theories_sealed'
            if result['repository_verified']:
                budget.finish('repository_verified')
                result['disposition'] = 'complete'
            else:
                budget.cancel(result['status'])
                result['disposition'] = 'pause'
                result['resume_requires_authorization'] = True
            result['generation_budget'] = budget.snapshot()
            durable(sessions/'result.json',result,immutable=True)
            return result
    finally:
        primary = sys.exc_info()[1]
        try:
            _cleanup_all([lambda: _pause_budget_for_exit(budget, primary)]
                         + ([executor.close] if executor is not None else []) + [resources.close]
                         + ([bridge.close] if bridge is not None else [])
                         + ([budget.close] if budget is not None else []),
                         primary=primary)
            if primary is not None:
                _attach_budget_pause(primary, budget)
        except BaseException as cleanup_error:
            _attach_budget_pause(cleanup_error, budget)
            raise
        finally:
            _cleanup_all([
                *(lambda signum=signum, handler=handler: signal.signal(signum, handler)
                  for signum, handler in old_signals.items()),
                # A failed join may leave callbacks able to mutate this run.
                # Keep ownership until they stop or the service exits.
                *([lambda: release_run_mutation_lock(lock)]
                  if executor is None or executor.shutdown_complete else []),
            ], primary=sys.exc_info()[1])
