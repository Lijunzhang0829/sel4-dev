# Modular Refine Proof Pipeline

This is the executable proof-generation core. Environment Preparation provides
sanitized contract packages, an Isabelle environment without the 21 human
contract-owning Proof theories, and the legal premise database. Existing
artifact directories retain historical names such as `M1`, `M2`, and
`M3`; these are not proof-generation modules.

## Modules

| Module | Sole responsibility |
|---|---|
| `proof_graph.py` | DAG schema, fixed-contract checks, role rules, cycle checks and executable order |
| `file_planner.py` | Generate exact theorem/helper statements and generated edges |
| `premise_retrieval.py` | Retrieve and rank legal premises for the current statement/residual |
| `proof_outline_salvage.py` | Recover a checked local Isar outline from failed candidates without changing the theorem DAG |
| `lemma_prover.py` | Generate and repair one node's proof body, with structured fallback |
| `stepwise_lemma_backend.py` | OSDI 2026 Stepwise/IsaREPL best-first backend for one node |
| `graph_revision_agent.py` | Approve and apply graph changes after RED evidence |
| `isabelle_node_checker.py` | Check statement shells, node proofs and complete theories |
| `proof_session_runner.py` | Advance files and build the shared `Generated_Refine` session |

The File Planner receives only a bounded declaration context, not a runtime
premise-retrieval result. It cannot select external premises and its output
schema has no proof field. The Lemma Prover has no graph-mutation interface.
Only the Graph Revision Agent can replace a graph, and deterministic checks
protect fixed contracts and GREEN nodes. Isabelle is the authority for
GREEN/RED.

## Data flow

```text
sanitized contract package
        |
        v
File Planner -> graph-v1.json
        |
        v
Isabelle statement check
        |
        v
executable node
        |
        +-> runtime premise retrieval
        |
        v
Lemma Prover -> proof candidate -> Isabelle
        |                         |
        | GREEN                   | candidate RED
        v                         v
next consumer         Local Proof-Outline/Salvage
                                  |
                                  v
                       next candidate in same node round
                                  |
                          round budget exhausted
                                  |
                                  v
                          Graph Revision Agent
                                  |
                                  v
                           graph-v2.json
```

A generated DAG contains only `contract`, `theorem`, and `helper` nodes.
External premises are retrieval results, never graph nodes. A helper is a
generated leaf. A theorem may depend on helpers or other generated theorems.

## Authoritative Runtime Flow

```text
Environment Preparation
  -> Session Planner
       output: immutable Proof-theory file list and mutable progress ledger
  -> File Planner
       input: all fixed contracts in one file plus bounded legal declaration context
       output: graph-v1 with exact generated statements, no proof bodies
  -> Isabelle Checker: statement-shell parse/type check
  -> select first executable graph node
  -> Premise Retrieval: legal facts for the node statement and latest residual
  -> Lemma Prover: Stepwise search, then whole-proof repair
       candidate RED -> Local Proof-Outline/Salvage
                        preserve valid local structure, plan the next
                        obtain/have/show sequence, and retry in the same round
  -> Isabelle Checker: node proof round is GREEN or RED
       GREEN -> persist proof and advance
       RED after candidate budget -> Graph Revision Agent review
                  RETRY_PROOF -> same graph, focused next proof round
                  REVISE_GRAPH -> checked transaction and new graph version
                  ABANDON_GENERATION -> stop this file attempt
  -> complete-theory check
       timeout/RED with all nodes GREEN
         -> extract the last diagnostic node marker from the complete log
         -> write `assembly-stabilization.json` for that exact node
         -> stabilize that proof body only
         -> recheck the changed node and the complete theory
  -> Generated_Refine build and premise publication
```

The statement-shell check is an operation of `isabelle_node_checker.py`, not
an independently numbered module.

A `decision-NNN` is one Graph Revision Agent review of RED evidence. It always
records the request, public decision rationale, and transaction outcome.
`RETRY_PROOF` does not create a graph version. Only an accepted
`REVISE_GRAPH` transaction creates a new immutable `graph-vN.json`.

The active graph is the exact `graph_source` stored in `files.json`.
Highest-version directory scanning is only a migration fallback. New graph
artifacts are create-once and structural revisions record their parent graph and
source decision, preventing resumed calls from overwriting the same version.

The File Planner receives a bounded set of legal declarations to understand
contract semantics, but it may not put those premises into the generated DAG or
commit that they will be used. For every contract it must emit an auditable
route: either a generated route with semantic steps and generated dependencies,
or a direct route justified by detailed definitional normalization or a precise
one-step semantic reduction. It may not select or name an external premise.
Vague references to an “existing”, “available”, or “standard” theorem are
rejected before `graph-v1` is written. Generated declarations that only rename
the proposition of a contract or another generated node are also rejected as
cosmetic wrappers. The deterministic comparison covers exact propositions,
irrelevant simple term type annotations, and the equivalent presentations
`assumes A shows B` and `shows A \\<Longrightarrow> B`.

File planning has at most three attempts. A rejected attempt is not a graph
revision: its deterministic diagnostic is appended to the next File Planner
payload, and no `graph-v1.json` is written until one proposal passes.

