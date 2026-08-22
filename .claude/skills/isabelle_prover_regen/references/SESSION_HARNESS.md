# Session-Staged Repo-Level Proof Regeneration Protocol

This document defines what the harness must do and the data passed between its
parts. It deliberately does not prescribe scripts, ranking algorithms, prompt
wording, or Isabelle process management. Those implementation decisions and
their history belong in `regen/HARNESS-EVOLUTION.md`.

## 0. Status and restart entry point

This protocol is normative; implementation remains incomplete. The operational
sequence, current code inventory, and next milestone are maintained in
`regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md`. A fresh conversation must
read that plan after this protocol and must not treat historical Schedule_R,
support-only session-DAG, or ArchAcc_R runs as an active Setting B generation.

The currently reproduced Stepwise-Codex backend is an M7 development backend,
not a repo planner. It proved a statement in a HOL smoke run, but the current
relay returns only the final answer and does not expose model tool events. It is
therefore non-sealable and must not receive human Proof source in a production
Refine/CRefine run. Details are in `regen/STEPWISE-CODEX.md`.

### Contents

1. Objective and claim
2. Two settings
3. Trusted and forbidden inputs
4. Main terms
5. Division of responsibility
6. Generated-theorem DAG
7. Premise Database
8. Graph Revision Agent
9. M0-M12 stages and artifacts
10. Optional single-theorem backend
11. Completion and evaluation
12. Deferred work

## 1. Objective and claim

The experiment fixes adjacent executable artifacts and their refinement
contracts, removes the human proof implementation, and asks models to regenerate
the proof corpus that makes every contract check.

Run the campaign in two stages:

1. regenerate Refine between the abstract specification and Haskell design;
2. seal generated Refine, then regenerate CRefine between that Haskell design
   and C against the generated Refine result.

The current experiment is repo-level because it generates complete Proof
theories, permits generated theorems to be called across generated files, and
integrates two complete Isabelle sessions. A single theorem remains an execution
unit, not the benchmark boundary.

The claim is limited. Support theories and the initial division of contracts
among Proof-theory file slots remain fixed. The experiment therefore regenerates
the contract-proof layer and its generated cross-file architecture; it does not
claim to regenerate every declaration or the complete module partition.

## 2. Two settings

Keep the settings separate in every manifest and result.

### Setting A: generated repository architecture

The model receives all fixed contracts and external semantics but not the human
target-session file graph. It creates theory files, contract ownership, imports,
generated theorem statements, and proofs. This is the stronger deferred setting.

### Setting B: generated Proof layer with frozen Support

This is the current setting. A deterministic classifier creates two classes:

- **Proof theory**: a file slot that owns at least one selected corres/connect
  contract;
- **Support theory**: a theory with no selected contract.

Support theories are retained unchanged as trusted library input. For each Proof
theory, retain only its file identity and all contracts assigned to that slot.
Each contract also retains its minimal contract-owned elaboration context: the
enclosing `context ... begin` opener(s) required for the source text to denote
the same Isabelle proposition, such as a locale interpretation. These openers
are part of contract identity, not a proof plan. They are re-created around that
contract alone and closed immediately afterwards.
Do not retain its human imports, body, helper names, helper statements,
declaration order, attributes other than contract-owned attributes, proof code,
consumer graph, or export plan.

The model creates the Proof theory's imports, generated theorem graph,
declaration order, attributes, and proofs. A generated import may point only to
frozen environment theories or previously sealed generated theories. This rule
makes generated cross-file dependencies acyclic without copying the human Proof
theory graph.

## 3. Trusted inputs and forbidden inputs

### Trusted inputs

- fixed abstract/Haskell/C endpoint definitions for the selected stage;
- fixed parent sessions and unchanged Support theories;
- exact selected corres/connect statements;
- contract-owned attributes such as `[corres]` when they are part of the fixed
  declaration;
- minimal enclosing contract-owned context openers needed to elaborate the same
  proposition; these may not contain `notes`, `declare`, helper declarations,
  methods, proof commands, or imports;
- declarations published by a sealed generated Proof theory;
- checker output produced by this generation.

### Forbidden model and retrieval inputs

- original human Proof-theory imports or body;
- human helper names, statements, roles, order, dependencies, or proofs;
- human explanations of which helper a contract needs;
- human downstream consumers, use graph, interface inventory, or export plan;
- facts exported only by the human version of a Proof theory;
- unsealed, red, abandoned, future, or sibling-generation facts;
- stock human Refine/CRefine heaps when a generated heap is required.

