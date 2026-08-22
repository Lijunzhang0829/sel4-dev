# Current Harness Architecture and Repo-Level Generation Flow

> Updated: 2026-08-17
> Branch: `regen-refine`
> Active environment campaign: `sel4-refine-setting-b-c55be4ed1c2f8c1b`
> Active proof run: `reports/regen/proof-runs/refine-main`

This document describes the harness that currently exists in this branch. It separates three things throughout:

1. code and artifacts that are implemented and have been exercised;
2. the operational flow that can be run now;
3. the complete repo-level Refine/CRefine flow that is still the target.

It must not be read as a claim that the whole seL4 proof repository has been regenerated. The current experiment fixes the code/specification endpoints, parent sessions, Support theories, Proof file slots, contract ownership, and exact contracts. It regenerates the contract-owning Proof-theory layer under Setting B.

## 1. Scope and current status

The fixed endpoints are:

```text
Abstract specification
        |
        | Refine: fixed corres/connect contracts
        v
Haskell design specification
        |
        | CRefine: fixed ccorres/connect contracts
        v
C implementation
```

The intended campaign order is:

```text
prepare isolated environment
        |
        v
generate all Refine Proof theories
        |
        v
clean-build and seal generated Refine
        |
        v
generate CRefine against that exact generated Refine heap
```

The current implementation has reached:

```text
Environment Preparation M0-M3   PASS
Session Planner                 PASS (21 files, 3 layers)
KHeap_R modular generation      BUILT
Remaining Refine Proof theories 20 PENDING
Generated Refine session seal   NOT IMPLEMENTED / NOT COMPLETE
CRefine generation              NOT STARTED
```

`KHeap_R` currently has five fixed contracts. Its final graph is revision 77 and contains 122 nodes; all 122 nodes are Isabelle GREEN. The assembled theory and shared `Generated_Refine` session build both pass. The runner records this as `built`, which is weaker than the strict theory/session seal described by the target protocol.

## 2. Architectural overview

The harness is divided into an environment/control plane and a proof-generation plane.

```text
ENVIRONMENT AND SESSION CONTROL

  Environment Preparation
  M0 campaign freeze
      -> M1 contract extraction/classification
      -> M2 isolated Isabelle workspace
      -> M3 legal Premise Database
                          |
                          v
                    Session Planner
                    layered file list
                          |
                          v
                    Session Runner
                          |
       +------------------+------------------+
       |                                     |
       v                                     v
  mutable progress                     generated premise
  and run artifacts                    publication


PER-THEORY PROOF GENERATION

  public contract package
           |
           v
      File Planner
           |
           v
  generated theorem DAG
           |
           v
  deterministic graph checks
           |
           v
  Isabelle statement-shell checks
           |
           v
  executable node selected
           |
           v
  Premise Retrieval -> Lemma Prover -> Stepwise-Codex
                              |              |
                              |              v
                              |       actual Isabelle state
                              |              |
                              +---- RED -----+
                              |              |
                              v              v
                    Proof Outline       Graph Revision Agent
                    / Salvage              |
                              +-------------+
                              |
                              v
                    revised certified DAG
                              |
                              v
                    all nodes GREEN
                              |
                              v
                    assemble/check/build/publish
```

The main authority separation is deliberate:

| Decision | Owner |
|---|---|
| Which Refine file to attempt next | Session Planner schedule + Session Runner |
| Initial theorem/helper decomposition | File Planner |
| Which existing facts to try for one node | Premise Retrieval |
| Proof body for one fixed statement | Lemma Prover / Stepwise-Codex |
| Whether a failed node needs structural change | Graph Revision Agent |
| Whether a statement or proof is valid | Isabelle |
| State persistence and module orchestration | Session Runner |

No LLM module is allowed to declare its own result correct. Isabelle is the semantic authority.

## 3. Units and states

The harness uses four different units:

- **Contract**: a fixed target proposition such as `getEndpoint_corres`. Its statement and owner file are not generated.
- **Generated node**: one contract, theorem, or helper declaration in a file-local theorem DAG. This is the unit proved by the Lemma Prover.
- **Theory**: a complete `.thy` file. This is the assembly and build unit.
- **Session/corpus**: all generated Refine theories, followed later by CRefine. This is the repo-level evaluation unit.

The important states are not interchangeable:

```text
node GREEN
    Isabelle accepted one declaration in its checked dependency context

complete theory GREEN
    Isabelle accepted the assembled file

session build GREEN
    Generated_Refine accepted the file together with previously built files

built
    current runner status after the checks above and premise publication

sealed/publishable
    stronger target state requiring complete provenance, transcript,
    dependency, automation and leakage audits; not fully implemented yet
```

