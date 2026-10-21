# Repository planning revision

Status: implemented for independent Refine data preparation, planning, theory scheduling, and direct checker/toolchain inputs; the full 287-contract proof run remains.

## Goal

Place a small data-organization module before Repository Planner. The planner should reason over the complete fixed-contract set, the legal Isabelle session context, likely contract relations, reusable existing facts, and possible shared helpers. It should produce a theory-level organization and priority plan; File Planner remains responsible for exact helper statements and the local proof DAG.

The prepared seL4 tree is the benchmark input. Removing the original target-session proofs is dataset preparation, not a runtime harness stage. Refine and CRefine are separate experiments and separate prepared datasets; no generated or supplied result flows from one experiment into the other. The first implementation targets Refine only. Neither the Repository Planner nor the new proof-run interface exposes environment IDs. A dataset-local checker context binds the sanitized l4v, Semantic Base, heaps, Isabelle installation, and immutable image identity; large assets may be linked rather than copied.

## Evidence from the original l4v layout

- `Refine` has parent `BaseRefine` and declares `Lib`, `CorresK`, and `AInvs` as session dependencies (`verification/l4v/proof/ROOT`).
- Refine theories directly import theories outside their own session, for example `BaseRefine.Include`, `AInvs.AInvs`, `AInvs.KernelInit_AI`, and `Lib.MonadicRewrite`.
- `CBaseRefine` declares `Refine` as a session dependency, and `Include_C.thy` directly imports `Refine.Refine`. CRefine therefore has a real cross-session proof dependency, not merely a build-order dependency.

Consequence: Repository Planner must see the legal session/theory visibility closure. Otherwise it will propose helpers that already exist upstream and miss important relations between contracts.

## New module: planning input preparation

Proposed file: `regen/preparation/planning_input.py`.

It consumes the already prepared repository, builds an immutable parent-premise database, and emits one `repository-planning-input.json` containing:

- the target stage and architecture;
- every fixed contract, including exact statement, owned context, attributes, and original owner theory;
- the ROOT session graph and resolved visible-session closure;
- the theory import graph for the target scope;
- a compact summary of the parent-premise database;
- deterministic relation hints between contracts, such as shared constants, shared definitions, owner/import proximity, and common candidate facts;
- content hashes and an input audit, used internally for reproducibility but not exposed as environment identities.

The database is target-relative. It contains only facts from the strict upstream session closure of the benchmark target:

1. for a Refine benchmark, facts from `BaseRefine`, its ancestors, and legal session dependencies such as `AInvs`, `Lib`, and `CorresK` are eligible; Refine and CRefine facts are absent;
2. facts are included only when they are available through an allowed parent theory/import context, not merely because their source file is in an upstream directory.

CRefine preparation is deliberately out of scope for this revision. When added, it must have its own sanitized contracts, parent-premise database, plan identity, run, and evaluation; it must not consume results from the Refine experiment.

Generated facts are not inserted into the immutable parent database. They remain in the run commit/fact store and become queryable only after checking and sealing.

The input validator rejects missing/duplicated contracts, facts outside the resolved visibility closure, forbidden historical target proofs, inconsistent owner metadata, unbounded source text, and instruction-like fields. Repository Planner receives structured proof data only, with no shell, filesystem, network, or arbitrary file-reading capability.

## Parent-premise database record

Proposed artifact: `parent-premises.sqlite`, accompanied by a reproducible metadata manifest. Each fact records only information needed for visibility, retrieval, and later Isabelle confirmation:

- identity: stable premise ID, canonical name, declaration name, and fact index;
- proposition: exact statement and normalized-statement hash;
- logical shape: declaration kind, assumptions/conclusion summary, constants and types mentioned;
- automation metadata: Isabelle attributes such as `simp`, `intro`, `elim`, and `wp`, plus definition equations when exported;
- ownership: owner theory, owner session, architecture, locale/context, and qualified aliases;
- visibility: allowed import/profile IDs and the session/theory path that makes the fact available to the target;
- provenance: parent build identity, theory/export hashes, source class, and checker/export evidence;
- retrieval fields: normalized tokens, symbol keys, theorem-family tags, and optional offline embedding reference.

Proof bodies are not needed by Repository Planner and are not stored in this database. A database hit proves discoverability, not applicability: the selected fact must still resolve in the concrete generated theory context.

The database should preserve three different relations rather than flattening them: session parent edges, additional session dependency edges, and theory import edges. These relations determine eligibility; lexical or embedding similarity determines only retrieval order.

## Repository Planner scope

Repository Planner should produce:

- a complete assignment of contracts to generated theories;
- evidence-backed contract-relation records;
- helper decisions of either `reuse_existing_fact` or `propose_auxiliary_intent`;
- a theory DAG and a tie-break priority among currently ready theories;
- a short rationale for grouping, reuse, and priority.