The deterministic graph check now also certifies the binding interface of every
generated statement. It rejects incompatible repeated `fixes` types and a
high-risk implicit primed variable whose spelling is one edit from an explicitly
fixed variable. This catches the observed KHeap_R `ob'`/`obj'` planning errors
without requiring dependency and consumer variable names to be identical;
Isabelle remains authoritative for general type/sort inference and theorem
instantiation. Every accepted graph records a `statement_certification` report.

## Current Refine run

The current runnable foundation is
`reports/regen/proof-runs/refine-main/`. It uses 21 sanitized Proof-file
packages, 287 fixed contracts, a checked Isabelle environment, a legal premise
database, and independent writable heaps.

The Session Planner list is bound into `run.json` and `files.json`.
`Refine.KHeap_R` is its first Proof-theory entry and owns five fixed contract
roots.

Runtime proof budgets are node-scoped. The active continuation keeps five
whole-proof candidates per prover round, permits ten complete prover rounds per
node (at most 50 whole-proof candidates), and permits 16 structural graph
transactions charged to each failing node. A model think call has a 180-second
hard timeout; an Isabelle node/round check has a 1,800-second timeout. Local salvage runs only between
failed candidates and does not consume a graph revision. Graph revision numbers
remain global version identifiers; they are not a shared file-level budget. The
exact policy used by a run is recorded in `run.json` under `node_budget`.

A failed round accepted as structural-revision evidence is charged to the
node's revision budget and credited back from its proof-round budget. This
leaves one integration attempt after the revised DAG passes statement checking.

### KHeap_R superseded early retry history

This subsection records an early bounded checkpoint and is retained to explain
why the harness changed. It is not the current KHeap_R status; the completed
continuation is recorded in the next subsection and in
`KHEAP-R-GENERATION-RETROSPECTIVE.md`.

The previous mixed-lifecycle run was deleted before this retry. The clean File
Planner proposal created seven generated nodes for `setObject_other_corres` and
left the four small endpoint/notification contracts direct. Statement checking
mapped one type error to the exact failing node, and decision 001 changed only
that node (`ko'` to the word address `ptr'`). Graph v2 then passed statement
checking and all seven generated nodes became GREEN.

`getEndpoint_corres` did not close directly. Decision 002 added
`regen_getEndpoint_object_corres_bridge`, preserving every existing GREEN node.
Graph v3 passed statement checking. Two complete prover rounds then left the
same semantic residual at the bridge: correspondence between abstract
`get_endpoint` and concrete `getObject`. Stepwise exhausted the same unfolded
state and whole-proof candidates either failed to close it or cited unavailable
facts.

Decisions 003 and 004 both returned `RETRY_PROOF`, because their classification
overweighted the last invalid fact reference. The repaired arbiter then
aggregated branch history and rejected another unchanged retry. Decision 007
split the definitional wrapper into endpoint lookup, load, concrete-success and
relation obligations. A corrected SPLIT transaction removes the failed node
while preserving unrelated generated nodes; the earlier merge implementation
incorrectly retained the split node as an unused declaration.

Isabelle then showed that the proposed unconditional `loadObject` result was
false without alignment and next-object separation guards. Decision 008
replaced the statement and its lookup consumer, and the guarded loadObject node
became GREEN on the first structured whole-proof candidate. Decisions 009 and
010 repaired two lookup-result type errors caught by statement certification.
Decision 011 moved the remaining lookup helper from raw `lookupAround` to the
`lookupAround2` abstraction used by the concrete operation.

The lookup helper was then retried under a five-round node budget. Each complete
round receives the previous round's proof body, exact Isabelle residual,
retrieved premise names, and Graph Revision guidance. Within a round, candidate
N receives candidate N-1 and its exact Isabelle diagnostic; the Stepwise
residual is also forwarded to the whole-proof fallback. Infrastructure crashes
are rolled back and do not consume a logical proof round. The default
whole-proof candidate count and complete node-round count are both five.

The final round retrieved `Invariants_H.obj_atE'`, `obj_at'_def'`,
`ps_clear_lookupAround2`, and the endpoint projection facts, but its strongest
proof still left seven non-endpoint kernel-object constructor branches. This is
no longer a missing-feedback or missing-premise failure: the proof body failed
to use the endpoint projection predicate strongly enough when eliminating
`obj_at'`. Decision 015 chose `ABANDON_GENERATION` only after all five complete
rounds were consumed. It rejected a graph split because the proposed children
would merely duplicate already retrieved legal premises rather than introduce a
new semantic obligation. At that historical checkpoint KHeap_R was failed at
`regen_getEndpoint_lookupAround_endpoint_slot` as a bounded, auditable
single-lemma specialization failure. Later continuation repaired the harness,
revised the graph, and superseded this status.

### KHeap_R completed continuation

`Refine.KHeap_R` is now `built` on graph v77. All 122 generated/contract nodes
and all five fixed contracts are Isabelle GREEN. The complete generated theory
checks in about 1,530 seconds and the real `Generated_Refine` session builds in
about 1,511 seconds. The 3,537-line generated source contains no `sorry`,
`oops`, or diagnostic marker.