## 4. Module inventory

### 4.1 Environment Preparation

Environment Preparation is a logical module implemented by four milestone programs. It constructs the only Isabelle environment that later modules should use.

#### M0: Campaign freeze

**Code:** `regen/refine_campaign.py`

**Purpose:** Freeze the experimental setting before generation begins.

**Inputs:**

- campaign config and contract-selection policy;
- repository and nested `verification/l4v` revisions;
- allowed and forbidden source classes/path policy;
- model/backend identities and budgets;
- hashes of the harness programs that define M0-M4 behavior.

**Outputs:**

- campaign ID;
- immutable M0 manifest;
- frozen input identities and policy hashes;
- trust-boundary and completion declarations.

M0 states the rules and binds their identities. It does not itself prevent file access or extract contracts. M1 and M2 implement those rules, and their results are checked against the M0 manifest.

#### M1: Contract extraction and Proof/Support classification

**Code:** `regen/refine_contracts.py`

**Purpose:** Read the trusted human source once, extract only the fixed experimental targets, and classify file slots.

**Inputs:**

- M0 manifest;
- trusted original Refine sources;
- frozen selector `operation-corres-v1`.

**Processing:**

- select target `corres`/`connect` declarations according to the fixed policy;
- preserve each exact proposition and contract-owned attributes;
- remove proof bodies and proof-context commands such as `notes`, `using`, `including`, and proof-only comments;
- classify a theory containing at least one selected contract as a Proof theory;
- classify a theory containing no selected contract as a Support theory.

**Outputs:**

- private contract manifest with exact statements, owners, source locations and hashes;
- one public JSON contract package per Proof theory;
- Support theory inventory;
- private forbidden Proof-source inventory;
- extraction and leakage audit.

**Current Refine result:** 41 theory slots, 21 Proof theories, 20 Support theories, and 287 selected contracts.

The public package is what planners may see. It does not expose the human Proof theory's imports, helper declarations, proof scripts, declaration order, dependency graph, or export design.

#### M2: Isolated Isabelle environment

**Code:**

- `regen/refine_m2_preflight.py`
- `regen/refine_m2_isolated.py`

**Purpose:** Turn M1's JSON packages into an Isabelle environment in which fixed contracts elaborate but original contract-owning Proof implementations and stock target heaps are unavailable.

**Inputs:**

- M0/M1 artifacts;
- frozen parent sessions and endpoints;
- frozen Support theories;
- forbidden Proof-source inventory.

**Outputs:**

- sanitized/isolated l4v workspace;
- contract-shell theories;
- frozen Support build/session cache;
- source-access and denial-probe audit;
- contract-shell elaboration results;
- M2 gate result and environment identity.

M2 does not rename a target merely to create cosmetic isolation. The meaningful boundary is the filesystem/container/heap context: later checkers mount the sanitized workspace, frozen Support, generated theories, and approved heaps, not the original Proof implementations or stock Refine/CRefine target heap.

#### M3: Premise Database

**Code:** `regen/refine_m3_premises.py`

**Purpose:** Publish a searchable inventory of facts that are legal in the isolated environment.

**Initial sources:**

- frozen parent sessions;
- currently eligible frozen Support theories.

**Excluded sources:**

- original facts from classified human Proof theories;
- planned, red, abandoned, future, or unbuilt generated declarations.

**Outputs:**

- `M3/premises.sqlite3`;
- versioned premise snapshot and verification report;
- declaration records containing statement, owner, source class, attributes, definition equations and provenance.

**Current snapshot:** 76,663 records from 382 owners: 74,893 parent records and 1,770 Support records. The exported metadata includes 135 effective `simp` facts, 3,788 `wp` facts, and 13 `corres` facts.

M3 provides availability and retrieval. It does not decide the file order and it does not insert existing premises into the generated theorem DAG.

### 4.2 Session Planner

**Code:** `regen/refine_m4_session.py`

**Historical artifact label:** M4.

**Purpose:** Produce the immutable Refine Proof-file generation schedule and initial mutable progress ledger.

**Inputs:**

- all public Refine Proof-file packages;
- contract identities and operation metadata;
- normalized Support availability/unlock evidence;
- fixed scheduling policy;
- M3 snapshot identity, but not premise records for theorem planning.

**Logic:**

1. deterministic extraction creates scheduling evidence;
2. one audited Codex call may propose candidate inter-file priority edges;
3. deterministic validation rejects cycles, unknown slots, duplicates and unsupported structures;
4. accepted contract-composition evidence creates layers;
5. operation call-graph evidence is only a weak serial-priority signal;
6. Support unlock information contributes evidence but does not expose human Proof architecture.

