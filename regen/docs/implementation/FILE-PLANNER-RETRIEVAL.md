# File Planner retrieval and accepted auxiliary reuse

Repository projection runs provide File Planner with explicit, audited premise
queries before its final graph proposal. `observation_request.premise_queries`
accepts one to four focused queries, with `auto`, `exact`, `definition`, or
`search` mode and an optional offset. The planner can issue further batches
within the shared planning timeout. It does not receive a fabricated proof state.

`context_contract_id` selects the exact owned context of a fixed or support
contract for native symbol resolution; omission selects global context. Unknown
contexts are rejected. Catalog sources remain frozen parents, retained semantic
exports and the run's frozen sealed imports. Unproved goals are never facts.
Symbol expansion is type/syntax evidence, not a proof certificate.

Every model response is transcript-validated. Queries, responses, returned
record IDs and the final plan are retained. A bad query returns a correction
message. Direct routes may cite returned fact IDs; graph and statement checking
still precede acceptance. Initial decomposition may be minimal and GRA can refine
it after proof feedback. No VERO-specific statements or proofs are built into
these prompts.

GRA's initial view includes historical Isabelle residuals, consecutive retry
information, consumer assumption provenance and a compact index of accepted
nodes. Its existing structural operations remain subject to immutable fixed
contracts/GREEN nodes, DAG validation, statement checking and commit validation.
SPLIT retains its parent. Repeated failure prompts a structural assessment, not
an automatic split or a budget reset.

Both GRA and Lemma Prover accept
`{"observation_request":{"auxiliary_query":{"node_ids":["accepted-node-id"]}}}`.
This returns up to four full accepted proofs, statements, original contexts and
proof digests. The harness restricts access to the checker's visible accepted
prefix; a later accepted node is unavailable when rechecking an earlier node.
New proof candidates still require complete verification. Cross-theory access
continues through frozen sealed imports, not other workers' drafts.

New code requires a new execution run because execution implementation hashes
are frozen at initialization. Use `run-session --max-parallel-theories 1` for a
single worker. The current theory runner generates one proof branch at a time,
so this also limits aggregate active proof branches to one. This does not set
Isabelle's internal CPU thread count.

Validation: 401 current regression tests passed, including iterative planner
retrieval/correction, evidence-based routes, auxiliary prefix isolation and GRA
structural evidence visibility. Mocked tests do not establish proof success;
the fresh experiment records native checking separately.
