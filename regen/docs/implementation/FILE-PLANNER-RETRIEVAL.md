# File Planner retrieval and evidence recovery

## Current document interface

The document File Planner uses `proof_action` with `query` for new candidates and
`read_evidence` for recovering known evidence. The premise database, ranking
algorithms, retrieval scopes and proof authority are unchanged by this interface
consolidation. Native diagnostics and symbol resolution retain their existing
operations; cross-theory discovery still uses `list_generated` / `read_generated`.

Subsequent changes add explicit `indexed-v1` bounded goal retrieval with optional
native typed matching, generated visibility metadata, and versioned usage
evidence. See [Long-task premise retrieval](LONG-TASK-PREMISE-RETRIEVAL.md) for
the current parameters, lifecycle semantics and fast validation commands.

`read_evidence` replaces four separately advertised operations: `read_facts`,
`read_queries`, `read_proofs`, and `read_context_index`. Those names remain accepted
by the controller for compatibility. New prompts, examples, generic tool schemas
and pagination links advertise the consolidated operation. Frozen historical
tool registries remain auditable through exact compatibility shapes, without
loosening schema validation or rewriting old receipts.

Read one or more previously returned references:

```json
{
  "action": "read_evidence",
  "refs": [
    {"kind": "fact", "id": "record ID returned by query"},
    {"kind": "query", "id": "saved query ID"},
    {"kind": "proof", "id": "accepted local entry ID"}
  ]
}
```

There are at most 24 refs total, at most four query refs and four proof refs.
The harness groups kinds into the existing serial observation transaction, with
per-group results in `observations`; this preserves memory selection and durable
recovery. A failure in one group does not make another group's successful read a
failure. Fact records retain complete propositions; proof refs return accepted
local declarations, contexts and proof bodies. No database search is repeated.

For navigation, use the mutually exclusive index form:

```json
{
  "action": "read_evidence",
  "index": "facts",
  "entry_id": "target entry ID",
  "offset": 0,
  "limit": 32
}
```

`index` is `facts` or `queries`. Omit `entry_id` for the full existing index.
Follow the returned `next_action`, which retains the target and index snapshot.
An unrelated target's new results do not invalidate a target-filtered snapshot;
changes to that target's index do. Index reads are standalone and do not submit
a checker operation. Ref reads may also be grouped inside `observe`, subject to
its existing limit on expanded read groups.

## Target retrieval memory and restart

`read_entry` now returns `retrieval_memory` alongside the current source entry and
planning advice. It derives the view from existing observation receipts/state;
there is no new database or persistent state format. For a target-specific query,
provide `context_entry_id`. Queries without a target remain global and are not
assigned to a contract by name or similarity. A newly drafted helper with no
history returns empty memory and remains readable.

The default view contains the most recent eight target-bound queries and eight
distinct complete candidate records, with totals and `read_evidence` continuation
actions. This is a display bound, not a change to query ranking or candidate
selection. It never shortens propositions. Restarting a controller or model
session does not suppress previously returned text. Global query counts and an
unfiltered navigation action remain available separately.

Restored facts are marked `is_proof_authority=false` and
`availability=requires_current_context_check`. New receipts record target and
environment bindings; old revisions are never inferred from opaque identities.
The view compares target/import/provider/prefix bindings and exposes separate
returned, explicitly read, attempted-proof-text and accepted-proof-text evidence.
Textual mentions are not native dependency or necessity certificates. Unattributed
reads remain unattributed; failed attempts do not blacklist facts. Related query
contexts and mention lists have display bounds with totals and continuation reads.

Imports revisions retain the existing retirement rules. This view does not
repopulate cleared fact/query caches from old environments. Legacy query summaries
still in retained history can remain navigable, with missing fact-record counts;
old full evidence must be read through retained original receipts. Reading memory
never adds imports, restores GREEN, changes fixed contracts or certifies a premise.

Implementation: `planning/evidence_reads.py`, `planning/retrieval_memory.py`,
`planning/document_interface.py`, `planning/parallel_file_planner.py` and
`planning/document_recall.py`. Tests include real task-receipt cold recovery with
synthetic catalog/checker results, target/global isolation, complete statements,
target pagination, imports retirement and exact historical registry compatibility.
These tests do not establish a model-quality or proof-success improvement.

## Legacy graph-mode retrieval and accepted auxiliary reuse

The following describes the older graph/prover/GRA paths, not the current
continuing document File Planner. Their historical contracts are preserved.

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

Historical validation: 401 regression tests passed, including iterative planner
retrieval/correction, evidence-based routes, auxiliary prefix isolation and GRA
structural evidence visibility. Mocked tests do not establish proof success;
the fresh experiment records native checking separately.
