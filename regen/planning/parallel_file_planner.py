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
from regen.execution.checker_policy import exports_available, theory_finished
from regen.infrastructure.codex_runtime import CodexRuntimeError, runtime_verified_settings
from regen.infrastructure.progress_events import event_scope, emit_event, generation_exclusion, observe_events
from regen.infrastructure.generation_monitor import GenerationMonitor
from regen.planning.proof_workspace_agent import WorkspaceController, workspace_tools, _tool
from regen.planning.planning_advice import ADVICE_INSTRUCTIONS, planning_advice
from regen.planning.theory_outcomes import (
    contract_dispositions_enabled, current_dispositions, pending_contract_ids,
)

INSTRUCTIONS = '''You are the continuing File Planner for the ONE assigned theory.
Own its proof graph and work normally until a verified finish/seal. Freely explore
contracts, helpers and coherent proof batches inside this theory. Ordinary proof
failure is feedback. Fixed contracts are not assumed true or provable: preserve them
and report unresolved work or submit a checked refutation when appropriate.
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
Before ending, seal the theory or use proof_action report_unresolved/check_refutation.
A final message alone is neither a seal nor a checked refutation. An unresolved outcome
preserves the accepted prefix and releases this worker for other dependency-ready theories.
'''


DOCUMENT_INSTRUCTIONS = '''You are the continuing File Planner for ONE Isabelle theory, using a persistent source workspace.
Read the assigned theory with read_theory. Its document.source is the editable .thy draft.
Implement the fixed goals by editing this file: batch several proofs, add/remove/reorder
helper declarations, check the current version, then revise using Isabelle feedback.
Use explicit sketches groups, without legacy proof-DAG, route or scheduling JSON. File order determines
which earlier proven declarations are available; oops slots export no theorem.

Use proof_action(theory, proposal):
{"action":"read_entry","entry_id":"stable entry ID"}
Read one entry's proof_body and expected_hash without rereading the complete draft.
Its retrieval_memory restores explicitly target-bound queries and complete recalled
candidates, with read_evidence navigation for older items. Global unbound queries
are kept separate. Retrieved or previously read facts are not proof authority.
For proof-only changes prefer:
{"action":"edit_proof","entry_id":"stable entry ID","proof_body":"complete replacement proof","expected_hash":"hash from read_entry or current source_sha256"}
expected_hash binds the whole current document. The harness preserves the entry's
declaration, context and markers. Read the current entry again after a stale-hash
rejection. Saving a proof body is not acceptance; check the returned source hash.
For helper declarations, ordering or other structural changes use:
{"action":"edit_theory","base_sha256":"hash from read_theory","edits":[{"old":"unique exact text","new":"replacement"}]}
Or replace the complete file using source instead of edits. Edits are sequential. Saving
performs boundary/hygiene checks, not Isabelle validation. Failed proof drafts persist.
Edit rejections report details with the failing zero-based edit_index, match_count
and source positions where available. Correct the indicated edit against the current
entry instead of repeating an ambiguous replacement or changing fixed declarations.
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
(read_evidence with proof refs retrieves accepted bodies), then check the complete draft. Unchanged accepted
prefixes are reused. Editing/reordering accepted bodies rechecks the affected suffix.
Read_theory returns the draft and last check feedback. You may edit a failed draft directly.
Pass if_source_sha256 to read_theory when you already hold that exact source; an unchanged
draft then returns source_unchanged=true without repeating its text. Omit the hash to
retrieve the complete source. Observation indexes are paginated; use the returned
read_evidence index actions for older fact/query addresses, not repeated whole-file reads.
Historical reads default to summaries. Use list_attempts with an entry_id to navigate
failed history, then read_attempt or read_attempts with view="full" for exact evidence.
Do not scan request numbers blindly. read_theory restores notes, recent actions and
target navigation, not all selected historical draft bodies. A batch containing an
entry does not prove that entry failed; follow checker_failed_entry_ids when available.
{"action":"finish","source_sha256":"current complete draft hash"}
Finish checks any pending complete draft, then runs complete-theory verification,
semantic audit and build. Every fixed goal and retained helper must pass before seal.
A final message is not a seal.

Preserve the theory name, fixed contract identity markers, exact statements and contexts.
To change imports, use edit_theory with base_sha256 and imports (a complete ordered
list), instead of source/edits. Draft edits accumulate without rebuilding Isabelle.
At the next native check the harness applies the final list, checks provider versions
and fixed target meanings, and revalidates proofs in that context. Use the base import
or certified generated provider imports. read_theory exposes the base import and
the provider directory; list_workspace and reads of another theory expose exact
import names and whether certified exports exist. Never import the current theory.
Old proof bodies are retained, but old-context GREEN is not current proof authority.
finish can check a complete pending draft before the full seal. Published theories
are immutable. An accepted native refutation also pins its certified imports;
refutation recertification in another environment is not supported. Plain reads
do not apply pending imports.
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
Ordinary proof failure should lead to editing or retrieval. A fixed spec may also be false
or unresolved under the current search. Use report_unresolved for a precise unresolved
issue, or check_refutation for an independently checked negative result. Neither seals
the theory or makes its unfinished contracts available as premises.
'''


REVIEW_WAIT_INSTRUCTIONS = '''
read_theory includes planning_review obligations. Before finish or report_unresolved,
record_review_decision for each pending assigned issue: issue_id, decision (import,
local, retain), reason, entry_ids and concrete remaining obligations. Import decisions
require accepted imports. A local decision allocates proof work; it does not establish it.
If selected generated providers are not yet exported, use proof_action with
{"action":"wait_for_providers","providers":["provider theory"],"entry_ids":["target ID"],
"reason":"exact interface needed","review_issue_ids":["matching issue ID"]}.
This releases your worker and resumes after all named providers publish certified
exports. It preserves proofs and costs and adds no imports. Do not report_unresolved
merely to wait for an unsealed provider. Waiting edges must remain acyclic. On resume,
check provider premises, select imports or a local alternative, and update review decisions.
'''
INSTRUCTIONS += '\n' + ADVICE_INSTRUCTIONS + REVIEW_WAIT_INSTRUCTIONS
DOCUMENT_INSTRUCTIONS += '\n' + ADVICE_INSTRUCTIONS + REVIEW_WAIT_INSTRUCTIONS

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
Use proof_obstacles in check feedback and read_theory.workflow to compare the
last repair with its failure. same_observation means repeated rendered feedback
in the same context, not semantic equivalence; changed_observation does not
certify progress. unknown covers timeouts, infrastructure and changed context.
Read linked read_failure receipts when needed. Explain which failed command or
residual obligation the next edit addresses and how it differs from prior attempts.
If the same observation survives edited candidates, reassess the rule instantiation,
missing invariant or helper interface rather than merely renaming tactics. Continue
hard proof work while useful; these observations impose no repair-round limit.
In decomposition reviews describe both the removed obligations and the remaining
hard mechanism (e.g. an invariant or address relation). An isolated obligation or
a short probe timeout is not evidence that a leaf is easy. Preserve successful
siblings; extract independently useful stable subproofs as checked helpers when
appropriate, without treating any uncommitted script prefix as GREEN.
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
    if exports_available(result.get('status')) and result.get('provider', {}).get('status') == 'completed':
        from regen.infrastructure.resource_budget import reclaim_theory_heaps
        result['heap_reclamation'] = reclaim_theory_heaps(root, result['theory'])