The human corpus may be read after the generated result is frozen for evaluation
only. It must never repair or guide a production generation.

## 4. Meaning of the main terms

- **contract**: a fixed theorem statement, normally corres/ccorres/connect, whose
  success is part of the benchmark result;
- **obligation theorem**: a model-created theorem that performs a meaningful
  proof step needed by one or more contracts;
- **helper**: a model-created, independently useful or encapsulated theorem used
  inside obligation theorems or contracts;
- **premise**: an already checked theorem or declaration that a new proof may
  call; premises are inputs to proof generation, not nodes to generate;
- **green**: accepted by Isabelle in the exact current context;
- **sealed theory**: a complete generated theory whose contracts, imports,
  exports, provenance, and build result are frozen for downstream use;
- **residual**: the goal and diagnostic left by Isabelle after a failed proof;
- **generation ID**: the immutable identity of one complete attempt to generate
  one Proof theory, including every planning, proving, revision, checking, and
  sealing call.

## 5. Division of responsibility

### Session Planner

The Session Planner coordinates the repository campaign. For fixed Proof slots
and fixed contract ownership, it creates one evidence-grounded layered
Proof-theory generation list and tracks each entry. It does not design theorem
statements or proof decompositions. M4 may record the M3 snapshot identity, but
it does not inspect premise records, retrieval scores, or theorem popularity to
choose the order.

It owns:

- Refine-before-CRefine staging;
- Proof/Support classification and fixed contract ownership;
- candidate priority-evidence audit, the layered file-generation list, and its
  immutable list hash;
- per-file pending, generating, sealed, failed, skipped, and retry state;
- publication state of initially available frozen Support and of frozen Support
  that becomes buildable only after named generated Proof slots seal;
- current sealed-theory inventory and generated heap identity;
- acceptance of generated cross-file imports and prevention of cycles;
- session completion and publication of sealed theories.

A failed file does not stop list traversal. Record the failure and continue with
the next pending file. A later file may still succeed using the frozen
environment and whatever generated theories have actually sealed. If it needs a
fact that would have come from a failed file, report that later as a
proof/premise-availability failure, not as a scheduling deadlock.

### File Planner

The File Planner designs one complete Proof theory from its contracts. It emits
only a graph of theorems that must be generated. It does not emit proof bodies
and does not put existing premises into that graph.

It owns:

- grouping all contracts in the file;
- deciding whether a contract is direct or needs obligation theorems/helpers;
- exact statements and roles of generated theorem nodes;
- dependency and consumer edges among generated nodes;
- a rough proof purpose or strategy for each generated node.

### Premise Database

The Premise Database is the searchable record of facts that have already been
checked and are eligible for reuse. It answers retrieval queries and records
provenance. It does not plan the theorem graph, invent new facts, add imports by
itself, or decide that a proof is correct.

### Lemma Prover

The Lemma Prover attempts one certified theorem node. It can generate and repair
a proof body using retrieved premises and Isabelle feedback. It cannot create or
change theorem statements, append graph nodes, or seal a file.

### Graph Revision Agent

The Graph Revision Agent judges whether machine evidence justifies changing the
current generated-theorem graph. It is independent of the File Planner and
Lemma Prover. It can approve a proposed graph change, request another proof or
retrieval attempt, or reject the proposal. It cannot write a proof or commit a
graph change directly.

### Deterministic checker

The checker is the final authority. It validates contract identity, statement
syntax/types, graph structure, premise provenance, generated imports, proof
dependencies, complete theory builds, and session builds. An LLM approval never
overrides a failed deterministic check.

## 6. Generated-theorem DAG

The File Planner graph contains only theorems that this campaign must generate:

```text
contract C1
|-- obligation theorem T1
|   |-- helper H1
|   `-- helper H2
`-- obligation theorem T2

contract C2
`-- obligation theorem T3
    `-- helper H1
```

An edge from an upper node to a lower node means the upper proof may call the
lower theorem. Execution starts at leaves and moves upward. Existing premises
are attached to proof-attempt records, not represented as graph leaves.

All three roles are Isabelle lemmas/theorems syntactically. Their distinction is
the harness meaning:

- contract statements are fixed and must not change;
- obligation theorem statements are generated and may compose several lower
  facts;
- helpers are generated leaves by default and may call only already available
  premises, not newly spawned theorem children.

