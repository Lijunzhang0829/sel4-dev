# Verified premise catalog and proof-state retrieval

New Refine runs use `regen.preparation.premise_database`, schema version 2.
The old `planning_input.prepare` writer and schema remain compatibility code for
existing preparations; its owner-session fallback has been removed. Do not use
that legacy catalog as the final catalog for a new proof run.

## Admission and evidence

`premise_source_evidence` loads the sealed `Semantic_Base` heap in a container
with networking disabled. It exports the loaded theory registry and session
qualifiers, and resolves every fact by declaration name plus zero-based index.
For each member it records the certificate theory, dynamic-collection flag,
hidden-hypothesis count, printed-proposition digest and typed term digest.

Admission requires a registered, non-wrapper certificate theory, agreement with
the export owner, an exact printed-proposition match, no hidden hypotheses, and
an allowed source/session pair. Frozen parents must belong to the upstream
closure; retained semantic facts must belong to `Semantic_Base`. Target human
proof facts are forbidden. Public canonical names must match the verified
name/index. Dynamic collections are quarantined even when their current
certificate names a registered theory: context transfer obscures member origins.
A collection namespace or derivation-name hint is not origin evidence.

Rejected record IDs and reasons live in `quarantine.jsonl`, outside SQLite.
The manifest binds export bytes, native evidence, exporter program, sealed
checker context, and database bytes. The planner and checker bind the new database
hash. The typed digest is explicitly labelled `isabelle-term-yxml-sha1`; it is
not a digest of pretty text or a claimed SHA256 identity. Artifact integrity uses
SHA256. Equal propositions are not merged: different public names and source
contexts can remain useful.

## Stored fields

| Field | Consumer/purpose |
| --- | --- |
| `record_id` | Stable selection, planning references and audit |
| `canonical_name` | Public lookup and proof reference |
| `declaration_name`, `fact_index` | Locate the native fact witness |
| `exact_statement` | Complete premise shown to planner/prover; retrieval |
| `proposition_digest` | Typed proposition identity in source evidence |
| `owner_theory`, `owner_session` | Registered source attribution and admission |
| `source_class` | Frozen parent / retained semantic source policy |
| `attributes_json` | Explicit attribute filtering |

Architecture, context profiles and source-export provenance are dataset-level
metadata. Repeated aliases, pretty-text hashes, regex-derived constants/types/
logical shape, duplicated definition equations/search text, empty locale/provenance
objects, repeated visibility paths, and null embedding references are removed.
`premise_search` is an external-content FTS5 index over name and statement; its
SQLite shadow tables are derived search structures, not additional fact stores.
The optional vector index is a separate rebuildable artifact.

## Retrieval and harness interface

The design borrows stepwise's **query from the current goal and hypotheses**.
The inspected stepwise implementation uses Sledgehammer/MaSh relevance from the
actual REPL state (`Auto_Isabelle.ML`, `_extract_hammer_facts`). This implementation
uses FTS5 and optional local vectors; it does not claim to implement MaSh or to
load any historical Refine proof cache. Proof examples are not imported.

Python callers with an actual proof state use:

```python
from regen.execution.proof_state_recall import recall_for_proof_state
page = recall_for_proof_state(database, proof_state, limit=5,
                             vector_index=optional_index)
```

Whole-proof prover and GRA can explicitly request:

```json
{"observation_request":{"proof_state_query":true,"use_vectors":true}}
```

`use_vectors` is optional. The harness supplies the node's complete fixed goal
and owned context; the request cannot substitute its own state. Whole-proof
rounds have no live intermediate REPL state, so this interface queries the node
entry state. A caller with a live REPL state can use the Python interface above.
The batch returns at most five frozen facts, with complete statements, and writes
its state digest and selected IDs to the round audit. Existing exact/definition
queries still work. This state query searches the verified base catalog; imported
sealed facts and current GREEN facts remain available through existing scoped
queries. A catalog candidate is not a proof: references and applicability must
still pass Isabelle in the current generated theory context.

Vectors can be built over either or both `canonical_name` and `exact_statement`.
The bundled encoder is deterministic sparse SHA256 feature hashing with log term
frequency and L2 normalization, stored as compact integer/float pairs. It is a
lexical vector representation, **not a learned semantic embedding**. FTS and
vector rankings are combined with reciprocal-rank fusion; vectors are opt-in.
Index hashes and the source database hash reject corrupt or stale indexes.
Neither building nor querying vectors makes external API calls.

## Reproducible preparation

The seed dataset supplies the already validated contracts, session closure and
sealed checker assets. It is left unchanged.

```sh
python3 -m regen.preparation.premise_source_evidence \
  --dataset SEED_DATASET --output NATIVE_EVIDENCE
python3 -m regen.preparation.premise_database \
  --dataset SEED_DATASET --export FULL_LEGAL_PREMISE_EXPORT \
  --evidence NATIVE_EVIDENCE --output NEW_DATASET
python3 -m regen.execution.proof_state_recall \
  --database NEW_DATASET/parent-premises.sqlite \
  --build-vectors NEW_DATASET/premise-vectors.sqlite \
  --fields canonical_name exact_statement
```

Create the new checker context with `checker_context.create_context` using the
same sealed assets, and prepare repository planning with the new
`repository-planning-input.json`. Immutable outputs are never overwritten.
The first run now has `dataset-v2` and `repository-plan-v2`; the adoption record
is `reports/regen/refine-first-run-001/database-refactor.json`. Original frozen
inputs remain available for audit. No external planning call is part of this
migration.