def _contract_view(controller, theory=None):
    if not contract_dispositions_enabled(controller.run):
        return {}
    theory = theory or controller.theory
    dispositions = current_dispositions(controller, theory)
    pending = pending_contract_ids(controller, theory)
    return {'contract_outcome_policy':controller.run['contract_outcome_policy'],
            'unresolved_evidence_policy':controller.run.get('unresolved_evidence_policy'),
            'contract_dispositions':{key:{'status':value['status'], 'reason':value['reason'],
                'classification':value['classification'], 'receipt_id':value['receipt']['event_id'],
                'available_as_positive_fact':False} for key,value in dispositions.items()},
            'pending_contract_ids':pending, 'all_contracts_disposed_or_proved':not pending}


def _progress_marker(controller, state):
    marker = (digest(state.get('proofs', {})), getattr(controller, '_proof_work_version', 0))
    if contract_dispositions_enabled(controller.run):
        marker += (digest({key:value['receipt']['event_id'] for key,value in
                           current_dispositions(controller).items()}),)
    return marker


def _continuation_prompt(theory, state, *, contract_view=None):
    if contract_view is not None:
        return (f'Continue the assigned theory {theory}. Contract dispositions stop only their '
                'own goals and supply no positive facts. Leave these goals as oops, preserve '
                'their fixed declarations, and attempt another pending contract. If no pending '
                'contracts remain, finish to build and publish only checked positive facts; '
                'do not retry disposed contracts or claim the entire theory was proved. '
                'Existing budgets and accepted proofs are unchanged. Current disposition view:\n'
                +json.dumps(contract_view, ensure_ascii=False))
    open_count = len(state['graph']['nodes']) - len(state['proofs'])
    return (f'Continue the same assigned theory {theory}. The previous model turn ended, '
            f'but checker authority still reports status={state["status"]!r} with '
            f'{open_count} open declarations. A final message is not completion. '
            'Use the current draft and latest feedback to keep editing and '
            'checking, or use proof_action report_unresolved/check_refutation when the fixed '
            'goals cannot presently be completed. A model claim alone does not establish falsity. '
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
        self.framework_blocked = False
        self.theory_outcome = None
        self.provider_wait = None
        self._proof_work_version = 0
        self._proof_work_seen = set()
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
            history_rows = [self.executor.get(key) for key in self.task_ids
                    if self.executor.records[key]['status']=='completed'
                    and self.executor.records[key]['kind'] in {'action','symbol'}]
        rows = [row for row in history_rows if row['task_id'] not in applied
            and row.get('result',{}).get('context_revision',
            row.get('binding',{}).get('context_revision',0)) == state.get('context_revision',0)]
        return merge(state,rows,self.executor.root,history_rows=history_rows)

    def _state(self, theory):
        package,state = super()._state(theory)
        if theory == self.theory:
            state = self._observation_state(state)
        return package,state

    def _runtime(self):
        from regen.execution.repository_execution import setup, ProviderPreparationError
        key = (str(self.root),self.theory,self.options['check_timeout'])
        with self._runtime_lock:
            if key in self.runtime_cache:
                cached = self.runtime_cache[key]
                current = cached[3].replay()
                if current and 'context_imports' in current and current['context_imports'] != cached[5]:
                    self.runtime_cache.pop(key)
                    cached[4].close_prefix()
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

    def _guard_disposed_write(self, proposal):
        """Keep stopped contracts unavailable without rewriting audited requests."""
        if not contract_dispositions_enabled(self.run) or not isinstance(proposal, dict):
            return
        action = proposal.get('action')
        if action not in {'edit_proof','edit_theory','submit_batch','check_theory',
                          'check_sketch','assess_sketch','probe_helpers','review_sketch'}:
            return
        disposed = set(current_dispositions(self))
        if not disposed:
            return
        selected = set()
        if action == 'edit_proof':
            # Clearing an obsolete failed body is harmless. A disposed slot may
            # remain oops, but must never acquire another candidate proof.
            if str(proposal.get('proof_body', '')).strip() != 'oops':
                selected.add(proposal.get('entry_id'))
        if action == 'submit_batch' and isinstance(proposal.get('proofs'), dict):
            selected.update(proposal['proofs'])
        for field in ('entry_ids', 'target_entry_ids'):
            values = proposal.get(field)
            if isinstance(values, list):
                selected.update(value for value in values if isinstance(value, str))
        if action == 'review_sketch':
            selected.add(proposal.get('target_entry_id'))
        if selected & disposed:
            raise ValueError('disposed contracts cannot receive positive proofs or checks: '
                             +', '.join(sorted(selected & disposed)))
        if action == 'edit_theory':
            from regen.planning.theory_document import parse, source_imports
            store, _ = self._document(); current = store.read()
            source = proposal.get('source', current['source'])
            if 'edits' in proposal:
                edits = proposal['edits']
                if not isinstance(edits, list):
                    return  # The ordinary edit validator supplies its exact error.
                for edit in edits:
                    if (not isinstance(edit, dict) or not isinstance(edit.get('old'), str)
                            or not edit['old'] or not isinstance(edit.get('new'), str)
                            or source.count(edit['old']) != 1):
                        return
                    source = source.replace(edit['old'], edit['new'], 1)
            if not isinstance(source, str):
                return
            _, before = parse(current['source'], store.package, store.imports)
            _, after = parse(source, store.package, source_imports(source,store.package['theory']))
            changed = [key for key in disposed if key in after and after[key] != before.get(key)]
            if changed:
                raise ValueError('disposed contract proof slots must stay oops: '+', '.join(sorted(changed)))

    def _framework_block_view(self):
        return {'framework_blocked':True, **getattr(self, '_framework_failure',
                                                   {'failure_scope':'trusted_audit_target'})}

    def handle(self, name, arguments, metadata):
        # Receipt inspection/cancellation may be used by an external supervisor.
        # All mathematical/read tools share one theory-local serial boundary.
        mathematical = name in {'proof_action', 'submit_check'}
        if mathematical and getattr(self, 'provider_wait', None):
            return {'theory_wait':True, 'provider_wait':self.provider_wait,
                    'notice':'This worker is waiting for provider publication; accepted work is retained.'}
        if mathematical and getattr(self, 'theory_outcome', None):
            return {'theory_stop':True, 'theory_outcome':self.theory_outcome,
                    'notice':'This theory session has stopped; accepted proofs remain unchanged.'}
        if mathematical and getattr(self, 'framework_blocked', False):
            return {**self._framework_block_view(),
                    'notice':'Preserve accepted proofs; this worker requires infrastructure repair.'}
        if self.stop_at_focus_contract and mathematical:
            if self.target_contract_green():
                raise ValueError(
                    'target contract is already journal-accepted; further '
                    'mathematical actions are blocked')
            if arguments.get('proposal', {}).get('action') == 'finish':
                raise ValueError(
                    'target-contract mode does not seal the whole theory')
        if name in {'get_check_result','wait_checks','cancel_check'}:
            result = self._handle(name, arguments, metadata)
            return {**result, **(self._framework_block_view() if getattr(self, 'framework_blocked', False) else {})}
        with self._tool_lock:
            self._check_session_active()
            if mathematical:
                self._guard_disposed_write(arguments.get('proposal'))
                if arguments.get('proposal',{}).get('action') in {'finish','report_unresolved'}:
                    from regen.planning.review_handoff import require_review_decisions
                    require_review_decisions(self)
            self._completed_action_state = None
            self._helper_attempt = None
            result = self._handle(name, arguments, metadata)
            if self.document_mode:
                from regen.planning.document_interface import public_view
                result = public_view(result)
            if getattr(self, 'framework_blocked', False):
                result = {**result, **self._framework_block_view()}
            if getattr(self, 'provider_wait', None):
                result = {**result, 'theory_wait':True, 'provider_wait':self.provider_wait}
            if getattr(self, 'theory_outcome', None):
                result = {**result, 'theory_stop':True, 'theory_outcome':self.theory_outcome}
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
            allow_import_edits=True, **strategy_options(self.generation_strategy['name'])),state

    @staticmethod
    def _uses_native_context(kind, proposal):
        if kind != 'observe':
            return True
        for read in proposal.get('reads', []):
            if read.get('action') == 'resolve_symbols':
                return True
            if read.get('action') == 'query':
                request = read.get('request', {})
                if request.get('goal_query') and request.get('structural_rerank'):
                    return True  # Typed candidate matching elaborates the target natively.
                if request.get('goal_query') or request.get('proof_state_query'):
                    continue  # These retrieve already captured propositions.
                queries = request.get('premise_queries', [request])
                if any(q.get('mode', 'auto') in {'auto', 'definition'} for q in queries):
                    return True  # Such queries may invoke the native term resolver.
        return False

    def _apply_pending_imports(self, runtime, state, directory):
        """One staged context transition at a native operation boundary."""
        store, _ = self._document(state=state)
        document = store.read()
        if not document['imports_pending']:
            return runtime, state, None
        if runtime[3].replay(repair=False) is None:
            # A new draft may choose imports before its first proof request.
            # Establish the same checked fixed scaffold as the ordinary batch
            # entry point before recording the first context revision.
            from regen.planning.batch_file_planner import initialize_batch_state
            state = self._observation_state(initialize_batch_state(runtime))
        from regen.execution.import_revisions import prepare_import_change, commit_import_change
        from regen.execution.import_semantics import check_import_semantics
        from regen.execution.proof_commits import durable
        prepared = None
        committed = False
        try:
            prepared = prepare_import_change(self.root, self.theory,
                document['draft_imports'], self.options['check_timeout'])
            if not prepared['changed']:
                return runtime, state, None
            candidate = prepared['runtime']
            semantics = check_import_semantics(runtime[4], candidate[4], runtime[1])
            durable(directory/'imports-semantics.json', semantics, immutable=True)
            if not semantics['valid']:
                return runtime, state, {'status':'imports_rejected',
                    'stage':semantics.get('stage'), 'semantic_check':semantics,
                    'notice':'Draft retained; the previous effective imports and accepted state are unchanged.'}
            self._check_session_active()
            check_cancelled()
            authority = candidate[3].replay(repair=False)
            new = commit_import_change(candidate, authority,
                {'check':semantics['after']['check'], 'semantic_check':semantics},
                prepared['provider_bindings'])
            committed = True
            # Runtime tuples retain the same document/journal directory; only
            # the checker and provider context are replaced.
            key = (str(self.root), self.theory, self.options['check_timeout'])
            self.runtime_cache[key] = candidate
            self._provider_preparation_error = None
            runtime[4].close_prefix()
            candidate = self._runtime()
            from regen.execution.repository_execution import progress
            progress(self.root)
            return candidate, self._observation_state(new), None
        except (ValueError, KeyError) as error:
            if prepared is not None:
                latest = prepared['runtime'][3].replay(repair=False)
                committed = committed or bool(latest and latest.get('context_revision',0)
                    != state.get('context_revision',0))
            if committed:
                raise  # Never describe an accepted context event as rejected.
            return runtime, state, {'status':'imports_rejected', 'diagnostic':str(error),
                'notice':'Correct the draft imports; no new context was accepted.'}
        finally:
            if prepared is not None and prepared.get('changed'):
                candidate = prepared['runtime']
                if not any(value is candidate for value in self.runtime_cache.values()):
                    candidate[4].close_prefix()

    def _handle(self, name, arguments, metadata):
        if name == 'proof_action' and isinstance(arguments.get('proposal'),dict):
            action=arguments['proposal'].get('action')
            if action in {'wait_for_providers','record_review_decision'}:
                if arguments.get('theory') != self.theory:
                    raise ValueError('only the assigned theory may make a scheduling/review decision')
                if action=='wait_for_providers':
                    from regen.planning.provider_waits import record_wait
                    return record_wait(self,arguments['proposal'],metadata)
                from regen.planning.review_handoff import record_review_decision
                return record_review_decision(self,arguments['proposal'],metadata)
        if (name == 'proof_action' and isinstance(arguments.get('proposal'), dict)
                and arguments['proposal'].get('action') in {'report_unresolved','check_refutation'}):
            if arguments.get('theory') != self.theory:
                raise ValueError('only the assigned theory may report an outcome')
            from regen.planning.theory_outcomes import handle_outcome
            return handle_outcome(self, arguments['proposal'], metadata)
        if self.document_mode and name=='proof_tool_help':
            from regen.planning.document_interface import tool_contracts
            return tool_contracts(**arguments)
        if self.document_mode and name=='read_theory' and arguments.get('theory')==self.theory:
            store,state = self._document()
            from regen.planning.batch_file_planner import planner_input
            from regen.planning.document_recall import compact_history_feedback, document_observations
            context = planner_input(self.shared._package(self.theory),state)
            document = store.read()
            advice = planning_advice(self, store, state)
            known_source = arguments.get('if_source_sha256')
            if known_source is not None and (not isinstance(known_source,str) or not known_source):
                raise ValueError('if_source_sha256 must be a nonempty source hash')
            if known_source == document.get('source_sha256'):
                document = {key:value for key,value in document.items() if key != 'source'}
                document['source_unchanged'] = True
            from regen.planning.review_handoff import review_context
            return {'theory':self.theory,'document':document,
                    'planning_review':review_context(self),
                    'context_revision':state.get('context_revision',0),
                    'import_options':{'base_import':self.run['base_import'],
                        'provider_directory':{'tool':'list_workspace','arguments':{}},
                        'notice':'Use the base import alone, or generated import names with certified exports. '
                                 'The next native action validates provider versions and applies the final draft list.'},
                    'accepted_ids':list(state['accepted_order']),
                    'accepted_source_sha256':state.get('document_source_sha256'),
                    'status':state['status'],'feedback':compact_history_feedback(state.get('batch_feedback',{}),state),
                    'observations':document_observations(context,state),
                    **({'planning_advice':advice} if advice else {}),
                    **_contract_view(self)}
        if self.document_mode and name=='read_theory':
            if 'if_source_sha256' in arguments:
                raise ValueError('if_source_sha256 applies only to the assigned draft')
            package,state = self._state(arguments['theory'])
            return {'theory':arguments['theory'], 'status':state['status'],
                    'import_option':self._import_option(package,state),
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
            if proposal.get('action') == 'read_entry':
                if set(proposal) != {'action','entry_id'}:
                    raise ValueError('read_entry accepts entry_id only')
                store, state = self._document()
                result = store.read_entry(proposal['entry_id'])
                from regen.execution.contract_refutation import refutation_capability
                fixed = next((c for c in store.package['contracts']
                              if c['contract_id'] == proposal['entry_id']), None)
                result['refutation_capability'] = (refutation_capability(fixed) if fixed else
                    {'can_attempt':False, 'reason':'not_fixed_contract'})
                from regen.planning.retrieval_memory import entry_retrieval_memory
                result['retrieval_memory'] = entry_retrieval_memory(state, proposal['entry_id'],
                    current_entry=result, directory=self.root/'theories'/Path(store.package['file_slot']).stem)
                advice = planning_advice(self, store, state, entry_id=proposal['entry_id'])
                if advice:
                    result['planning_advice'] = advice
                from regen.planning.unresolved_evidence import strict_policy_enabled, eligibility_view
                if strict_policy_enabled(self.run):
                    result['unresolved_eligibility'] = eligibility_view(self, proposal['entry_id'])
                if contract_dispositions_enabled(self.run):
                    result['contract_disposition'] = _contract_view(self)['contract_dispositions'].get(proposal['entry_id'])
                return result
            if proposal.get('action') == 'read_evidence':
                from regen.planning.evidence_reads import evidence_reads
                reads = evidence_reads(proposal)
                if 'index' in proposal:
                    from regen.planning.batch_file_planner import planner_input
                    from regen.planning.document_recall import context_index_page
                    from regen.planning.retrieval_memory import retrieval_index_context
                    package, state = self._state(self.theory)
                    request = dict(reads[0])
                    entry_id = request.pop('entry_id', None)
                    context = (retrieval_index_context(state, entry_id) if entry_id is not None
                               else planner_input(package, state))
                    result = context_index_page(context, request)
                    if entry_id is not None:
                        result['entry_id'] = entry_id
                        if result['next_action']:
                            result['next_action']['entry_id'] = entry_id
                    return result
                # Reuse the existing serial observation transaction and its
                # durable recovery. Public consolidation changes no authority.
                return self._handle(name, {**arguments, 'proposal': {
                    'action': 'observe', 'reads': reads}}, metadata)
            if proposal.get('action') == 'observe' and isinstance(proposal.get('reads'), list):
                from regen.planning.evidence_reads import evidence_reads
                reads = []
                for request in proposal['reads']:
                    if isinstance(request, dict) and request.get('action') == 'read_evidence':
                        if 'index' in request:
                            raise ValueError('read_evidence index is a standalone navigation action')
                        reads.extend(evidence_reads(request))
                    else:
                        reads.append(request)
                proposal = {**proposal, 'reads': reads}
                arguments = {**arguments, 'proposal': proposal}
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
                if theory_finished(state['status']): raise ValueError('terminal theory is immutable')
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
                if theory_finished(state['status']):
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
            if proposal.get('action') not in {'edit_theory','edit_proof','check_theory','check_sketch','probe_helpers','assess_sketch','finish'}:
                from regen.planning.document_interface import internal_request
                proposal = internal_request(proposal)
                arguments = {**arguments, 'proposal':proposal}
            allowed = {'edit_theory','edit_proof','check_theory','check_sketch','probe_helpers','assess_sketch','finish','observe','query','read_facts',
                       'read_proofs','read_attempts','read_queries','read_diagnostic',
                       'read_proof_state','resolve_symbols'}
            if proposal.get('action') not in allowed:
                raise ValueError('document mode requires source edits/checks, not legacy graph actions')
            if proposal.get('action') in {'edit_theory','edit_proof'}:
                from regen.infrastructure.workspace_transcript import record_workspace_action
                store,state=self._document()
                if theory_finished(state['status']):
                    raise ValueError('terminal theory is immutable')
                prefix=store.root/'edits'/uuid4().hex/'call'
                record_workspace_action(prefix,'Persistent theory source edit',proposal,metadata,
                                        theory=self.theory,model=self.model)
                edit = store.edit_proof if proposal['action'] == 'edit_proof' else store.edit
                return edit(proposal,{'transcript_prefix':str(prefix)})
            if proposal.get('action')=='finish':
                store,state=self._document()
                current=store.read()
                dispositions = current_dispositions(self)
                if dispositions:
                    from regen.planning.theory_document import parse
                    _, draft_proofs = parse(current['source'], store.package, store.imports)
                    if (proposal.get('source_sha256') != current['source_sha256']
                            or pending_contract_ids(self)
                            or any(draft_proofs.get(key) != value for key,value in state['proofs'].items())):
                        raise ValueError('partial export requires every fixed contract proved/disposed and preserves every accepted proof')
                elif (proposal.get('source_sha256')!=current['source_sha256'] or current['open_ids']):
                    raise ValueError('seal requires the current source hash and complete proof bodies with no open slots')
        if self.document_mode and name == 'prepare_reuse':
            raise ValueError('read_generated and copy source entries into the document for fresh checking')
        if name == 'list_workspace':
            if arguments.get('assigned_only') is True:
                package,state = self._state(self.theory)
                ready = self._theory_readiness(self.theory,package=package,state=state)
                return {'run_id':self.run['run_id'],'assigned_theory':self.theory,
                    'status':state['status'],'contract_count':len(package['contracts']),
                    'accepted_entry_count':len(state['proofs']),
                    'required_imports':self._effective_providers(package,state),
                    'ready_for_proof_action':ready['ready_for_proof_action'],
                    'unsealed_providers':ready['unsealed_providers'],
                    **_contract_view(self),
                    'assigned_writer_count':1,'theory_tool_execution':'serial',
                    'notice':'Read this theory for exact goals/draft and feedback. list_workspace '
                             'lists other theories on demand; list_generated/read_generated expose '
                             'reference-only material. Effective checked imports govern legal facts; '
                             'pending draft imports apply at the next native action.'}
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
            if row.get('error_type') == 'NativePreparationError':
                from regen.execution.check_tasks import NativePreparationError
                raise NativePreparationError(row.get('diagnostic', 'native execution infrastructure failed'))
            self._record_proof_work(row, arguments)
            return self._public(row)

    def _record_proof_work(self, row, arguments):
        # Only trusted checker output for a new candidate counts. Reads, edits,
        # invalid requests and identical retries do not keep a theory slot alive.
        if row.get('status') != 'completed' or arguments.get('kind') not in {'proof_batch','sketch','statement','refutation'}:
            return
        def checked(value):
            if isinstance(value, dict):
                if (value.get('framework_blocked')
                        or value.get('cached_failure')
                        or value.get('failure_scope') not in (None, '', 'candidate', 'proof-search')
                        or value.get('verdict') == 'infrastructure_error'):
                    return False
                return (value.get('verdict') in {'green','red','timeout'}
                        or any(checked(item) for item in value.values()))
            return isinstance(value, list) and any(checked(item) for item in value)
        full = self.executor.get(row['task_id'])
        if not checked(full.get('result')):
            return
        proposal = {k:v for k,v in arguments['proposal'].items() if k not in {'reason','notes'}}
        fingerprint = digest({'proposal':proposal, 'binding':full.get('binding')})
        if fingerprint not in self._proof_work_seen:
            self._proof_work_seen.add(fingerprint)
            self._proof_work_version += 1

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
        binding = {key:snapshot.get(key) for key in ('graph','proofs','accepted_order','context_revision','context_imports')}
        binding = {'state_sha256':digest(binding),'run_id':self.run['run_id'],
                   'package_sha256':package['package_payload_sha256'],
                   'context_revision':snapshot.get('context_revision',0)}
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
                    expected_state = binding['state_sha256']
                    if self.document_mode and self._uses_native_context(kind, proposal):
                        with self._action_lock:
                            _, current_state = self._state(self.theory)
                            actual = digest({key:current_state.get(key) for key in
                                ('graph','proofs','accepted_order','context_revision','context_imports')})
                            if actual != binding['state_sha256']:
                                raise ValueError('submitted candidate context is stale')
                            if proposal.get('action') in {'check_theory','check_sketch','assess_sketch','probe_helpers','finish'}:
                                store, _ = self._document(state=current_state)
                                store.checked_source(proposal.get('source_sha256'))
                            runtime, snapshot_after, rejected = self._apply_pending_imports(
                                runtime, current_state, directory)
                            if rejected is not None:
                                return rejected
                            # The context change is an independently validated
                            # journal event within this same tool request.
                            expected_state = digest({key:snapshot_after.get(key) for key in
                                ('graph','proofs','accepted_order','context_revision','context_imports')})
                    else:
                        snapshot_after = snapshot
                    if kind in {'proof_batch','seal','sketch'}:
                        with self._action_lock:
                            # Read-only observations never mutate this accepted snapshot.
                            _, now = self._state(self.theory)
                            actual = digest({key:now.get(key) for key in
                                ('graph','proofs','accepted_order','context_revision','context_imports')})
                            # Initial scaffold creation happens inside proof_action below.
                            if actual != expected_state:
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
                            blocked = next((value for value in (result, result.get('feedback', {}))
                                            if value.get('framework_blocked')), None)
                            if blocked is not None:
                                self.framework_blocked = True
                                self._framework_failure = {key:blocked[key] for key in
                                    ('failure_scope','native_failure_stage','diagnostic') if key in blocked}
                                self._framework_failure.setdefault('failure_scope','trusted_audit_target')
                    else:
                        result = self._observe(kind,proposal,snapshot_after,package,directory)
                        result['context_revision'] = snapshot_after.get('context_revision',0)
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
                       model=None, call_timeout=600, check_timeout=900, candidate_timeout=300,
                       codex_home=None, cancel_event=None, document_mode=True,
                       generation_strategy_name='baseline', focus_contract_id=None,
                       stop_at_focus_contract=False, and_or_control=None, retry_context=None,
                       http_retry_mode=False, effective_budget=False,
                       feedback_retry_mode=True, retry_failed_request_id=None,
                       no_progress_turn_limit=3, retry_unresolved=False):
    from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
    from regen.infrastructure.workspace_transcript import audit_workspace_session
    from regen.planning.batch_file_planner import INSTRUCTIONS as FORMATS
    if type(no_progress_turn_limit) is not int or not 1 <= no_progress_turn_limit <= 100:
        raise ValueError('no_progress_turn_limit must be an integer from 1 to 100')
    if type(retry_unresolved) is not bool:
        raise ValueError('retry_unresolved must be boolean')
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
        from regen.execution.checker_policy import validate_outcome_upgrade_readiness
        validate_outcome_upgrade_readiness(controller.run, root)
        contract_mode = contract_dispositions_enabled(controller.run)
        from regen.planning.unresolved_evidence import strict_policy_enabled, INSTRUCTIONS as EVIDENCE_INSTRUCTIONS
        evidence_mode = strict_policy_enabled(controller.run)
        if contract_mode and not document_mode:
            raise ValueError('contract-dispositions-v1 requires the document File Planner interface')
        if contract_mode and retry_unresolved:
            raise ValueError('retry_unresolved requires an explicit contract retry event; unsupported under contract-dispositions-v1')
        with event_scope(directory,run_id=controller.run['run_id'],theory=theory):
            durable(directory/'policy.json',{'theory':theory,**controller.options,
                    'generation_strategy':strategy,
                    'focus_contract_id':focus_contract_id,
                    'stop_at_focus_contract':stop_at_focus_contract,
                    'http_retry_mode':http_retry_mode,
                    'feedback_retry_mode':feedback_retry_mode,
                    'retry_failed_request_id':retry_failed_request_id,
                    'retry_unresolved':retry_unresolved,
                    **resources.policy,'mode':'parallel-file-planner-v1','theory_tool_execution':'serial',
                    'progress_policy':{'no_progress_turn_limit':no_progress_turn_limit,
                        'no_progress_authority':'accepted-proof-or-new-native-candidate-v1',
                        'local_stop_preserves_global_budget':True,
                        'repeated_result_limit':None if effective_budget else 8,
                        'no_information_timeout_seconds':None if effective_budget else call_timeout,
                        'policy':'bounded-final-turns-v1',
                        'information_guard_policy':'disabled-global-time-budget-v1' if effective_budget else 'information-progress-v1',
                        'callback_time_excluded':True,
                        'sleep_policy_version':2,'sleep_item_max_ms':60000,
                        'sleep_semantics':'protocol-display-only; not proof progress',
                        'deadline_authority':('global effective budget; native/action and transport deadlines remain active'
                            if effective_budget else 'no-information deadline and repeated-result limit; native/action deadlines govern callbacks')},
                    'file_planner_interface':'document' if document_mode else 'graph'},immutable=True)
            # Direct session callers obey the same durable stop authority as the
            # repository coordinator. A refutation cannot be retried as a positive
            # goal; an UNKNOWN retry must be explicitly selected and recorded.
            from regen.planning.theory_outcomes import current_outcome
            retained = current_outcome(controller, theory)
            if retained and (retained['status'] == 'refuted' or not retry_unresolved):
                _, state = controller._state(theory)
                result = {'theory':theory, 'status':retained['status'],
                          'theory_status':state['status'], 'theory_outcome':retained,
                          'retained_from_prior_invocation':True,
                          'provider':{'status':'not_started_retained_theory_outcome'},
                          'provider_turns':[], 'continuation_turns':0,
                          'audit':{'terminal':False, 'provider_not_started':'retained_theory_outcome'},
                          'task_ids':sorted(controller.task_ids), 'artifact_dir':str(directory)}
                durable(directory/'result.json', result, immutable=True)
                return result
            from regen.planning.provider_waits import current_wait, claim_ready_wait
            waiting = current_wait(controller,theory)
            if waiting and not waiting['ready']:
                result = {'theory':theory,'status':'waiting_for_provider','provider_wait':waiting,
                          'provider':{'status':'not_started_waiting_for_provider'},
                          'task_ids':sorted(controller.task_ids),'artifact_dir':str(directory)}
                durable(directory/'result.json',result,immutable=True)
                return result
            resumed_wait = claim_ready_wait(controller,theory) if waiting else None
            # Do not install a retained UNKNOWN into the fresh controller on an
            # authorized retry: doing so would latch its mathematical tool gate.
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
                if contract_mode:
                    prompt += ('\nCONTRACT DISPOSITION POLICY: contract-dispositions-v1. '
                        'report_unresolved requires explicit entry_ids and stops only those fixed contracts. '
                        'check_refutation stops only its checked entry_id. Continue other pending goals; '
                        'disposed goals supply no theorem and remain oops. When every fixed goal is '
                        'proved or disposed, use finish to publish only the checked positive subset. '
                        'An exported_partial theory is usable by consumers but is not fully proved. '
                        'Do not retry or strengthen/weaken disposed contracts.\n')
                if evidence_mode:
                    prompt += '\n'+EVIDENCE_INSTRUCTIONS+'\n'
                if resumed_wait is not None:
                    prompt += '\nPROVIDER WAIT RESUMED (imports unchanged):\n'+json.dumps(resumed_wait,ensure_ascii=False)
                if retry_context is not None:
                    prompt += '\nRECOVERY RECEIPT (not mathematical advice):\n'+json.dumps(retry_context,ensure_ascii=False)
                if focus_contract_id is not None:
                    prompt += ('\nFOCUS FIXED CONTRACT ID: '+focus_contract_id+
                        '\nPrioritize this fixed contract and its supporting helpers. '
                        'All ordinary proof, legal-fact and checker-acceptance rules remain unchanged.')
                providers = []
                transport_anomaly = None
                session_stop_reason = None
                target_completed = controller.target_contract_green()
                if not target_completed:
                    with CodexWorkspaceSession(tools=tools(document_mode),codex_bin=settings['codex_bin'],codex_home=home,
                                               allow_external_call=True,progress_timeout=0 if effective_budget else call_timeout,
                                               http_retry_mode=http_retry_mode,
                                               feedback_retry_mode=feedback_retry_mode,
                                               retry_failed_request_id=retry_failed_request_id) as session:
                        next_prompt = prompt
                        no_progress_turns = 0
                        idle_targets = set()
                        _, progress_state = controller._state(theory)
                        progress_marker = _progress_marker(controller, progress_state)
                        while True:
                            try:
                                provider = session.run(next_prompt,controller.handle,artifact_dir=directory,model=controller.model,
                                                       reasoning_effort=settings['reasoning_effort'],timeout=call_timeout,
                                                       cancel_event=cancel_event,
                                                       on_abort=getattr(controller,'abort_pending',None),
                                                       stop_when=lambda value: (
                                                           'framework_blocked' if isinstance(value,dict) and value.get('framework_blocked')
                                                           else 'waiting_for_provider' if isinstance(value,dict) and value.get('theory_wait')
                                                           else 'theory_outcome' if isinstance(value,dict) and value.get('theory_stop')
                                                           else 'target_contract_green' if stop_at_focus_contract and isinstance(value,dict)
                                                           and value.get('target_contract_green') else None))
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
                            if (target_completed or theory_finished(state['status'])
                                    or getattr(controller, 'theory_outcome', None)
                                    or getattr(controller, 'provider_wait', None)):
                                break
                            if (getattr(controller,'framework_blocked',False)
                                    or state.get('batch_feedback', {}).get('framework_blocked')):
                                blocked_feedback = {**state.get('batch_feedback', {}),
                                                    **getattr(controller, '_framework_failure', {})}
                                emit_event('generation_framework_blocked', theory=theory,
                                           failure_scope=blocked_feedback.get('failure_scope', 'trusted_audit_target'),
                                           native_failure_stage=blocked_feedback.get('native_failure_stage'))
                                break
                            # Idle turns are not mathematical failure evidence.
                            # New runs rotate through pending goals before pausing
                            # the worker; historical policies retain their fallback.
                            marker = _progress_marker(controller, state)
                            if marker != progress_marker:
                                idle_targets.clear()
                            no_progress_turns = no_progress_turns + 1 if marker == progress_marker else 0
                            progress_marker = marker
                            if no_progress_turns >= no_progress_turn_limit:
                                from regen.planning.theory_outcomes import record_unresolved
                                if contract_mode:
                                    pending = pending_contract_ids(controller)
                                    if not pending:
                                        session_stop_reason = 'positive_exports_not_published'
                                        emit_event('contract_exports_unpublished', theory=theory,
                                                   reason='no_progress_with_no_pending_contracts')
                                        break
                                    if evidence_mode:
                                        other = next((key for key in pending if key not in idle_targets), None)
                                        if other is None:
                                            session_stop_reason = 'planner_idle_without_outcome_evidence'
                                            emit_event('planner_idle', theory=theory,
                                                reason=session_stop_reason, pending_contract_ids=pending,
                                                completed_turns=no_progress_turns,
                                                positive_proofs_added=0, dispositions_added=0)
                                            break
                                        idle_targets.add(other)
                                        no_progress_turns = 0
                                        next_prompt = _continuation_prompt(theory, state,
                                            contract_view=_contract_view(controller))
                                        next_prompt += ('\nThe previous turn added no native proof work. '
                                            'Try this still-pending contract next: '+other+'. '
                                            'Read its entry and latest failure evidence, revise a candidate '
                                            'or perform a native check. Idle replies do not justify UNKNOWN. '
                                            +EVIDENCE_INSTRUCTIONS)
                                        emit_event('planner_idle_target_selected', theory=theory,
                                            entry_id=other, pending_contract_ids=pending)
                                        continue
                                    record_unresolved(controller, reason='no_progress', origin='controller',
                                        entry_ids=[pending[0]], classification='no_progress',
                                        details={'completed_turns_without_proof_work':no_progress_turns,
                                                 'limit':no_progress_turn_limit,
                                                 'target_selection':'first_pending_contract',
                                                 'disposition_basis':'controller_idle_fallback'})
                                    emit_event('contract_unresolved', theory=theory, entry_id=pending[0],
                                               reason='no_progress', completed_turns=no_progress_turns)
                                    no_progress_turns = 0
                                    progress_marker = _progress_marker(controller, state)
                                    next_prompt = _continuation_prompt(theory, state,
                                        contract_view=_contract_view(controller))
                                    continue
                                record_unresolved(controller, reason='no_progress', origin='controller',
                                    classification='no_progress',
                                    details={'completed_turns_without_proof_work':no_progress_turns,
                                             'limit':no_progress_turn_limit})
                                emit_event('theory_unresolved', theory=theory, reason='no_progress',
                                           completed_turns=no_progress_turns)
                                break
                            next_prompt = _continuation_prompt(theory,state,
                                contract_view=_contract_view(controller) if contract_mode else None)
                    if transport_anomaly is None:
                        audit = audit_workspace_session(
                            directory/'session-transcript.jsonl',
                            require_terminal=not target_completed and not getattr(controller,'framework_blocked',False)
                                and not getattr(controller, 'theory_outcome', None)
                                and not getattr(controller, 'provider_wait', None)
                                and not state.get('batch_feedback', {}).get('framework_blocked'),
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
            outcome = getattr(controller, 'theory_outcome', None)
            waiting = getattr(controller,'provider_wait',None)
            status = ('waiting_for_provider' if waiting else 'target_green_completed'
                      if target_completed else 'unpublished' if session_stop_reason else outcome['status'] if outcome else 'framework_blocked'
                      if getattr(controller,'framework_blocked',False)
                         or state.get('batch_feedback', {}).get('framework_blocked') else state['status'])
            provider = (providers[-1] if providers else
                        {'status':'not_started_target_already_green'})
            result = {'theory':theory,'status':status,
                      'theory_status':state['status'],'provider':provider,
                      'provider_turns':providers,'continuation_turns':len(providers)-1,'audit':audit,
                      'task_ids':sorted(controller.task_ids),'artifact_dir':str(directory)}
            if contract_mode:
                result.update(_contract_view(controller))
            if session_stop_reason:
                result['session_stop_reason'] = session_stop_reason
            if waiting:
                result['provider_wait'] = waiting
            if outcome:
                result['theory_outcome'] = outcome
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
    if not isinstance(value, dict) or set(value) - {
            'stages', 'session_stall_retries', 'provider_http_retries', 'provider_feedback_retries',
            'no_progress_turn_limit', 'retry_unresolved'}:
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
    feedback_retries = value.get('provider_feedback_retries', 1)
    if type(feedback_retries) is not int or feedback_retries not in (0, 1):
        raise ValueError('provider_feedback_retries must be 0 or 1')
    no_progress = value.get('no_progress_turn_limit', 3)
    if type(no_progress) is not int or not 1 <= no_progress <= 100:
        raise ValueError('no_progress_turn_limit must be an integer from 1 to 100')
    retry_unresolved = value.get('retry_unresolved', False)
    if type(retry_unresolved) is not bool:
        raise ValueError('retry_unresolved must be boolean')
    out = {'stages':stages, 'session_stall_retries':retries,
           'provider_feedback_retries':feedback_retries, 'no_progress_turn_limit':no_progress,
           'retry_unresolved':retry_unresolved}
    if 'provider_http_retries' in value:
        http_retries = value['provider_http_retries']
        if type(http_retries) is not int or http_retries not in (0, 1):
            raise ValueError('provider_http_retries must be 0 or 1')
        out['provider_http_retries'] = http_retries
    return out


def _stage_eligible(rows, policy, active=()):
    states = {row['theory']:row for row in rows}
    publication_pending = set()
    def include_wait_providers(selected):
        selected=set(selected); todo=list(selected)
        while todo:
            name=todo.pop(); row=states.get(name,{})
            wait=row.get('provider_wait',{})
            dependencies=[p['theory'] for p in wait.get('providers',[])]
            if name not in selected:
                selected.add(name)
            if wait or name not in original:
                dependencies += [p['theory'] if isinstance(p,dict) else p
                                 for p in row.get('required_imports',[])]
            for provider in dependencies:
                if provider in states and provider not in selected:
                    selected.add(provider); todo.append(provider)
        return selected
    original=set()

    def finished(theory):
        row = states.get(theory, {})
        return (theory_finished(row.get('status'))
                or (row.get('pending_contract_ids') == [] and bool(row.get('contract_dispositions'))))
    for stage in policy['stages']:
        if any(not finished(theory) or theory in active for theory in stage):
            original=set(stage) | publication_pending
            return include_wait_providers(original)
        # A stopped contract scope can release independent later work before its
        # positive exports are published. Keep that publication runnable on a
        # later invocation too: the coordinator's per-invocation failures exclude
        # a worker that already returned unpublished, without permanently
        # stranding consumers behind an unexported earlier stage.
        publication_pending.update(theory for theory in stage
            if not theory_finished(states.get(theory, {}).get('status')))
    return {row['theory'] for row in rows if row['selected']}


def _retry_stalled(error, attempt, retries):
    from regen.infrastructure.codex_workspace_session import SessionStalled, ProviderOverloaded
    # A cleanup failure may leave a live owner; never overlap it with a retry.
    return (isinstance(error, (SessionStalled, ProviderOverloaded)) and attempt <= retries
            and not getattr(error, '__notes__', ()))


FEEDBACK_RETRY_POLICY = 'provider-feedback-retry-v1'


def _feedback_recovery_state(rows, theory):
    """One durable replacement per incident; only its bound response resets it.

    A reserved but unstarted replacement can survive a coordinator restart. Once
    started, a missing success/failure receipt is an uncertain attempt, never an
    extra retry allowance. Completed tools and proof commits are not replayed.
    """
    from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
    from regen.infrastructure.provider_request_accounting import POLICY as CREDIT_POLICY
    state = {'retries':0, 'failed_request_id':None, 'started':False, 'stopped':None}
    for row in rows:
        data = row['data']
        kind = row['kind']
        if (kind == CHECKPOINT_KIND
                and data.get('boundary') == 'provider_request_retry_credit'):
            # Credit and adjusted counters are atomic; its derived success
            # event may be absent after a crash between the two publications.
            correction = data.get('correction', {})
            if correction.get('policy') != CREDIT_POLICY:
                continue
            data = correction
            kind = 'provider_feedback_retry_succeeded'
        if data.get('theory') != theory:
            continue
        if kind == 'provider_feedback_retry_succeeded':
            if (state['failed_request_id'] is not None
                    and data.get('failed_request_id') == state['failed_request_id']):
                state = {'retries':0, 'failed_request_id':None,
                         'started':False, 'stopped':None}
        elif data.get('retry_policy') == FEEDBACK_RETRY_POLICY:
            if kind == 'provider_feedback_retry_scheduled':
                state.update(retries=state['retries']+1,
                    failed_request_id=data['failed_request_id'], started=False, stopped=None)
            elif (kind == 'provider_feedback_retry_started'
                  and data.get('failed_request_id') == state['failed_request_id']):
                state['started'] = True
            elif kind == 'provider_feedback_retry_stopped':
                state['stopped'] = data['reason']
    return state


def _run_budgeted_theory_session(bridge, *args, **kwargs):
    with bridge.worker(args[1]):
        return run_theory_session(*args, effective_budget=True, **kwargs)


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
                           check_timeout=900, candidate_timeout=300, allow_external_call=False,
                           codex_home=None, integrate=True, file_planner_interface='document',
                           execution_policy=None, native_cpus=4, native_memory_mb=16384,
                           native_pids_limit=1024, provider_build_timeout=1800,
                           build_ml_heap_mb=None, generation_strategy_name='sketch-reviewed-v1', focus_contract_id=None,
                           effective_seconds=172800, resume_authorization=None,
                           extend_effective_seconds=None, planning_budget=None, wall_seconds=None):
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
        from regen.execution.checker_policy import validate_outcome_upgrade_readiness
        validate_outcome_upgrade_readiness(overview.run, root)
        overview.document_mode = file_planner_interface == 'document'
        selected = overview.selected
        policy = _execution_policy(execution_policy, selected)
        if contract_dispositions_enabled(overview.run) and file_planner_interface != 'document':
            raise ValueError('contract-dispositions-v1 requires the document File Planner interface')
        if contract_dispositions_enabled(overview.run) and policy['retry_unresolved']:
            raise ValueError('retry_unresolved requires an explicit contract retry event; unsupported under contract-dispositions-v1')
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
        if wall_seconds is not None:
            workspace_policy['generation_wall_seconds'] = wall_seconds
        from regen.infrastructure.parallel_generation_budget import initialize_parallel_budget
        rows = overview.list_workspace()['theories']
        rows_observed_at = GenerationMonitor.now()
        budget, bridge = initialize_parallel_budget(root, overview, workspace_policy,
            seconds=effective_seconds, resume_authorization=resume_authorization,
            extension_seconds=extend_effective_seconds, rows=rows,
            planning_budget=planning_budget, wall_seconds=wall_seconds)
        budget.ledger.append('parallel_budget_invocation',
            service_invocation_id=os.environ.get('INVOCATION_ID'), pid=os.getpid())
        durable(root/'parallel-workspace-policy.json', workspace_policy, immutable=True)
        executor = CheckExecutor(root/'check-tasks',workers=max_parallel_theories)
        results, active, failures = [], {}, {}
        from regen.planning.theory_outcomes import current_outcome
        for row in rows:
            if not row['selected'] or theory_finished(row['status']):
                continue
            outcome = current_outcome(overview, row['theory'])
            if outcome and (outcome['status'] == 'refuted' or not policy['retry_unresolved']):
                failures[row['theory']] = {'reason':'theory_'+outcome['status'],
                    'status':outcome['status'], 'outcome':outcome, 'retained_from_prior_invocation':True}
        attempts, directories = {}, {}
        stall_attempts = {}
        from regen.infrastructure.provider_http_failure import recovery_streak, recovery_decision
        retry_not_before = {}
        cancel_event = budget.event
        budget.start(abort=cancel_event.set)
        sessions = root/'parallel-sessions'/uuid4().hex
        with event_scope(sessions,run_id=overview.run['run_id'],phase='proof') as monitor, \
                observe_events(bridge.handle, publication_guard=bridge.acceptance):
            with ThreadPoolExecutor(max_workers=max_parallel_theories,thread_name_prefix='file-planner') as agents:
                try:
                    if monitor is not None:
                        monitor.transition('proof_construction')
                    refresh = False
                    while True:
                        budget.check()
                        if refresh:
                            rows = overview.list_workspace()['theories']
                            rows_observed_at = GenerationMonitor.now()
                        eligible = _stage_eligible(rows, policy, active.values())
                        for row in rows:
                            if len(active)>=max_parallel_theories:
                                break
                            theory = row['theory']
                            if (theory not in eligible or theory in failures or theory in active.values()
                                    or not row['ready_for_proof_action']
                                    or retry_not_before.get(theory, 0) > monotonic()):
                                continue
                            feedback_state = _feedback_recovery_state(budget.ledger.rows, theory)
                            if feedback_state['stopped'] or feedback_state['started']:
                                # A previous coordinator already spent this replacement.
                                # Do not turn explicit run resume into another request.
                                budget.cancel(feedback_state['stopped'] or 'provider_feedback_retry_interrupted')
                                budget.check()
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
                            if feedback_state['failed_request_id'] is not None:
                                budget.ledger.append('provider_feedback_retry_started', theory=theory,
                                    failed_request_id=feedback_state['failed_request_id'],
                                    retry_policy=FEEDBACK_RETRY_POLICY,
                                    attempt_directory=str(directory), accepted_state_preserved=True)
                            future = agents.submit(context.run,_run_budgeted_theory_session,bridge,root,theory,
                                resources=resources,executor=executor,settings=settings,directory=directory,
                                generation_strategy_name=generation_strategy_name,
                                focus_contract_id=focus_contract_id,
                                retry_context=retry_summary(results,theory),
                                http_retry_mode=bool(policy.get('provider_http_retries', 0)),
                                feedback_retry_mode=bool(policy['provider_feedback_retries']),
                                no_progress_turn_limit=policy['no_progress_turn_limit'],
                                retry_unresolved=policy['retry_unresolved'],
                                retry_failed_request_id=feedback_state['failed_request_id'],
                                codex_home=codex_home,cancel_event=cancel_event,document_mode=file_planner_interface=='document',**overview.options)
                            active[future] = theory
                        if monitor is not None:
                            monitor.update_workspace(rows,active=active.values(),failures=failures,
                                observed_at=rows_observed_at,
                                limits={'max_parallel_theories':max_parallel_theories,
                                        'native_slots':native_slots})
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
                                if (not theory_finished(completed.get('status'))
                                        and completed.get('status') != 'waiting_for_provider'):
                                    failures[theory] = {'reason':('framework_blocked' if completed.get('status') == 'framework_blocked'
                                                                  else completed.get('session_stop_reason','positive_exports_not_published') if completed.get('status') == 'unpublished'
                                                                  else 'theory_'+completed['status'] if completed.get('status') in {'unknown','refuted'}
                                                                  else 'session_returned_unsealed'),
                                                        'status':completed.get('status')}
                                    if completed.get('theory_outcome'):
                                        failures[theory]['outcome'] = completed['theory_outcome']
                            except Exception as error:
                                from regen.infrastructure.codex_workspace_session import ProviderHTTPError, ProviderFeedbackError
                                result = {'theory':theory,'status':'infrastructure_error',
                                          'error_type':type(error).__name__,'diagnostic':str(error)}
                                if isinstance(error, ProviderFeedbackError):
                                    recovery = _feedback_recovery_state(budget.ledger.rows, theory)
                                    if not isinstance(error.failed_request_id, str) or not error.failed_request_id:
                                        budget.cancel('provider_feedback_missing_request_identity')
                                        raise
                                    reason = (
                                        'provider_feedback_cleanup_failed' if getattr(error, '__notes__', ())
                                        else 'provider_feedback_retry_disabled' if not policy['provider_feedback_retries']
                                        else 'provider_feedback_retry_exhausted' if recovery['retries'] >= 1
                                        else None)
                                    result.update(attempt=attempts[theory], retry_scheduled=reason is None,
                                        failed_request_id=error.failed_request_id, failure=error.failure)
                                    durable(directories[theory]/'failure.json', result, immutable=True)
                                    results.append(result)
                                    receipt = dict(theory=theory, failed_request_id=error.failed_request_id,
                                        failure=error.failure, retry_policy=FEEDBACK_RETRY_POLICY,
                                        prior_attempt_directory=str(directories[theory]),
                                        accepted_state_preserved=True, replay_executed_tools=False)
                                    if reason is not None:
                                        budget.ledger.append('provider_feedback_retry_stopped', **receipt,
                                            reason=reason, retries_used=recovery['retries'])
                                        budget.cancel(reason)
                                        raise
                                    # The future has joined its session/controller cleanup.
                                    # Reserve before any replacement can issue provider work.
                                    budget.ledger.append('provider_feedback_retry_scheduled', **receipt,
                                        retry_number=1, next_attempt=attempts[theory]+1)
                                    emit_event('provider_feedback_retry_scheduled', theory=theory,
                                        failed_request_id=error.failed_request_id,
                                        next_attempt=attempts[theory]+1)
                                    continue
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
                        *([lambda: monitor.transition('proof_construction','paused',
                            reason=str(error),active_theories=list(active.values()),
                            generation_budget=budget.snapshot())] if monitor is not None else []),
                    ], primary=error)
                    raise
            final = overview.list_workspace()
            final['worker_count'] = max_parallel_theories
            if monitor is not None:
                monitor.update_workspace(final['theories'],failures=failures,
                    limits={'max_parallel_theories':max_parallel_theories,'native_slots':native_slots})
            incomplete = [row for row in final['theories']
                          if row['selected'] and not theory_finished(row['status'])]
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
            for row in incomplete:
                waiting=row.get('provider_wait')
                if waiting and row['theory'] not in failures:
                    unavailable=waiting['unavailable_providers']
                    blocked[row['theory']]={'reason':'waiting_for_provider',
                        'unsealed_providers':unavailable,'wait_receipt_id':waiting['receipt_id'],
                        'provider_failures':{name:failures[name] for name in unavailable if name in failures},
                        'notice':'No proof success claimed; wait resumes only after certified provider publication.'}
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
                    if monitor is not None:
                        monitor.transition('integration')
                    with bridge.integration():
                        result['integration'] = integrate_repository(root,timeout=check_timeout)
                except BaseException as error:
                    if monitor is not None:
                        _cleanup_all([lambda: monitor.transition('integration','failed',
                            error_type=type(error).__name__)], primary=error)
                    raise
                finally:
                    CURRENT_RESOURCES.reset(marker)
                result['repository_verified'] = result['integration']['repository_verified']
                result['status'] = 'repository_verified' if result['repository_verified'] else 'integration_failed'
            elif all(row['status']=='sealed' for row in final['theories'] if row['selected']):
                result['status'] = 'selected_theories_sealed'
            elif all(theory_finished(row['status']) for row in final['theories'] if row['selected']):
                result['status'] = 'selected_theories_exported'
                result['all_selected_exported'] = True
                result['all_selected_contracts_proved'] = False
            if result['repository_verified']:
                budget.finish('repository_verified')
                result['disposition'] = 'complete'
            else:
                budget.cancel(result['status'])
                result['disposition'] = 'pause'
                result['resume_requires_authorization'] = True
            result['generation_budget'] = budget.snapshot()
            durable(sessions/'result.json',result,immutable=True)
            if monitor is not None:
                monitor.transition('repository','completed' if result['repository_verified'] else 'paused',
                    outcome=result['status'],repository_verified=result['repository_verified'],
                    result_path=str(sessions/'result.json'),generation_budget=result['generation_budget'])
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