The final continuation established four rules that apply to every later file:

1. Statement/consumer type flow must be checked before spending proof-search
   budget. Two final graph revisions corrected accidentally independent or
   underconstrained projected-object variables.
2. Premise retrieval does not solve premise instantiation. Long proofs should
   use named Isar facts, explicit `where` parameters, and the strongest GREEN
   generated dependency instead of broad re-expansion.
3. Node GREEN, complete-theory GREEN, and session built are distinct states.
   Complete checking exposed two context-sensitive automation proofs even after
   all nodes were individually GREEN.
4. Long Isabelle operations must emit heartbeat events. Complete checks now
   print diagnostic node markers, and generated-session builds emit 30-second
   progress events.

The full chronology, repeated failure classes, assembly repair, next-theory
checklist, budget policy, and evidence paths are in
`regen/KHEAP-R-GENERATION-RETROSPECTIVE.md`.

## Quick start

Inspect the session:

```bash
sed -n '1,160p' reports/regen/proof-runs/refine-main/run.json
sed -n '1,220p' reports/regen/proof-runs/refine-main/files.json
```

Run one complete Proof theory:

```bash
python3 regen/proof_session_runner.py run-file \
  --run-dir reports/regen/proof-runs/refine-main \
  --theory Refine.Bits_R \
  --provider codex \
  --planner-model gpt-5.5 \
  --revision-model gpt-5.5 \
  --prover-model gpt-5.5 \
  --lemma-backend stepwise
```

The command performs File Planner, statement certification, leaf-to-root node
proof, runtime retrieval, graph revision when justified, complete theory check,
and a `Generated_Refine` session build.

To stop after the auditable File Planner plan:

```bash
python3 regen/proof_session_runner.py plan-file \
  --run-dir reports/regen/proof-runs/refine-main \
  --theory Refine.KHeap_R \
  --provider codex \
  --planner-model gpt-5.5
```

A later `run-file` reuses the latest accepted graph and restores GREEN node
proofs from `progress.json` instead of calling the File Planner or prover again.

Important outputs:

```text
theories/Bits_R/graph-v1.json
theories/Bits_R/graph-check-v1.json
theories/Bits_R/nodes/<node>/attempts.json
theories/Bits_R/nodes/<node>/result.json
theories/Bits_R/complete-check.json
theories/Bits_R/session-build.json
generated/Bits_R.thy
generated/ROOT
generated-premises.json
files.json
```

Every model call retains one canonical copy of its prompt, raw provider stream,
final response, stderr, metadata, and audit beside the owning theory or node.
Artifact prefixes are create-once. If a node round is deliberately rerun, the
allocator adds `--call-NNN`; low-level providers reject every overwrite. A
`running` meta/audit shell is written before provider execution, so an external
kill leaves an explicit incomplete call rather than a prompt-only mystery.

The review surface is `module-logs/<module>/<run>.json`, schema version 2. It is
a lightweight index, not a second evidence store. Each attempt records context,
metadata, audit summary, and `{path,size_bytes,sha256}` references for the
canonical transcript and module outputs. JSON outputs also expose a small status
summary such as `green`, `decision`, `operation`, or `graph_revision`. Prompt,
raw stream, final response, proof result, request, graph, and transaction bodies
are never copied into the module log.

KHeap_R exposed why this discipline is required. Repeated manual continuation of
one nominal round reused `attempt-NNN` and `after-attempt-NNN`, overwriting old
files while append-only module logs retained their old hashes. Four Graph
Revision calls were externally interrupted after request/prompt creation but
before terminal metadata, and three late decisions bypassed the standard module
logger. The old embedded mirror occupied about 466 MiB for one theory; replaying
its entries as schema-v2 references projects about 22 MiB, a roughly 95 percent
reduction without deleting canonical evidence.

The minimum review bundle for a built theory is therefore: initial/final graph
and graph transactions; one module index; final proof per GREEN node; concise
RED residual/attempt summaries; exact canonical transcripts; final Isabelle
node checks used by those proofs; complete-theory and session build results.
Intermediate checker source files, repeated full graph snapshots, and Stepwise
state dumps are development evidence and may later be compressed or moved to
cold storage, but are not duplicated into the review index.

Log damage is operational evidence, not a campaign failure. The reconciler uses
five states: `active`, `complete`, `interrupted`, `superseded`, and `corrupt`.
Restarting after an `active` call with no live owner reclassifies it as
`interrupted`; rerunning the same logical attempt allocates a new physical call
and leaves the old one `superseded`. Missing files or hash disagreement are
`corrupt`. None of these states blocks proof search. They block only selecting
that physical call as the final evidence for a GREEN node or an applied graph
transaction. The low-level writer rejects an in-place collision, while every
pipeline caller allocates the next physical call automatically, so collision is
recoverable rather than a theory failure.

