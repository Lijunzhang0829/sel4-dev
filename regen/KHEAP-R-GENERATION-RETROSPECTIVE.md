# KHeap_R Generation Retrospective

## Scope and result

This report records the first complete Proof-theory generation run in the
modular Refine pipeline. It is operational guidance for later theories, not a
claim that the complete Refine session has been generated.

- Run: `reports/regen/proof-runs/refine-main`
- Theory: `Refine.KHeap_R`
- Final graph: v77
- Generated nodes and contracts: 122/122 Isabelle GREEN
- Fixed contracts: 5/5 GREEN
- Complete-theory check: GREEN in 1,530 seconds
- `Generated_Refine` session build: GREEN in 1,511 seconds
- Generated source: 3,537 lines, with no `sorry`, `oops`, or diagnostic marker

The five fixed contracts are `getEndpoint_corres`,
`getNotification_corres`, `setEndpoint_corres`,
`setNotification_corres`, and `setObject_other_corres`.

## Evidence boundary

This was also a harness-development run. Graph v77 includes accepted graph
transactions produced while the planner, runner, Stepwise relay, salvage layer,
and Graph Revision transaction logic were being repaired. The revision number
must not be interpreted as 77 independent semantic discoveries, and the elapsed
time/call count must not be reported as a clean autonomous benchmark result.

Some final diagnosis and assembly stabilization were initiated from this
operator conversation, although every accepted statement and proof was checked
by the isolated Isabelle environment and every external model call retained its
transcript. KHeap_R is evidence that the repaired pipeline can generate and
integrate this theory; the next untouched theory is the appropriate test of how
much of that capability now runs without conversation-level intervention.

## What the run established

### 1. File planning is a hypothesis, not a finished proof architecture

The initial File Planner graph was far too small for the semantic work exposed
by Isabelle. Later graph versions added lookup, projection, state-relation,
ready-queue, map-to-CTE, relation-cut, and no-fail obligations. This is expected:
the planner sees fixed contracts and legal declarations but no proof execution.

Future File Planner output should therefore be judged by whether it provides a
sound first decomposition and useful semantic boundaries. Node count or graph
stability at v1 is not a quality target. The planner must not compensate by
inventing long chains of cosmetic wrappers.

### 2. Guards belong to the semantic interface of a generated theorem

Several early statements were false or unusable because they omitted alignment,
object-size, successor-separation, object-kind, or parent guards. In particular,
`loadObject`, `lookupAround2`, `updateObject_default`, and relation-cut arguments
cannot be planned from the final relation alone.

When a failed theorem is split, each child must retain the parent assumptions it
needs. A split is invalid if it makes a child stronger by silently deleting a
guard. Statement certification catches syntax and types; it does not prove that
the proposed statement is semantically true.

### 3. Free-variable spelling and type flow require deterministic checks

The v75 statement of
`regen_setObject_other_updateObject_default_checks_no_fail` fixed `ob'` but used
`obj'` in `projectKO_opt ko = Some obj'`. Isabelle therefore inferred an
independent result type. Decision 307 corrected only that generated statement.

The v76 `regen_setObject_other_updateObject_default_no_fail` left its projected
object underconstrained at `pre_storable`; decision 308 fixed `obj'` at the same
`'a::pspace_storable` type as `ob'`.

These are planning defects, not hard proof-search failures. Before proof search,
the certifier should check:

- every free variable in assumptions and conclusions is intentionally bound;
- similarly named variables such as `ob'` and `obj'` are not accidental aliases;
- a producer's result type unifies with every consumer occurrence;
- polymorphic monadic results remain typed when the goal observes only `snd`.

### 4. `snd` can erase the type needed to instantiate polymorphic monads

The residual `not (snd (projectKO ko s))` does not constrain the result element
type. Generic `projectKO` facts then elaborate at unrelated type instances. The
successful graph added `regen_setObject_other_projectKO_typed_no_fail`, whose
statement explicitly annotates the complete result type.