A contract may be proved directly, may call helpers directly, or may call one or
more obligation theorems. Do not force wrapper nodes merely to make the graph
look deep. A helper can serve several obligation theorems or contracts.

The initial graph is a complete theorem-level hypothesis, not a prediction of
every tactic or every external premise that the proof will use. Actual premise
use is discovered during proof generation and audited after checking.

If a helper is too hard, the Lemma Prover cannot recursively add subhelpers. It
must ask the Graph Revision Agent. The normal repair is to replace/split it into
smaller sibling helper leaves and reconnect its consumer. If the original
statement is genuinely required as a composition boundary, the Agent may approve
promoting it to an obligation theorem with new helper leaves.

## 7. Premise Database contract

### Records

Each record contains at least:

- canonical declaration ID and name;
- exact statement and normalized statement hash;
- owner theory and session;
- source class: frozen parent, frozen Support, sealed generated Proof, or current
  local green node;
- Isabelle attributes such as `[simp]`, `[wp]`, or `[corres]`;
- defining equations where the declaration is a definition;
- source/heap/generation provenance and publication state.

Source location is an audit field. It is not shown to the model when it points
into a forbidden human Proof file.

### Publication states

The database exposes four logically separate stores:

1. frozen parent-session declarations;
2. frozen Support-theory declarations;
3. persistent declarations from sealed generated Proof theories;
4. temporary green declarations from the current generated Proof theory.

Red statements, planned nodes, abandoned generations, and original facts from
classified contract-owning human Proof theories are never callable records.
Human-written facts in frozen Support theories remain legal Setting B inputs.

### Retrieval timing

Retrieval occurs at three distinct times:

1. **contract elaboration**: resolve types, constants, syntax, locales, and
   definitions needed to understand the fixed statement; this is environment
   resolution, not theorem proof planning;
2. **node proof start**: search all currently eligible records and send only a
   bounded relevant subset to the Lemma Prover;
3. **after failure**: query again with the exact Isabelle residual, failed proof,
   and unresolved names before proposing a new theorem node.

The File Planner may query the database for environmental understanding, but its
output must not hard-code uncertain premise nodes or premise dependency edges.

### Search versus import

Finding a premise does not automatically make it callable. A new file may search
all previously sealed generated Proof theories that are legal under the fixed
generation order, even though they are not yet its ancestors. For a premise
owned by a frozen or sealed theory, the owner first becomes a candidate import.
The Session Planner checks its seal, source, list position, and cycle safety;
only an accepted candidate enters the temporary proof context and becomes an
actual ancestor. Local green nodes need no import.

Do not import every theory in the database. That would load large theorem and
`simp`/`wp` pools, create namespace collisions and hidden dependencies, and make
the generated architecture meaningless. M7 checks a proof with its approved
candidate imports and records the dependencies actually used. M9 removes unused
candidates, minimizes the final imports, and clean-checks the complete theory.

### Explicit and implicit use

`rule X`, `simp add: X`, and `wp X` explicitly name a premise. Bare `simp`, `wp`,
or `auto` may use imported declarations registered in attribute pools without
naming them in the proof text. Retrieval assists the model before it writes a
proof; Isabelle automation selects facts while executing that proof. These are
different stages.

The final dependency record must therefore distinguish explicit use from
implicit `simp`, `wp`, `corres`, or other automation use. A successful source
text scan alone is insufficient provenance evidence.

## 8. Graph Revision Agent contract

### Request input

Every request is bound to one generation and graph hash and contains:

- the red node's exact statement, role, dependencies, and consumers;
- all proof attempts made for that node;
- machine-checked residuals, failure locations, and best proof frontier;
- premise queries, returned records, and no-match/duplicate evidence;
- the proposed graph operation and exact new/replacement statements;
- remaining node, revision, attempt, and wall-time budgets.

The Agent receives no human proof or human decomposition metadata.

### Allowed decisions

The implemented API groups the detailed actions below into three top-level
decisions:

- `RETRY_PROOF`: keep the DAG and optionally refine retrieval/repair guidance;
- `REVISE_GRAPH`: apply one checked `ADD`, `DELETE`, `SPLIT`,
  `CHANGE_ROLE`, `REPLACE_STATEMENT`, or `CHANGE_EDGE` transaction;
- `ABANDON_GENERATION`: stop this file generation.