Retention is stage-based. While a node is RED, all candidates remain available
for repair. Once it is GREEN, retain the winning proof/check, the last useful RED
residual, premise-use summary, and any graph decision it triggered; older
candidate prompts, checker sources, Stepwise states, and repeated retrieval dumps
may be removed. Once the complete theory builds, retain the final graph,
transactions that produced it, final node proofs, contract proofs, compact module
index, and complete build logs. Cleanup must never delete the currently selected
winning call or an applied graph transaction, but it need not preserve every
intermediate development state.

## Model roles

The three model roles are independently configurable. The intended experiment is
to use a stronger model for File/Session planning and graph arbitration while
keeping the OSDI 2026 Stepwise backend on Codex 5.5. On 2026-07-31 the local
ChatGPT Codex provider rejected `gpt-5.6` as unsupported, so all current defaults
remain `gpt-5.5`. When 5.6 becomes available, only `--planner-model` and
`--revision-model` need to change.

## Local proof-outline and salvage

`proof_outline_salvage.py` is a separate module inside the Lemma Prover
lifecycle. After a whole-proof candidate is RED, it receives the fixed node,
bounded failed candidates, exact Isabelle residuals, Stepwise residual, and the
names of premises retrieved for those candidates. It emits no proof body and has
no graph interface. Its output is a bounded local DAG of
`assume/obtain/have/cases/show/automation` steps plus preservation and avoidance
guidance.

The deterministic validator enforces at most eight local steps, backward-only
local dependencies, exact copying of preserved fragments, and premise names
drawn only from the eligible retrieval set. It rejects outer declarations,
imports, admitted proofs, graph operations, and qualified facts hidden in
statements or method hints. The checked outline is included in the next Lemma
Prover prompt and retrieval query. Every LLM call has its own transcript below
the node round and a review mirror under
`module-logs/proof-outline-salvage/`.

The first offline fixture is derived from KHeap_R
`regen_getEndpoint_lookupAround_endpoint_slot` round 09. It distinguishes the
initial-method failures from the later eight-constructor branch explosion and
produces the local order: preserve the endpoint guard, extract the endpoint
object and `ps_clear`, specialize `ps_clear_lookupAround2`, then normalize
wrappers and split only the successor option. This is a proof-body outline, not
a new theorem DAG.

The KHeap_R relay evaluation exposed a more precise ordering discipline for
projection-heavy goals: first obtain the generic object and retain every parent
guard, then prove its concrete constructor from the projection fact, specialize
the guards to that constructor, apply the semantic lookup premise, and only
then split the one result option required by the contract. This eight-step shape
is retained as theorem-specific test evidence; it is not a universal fallback.
The deterministic fallback is bound to the current node ID, fixed statement,
latest candidate, and latest Isabelle residual. It emits only a generic local
repair order and must not invent endpoint, projection, size, or `ps_clear`
obligations absent from the current target.

The Lemma Prover may set a separate `salvage_provider`. `codex-relay` is useful
for development evaluation because it avoids the local Codex CLI skill/tool
dispatch that contaminated the first run. Its HTTP API exposes only answer
text, however, so transcripts and influenced Lemma Prover results are explicitly
`sealable_generation: false` and
cannot satisfy a production theory seal. The ordinary audited provider remains
required for sealable proof generation.

## Stepwise state input contract

Stepwise-Codex is a node-local backend, not a second File Planner. For each
selected Isabelle state, the model receives only:

1. the exact current proof state;
2. the Isabelle-checked proof prefix leading to that state;
3. dynamically retrieved legal premises, including state-specific bridge facts;
4. prior audited repair guidance and forbidden fact names; and
5. the instruction to return only bounded next-command candidates.

The per-state audit file now records `proof_state`, `proof_prefix`, the base
retrieval query, repair guidance, state-derived bridge terms, forbidden facts,
and every premise shown to the model. The prompt explicitly permits facts from
the legal premise hints; the earlier wording incorrectly said that only facts
already printed in the proof state could be used.

Concrete residuals are mapped back to named legal bridge families before
ranking. In particular, aligned word upper-bound and bit-mask residuals retrieve
`is_aligned_no_overflow`; endpoint object-size states retrieve `objBits_simps`,
`injectKO_ep`, `objBitsKO_def`, and `epSizeBits_def`; and `fst`/`snd` lookup
states retrieve pair-reconstruction facts. These are retrieval hints, not
hard-coded proof commands, and every returned record still comes from the legal
Premise Database.

Residual phase matters. If alignment has already been unfolded to a mask
equality, Stepwise also retrieves `is_aligned_mask` and `mask_def` and is told
not to apply a no-overflow elimination rule directly. It must first reconstruct
the local `is_aligned` fact. This distinction was added after a real Codex probe
selected the right theorem family but instantiated it at the wrong abstraction
level; Isabelle rejected that command, while the reconstruct-then-apply sequence
was GREEN.

Stepwise tree depth is independent from the five whole-proof repair candidates.
Five whole-proof retries map to ten tree rounds, two selected frontier states,
and depth 48. The first repair used 40 rounds and four selected states, but a
real KHeap_R run reached only six rounds before the 30-minute wall-time limit;
that nominal budget was not executable. Timeout now produces a structured RED
artifact instead of escaping the Lemma Prover lifecycle.