**Outputs:**

- `M4/file-generation-list.json` with immutable layers and serial fallback order;
- `M4/session-progress-ledger.json`;
- dependency-evidence artifacts and audit;
- complete LLM transcript for the planning call.

**Current schedule:**

```text
Layer 0:
  KHeap_R, Bits_R, ArchAcc_R, CSpace1_R, Refine, Tcb_R,
  TcbAcc_R, Finalise_R, Detype_R, InterruptAcc_R, Interrupt_R,
  CNodeInv_R, IpcCancel_R, Retype_R, Schedule_R, VSpace_R

Layer 1:
  Arch_R, CSpace_R, Ipc_R, Untyped_R

Layer 2:
  Syscall_R
```

Theories in one layer have no accepted contract-composition edge separating them. This does not prove semantic independence and does not require parallel execution.

The Session Planner does not design helpers, theorem statements, proof bodies, or external premise use.

### 4.3 Session Runner

**Code:** `regen/proof_session_runner.py`

**Purpose:** Bind an environment campaign and Session Planner schedule into one proof run, then orchestrate one theory at a time.

**Persistent state:**

- `run.json`: environment, schedule, database, target and active budgets;
- `files.json`: mutable status of all scheduled theories;
- per-theory graph/proof/check artifacts;
- generated source directory;
- generated premise publication file.

**Commands:**

```text
init       bind environment and schedule to a run
plan-file  invoke File Planner for one theory
run-file   execute the graph/prove/revise/assemble loop for one theory
```

The current runner does not yet expose a single `run-session` command that automatically traverses all 21 entries. Full traversal is performed by repeatedly invoking `run-file` according to the bound schedule.

The active run budget is:

| Budget | Value |
|---|---:|
| Whole-proof candidates per node round | 5 |
| Prover rounds per node | 10 |
| Whole-proof attempt ceiling per node | 50 |
| Structural revisions per node | 16 |
| Stepwise round timeout | 1,800 seconds |
| One Stepwise think call timeout | 180 seconds |

### 4.4 File Planner

**Code:** `regen/file_planner.py`

**Purpose:** Design an initial generated theorem DAG for one complete Proof theory.

**Inputs:**

- the file's public package containing all fixed contracts;
- legal declarations needed to understand the contract vocabulary;
- graph/node budgets and output schema;
- deterministic diagnostics from a rejected prior planning attempt.

The bounded declaration context is explanatory context, not a preselected external-premise plan.

**Outputs:**

- fixed contract root nodes;
- generated theorem/obligation nodes;
- generated helper leaves;
- exact statement, role, generated dependencies, consumers, rationale and rough strategy for every generated node;
- no proof bodies;
- no external premise nodes.

The planner may produce a direct contract proof with no helper, a contract supported by theorem nodes, or a deeper theorem/helper decomposition. A helper is a generated leaf: it has no generated theorem children, but it may use legal external premises when proved.

The File Planner can retry after deterministic schema/quality rejection, but it is not rerun after every proof failure. Runtime structural changes belong to the Graph Revision Agent.

### 4.5 Proof Graph and deterministic checks

**Code:** `regen/proof_graph.py`

**Purpose:** Define the generated-DAG schema and reject invalid graph structures before proof generation.

**Checks include:**

- fixed contract statement and coverage;
- unique node IDs and Isabelle names;
- roles limited to contract/theorem/helper;
- no proof body or external-premise payload in planner nodes;
- all generated dependencies exist;
- graph acyclicity and executable leaf-to-root order;
- helper-leaf discipline;
- generated nodes have real consumers;
- duplicate statements and cosmetic wrappers;
- graph/node budgets.

Every accepted structural change creates a new immutable `graph-vN.json` and corresponding check artifact. Many graph versions therefore mean the theorem architecture evolved during proof repair; they are not File Planner reruns unless the log explicitly shows a new planner call.

There is no standalone module named M6 in the modular architecture. Statement certification is an Isabelle Checker operation performed on a graph artifact.

### 4.6 Premise Retrieval

**Code:** `regen/premise_retrieval.py`

**Purpose:** Retrieve legal existing facts for the current node and current residual.

**Sources:**

- M3 SQLite database;
- previously published generated declarations;
- already GREEN dependencies in the current theory.

**Timing:**

- before the first proof attempt, using the node statement and strategy;
- again after meaningful Isabelle residuals expose missing vocabulary or subgoals;
- after a Graph Revision Agent decision requests another retrieval pass.

**Outputs:** ranked premise records with names, statements, owners, attributes and provenance.

