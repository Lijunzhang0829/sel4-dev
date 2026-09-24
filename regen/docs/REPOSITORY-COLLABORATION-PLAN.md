# Repository collaboration planning protocol

New workspace planning policies select `proposal_schema_version: 6`. Existing
policy files without this key retain schema 5; schema 3/4/5 plans remain readable
under their original import semantics. Never rewrite a frozen plan or running
theory to adopt the new grouping.

Schema 6 keeps the complete schema-5 theory fields, including `required_imports`,
and adds two top-level arrays:

```json
{
  "collaboration_groups": [
    {"id": "OperationFamily", "contract_ids": ["C0001", "C0002"],
     "reason": "Related state access/update operations warrant joint exploration."}
  ],
  "reuse_suggestions": [
    {"provider": "Generated_Access", "consumer": "Generated_Update",
     "reason": "A common invariant may support both operations."}
  ]
}
```

These are additional fields, not a complete proposal. Groups may overlap and
span physical theories. Artifact construction derives their `member_theories`.
Reuse suggestions may be reciprocal; endpoints must be known distinct theories.
Both relations are advice: they add no import, scheduling prerequisite, proof
edge, available fact, or authority to cite an unsealed result. Empty arrays are
valid. Only `required_imports` form the mandatory DAG and override the theories
array's ready priority. Imports still freeze after their providers seal.

The prompt asks for grouping by shared semantic operations, access/update
behavior and invariants. It leaves precise helper statements and proof routes to
File Planner. Contract visibility is frozen per run. `owner-blind-v1` preserves the original
hidden-ownership baseline; `source-theory-visible-v1` exposes only `owner_theory`
on sanitized contracts/cards, with the same opaque IDs. The latter is the default
for new Astra-medium comparison runs following the user request of 2026-09-24.
Source theory names are reference metadata, not a prescribed new allocation.
Original proof bodies, deleted helpers and the original import graph remain unavailable. No case-specific theorem or helper is hard-coded in the prompt.

## Pure construction and regrouping

```python
from regen.planning.import_contract import build_bundle_v6, safe_repartition

artifacts = build_bundle_v6(payload, proposal)
# Or use the exact payload that validated an existing schema 4/5/6 plan:
replacement = safe_repartition(payload, old_plan, replacement_proposal)
```

`payload` includes frozen declarations, owned contexts/attributes, context
profiles, parent-premise binding and inspection evidence. For an accepted
workspace plan, reconstruct it as:

```python
payload = {**read_json("planner-validation-input.json"),
           "planner_evidence": read_json("planner-evidence.json")}
```

`safe_repartition` checks this payload against the old plan's input digest and
checks unchanged exact-once contracts and semantic contexts. The replacement
proposal explicitly specifies every new assignment and hard import. Changing
old hard imports is allowed in this new plan; all new hard edges are checked for
cycles. Unknown profiles, premises and advisory endpoints are rejected. Exact
statements and contexts come from the frozen payload, never model substitutions.

Both functions return fresh in-memory artifacts and leave all inputs untouched.
The plan/projection schema is 6; the file-generation-list schema is 7. Projection
hashes are rebound after adding advice. Regrouping records its source plan digest
and obtains a new plan identity; it never copies a run binding, GREEN status,
proof body or generation history. Save to a new output and start a new run at
zero proved nodes. Normal publication still requires declaration/import
preflight; this pure function does not run Isabelle or publish a plan.

This change makes possible collaboration explicit. It does not itself implement
cross-theory concurrent proving, dynamic imports, helper migration or proof
reuse. Tests establish protocol consistency and legacy compatibility, not a
proof-success or runtime improvement.