The reusable rule is: if a residual observes only the failure bit of a
polymorphic computation, carry an explicit computation type or first derive a
typed execution equation. Repeated type-unification failures must be classified
as statement/consumer mismatch rather than proof-search exhaustion.

### 5. Premise retrieval and premise application are different capabilities

Many rounds retrieved the right facts but failed to instantiate them in the
right order. Typical symptoms were:

- `THM OF: multiple unifiers`;
- the same small residual after broad `simp` or `auto`;
- a correct bridge theorem present in dependencies but re-proved from definitions;
- a candidate naming unavailable or incorrectly qualified facts.

The successful response was structured Isar with named assumptions, explicit
`where` parameters, intermediate `have` facts, and direct application of GREEN
generated dependencies. Retrieval success must never be counted as proof
progress by itself.

### 6. Local proof-outline salvage is useful only with an authoritative prefix

For long proofs, restarting from the theorem header repeatedly lost already
valid structure. Salvage became useful when it received:

- the last checker-accepted local prefix;
- the first rejected command and exact residual;
- previous candidates in the same round;
- the current legal premises and GREEN dependencies.

Stepwise repair should resume from that local state. It should not rebuild the
whole Isar proof unless the prefix itself is invalid. Within one round,
candidate N must receive candidate N-1 and its Isabelle diagnostic. Across
rounds, the next request must receive the strongest prior candidate, retrieval
history, and Graph Revision guidance.

### 7. Graph Revision must distinguish proof errors from graph errors

The useful classifications in this run were:

- `PROOF_BODY_ERROR`: statement and dependencies match; retry with focused repair;
- `STATEMENT_CONSUMER_MISMATCH`: variable, type, guard, or producer/consumer mismatch;
- `GRAPH_DEPENDENCY_GAP`: a genuinely new semantic bridge is required;
- duplicate/cosmetic node: reuse a stronger existing node;
- infrastructure/transport failure: do not charge proof or graph budget.

Decision 309 correctly retained graph v77 and told the prover to apply the
existing no-fail dependency. Decision 311 likewise retained v77 and replaced an
ambiguous `OF` chain with explicit structured application. A proposed revision
must still pass deterministic transaction checks; an LLM request to add a node
does not itself create a graph version.

### 8. Node GREEN is necessary but not sufficient for file GREEN

Each node is checked with only its transitive generated dependencies. A complete
theory loads all 122 declarations into one context. Earlier declarations can
enlarge simp, classical, congruence, and attribute pools, so a proof that is fast
in a small dependency closure may become intractable after concatenation.

The first complete checks timed out at 1,800 and 3,600 seconds. Diagnostic node
markers identified two context-sensitive proofs:

1. `regen_setObject_other_projectKO_objBits` changed from `simp add` to
   `simp only` with two named definitions.
2. `regen_setEndpoint_state_relation_update` changed its final `auto` to one
   `clarsimp` after all component relations had already been proved.

Both replacement bodies were checked individually and then together in the
complete theory. No statement, dependency, or graph node changed. The final
complete check finished in about 25.5 minutes.

Consequently, the runtime must preserve three separate states:

- node GREEN;
- complete-theory GREEN;
- generated-session GREEN/built.

### 9. Long checker work must remain visible

Model think calls have a 180-second hard timeout and emit progress events.
Isabelle node and complete checks emit 30-second heartbeats. The final session
build originally emitted no heartbeat and looked like a stalled conversation
for roughly 25 minutes, although Poly/ML was using CPU and eventually succeeded.

The checker now emits `isabelle_build_started`, 30-second
`isabelle_build_heartbeat`, and `isabelle_build_finished` events. A successful
`built` or `sealed` transition also removes stale `failed_node` and `reason`
fields from the file ledger.