Retrieval is advisory. A fact returned by retrieval is not automatically a dependency, and a fact not returned is not forbidden if it is otherwise present in the legal Isabelle context. Actual acceptance and dependency auditing remain Isabelle's job.

The Premise Database remains necessary even when the model emits `simp add`, `simp only`, `wp`, or `auto`: it supplies named, provenance-checked candidates and definitions, while Isabelle executes automation and determines the resulting proof state.

### 4.7 Lemma Prover

**Code:** `regen/lemma_prover.py`

**Purpose:** Prove exactly one executable graph node without changing its statement or graph.

**Inputs:**

- exact certified statement;
- GREEN generated dependencies;
- retrieved legal premises;
- rough strategy;
- prior candidates and exact Isabelle diagnostics;
- node/round budgets.

**Outputs on success:**

- Isabelle-accepted proof body;
- candidate/check logs;
- actual accepted source for the node.

**Outputs on failure:**

- structured RED result;
- exact residual/error and failure class;
- candidate history, retrieval history and best available frontier;
- evidence for salvage or graph revision.

The current order is:

```text
Stepwise-Codex search
    -> if not closed, structured whole-proof candidates
    -> Isabelle checks every candidate
    -> residual feeds the next candidate and retrieval
    -> optional local proof-outline/salvage guidance
```

The Lemma Prover may alter only the proof body. It cannot add a helper, modify a statement, change edges, or publish a red result.

### 4.8 Stepwise-Codex backend

**Code:**

- `regen/stepwise_lemma_backend.py`
- `regen/stepwise_codex.py`
- vendored/reproduced Stepwise controller and IsaREPL integration used by the backend.

**Purpose:** Search locally from actual Isabelle proof states instead of asking the model to invent an entire long proof without feedback.

**Execution model:**

```text
exact theorem + verified prefix + legal context
        |
        v
Codex proposes one or more next proof steps
        |
        v
IsaREPL executes each candidate in Isabelle
        |
        +-- success state -> add branch to search tree
        |
        +-- error/residual -> retain feedback, try another branch
```

The model does not simulate the simplifier or WP engine. If it proposes `simp`, `simp add: ...`, `wp`, or another method, Isabelle computes the actual next state. This is the central benefit of Stepwise search.

The backend supports both fresh theorem generation and continuation from a checker-verified prefix/residual. The continuation path is especially important for long proofs: it preserves already verified work and searches only the unresolved suffix, without removing the original whole-proof ability.

Current search settings include a bounded tree width/depth, up to ten tree attempts within a node round, a 180-second timeout for one model `think`, and a 1,800-second round timeout. Every state request and provider response is logged while the node is active so search can resume and be diagnosed. After the node or theory reaches a terminal state, the retention policy in Section 4.13 compacts these records.

### 4.9 Proof Outline / Salvage

**Code:** `regen/proof_outline_salvage.py`

**Purpose:** Convert repeated RED proof evidence into a bounded local proof-body outline before escalating to graph surgery.

**Inputs:**

- fixed node statement;
- failed candidate proofs;
- exact Isabelle residuals;
- retrieved premise names;
- best Stepwise prefix/frontier.

**Output:** a validated local outline using operations such as `assume`, `obtain`, `have`, `cases`, `show`, and bounded automation suggestions.

The outline is not a proof and cannot add theorem nodes. It is fed back into the Lemma Prover as repair guidance. This layer handles failures caused by ordering, instantiation, missing case splits, or a nearly complete long proof without unnecessarily expanding the theorem graph.

### 4.10 Graph Revision Agent

**Code:** `regen/graph_revision_agent.py`

**Purpose:** Decide whether persistent RED evidence requires a theorem-graph change.

**Inputs:**

- current graph and graph hash/version;
- failed node statement, role, dependencies and consumers;
- all attempts and exact Isabelle residuals;
- premise retrieval results and duplicate/no-match evidence;
- proof frontier and remaining budgets;
- prior graph-revision history.

**Decisions:**

- `RETRY_PROOF`: graph is adequate; retry with better proof/retrieval guidance;
- `REVISE_GRAPH`: apply explicit graph operations;
- `ABANDON_GENERATION`: evidence and budgets do not justify another attempt.

**Supported structural operations:** add/delete/split generated nodes, change a generated node role or statement, and change generated dependency edges.

**Hard constraints:**

- fixed contracts cannot change;
- already GREEN declarations cannot be silently rewritten;
- parent guards cannot be dropped;
- revisions cannot create cycles, unused nodes, duplicate facts, or budget overflow;
- all new/changed statements return to deterministic graph and Isabelle shell checks.