Each answer must classify the RED evidence. Proof-body errors, premise-retrieval
gaps, search exhaustion, and tool/timeouts can only authorize `RETRY_PROOF`.
A graph transaction requires concrete residual evidence for a
statement/consumer mismatch, an overloaded node, a missing generated
dependency, or a duplicate generated node. Resource exhaustion or an unsound
generation may authorize abandonment.

- `KEEP_AND_REPAIR_PROOF`: the statement/graph is adequate;
- `RETRIEVE_AGAIN`: evidence suggests a missing existing premise;
- `ADD_OBLIGATION`: add a model-generated intermediate theorem;
- `ADD_HELPER_LEAF`: add one independently provable leaf;
- `SPLIT_NODE`: replace an overloaded node with smaller nodes;
- `CHANGE_ROLE`: normally promote a helper to an obligation theorem;
- `REPLACE_STATEMENT`: fix a generated statement that omitted a required guard
  or does not match its consumer;
- `REUSE_EXISTING`: reject a duplicate generated statement and use a premise;
- `REJECT_CHANGE`: keep the graph unchanged;
- `ABANDON_GENERATION`: the current file-planning hypothesis is no longer
  credible and a source-linked generation must start.

### Acceptance checks and limits

After an Agent decision, deterministic checks must still reject:

- changes to fixed contracts;
- cycles, missing consumers, or references to non-existent nodes;
- inaccessible premises or imports of unsealed/future generated theories;
- helpers with generated children unless role promotion was approved;
- wrappers, vacuous facts, duplicate statements, or unused nodes;
- hidden assumption strengthening or dropped parent guards;
- statement/consumer mismatch, namespace collisions, or budget overflow.

Green nodes are immutable within a generation. Every accepted change creates a
new graph hash and invalidates affected unsealed consumers. A sealed theory is
not backpatched merely because a later file would prefer a different helper.

For every request, preserve the exact model transcript plus a structured
`request.json`, `decision.json`, and `transaction.json`. These contain the
failed node, attempts, Isabelle residual, retrieved-premise evidence, remaining
budget, public failure classification and rationale, alternatives considered,
confidence, and any applied graph diff. This is an auditable public rationale;
it must not be described as access to the provider's hidden chain of thought.

## 9. End-to-end stages and artifacts

Each stage has a narrow input and output. No stage may silently read another
stage's forbidden source.

### M0: freeze the campaign

**Input:** repository revision, architecture, target sessions, chosen setting,
contract policy, model identities, budgets, and completion criteria.

**Output:** `campaign-manifest`, containing hashes and the exact experimental
claim, trust boundary, exclusions, and Refine-to-CRefine order.

### M1: extract contracts and classify file slots

**Input:** trusted original session sources and deterministic contract policy.

**Output:** private `contract-manifest` with exact statement, statement/context
hashes, minimal contract-owned elaboration context, owner file slot, and source
location; public `proof-file-package` records with file identity, contract
statements, and only those context openers; Proof/Support classification and
hashes.

Within generation preparation, only this trusted stage reads original Proof
bodies. Hash and source location are audit metadata, not planner hints. A
separate human-comparison workspace may read the human corpus only after a
generated result is frozen, and its output cannot flow back into generation.

### M2: build the generation environment

**Input:** campaign manifest, frozen endpoints/parents/Support, public Proof-file
packages, and forbidden-source list.

**Output:** sanitized generation workspace, source-access audit, frozen
environment identity, and contract-elaboration results. Contract shells rebuild
each minimal contract-owned context independently. They contain no human Proof
imports, surrounding declarations, or bodies.

### M3: publish the initial Premise Database

**Input:** checked parent sessions and frozen Support theories.

**Output:** versioned premise snapshot with declaration records, provenance,
attributes, and publication state. No original fact from a classified
contract-owning human Proof theory is present; frozen Support facts are legal.

### M4: plan the session file order

**Input:** all public Proof-file packages and exact selected contract statements,
Proof/Support classification, normalized M2 Support availability/unlock facts,
Refine-or-CRefine stage, fixed scheduling policy, and M3 snapshot identity and
publication counts. Individual Premise Database records and retrieval statistics
are not inputs.

**Output:** one immutable layered file-generation-list containing every
Proof-file identity, contract IDs, serial fallback order, and initial pending
status, plus a mutable session-progress status table. Audit artifacts retain
dependency evidence and the complete planner transcript. This is not a helper
plan and does not copy human Proof imports.