Stepwise allocates an OS-selected loopback port for each invocation instead of
sharing port 25670. Runtime RED results are classified before they enter theorem
repair: IsaREPL port/startup failures, Docker failures, and model-provider
connection failures are infrastructure failures. They emit
`node_infrastructure_error`, do not consume a node proof round, and never reach
the Graph Revision Agent. A Stepwise wall-time exhaustion after search starts,
ordinary tactic failure, search exhaustion, and final replay mismatch remain
proof-search evidence.

Stepwise uses two separate wall-time limits. The think-timeout option limits one
proof-state-to-Codex request and defaults to 180 seconds. The timeout option
limits the whole node round, including all selected states, model calls, and
Isabelle steps. The effective per-think limit is the smaller of the think and
round limits, and both values are recorded in premise-retrieval.json and the
run's node budget. A per-think timeout preserves the partial audited provider
stream and returns a provider failure to the proof search; it does not silently
wait for the remaining tree budget.

Every Stepwise Docker invocation has a generation-, node-, round-, and
process-bound name. When the whole-round deadline expires, or the runner is
interrupted, the backend executes docker rm -f for that exact container before
returning or re-raising. This prevents a terminated Docker client from leaving
IsaREPL and model-search work running in the background. Total run-file wall
time may still span many nodes; it must not be interpreted as one stuck think.

Audited Stepwise proof-state calls use a clean Codex runtime. They ignore user
configuration and rules, do not persist Codex sessions, and disable apps,
plugins, remote-plugin synchronization, and hooks. The model remains gpt-5.5
with reasoning effort explicitly fixed to medium. This keeps File Planner and
Graph Revision Agent configuration unchanged while removing unrelated startup
networking from the high-frequency Lemma Prover path. The change followed
KHeap_R traces where successful 10-to-30-second proof generations expanded to
60-to-90 seconds because Apps MCP handshakes, plugin-catalog requests, and model
metadata refreshes repeatedly failed. Provider NDJSON, stderr, final output, and
the clean-runtime flag remain in each call transcript.

The next Stepwise round also receives the latest certified local salvage outline
from the previous whole-proof round. This is ordering guidance, not a proof
body: it prevents a retrieved downstream rule such as `ps_clear_lookupAround2`
from being applied while its endpoint constructor, guards, size normalization,
and word upper bound are still generic.

For long proofs, Stepwise now has an optional verified-prefix repair mode. The
Lemma Prover binds up to five RED whole-proof candidates from the prior round to
the exact unchanged statement and passes them as a warm-start request. After
Stepwise reaches the theorem, IsaREPL clones that root state, uses Isabelle's
parser to split each candidate into outer commands, and replays each candidate
independently in the sanitized context. Replay stops at the first rejected
command. The candidate with the most mechanically accepted commands, breaking
ties by fewer remaining subgoals, becomes the tree root. Its resulting Isabelle
state is the first state sent to Codex, and Stepwise searches only for the
remaining suffix. The request, every candidate replay, first rejection,
selected prefix, and resulting state are recorded in
`warm-start-request.json` and `warm-start-replay.json`.

This is an additive repair path. A node with no prior candidate follows the
original statement-to-proof code path unchanged. A missing, empty, or
statement-mismatched candidate is rejected before launch. If Isabelle accepts
no prefix command, Stepwise falls back to its original empty-prefix root.
Regardless of start mode, the assembled complete proof is checked again by the
authoritative IsabelleChecker before it can become GREEN.

The KHeap_R lookup diagnosis established the intended local order: recover the
endpoint constructor and guards, normalize endpoint size, prove the aligned
word upper bound with a named bridge before unfolding bit arithmetic, apply
`ps_clear_lookupAround2`, reconstruct the result pair explicitly, and only then
assemble the existential contract result. The independently repaired proof was
Isabelle GREEN, but because it was produced through an interactive diagnostic
session rather than the Lemma Prover, it is not itself publishable generation
evidence. A Stepwise rerun must create its own complete transcript and GREEN
checker result.

The first full Stepwise rerun (`round-15`) made 18 audited Codex state calls but
timed out after 1800 seconds. Its prefix applied `ps_clear_lookupAround2` while
the object was still generic, producing 9--29 subgoals; correct premise
availability alone did not repair the ordering. A first isolated residual probe
then selected the right no-overflow theorem family at the wrong phase and was
rejected by Isabelle. After adding `is_aligned_mask`, `mask_def`, and explicit
phase guidance, a second real Codex 5.5 probe selected
`subgoal_tac "is_aligned ptr 4"` first and proposed the expected reconstruction
and no-overflow steps. The explicit reconstruct-then-apply residual proof was
Isabelle GREEN. This validates the state-level input contract, not end-to-end
pure-Stepwise success on the full helper.

For production continuation, `salvage_provider=deterministic` skips a second
model call and emits a target-bound four-stage fallback: retain assumptions,
re-enter the first rejected branch with typed names, prove only its local fact,
and resume the accepted proof. This path is sealable: it changes no statement
or theorem graph, and the subsequent audited Lemma Prover still owns the proof
body. A semantic outline requires the audited Codex salvage provider.