Each call initially records the complete request, model transcript, decision, rationale, proposed transaction, deterministic approval/rejection, new graph version and invalidation set. Applied graph changes retain this causal evidence permanently; rejected or retry-only calls may be compacted after their relevant node reaches a terminal state. This evidence is intended to improve later File Planner prompts and explain why the graph grew.

### 4.11 Isabelle Checker

**Code:** `regen/isabelle_node_checker.py`

**Purpose:** Be the single semantic authority for generated statements, proof bodies, assembled theories and generated-session builds.

**Checks:**

1. **Graph statement check:** declare graph nodes with proof holes in an isolated shell to validate syntax, types, names and context.
2. **Node check:** build the target node with its generated dependency closure and candidate proof.
3. **Complete theory check:** assemble all declarations in final order and check the full `.thy` file.
4. **Generated session build:** build `Generated_Refine` with previously accepted generated theories.

The checker runs Isabelle in the M2 environment with networking disabled and only approved mounts/heaps. It emits heartbeats, enforces timeouts, and cleans up timed-out processes.

An optional audit path snapshots the active `simp`/`wp` context and uses Isabelle `Thm_Deps` to extract dependencies of the accepted theorem and detect skipped proofs. This code exists and has been tested on generated examples, but it is not yet a mandatory gate on every Stepwise GREEN leaf or final theory publication.

### 4.12 Theory assembly and generated premise publication

**Current implementation:** `regen/proof_session_runner.py` together with `regen/isabelle_node_checker.py`.

After all nodes are GREEN, the runner:

1. renders the complete generated theory;
2. runs complete-theory checking;
3. may stabilize proof bodies when the assembled automation context differs from isolated node checks;
4. writes `generated/<Theory>.thy`;
5. builds the shared `Generated_Refine` session;
6. publishes generated declaration records to `generated-premises.json`;
7. changes the theory status to `built`.

The current import implementation is intentionally simple: built theories are linearized through the most recently built generated theory, giving later files transitive access to earlier generated files. Dependency-derived candidate imports and final import minimization from the target protocol are not implemented yet.

Likewise, dynamic rebuilding/unlocking of Support theories that depend on newly generated Proof theories is represented in planning evidence but is not yet integrated into the runtime publication loop.

### 4.13 Logs and observability

**Code:**

- `regen/module_run_log.py`
- `regen/llm_transcript.py`
- `regen/progress_events.py`

The logging design has two goals that must not be confused:

1. keep enough active-run detail to resume, repair and diagnose generation;
2. retain a small causal record explaining what was planned, what changed, what Isabelle accepted and why the final theory was published.

It does **not** treat every intermediate prompt, search state or checker source as permanent experiment evidence. A provider interruption, overwritten development artifact or repaired harness bug is tolerated. Such a call is marked `interrupted`, `superseded` or `corrupt` and cannot be selected as the evidence for a final GREEN proof or applied graph transaction, but it does not invalidate later healthy work.

#### Log classes

- **Control artifacts:** campaign rules, contracts, environment gates, premise snapshots, schedule and progress ledger. These define the run and are always retained.
- **Planning and decision evidence:** the accepted File Planner output and every applied Graph Revision transaction. These explain theorem architecture and are always retained.
- **Semantic evidence:** selected GREEN proofs, action-triggering RED residuals and final Isabelle checks. These explain correctness and failure routing and are always retained.
- **Recovery data:** current candidates, Stepwise states, repeated retrieval results and temporary checker sources. These are retained only while they can help an active RED node.
- **Observability data:** heartbeats, provider chunks, repeated status events and verbose command output. These may be rolled up as soon as the operation terminates.

#### Module retention matrix