M4 is dependency-aware generation scheduling, not proof-architecture synthesis.
One audited model call may propose contract-composition and operation-call
priority edges only when it cites exact visible contracts and statement symbols.
Deterministic code validates citations, rejects unsupported edges, collapses
composition cycles, derives layers, and breaks same-layer ties. Contract
composition determines strata; mechanically extracted Support unlock evidence
and weak operation-call evidence only prioritize the serial fallback. A call
edge is never asserted to be an Isabelle proof dependency. Same-layer files have
no accepted composition ordering between them; this does not prove independence.

A file-generation-list entry is a Proof theory/file slot, not a theorem. In the
current Refine campaign the first entry is `Refine.KHeap_R`, corresponding to
`KHeap_R.thy`. Its five fixed contracts become graph roots only when M5 invokes
the File Planner; M4 does not pre-plan any theorem/helper inside that file.

Support with no selected contracts remains frozen human input. Distinguish
initially published frozen Support from proof-dependent frozen Support whose
unchanged source becomes buildable only after generated dependencies seal. After
each theory seal, the runner checks for newly buildable frozen Support, checks
and publishes it, and records the event in the mutable ledger.

The runner traverses the list. The first M5 attempt for an entry creates a fresh
generation ID and file-generation-task. Success changes the entry to sealed;
exhausted failure changes it to failed; a policy decision may mark it skipped.
In all three cases traversal may continue. A retry that derives from a failed or
abandoned attempt creates a new generation ID with a predecessor/source link;
it does not change the original list or its hash.

### M5: create the initial theorem graph

**Input:** file-generation task, all local contract statements, environment
declarations needed to understand them, optional premise search results, graph
budget, and output schema.

**Output:** `generated-theorem-dag` with fixed contract roots, exact generated
obligation/helper statements, roles, generated-node edges, consumers, rationale,
and rough strategies. It contains no proof bodies and no premise nodes.

### M6: certify statements and graph

**Input:** generated theorem DAG, fixed contracts, sanitized Isabelle context,
and premise snapshot.

**Output:** certified DAG plus graph hash and executable node order, or exact
statement/structure diagnostics returned to the File Planner. Certification does
not claim that the theorems are true.

### M7: prove one executable node

**Input:** one certified node, green generated dependencies, current eligible
premise snapshot, node-specific retrieval, rough strategy, attempt history, and
latest residual.

When retrieval selects a cross-file premise, its owner is a candidate import.
The Session Planner must approve that candidate before Isabelle checks the proof
in a temporary context containing it.

**Output:** a green proof and actual dependency record, or a red `proof-result`
with candidate source, Isabelle residual, retrieval history, and failure class.
The result also records backend identity, source/version hash, model-call audit
status, and whether the evidence is sealable. A non-sealable backend result may
be used to develop the harness but cannot publish a green premise at M10.

### M8: revise the graph when evidence requires it

**Input:** Graph Revision request defined in Section 8.

**Output:** signed Agent decision, then either a deterministically accepted graph
transaction with new hash/invalidation set, an instruction to retry retrieval or
proof, or an abandoned generation. Return accepted nodes to M6 and M7.

### M9: assemble and seal one Proof theory

**Input:** all green generated nodes, fixed contracts, actual local/cross-file
dependencies, attribute declarations, and generation ledger.

**Output:** complete generated `.thy`, synthesized/minimized imports, clean theory
build, dependency/attribute audit, immutable transcript bundle, and `theory-seal`.

M9 retains only candidate imports justified by actual successful dependencies,
removes unused candidates, minimizes the remaining imports, and then clean-checks
the complete theory. The proof-time candidate set and final import set are
separate recorded artifacts.

The generation ID covers one file attempt from task creation immediately before
M5 through M9 and every LLM call. Store exact prompts,
raw provider streams, final responses, stderr, candidate files, Isabelle output,
graph revisions, timestamps, model metadata, and hashes.

### M10: publish a sealed generated theory

**Input:** theory seal and checked Isabelle exports.

**Output:** new persistent generated-premise records, updated sealed-theory
inventory and heap/checkpoint identity, and the current file's sealed status in
session-progress. The runner then advances to the next pending list entry.

### M11: complete and seal Refine

**Input:** every Refine Proof-file package, frozen Refine Support, all generated
theory seals, and ledgers.