KHeap_R continuation rounds 10-14 add frontier preservation. Later attempts
must combine independently successful local blocks instead of replacing one
with an older failing variant: typed endpoint extraction, singleton membership
through projectKO_eq2, constructor resolution through projectKO_opt_ep,
retained objBitsKO guards, explicit ps_clear_lookupAround2[OF ...], and
final-only objBits normalization. The remaining round-14 residual is the word
boundary goal ptr && 0xF = 0 ==> ptr <= 0xF + ptr; it is recorded as a local
proof/arithmetic failure, not a reason to revise the theorem DAG.

## Graph revision

After a node exhausts its prover round, the Graph Revision Agent must choose:

- `RETRY_PROOF`: keep the graph and provide a new retrieval query/repair hint;
- `REVISE_GRAPH`: submit a complete replacement generated graph and an
  ADD/DELETE/SPLIT/role/statement/edge operation;
- `ABANDON_GENERATION`: stop the current file attempt.

Every decision has a unique transcript ID. Fixed contracts cannot change.
GREEN theorem declarations and dependencies cannot change, but they may gain a
new consumer.

The agent must not create a helper that merely restates or wraps an eligible
premise. If legal retrieved facts bridge the current residual, the failure is a
retrieval/proof-body problem and the legal result is `RETRY_PROOF`, possibly
with a narrower retrieval query. Structural changes are reserved for evidence
that the generated theorem statement or dependency shape is wrong.

Each decision also writes `graph-revisions/decision-NNN/request.json`,
`decision.json`, and `transaction.json`. These preserve the RED evidence and
budget context, the model's public failure classification and approval
rationale, and the applied graph diff. Proof-body, retrieval, search, and tool
failures may only authorize `RETRY_PROOF`; graph changes require a structural
failure class backed by concrete residual evidence. These records are public
decision rationales, not a claim to expose a provider's hidden chain of thought.
The deterministic runner checks the remaining graph-revision budget before
writing any replacement graph. A rejected transaction remains auditable but can
never become the active resume target.

The arbiter evaluates the complete node history, not only the final candidate.
Each request includes prior decisions, their transaction outcome/rejection
reason, and hashes of normalized semantic goals from every whole-proof attempt.
Repeated semantic goals remain evidence for better proof organization, but do
not impose the old two-round hard stop. `retry_proof_allowed` becomes false
only when the configured node proof-round limit is reached; its current default
is five. At that point the agent must submit a justified graph operation or
`ABANDON_GENERATION`. Resume also performs this review before spending another
prover round.

Partial graph operations are merged by operation semantics. `ADD` overlays new
nodes and preserves existing generated nodes. `SPLIT` removes the failed node
before adding its replacements, while preserving unrelated nodes. Deterministic
graph validation then rejects cycles, unused nodes, changed GREEN declarations,
and out-of-scope edits.

Statement-certification revisions receive the current GREEN node set. Unchanged
GREEN declarations retain their proofs and status after the revised graph is
accepted; removed nodes are pruned from the proof map. This prevents a local
statement type repair from silently resetting unrelated completed branches.

New graph proposals receive a second structural quality check. A generated
`corres` node connected directly to a consumer is rejected when the two
propositions have at least 0.92 textual similarity or differ by at most one
logical token. This catches definitional wrappers such as replacing
`getEndpoint` by `getObject` while preserving the same relation, guards and
abstract operation.

## Stepwise backend

`stepwise_lemma_backend.py` reuses the official Stepwise
`TreeSearchProver` and `IsaREPL.jar`. Proof-state model calls use the existing
no-tool audited Codex CLI rather than the old repository-readable relay. The
container sees only sanitized l4v, M2 Support, generated theories, the target
node, official Stepwise code and an independent heap.

Each Stepwise request carries an encoded checked proof prefix together with the
current Isabelle state. Premises are retrieved again for that state instead of
being frozen at the theorem's initial goal. The ranking penalizes broad
`auto`/`fastforce` steps that explode the frontier. If Stepwise exhausts its
search, `lemma_prover.py` falls back to a structured whole-proof attempt using
named intermediate facts and Isabelle residual repair. RED results retain the
full statements and provenance of the highest-ranked legal premises so the
Graph Revision Agent can distinguish missing proof organization from a genuinely
missing theorem node.

A pure HOL smoke produced `by simp` for
`rev (rev xs) = xs`; Stepwise accepted it and a separate
`IsabelleChecker` run returned GREEN.

## Abstract setter abbreviations and dependency-scoped Stepwise targets

The `KHeap_R` `regen_setEndpoint_set_object_bridge` run exposed two interface
grounding defects. Lower-case abstract `set_endpoint` and `set_notification` are
Isabelle abbreviations for `set_simple_ko Endpoint` and `set_simple_ko
Notification`; they do not create `set_endpoint_def` or
`set_notification_def` facts. Runtime retrieval now maps these constants to
`KHeap_A.set_simple_ko_def` and `KHeap_A.set_object_def`. Lemma prompts state
that identifiers are case-sensitive and prohibit replacing the lower-case
abstract operation with concrete `setEndpoint`/`setEndpoint_def`.