| Module | Produced while running | Retained after node/theory completion | Removed or compacted |
|---|---|---|---|
| Environment Preparation M0-M3 | manifests, extraction audits, M2 denial/elaboration results, premise database and snapshot report | final M0-M3 artifacts and gate summaries | warm-build chatter, duplicate verifier output and failed temporary workspaces after a later gate passes |
| Session Planner | scheduling evidence, model transcript, proposed/accepted edges, file list and ledger | accepted edge evidence, final layered file list, planner final response and ledger | rejected duplicate proposals, provider stream chunks and repeated validation dumps |
| Session Runner | progress events, status transitions and recovery checkpoints | final per-file statuses, selected graph/proof references, theory/session summary | repeated heartbeats and superseded checkpoints |
| File Planner | exact input, model transcript, proposed graph and deterministic checks | one accepted initial plan, its exact statements, accepted graph and concise rejection summary | rejected full graph copies and provider stream chunks once an accepted plan exists |
| Premise Retrieval | query, ranked records and provenance for each attempt | premises actually used by the selected proof, retrieval statistics, and the last no-match/result that caused graph revision | repeated full ranked lists and duplicate premise payloads |
| Lemma Prover | prompts, candidates, feedback, residuals and round state | selected GREEN proof and check; final useful RED residual before success or revision; attempt/retrieval summary | older failed candidates, duplicated prompts, temporary checker theories and repeated residuals |
| Stepwise-Codex | proof-state tree, state requests, branches, model replies and timeouts | winning verified path when Stepwise closes the node; otherwise a compact search/frontier summary used by the winning repair or revision | losing branch transcripts, per-state dumps and duplicate context/premise payloads |
| Proof Outline / Salvage | failed-proof digest, proposed outline and validation | only an outline that influenced the next accepted proof or an applied graph revision, plus an aggregate result | rejected/no-op outlines and their full transcripts |
| Graph Revision Agent | request, relevant RED evidence, transcript, decision and proposed transaction | every applied transaction with rationale, triggering residual, deterministic approval, invalidation set and graph delta; final abandon decision | retry-only/rejected full payloads after a concise decision summary is recorded |
| Isabelle Checker | commands, temporary theory, stdout/stderr, residual and classification | check for each selected GREEN proof; graph-shell failure that caused revision; final complete-theory and generated-session build results | checks for discarded candidates, duplicate successful checks and temporary checker sources |
| Theory assembly/publication | assembly candidates, stabilization attempts, generated source, build and premise publication | final `.thy`, final graph/dependency map, any proof changed during stabilization with its check, complete build, publication summary and theory status | failed assembly candidates and intermediate rendered sources after final build |
| Refine/CRefine session seal | clean-build output, coverage, dependency/provenance and leakage audits | final generated sources, heap identity, coverage/audit report, final premise snapshot and partial/final campaign report | per-file debug material already represented by retained theory bundles |

The table defines **logical retention**, not a requirement to preserve duplicate physical copies. Large artifacts have one canonical owner. Module-oriented logs are lightweight indexes that point to those artifacts rather than embedding their contents.

Module indexes are kept under directories such as:

```text
environment-preparation/
session-planner/
file-planner/
lemma-prover/
proof-outline-salvage/
graph-revision-agent/
```

Each index entry records the module, logical operation, status, compact result and references to canonical artifacts. The canonical provider transcript is created before the external call and ends in one of five states:

```text
active       call is still owned by a live operation
complete     call ended with a usable terminal record
interrupted  operation ended before a terminal provider record
superseded   a later physical call replaced this logical attempt
corrupt      referenced artifacts are missing or inconsistent
```

Retries allocate a new physical call identifier. They do not rewrite the previous call in place. Only a `complete` call may be selected as the model evidence for a final proof or applied graph transaction.

#### Compaction points

Logging is compacted at three deterministic lifecycle points:

1. **Node GREEN:** keep the selected proof/check, premise-use summary, the final useful RED residual and causally relevant planner/revision records. Remove losing proof-search detail.
2. **Theory built:** keep the initial and final graph, applied graph deltas, final proof for every node, final generated source, complete-theory/session checks and publication summary. Remove redundant full graph snapshots and node recovery material.
3. **Session sealed or reported partial:** keep final theory bundles, session schedule/progress, coverage, generated premise snapshot, clean-build/audit evidence and failure summaries. Remove observability and debug material already represented by those bundles.

Compaction writes a small retention summary containing what category was removed and why. It must never remove the selected winning call, an applied graph transaction, the residual that justified that transaction, a final generated source, or an Isabelle result used to mark a node/theory/session successful.

The minimum durable bundle for one built theory is therefore:

```text
accepted initial plan
initial graph + final graph + applied graph deltas
final proof and selected Isabelle check for every GREEN node
causally relevant RED residuals and revision decisions
premise-use/retrieval summary
final generated .thy
complete-theory check + Generated_Refine build result
generated-premise publication summary
compact module index + retention summary
```

Raw provider NDJSON is permanent only for the accepted File Planner call, a selected GREEN proof call, and an applied Graph Revision decision. For all other calls it is active-run recovery data and may be deleted after its compact terminal summary is produced.

The runner exposes progress events so a delegated Codex call is visible as an active module operation rather than an unexplained frontend `think` pause. Heartbeats are operational signals, not durable proof evidence, and are summarized by start/end time, timeout and terminal status.

## 5. Complete repo-level generation flow

The following is the full intended flow, annotated with current implementation status.

### Phase A: Prepare the legal environment

```text
M0 freeze campaign                         IMPLEMENTED, PASS
  -> M1 extract contracts/classify files   IMPLEMENTED, PASS
  -> M2 isolate and elaborate              IMPLEMENTED, PASS
  -> M3 export legal premises              IMPLEMENTED, PASS
```