**Output:** if every required file is sealed, a clean generated Refine session,
exact contract coverage report, sealed Refine heap, generated premise snapshot,
and leakage/dependency audit. Otherwise output a partial-campaign report listing
sealed, failed, skipped, and premise-unavailable files; do not claim a Refine
session seal.

### M12: generate and seal CRefine

**Input:** sealed generated Refine heap and premise snapshot, frozen CRefine
environment/Support, and all CRefine contract packages.

**Output:** clean generated CRefine session against generated Refine, final
two-session chain seal, and complete repo-level report.

## 10. Optional single-theorem backend

The available model is the repository's Codex relay with model gpt-5.5. Do not
assume access to a paper's fine-tuned checkpoints or training pipeline. Separate
the algorithm around the model from the model used in the paper: Isabelle
interaction, search, retrieval, candidate repair, and proof checking can be
reproduced while replacing the paper's neural call with the logged Codex API.
The current relay is read-only but not mechanically no-tool: it can inspect
repository files, and its response omits raw tool events. Prompt instructions
alone are not a leakage barrier.

Recent Isabelle-oriented candidates, newest first:

- **IsabeLLM consensus verification (June 2026)** adds retrieval-augmented
  context, error tracing, counterexample generation, current Isabelle support,
  and Sledgehammer integration. It is useful for retrieval/repair components,
  but its reported application is Bitcoin consensus rather than seL4.
  <https://arxiv.org/abs/2606.18098>
- **Stepwise / Neuro-Symbolic Proof Generation for Scaling Systems Software
  Verification (March 2026, OSDI 2026)** uses an Isabelle REPL, best-first search
  over proof states, symbolic repair/filtering, and automatic subgoal discharge.
  It is evaluated directly on seL4 and has public code. This is the primary
  architecture-reproduction candidate for hard generated nodes.
  <https://arxiv.org/abs/2603.19715>
- **AutoReal (February 2026)** uses seL4-specific context augmentation and
  chain-of-thought proof training. Its context construction is relevant, but its
  result depends on a fine-tuned 7B prover, so it is not a direct Codex-API
  backend. <https://arxiv.org/abs/2602.08384>
- **Isabellm (January 2026)** combines API/local-model calls with bounded
  stepwise search, proof-outline planning, premise selection, micro-RAG, and
  counterexample-guided repair. It is a practical secondary implementation
  reference, although its own report describes parts of fill/repair as
  unreliable or unfinished. <https://arxiv.org/abs/2601.04653>
- **A Minimalist Proof Language for Isabelle/HOL (July 2025, OOPSLA 2026)**
  reduces proof generation to a ten-operation Minilang and translates the result
  back to Isabelle. It may reduce syntax failures, but it changes the proof
  language and was evaluated with fine-tuned models, so defer it until native
  Isar generation has a baseline. <https://arxiv.org/abs/2507.18885>
- **HybridProver (May 2025)** combines whole-proof candidates with tactic-level
  sketch completion. Its two-granularity control flow is relevant even when the
  fine-tuned models are replaced by Codex. <https://arxiv.org/abs/2505.15740>
- **ProofAug (January 2025, ICML 2025)** analyzes failed proof structure and
  invokes automation at multiple granularities, with recursive proof search and
  an official Isabelle implementation. It is a strong candidate for salvaging
  partially useful Codex proofs. <https://arxiv.org/abs/2501.18310>
- **Baldur (ESEC/FSE 2023)** remains the simple whole-proof generation plus
  Isabelle-error repair baseline. <https://arxiv.org/abs/2303.04910>

Use a staged comparison rather than choosing by published pass rate:

1. **Codex whole-proof baseline:** generate several complete proof bodies, check
   each in Isabelle, and repair with the exact failed candidate and diagnostic.
2. **Stepwise adapter:** reproduce the public Isabelle REPL and best-first
   proof-state controller, replacing its fine-tuned model server with the logged
   gpt-5.5 relay.
3. **Structure-aware hybrid:** add ProofAug-style salvage and multigranular
   automation, then test IsabeLLM-style residual retrieval, counterexamples, and
   Sledgehammer as separately measured components.

All variants keep the same harness boundary:

- input: one certified theorem statement, green generated dependencies, eligible
  premise records, current Isabelle context, and previous diagnostics;
- output: a checking proof body or a structured red result;
- forbidden: changing the theorem statement, appending graph nodes, reading
  human Proof bodies, or committing imports/graph revisions.