It must not produce exact helper propositions, tactics, proofs, or a contract-level proof DAG. Those require a concrete local Isabelle context and remain File Planner work.

## Harness interfaces for the planner agent

The planner receives a compact repository summary initially, then only bounded read-only interfaces:

1. `inspect_contracts(contract_ids)` — return exact statements and owned contexts for a bounded contract set.
2. `search_parent_premises(query, contract_ids, context_profile_ids, attributes, limit)` — retrieve ranked eligible parent facts/helpers.
3. `inspect_parent_premises(record_ids)` — return exact statements, definitions, attributes, ownership, visibility, and provenance for selected results.
4. `resolve_symbols(symbols, context_profile_id)` — resolve names in an actual allowed Isabelle import context and report ambiguity or invisibility.
5. `validate_repository_plan(plan)` — check contract coverage, allowed premise/profile IDs, schema, acyclicity, and separation between theory-level intents and file-level proof details.

Search results must use stable premise IDs. A planner claim that a helper already exists is accepted only when it cites a returned legal fact ID. Absence from a bounded search is not evidence that no such helper exists.

## Repository Planner workflow and context control

Repository Planner uses a bounded five-step workflow:

1. **Orient:** receive all compact contract cards, owner-theory groups, session/import summaries, deterministic relation hints, and available context profiles. Full parent facts are not placed in the initial prompt.
2. **Inspect:** request exact contracts in related batches. Relation hints are candidates, not accepted proof dependencies.
3. **Reuse before invention:** search the parent-premise database for each group. Cite an inspected fact when reusable; otherwise emit only a possible auxiliary intent.
4. **Construct:** assign every contract exactly once, choose allowed parent context profiles, create the theory DAG, and rank currently ready theories.
5. **Validate and repair:** submit the structured plan to the deterministic validator. A correction call receives only the previous draft and compact validation errors, not accumulated search transcripts.

The initial input contains:

- target session, architecture, and benchmark policy;
- compact cards for every contract: ID, name, owner theory, normalized statement hash, principal symbols, and short logical-shape summary;
- session/import closure summary and allowed context profiles;
- bounded deterministic contract-relation candidates;
- parent-premise database counts and query interface description;
- output schema and context/tool budgets.

Exact contract statements are fetched in bounded batches and are never truncated. Parent facts are returned only on demand with hard result/count limits. Tool responses use IDs and compact records by default; exact statements are expanded only by `inspect_*`. Search transcripts do not become part of later repair prompts.

The final output contains:

- `contract_relations`: typed candidate relations with evidence IDs;
- `theories`: generated theory name, purpose, contract IDs, allowed context-profile IDs, reused parent-premise IDs, and auxiliary intents;
- `suggested_theory_edges`: provider, consumer, and reason;
- `priority_order`: tie-break order among ready theories;
- `ordering_rationale` and unresolved retrieval questions.

The output contains no proof, tactic, exact new helper statement, or contract-level proof DAG. Exact helper statements and local dependencies remain File Planner responsibilities.

## Plan identity and revisions

One repository-generation run owns one stable `plan_id`. Repository-level replanning within that run appends immutable revisions; it does not create a replacement plan ID:

- `plan_id`: stable identity of the logical repository plan for the run;
- `revision`: monotonically increasing plan-log sequence; entries retain checksums without a separate revision ID.

The authoritative layout is `planning/<plan_id>/revisions/<sequence>.json` plus a rebuildable `HEAD.json`. `repository-plan.json` is the current projection, not a second authority.

When a theory starts, its file projection and external context are frozen against one plan-log sequence. Later plan revisions may change priority, edges, grouping, or helper intents only for theories that have not started. Running, GREEN, and sealed theories are immutable constraints on every later revision.

Resume and recovery retain the same `plan_id` and revision chain. A fresh independent generation receives a new `plan_id`; explicit warm-start or experiment forks may record `derived_from_plan_id`, but do not inherit the old identity. Ordinary revision does not use `supersedes_plan_id`.

## File Planner lifecycle and graph authority

File Planner runs once when one theory first becomes ready, before any proof node in that theory is attempted. At that point the coordinator freezes the theory's repository-plan revision, exact contracts, allowed context profile, explicit sealed provider theories, and parent-premise snapshot. File Planner may use bounded premise search over that frozen legal context. It refines Repository Planner's auxiliary intents, identifies the missing reasoning links between the contracts and visible premises, and turns only those gaps into exact local helper/theorem statements and scheduling dependencies.

This is the initial graph-construction phase, not runtime replanning. File Planner owns the draft graph and may add, remove, rename, or restate generated nodes and alter their edges while responding to schema, DAG, context, duplication, and statement-elaboration errors. Fixed contract statements and the frozen external context are immutable. The initial graph is accepted only after every statement elaborates and the graph passes deterministic validation; acceptance creates immutable graph revision 1.