Result: a clean Isabelle environment, public packages for 21 Refine Proof theories, and an initial legal premise database.

### Phase B: Plan the Refine session

```text
Session Planner
  -> candidate dependency evidence
  -> deterministic audit
  -> immutable 3-layer/21-file schedule
  -> mutable session ledger
```

Status: implemented and bound to `refine-main`.

### Phase C: Generate one Proof theory

For each scheduled file:

```text
1. Load all fixed contracts for the file.
2. File Planner emits an exact generated theorem DAG.
3. Deterministic graph checks reject malformed architecture.
4. Isabelle checks all declaration shells.
5. Select an executable node whose generated dependencies are GREEN.
6. Retrieve legal premises for that node.
7. Lemma Prover runs Stepwise and whole-proof repair.
8. Isabelle accepts the proof or returns an exact residual.
9. On local RED, retry/retrieve/salvage.
10. On structural RED, ask Graph Revision Agent.
11. Recheck every revised statement and graph.
12. Repeat until all fixed contract roots are GREEN or the file is abandoned.
13. Assemble and check the complete theory.
14. Build it in Generated_Refine.
15. Publish generated facts and update the file ledger.
```

Status: this loop has been exercised end to end on `KHeap_R`. The remaining 20 theories have not yet been run in the current campaign.

### Phase D: Continue across Refine files

After one theory is built, later theory generation may retrieve its 122 currently published generated declarations. The runner then moves to the next pending entry according to the schedule/serial fallback.

If a file fails, the target protocol allows recording the failure and continuing with files that do not require it. The current runner has per-file commands and persistent status, but whole-list automatic traversal and dependency-aware skip/retry policy still need a thin session-level driver.

### Phase E: Seal generated Refine

After all 21 Proof theories succeed, the target flow requires:

- a clean build from frozen parents, Support, and generated Proof theories only;
- exact-once coverage of all 287 contracts;
- explicit and automation dependency/provenance audit;
- no `sorry`, `oops`, new axioms, forbidden Proof source/fact, or stock target heap;
- complete transcript/artifact hashes;
- one immutable generated Refine heap and session seal.

Status: not implemented as a complete M11/session-sealing command. A single theory being `built` must not be reported as a sealed Refine session.

### Phase F: Generate CRefine

The target flow then repeats environment extraction/planning/generation for CRefine, using the exact generated Refine heap as its legal target layer. The stock human Refine heap cannot be substituted.

Status: not started. Existing modules are Refine-oriented and require explicit CRefine adaptation and a completed generated Refine input.

## 6. Failure routing

The harness distinguishes proof mistakes from graph mistakes using Isabelle evidence.

```text
Syntax/type/name error in statement
    -> graph/statement certification; planner or revision must fix statement

Proof candidate parser/tactic error
    -> Lemma Prover retries using exact diagnostic

Proof makes progress but leaves subgoals
    -> Stepwise continuation, residual retrieval, or salvage outline

Repeated failure with stable missing intermediate fact
    -> Graph Revision Agent may add/split a generated node

New helper itself needs generated children
    -> promote/change role or split through an approved graph transaction

Budget exhausted with no justified new structure
    -> ABANDON_GENERATION and preserve RED evidence

All nodes GREEN but complete file fails
    -> assembly/context stabilization; do not reopen graph unless evidence
       shows a genuine statement/dependency problem
```

A fixed retry count alone does not decide that a lemma is intrinsically too hard. The decision uses residual stability, proof progress, retrieval results, salvage frontier, repeated failure class and remaining structural budget.

## 7. Premises, automation, and reproducibility

There are three related but different mechanisms:

1. **Premise retrieval** finds named legal declarations that may help.
2. **Explicit proof methods** such as `rule X`, `simp add: X`, or `wp add: X` name selected facts in the proof.
3. **Automation pools** let `simp`, `wp`, or `auto` use facts registered in the current Isabelle context without listing every name in source text.

Stepwise delegates the execution of all three to Isabelle. It should not emulate simplification or WP internally. This makes search feedback accurate, but it creates a provenance requirement: a successful source-level scan does not reveal every implicit automation dependency.

Current policy:

- generated facts are not automatically registered globally as `[simp]` or `[wp]`;
- prefer explicit `simp add`, `simp only`, or local `wp` additions where stability matters;
- allow ordinary automation when Isabelle state feedback shows it is useful;
- keep the Premise Database for retrieval and source legality;
- use the optional context/`Thm_Deps` audit to develop mandatory accepted-proof dependency extraction.

The future Automation Interface Pass described in `MODULAR-PROOF-PIPELINE.md` would decide whether a generated theorem should become a stable shared automation rule after proof success. It is documented but not implemented.