Stepwise premise hints now include the current node's certified GREEN generated
dependencies before database results. Its temporary target theory replays only
the transitive generated dependency closure, rather than every earlier GREEN
node in execution order. This prevents an unrelated old proof from blocking
IsaREPL initialization and makes the model-visible context match the theorem
DAG.

With these changes, `regen_setEndpoint_set_object_bridge` was retried in the
existing `refine-main` generation at graph revision 22. Stepwise initialization
on the pre-fix broad prefix failed and correctly fell back to structured
whole-proof generation. The first fallback candidate used
`regen_get_object_endpoint_to_get_endpoint` and
`KHeap_A.set_simple_ko_def`; Isabelle returned GREEN in round 11, attempt 1,
and the proof was written back to the existing progress ledger.

The follow-up `regen_setEndpoint_abstract_success` capability test also exposed
stale lifecycle evidence: its old RED rounds predated the new bridge dependency,
but the runner retained their budget, residual and rejected-transaction retrieval
hint. RED feedback is now bound to both statement and dependency IDs. A graph
revision that changes an unproved node's dependencies resets that node's proof
rounds, retrieval hint and feedback while preserving every GREEN proof.

After this reset, round 9 was run with an empty theorem-specific retrieval hint:
the model saw only the certified statement/strategy, the GREEN bridge, legal
premises and Isabelle states. Dependency-scoped Stepwise initialized correctly
and advanced the proof, then structured whole-proof repair closed it in two
candidates. Attempt 1 isolated `get`/`put` result membership; attempt 2 added
only local `get_def`, `put_def` and `return_def` simplification and was Isabelle
GREEN. This is positive evidence for autonomous premise use and residual repair.
The remaining performance defect is that the ordinary node checker still
replays all saved GREEN proofs rather than only the target dependency closure;
the two checks took about 15 minutes each.

## Provider errors and local syntax recovery

Codex NDJSON `item.type=error` records transport reconnect and fallback
diagnostics; they are not model tool calls. The no-tool audit treats
`reasoning`, `agent_message`, and `error` as passive records while still
rejecting every unknown active item type, including `command_execution`,
`file_change`, and MCP calls. Transport diagnostics remain counted in the
audit. A provider or audit failure is recorded as a RED
`llm_provider_error` attempt and consumes a retry; it no longer terminates the
file runner with an uncaught exception.

The KHeap_R notification loadObject run exposed a separate local syntax
failure: `case (Some next)` is invalid because `next` is an Isar keyword.
Lemma Prover prompts now require neutral case variables such as `nxt`.
The original Codex transcript remains immutable. A precisely scoped local
repair renamed the case variable and changed the failed Some-branch guard
extraction from broad `auto` to `using aft Some by blast`; the repaired
fixed-statement proof was Isabelle GREEN in the graph-v15 context.

## Minimal trust boundary

The infrastructure checks only what the experiment needs:

1. the selected M2 environment and M3 database are PASS and physically usable;
2. public packages expose contracts but no human body, helper or import data;
3. model calls run through the existing no-tool audited transport;
4. graph permissions are enforced by module APIs and deterministic validation;
5. Isabelle accepts every statement, proof body and complete generated theory;
6. only a successfully built theory enters the generated premise records.

This pipeline does not add adversarial threat modeling, repeated whole-tree
rehashing, or a second sealing hierarchy.

## Runner visibility and semantic graph-repair rules (2026-08-12)

The runner emits machine-readable `[regen-event]` lines for every long-lived
child stage. LLM calls emit `llm_started`, a 30-second `llm_heartbeat`, and
`llm_finished`. Isabelle checks use the analogous start/heartbeat/finish
sequence. Stepwise-Codex now also emits `stepwise_started`,
`stepwise_heartbeat`, and `stepwise_finished` around the complete container
lifecycle, including Isa-Repl and theory initialization before the first model
call. The artifact prefix, node, round, container, elapsed time, timeout and
return status distinguish active work from a stalled provider or UI.

The KHeap_R `setObject_other_corres` run established three graph-repair rules:

1. Generated branch statements must preserve parent guards, including shared
   type variables. Splitting a contract whose `obj` and `ob'` share one
   `'a::pspace_storable` type into independently polymorphic helper variables
   creates false constructor branches even when every declaration elaborates.
2. A revision may not move the residual goal into a new assumption. Every new
   assumption needs a provider in the fixed consumer, a GREEN generated
   dependency, or a legal framework premise. `graph-v52` was rejected by this
   rule after it assumed `injectKO ob' = injectKO obj` instead of proving it.
3. Retrieval success is not proof success. When the required facts are present
   but the prover cannot order their instantiations, the local outline must name
   the bridge sequence. The data relation-cut helper became GREEN only after
   ordering `project_inject`, `project_koType`, `koTypeOf` constructor
   elimination, and the concrete `koType` equations.

Trusted framework defining equations may refute a generated statement. Such a
failure is `STATEMENT_CONSUMER_MISMATCH`, not premise retrieval or search
exhaustion, and `retry_proof_allowed=false` must survive context enrichment.
Local replacement transactions prune stale unproved nodes only inside the
failed node's affected branch; GREEN and unrelated branches remain immutable.