Provider transport is a separate failure class. The attempted `gpt-5.6` Graph
Revision call was rejected by the current ChatGPT Codex account before a model
response existed; the successful retry used `gpt-5.5`. Unsupported-model,
network, relay, and think-timeout failures must be logged but must not consume a
logical proof attempt or be presented to Graph Revision as Isabelle evidence.

## Budget used for continuation

The active per-node policy is recorded in `run.json`:

- model think timeout: 180 seconds;
- Isabelle/Stepwise round timeout: 1,800 seconds;
- whole-proof candidates per round: 5;
- prover rounds per node: 10;
- whole-proof ceiling per node: 50;
- structural revisions charged to one node: 16.

Budgets are ceilings, not targets. A repeated residual should be classified
before spending all remaining attempts. Infrastructure failures do not consume
logical proof rounds. A structural revision invalidates only changed nodes and
their consumers; unrelated GREEN nodes remain immutable.

## Mandatory policy for the next Proof theory

### Before File Planning

1. Load all fixed contracts for the file at once.
2. Expose only sanitized declarations and legal premise metadata.
3. Include exact type information for polymorphic operations and relation endpoints.
4. Record one audited planner call and reject vague or cosmetic routes.

### Before proving a node

1. Certify the complete statement shell and graph.
2. Run variable-binding, sort-flow, producer/consumer, and parent-guard checks.
3. Retrieve premises for the current statement and latest residual, not only the
   theorem name.
4. Prefer direct generated dependencies over re-expanding definitions.

### After a RED attempt

1. Preserve candidate source and exact Isabelle residual.
2. Determine whether the first failure is syntax/type, local proof control,
   missing premise, false statement, or graph structure.
3. Resume from the strongest checked prefix for long proofs.
4. Ask Graph Revision only after proof-local repair and retrieval evidence are
   insufficient.
5. Reject new helpers that merely restate an existing GREEN equation or projection.

### Before declaring a file built

1. Assemble nodes in certified graph order.
2. Run a marker-instrumented complete-theory check; markers are diagnostic only
   and must not enter generated source.
3. If complete checking is much slower than node checking, replace broad
   automation with `simp only`, explicit rules, or structured conjunction proofs.
4. Re-run the complete theory after every assembly-only repair.
5. Build the real `Generated_Refine` session and publish premises only after it
   is GREEN.

## Harness work derived from repeated failures

Implemented:

- exact active-graph binding and immutable graph versions;
- candidate-to-candidate and round-to-round feedback propagation;
- local proof-outline salvage from checker residuals;
- explicit `projectKO` type-erasure guidance and candidate prechecks;
- corrected DELETE/replace graph transaction handling;
- diagnostic complete-theory node markers;
- session-build heartbeat and timeout cleanup;
- stale failure metadata cleanup on `built`/`sealed`;
- assembly-salvage evidence with old/new proof hashes and checker logs.

Required next:

- automate free-variable spelling and producer/consumer sort-flow checks before
  expensive proof search;
- make complete-theory timeout a first-class repair event instead of raising an
  unclassified runner exception;
- add an assembly stabilization stage that can invalidate and recheck only the
  context-sensitive proof body while preserving the graph;
- report the last diagnostic node marker automatically on timeout;
- audit and minimize generated theorem attributes before downstream publication;
- summarize repeated residual fingerprints so the Graph Revision Agent sees
  semantic history rather than only the last candidate.

## Evidence

- `theories/KHeap_R/graph-v77.json`
- `theories/KHeap_R/progress.json`
- `theories/KHeap_R/complete-check.json`
- `theories/KHeap_R/session-build.json`
- `theories/KHeap_R/assembly-salvage/evidence.json`
- `theories/KHeap_R/assembly-salvage/*.log`
- `theories/KHeap_R/llm/graph-decision-*.{prompt,ndjson,final,audit}.txt`
- `generated/KHeap_R.thy`
- `generated/build.log`

All paths above are relative to
`reports/regen/proof-runs/refine-main/`.