The repo-level experiment should report backend success separately from File
Planner and Graph Revision success. A stronger node prover must not receive
extra human decomposition information.

Current implementation status (2026-07-31): `regen/stepwise_codex.py`
continues to wrap the unchanged public Stepwise checkout at commit
`3cb5e3ba48f6998696f6c721ab382217b76dc21a`.
`regen/stepwise_lemma_backend.py` connects it to the modular Lemma Prover.

The production adapter no longer asks the repository-readable relay for proof
steps. A host-side no-tool audited Codex call serves every proof-state request.
The Stepwise container receives only sanitized l4v, M2 Support, generated
theories, the exact target node, official Stepwise code and the run's heap.
Prompts, raw Codex streams, parsed commands, search logs and IsaREPL logs remain
under the node round.

A pure HOL wiring smoke produced `by simp` for `rev (rev xs) = xs`.
Stepwise accepted the command and an independent `M2IsabelleChecker` replay
returned GREEN. The backend can solve only the certified node it receives; any
new theorem statement still requires a Graph Revision Agent transaction.

### KHeap_R completed integration evidence (2026-08-17)

`Refine.KHeap_R` is the first built Proof theory in the modular continuation.
Its final graph v77 contains 122 generated/contract nodes; all nodes and all five
fixed contracts are Isabelle GREEN. The 3,537-line generated theory passes a
complete check and the real `Generated_Refine` session build. No generated
source contains diagnostic markers, `sorry`, or `oops`.

This run makes the following rules mandatory for later theories:

1. Treat File Planner output as an initial semantic hypothesis. Graph Revision
   may add or replace generated nodes only from accumulated Isabelle evidence;
   fixed contracts and unrelated GREEN nodes remain immutable.
2. Before proof search, check free-variable binding, parent-guard preservation,
   producer/consumer type flow, and explicit result types for polymorphic
   computations observed only through `snd`.
3. Do not equate retrieval with use. When the right premise is already present,
   prefer named Isar facts and explicit `where` instantiation over broad `OF`,
   `simp`, or re-expansion of definitions.
4. Resume long proof repair from the strongest checker-accepted local prefix and
   exact residual. Candidate-to-candidate and round-to-round feedback is part of
   the Lemma Prover contract.
5. Keep node GREEN, complete-theory GREEN, and generated-session built as
   distinct states. Complete checks use diagnostic node markers to locate
   context-sensitive automation; markers never enter generated source.
6. Model, checker, and session-build operations emit heartbeat events. Provider,
   relay, network, and unsupported-model failures do not consume logical proof
   budgets.
7. Publish generated premises only after the complete theory and generated
   session both pass. A built/sealed ledger entry must not retain stale failure
   metadata.

The active per-node ceiling is five candidates per round, ten prover rounds, a
180-second model-think timeout, a 1,800-second Isabelle check timeout, and 16
structural revisions. These are ceilings rather than targets; stable residuals
must be classified before blindly spending the remaining budget.

The complete chronology, assembly-only proof stabilization, experiment boundary,
and evidence paths are recorded in
`regen/KHEAP-R-GENERATION-RETROSPECTIVE.md`. Because the harness changed during
this run, KHeap_R is development evidence rather than a clean autonomous
benchmark measurement.

## 11. Completion and evaluation

Success requires:

- exact once-only proof of every selected Refine and CRefine contract;
- no `sorry`, `oops`, new axioms, or contract weakening;
- no original source, imports, or facts from classified contract-owning human
  Proof theories, and no stock target heap leakage; frozen Support facts remain
  legal Setting B inputs;
- every explicit and implicit premise use traced to frozen or generated sources;
- every Proof theory and both sessions clean-built from recorded artifacts;
- CRefine built against the exact sealed generated Refine heap;
- complete immutable LLM and Isabelle transcripts for every generation.

Report contract coverage, generated cross-file dependencies, helper reuse,
compositional depth, duplicate facts, generated import structure, attribute-pool
effects, proof and build cost, graph revision causes, and Lemma Prover failures.
Compare against the human corpus only after generation is sealed.

## 12. Deferred work

- Setting A file/contract architecture generation;
- joint Refine/CRefine planning;
- backpatching a sealed ancestor for later reuse;
- global helper promotion and deduplication during the headline run;
- optimal premise ranking and optimal single-theorem proving;
- semantic equivalence of generated helpers beyond checker-visible statements.

These are separate experiments. They must not leak human theorem decomposition
into the current Setting B campaign.