## Automation Advisor experiment (2026-08-20)

The `regen-refine-automation-advisor` branch separates three inputs to the
node-local prover. They are complementary, not interchangeable:

```text
checker-verified current Isabelle state
  + Premise Retrieval: WHAT EXISTS?
  + Automation Advisor: WHAT LOCAL TRANSFORMATION MAY BE WORTH TRYING?
  + Local Proof Outline: WHAT PROOF STRUCTURE SHOULD BE ATTEMPTED?
  -> Codex next-command proposals
  -> IsaREPL verified successor states
  -> Stepwise search policy and branch scoring
  -> independent full-script Isabelle replay
```

Premise Retrieval supplies legal names, exact statements, attributes, owners,
and provenance. Existing premises are not theorem-DAG nodes. GREEN generated
dependencies are placed before dynamically retrieved records so their complete
statements cannot be truncated from the bounded Stepwise prompt.

The Automation Advisor stays deliberately small. It proposes only local probes:

- `simp add:` for retrieved defining equations whose exact stem occurs in the
  current state;
- plain `simp`;
- guarded `option.splits` or `if_split` cleanup when that syntax occurs in the
  current state;
- plain `wp` only when both the state is Hoare-shaped and retrieval contains a
  Hoare-shaped legal fact.

Isabelle decides whether each probe is legal and whether it advances the state.
The Advisor does not dump or simulate the complete active simpset/WP table, and
it does not expand into a general tactic recommender.

`local_proof_outline.py` is a separate initial planning module. It receives the
fixed node, certified GREEN generated dependencies, and retrieved legal
premises. It returns at most eight semantic milestones with exact dependency
names, but no proof command, proof body, new theorem, or graph edit. The same
outline is included in every state prompt, while current-state residuals remain
the authority for the next command. If the model output is invalid, a bounded
deterministic dependency-order fallback is used. The planner request, raw model
stream, final response, validated outline, and fallback reason are logged.

The tested hosted Codex transport does not support the requested `gpt-5.6`
identifier and returned HTTP 400. Outline planning therefore uses audited
`gpt-5.5` for this experiment; this is recorded rather than silently changing
the configured model.

### Success semantics

Stepwise search acceptance and node acceptance are distinct:

```text
search GREEN -> reconstruct proof commands -> independent full-theory replay
                                            -> replay GREEN or replay RED
```

Only replay GREEN is a node GREEN. A search-GREEN/replay-RED result is now
classified as `STEPWISE_REPLAY_MISMATCH`; it is never published or reused as a
premise. Search exhaustion is `STEPWISE_SEARCH_EXHAUSTED`.

### Retry result

Three no-leak KHeap_R cases were retried after adding the outline and tightening
the Advisor:

- `getEndpoint_corres` reached the correspondence introduction and several
  generated-dependency obligations, but did not construct the remaining typed
  endpoint witnesses and branch-local Isar structure.
- `regen_getEndpoint_concrete_preserves_state` still unfolded `getObject`,
  `lookupAround`, and `magnitudeCheck`, ending with nine structural subgoals.
  Local simp/split advice was insufficient; the missing capability is stable
  multi-subgoal composition, not premise availability.
- `regen_setObject_other_cte_not_tcb_relation_cut` was marked GREEN by the
  Stepwise tree search, but independent replay rejected its fourth `apply` with
  an existential witness equality still open. The final result is RED and
  exposed the need for the status distinction above.

The result is therefore partial: premise visibility is improved, local
automation contributes useful branches, and the outline improves search
direction, but no new replay-GREEN theorem was obtained in this retry. The
remaining bottleneck is executing a long structured outline across local
assumptions and multiple subgoals without losing the selected dependency path.

### Stepwise shell parity and response decoding

The first helper retry exposed two harness defects that must not be counted as
theorem-generation failures:

1. `_target_source` removed `context begin interpretation Arch .`, while the
   independent node checker preserved it. Search and replay therefore checked
   different declarations. Stepwise targets now preserve the exact certified
   node context; a regression test checks this parity.
2. Codex sometimes writes literal Isabelle symbols such as `\<lambda>` inside
   otherwise valid JSON. Because a single backslash is not a JSON escape, the
   fallback parser treated the entire JSON object as one tactic. The parser now
   repairs only unescaped `\<...>` boundaries before decoding, with the actual
   witness-response format covered by a regression test.

After both repairs, helper round 7 performed 12 audited state calls in the same
`Arch` context and returned `STEPWISE_SEARCH_EXHAUSTED`. It reached explicit
witness reasoning but selected `undefined` for the capability/CTE contents,
leaving `cap_relation undefined x1`; case analysis then expanded this into 12
capability subgoals. This is valid negative evidence about witness selection and
branch scoring. It is not a context, transport, JSON, timeout, or premise
availability failure.

The independent replay gate remains mandatory even after context parity is
fixed. `STEPWISE_REPLAY_MISMATCH` is retained as a defensive diagnostic for any
future search path that cannot be reconstructed.