After graph revision 1 is committed, File Planner is not called again for that theory. Runtime graph mutation belongs exclusively to the theory-local Graph Revision Agent. Following a failed proof phase, it may issue retry guidance without changing the graph or propose an append-only graph revision that adds missing helper/theorem nodes and changes uncommitted scheduling dependencies. A split retains the failed parent node. It may not change fixed contracts, GREEN nodes, accepted proof state, or the frozen external premise context. Every proposal is validated and statement-elaborated before it becomes the next accepted graph revision.

The ownership boundary is therefore:

- File Planner: construct and repair the pre-execution draft, then commit the initial graph;
- Lemma Prover: produce proof bodies only and never modify the graph;
- Graph Revision Agent: sole owner of graph changes after execution begins;
- harness: validate, commit, schedule, and reject stale or illegal mutations.

The initial decomposition is intentionally evidence-driven rather than free-form. Each generated node records its exact statement, semantic role, intended consumers, generated prerequisites, visible-premise evidence or premise query, and a short explanation of the missing reasoning link it closes. Names follow a stable domain/operation/property convention, but identity and retrieval rely on qualified IDs and normalized statements rather than names alone. File Planner should prefer a direct contract node when no genuine reasoning gap is identified and should not create cosmetic wrappers.

## Proof-node execution and parallel scheduling

Proof-node generation remains simple: each ready node receives bounded multi-round whole-proof attempts. The prover is not required to manufacture another internal subproof hierarchy. When those rounds expose a structural obstacle, the Graph Revision Agent decides whether to retry with guidance, revise the graph, or stop. This preserves a clean separation between proving one fixed statement and changing the local proof architecture.

Multi-theory concurrency follows the repository theory DAG. The coordinator starts all resource-permitted ready theories, freezes only their explicitly planned provider set, and gives each worker an isolated checker/workspace. Theory sealing and generated-premise publication are atomic coordinator operations; the order in which unrelated theories happen to finish must not widen another theory's context.

Within a theory, the harness derives the ready frontier from the accepted File Planner graph rather than asking the planner to invent parallel groups. Expensive proof-candidate generation may run concurrently for independent ready nodes, but canonical checking, GREEN commits, graph revisions, and theory sealing remain serialized. Every candidate is bound to its graph revision, base proof commit, accepted-prefix identity, and premise snapshot. A candidate whose bindings became stale is revalidated or discarded before commit. This combines dependency-DAG scheduling and isolated workers with optimistic context consistency, while preserving deterministic Isabelle state.

## Implemented code changes

- Add `regen/preparation/planning_input.py` and focused tests.
- Build `parent-premises.sqlite` from the target-relative upstream session/import closure.
- Change `regen/planning/repository_planner.py` to consume the single validated planning input instead of environment gate/dataflow IDs.
- Extend `regen/planning/theory_plan.py` with contract relations, allowed context profiles, and explicit helper reuse/intent decisions while retaining theory-level granularity.
- Add an append-only repository-plan revision journal and freeze each started theory projection to its accepted plan-log sequence.
- Change File Planner to run at theory start against the frozen legal-premise snapshot, construct an exact evidence-backed local DAG, and repair only its pre-execution draft until validation and statement elaboration succeed.
- Make the accepted initial graph immutable and route every later graph change through the theory-local Graph Revision Agent and append-only graph revision protocol.
- Add ready-frontier scheduling with isolated theory workers; keep each theory's checker commits and Graph Revision Agent decisions serialized, and serialize shared progress/publication projections.
- Reuse and simplify `regen/preparation/premise_catalog.py` as the backing fact index, adding resolved session visibility metadata.
- Add deterministic validation tests proving that the Refine dataset excludes Refine/CRefine facts and target-session facts never enter the parent database.
- Update the harness and logging documents after the schemas are fixed.
- Add `regen/preparation/checker_context.py` and a dataset-local `checker-context.json`; schema 5 execution consumes it directly. Keep the old environment pipeline only for schema 2/3 replay and the one-time migration adapter.

## Acceptance criteria

- All fixed contracts occur exactly once in the plan.
- Every reused helper resolves to a legal visible fact.
- The theory graph is acyclic and its priority is explicit.
- Parent-session facts are discoverable without exposing target-session proofs.
- The Refine input identifies one independent benchmark and contains no CRefine data or cross-experiment inheritance metadata.
- Replanning preserves one stable `plan_id`, immutable revision history, and already-started theory projections.
- File Planner runs before the first proof attempt, and every initial generated statement elaborates in the frozen theory context.
- Once execution begins, only the Graph Revision Agent can propose graph mutations; fixed contracts, GREEN nodes, proof state, and frozen external context cannot change.
- Each proof node uses bounded multi-round whole-proof generation, with structural decomposition delegated to graph revision after failure.
- Concurrent candidates cannot commit against a stale graph, GREEN prefix, proof commit, or premise snapshot.
- The planner can operate from the prepared repository without an environment ID or unrestricted repository access.