## 8. Current run artifacts

Important current paths:

```text
Environment campaign:
  reports/regen/refine-setting-b/
    sel4-refine-setting-b-c55be4ed1c2f8c1b/

M3 premise database:
  .../M3/premises.sqlite3

M4 schedule:
  .../M4/file-generation-list.json

Proof run:
  reports/regen/proof-runs/refine-main/

Session state:
  reports/regen/proof-runs/refine-main/run.json
  reports/regen/proof-runs/refine-main/files.json

KHeap_R generation:
  reports/regen/proof-runs/refine-main/theories/KHeap_R/

Generated source:
  reports/regen/proof-runs/refine-main/generated/KHeap_R.thy

Published generated facts:
  reports/regen/proof-runs/refine-main/generated-premises.json
```

Current file status:

```text
built:  KHeap_R
pending: Bits_R, ArchAcc_R, CSpace1_R, Refine, Tcb_R, TcbAcc_R,
         Finalise_R, Detype_R, InterruptAcc_R, Interrupt_R,
         CNodeInv_R, IpcCancel_R, Retype_R, Schedule_R, VSpace_R,
         Arch_R, CSpace_R, Ipc_R, Untyped_R, Syscall_R
```

## 9. Known gaps and deferred improvements

The following must remain explicit when reporting results:

1. Only `KHeap_R` has completed the modular generation loop in this campaign.
2. `built` is not yet the strict immutable theory seal from the full protocol.
3. There is no one-command automatic traversal of the 21-file schedule.
4. Imports currently form a simple generated chain; actual-dependency import synthesis and minimization are pending.
5. Runtime Support unlock/build/publication is not integrated.
6. Optional `simp`/`wp` context and proof-term dependency extraction is not yet a mandatory publication gate.
7. A generated theorem automation-interface decision pass is documented but not implemented.
8. A post-file Graph Abstraction Pass for discovering cross-contract reusable helpers is documented but intentionally deferred.
9. Full generated Refine clean seal/heap creation is not implemented.
10. CRefine generation has not started.

These gaps do not invalidate the tested File Planner/Lemma Prover/Graph Revision/Isabelle loop. They define the remaining work needed to turn one built Proof theory into a complete repo-level Refine/CRefine result.

## 10. Minimal operator workflow

The current proof run is already initialized. The practical continuation is:

```bash
# Inspect bound environment, schedule and budgets
python3 -m json.tool reports/regen/proof-runs/refine-main/run.json

# Inspect theory statuses
python3 -m json.tool reports/regen/proof-runs/refine-main/files.json

# Plan one pending theory
python3 regen/proof_session_runner.py plan-file \
  --run-dir reports/regen/proof-runs/refine-main \
  --theory Bits_R

# Execute its current graph
python3 regen/proof_session_runner.py run-file \
  --run-dir reports/regen/proof-runs/refine-main \
  --theory Bits_R \
  --prover-backend stepwise
```

External Codex calls require the normal explicit provider authorization used by this repository. All planner, prover and revision calls must produce a recoverable running record and a terminal summary; only the causally relevant calls defined in Section 4.13 remain as full durable transcripts.

## 11. Authoritative design and implementation references

- `regen/MODULAR-PROOF-PIPELINE.md`: modular architecture and lessons from KHeap_R.
- `regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md`: Setting B experiment protocol and milestone boundaries.
- `regen/refine_campaign.py`: M0.
- `regen/refine_contracts.py`: M1.
- `regen/refine_m2_preflight.py`, `regen/refine_m2_isolated.py`: M2.
- `regen/refine_m3_premises.py`: M3.
- `regen/refine_m4_session.py`: Session Planner.
- `regen/proof_session_runner.py`: orchestration and persistence.
- `regen/file_planner.py`, `regen/proof_graph.py`: file-local theorem architecture.
- `regen/premise_retrieval.py`: legal fact retrieval.
- `regen/lemma_prover.py`: node proof lifecycle.
- `regen/stepwise_lemma_backend.py`, `regen/stepwise_codex.py`: state-guided proof search.
- `regen/proof_outline_salvage.py`: local repair outline.
- `regen/graph_revision_agent.py`: controlled graph transactions.
- `regen/isabelle_node_checker.py`: semantic checking and build authority.

In one sentence, the current harness is:

> A clean-environment, session-scheduled, file-planned and Isabelle-checked proof-generation pipeline in which Codex designs a generated theorem graph, proves one fixed node at a time with state feedback, requests audited graph revisions when local repair is insufficient, and publishes successful generated facts across files; it has completed KHeap_R but not yet the full Refine or CRefine corpus.
