# Premise retrieval laboratory

The subsequent production `indexed-v1` candidate pipeline, native matching,
generated visibility and versioned target memory are documented in
[Long-task premise retrieval](LONG-TASK-PREMISE-RETRIEVAL.md). The lexical
ablations below remain a separate diagnostic baseline.

This is a small, offline environment for reproducing retrieval failures and
comparing candidate strategies. It runs the current production readers as its
baseline. Experimental strategies are isolated in `regen/analysis`; they are
not installed in File Planner or used to migrate any existing database.

No model, Isabelle process, network service, or additional Python package is
required. Python's SQLite must provide FTS5, as it does for the existing premise
database. The lab does not read removed proofs.

## Run

From the repository root, start with the small diagnostic corpus:

```bash
python3 -m regen.tools.premise_retrieval_lab \
  --output /tmp/premise-lab-small-001 \
  --noise-records 0
```

Then add distractors to observe how rankings change as the corpus grows:

```bash
python3 -m regen.tools.premise_retrieval_lab \
  --output /tmp/premise-lab-growth-001 \
  --noise-records 0 1000 10000 \
  --seeds 0
```

Use a new output directory for every run. Existing directories are never
overwritten. `--seeds 0 1 2` repeats with different deterministic distractors;
these are sensitivity checks, not independent samples of real proof tasks.
Larger corpora can be requested explicitly. Start small: traversing all pages
repeats the production query and its integrity checks.

## Controlled strategy changes

| Variant | Change relative to the production baseline |
|---|---|
| `baseline` | Calls `recall_premises` or `recall_for_proof_state` directly. |
| `body_candidates` | For explicit search, retains body-only matches even when name matches exist. Keeps the original all-terms requirement and scoring. |
| `normalized_fts` | For goal queries, indexes and queries the same token representation. Keeps BM25, token chunks and reciprocal-rank fusion. |
| `digest_groups` | Groups same-digest candidates after applying the common candidate budget. Preserves representative statements and alias provenance. |
| `combined` | Applies the three changes together. |

Exact and definition lookup remain unchanged in all variants. No change learns
features or uses gold labels. `body_candidates` shares private identifier
helpers with production and deliberately reproduces its scoring; source hashes
record this dependency. The normalized index encodes each token as an ASCII
word so SQLite cannot split underscores/apostrophes a second time. It is bound
to the source database hash and its own index hash.

These ablations test specific mechanisms, not a completed retrieval redesign.
They do not add typed term matching, change source interleaving, train embeddings,
index generated theories, or implement import availability checks.

## Fixtures and judgments

`regen/analysis/premise_lab_fixtures.py` defines the synthetic records, queries,
authored relevance judgments and seeded distractors. Cases cover name/body
competition, tokenization, aliases, exact spelling, definition boundaries,
pagination, and cases where lexical retrieval cannot determine applicability.

Each query has a rationale and `judgment: "synthetic_authored"`. Positive labels
are `relevant_ids`; negative controls use `forbidden_ids` or `expected_empty`.
Labels are evaluated outside the database and are never passed to a strategy.
The synthetic digest identifies a fixture proposition group; it is not an
Isabelle term certificate. The fixture catalog has the existing v2 schema plus
an explicit laboratory marker. Its manifest checks byte integrity only and
declares `native_validated: false`. Never use this catalog as generation input.

To add a case, extend `build_fixture`, or edit a run's `fixture.json` and rerun:

```bash
python3 -m regen.tools.premise_retrieval_lab \
  --fixture /tmp/my-synthetic-fixture.json \
  --output /tmp/premise-lab-custom-001 \
  --noise-records 0 --seeds 0 \
  --variants baseline combined
```

An unknown strategy, inconsistent label, or reused output directory is rejected.
Adding a new strategy means extending the isolated strategy module, then testing
it against the same fixture and budget; it does not require changing the harness.

## Read results

- `parameters.json`: parameters, Python/SQLite versions and implementation hashes.
- `summary.json` and `REPORT.md`: mode/category summaries and per-case changes.
- `noise-N-seed-S/fixture.json`: the exact inputs and judgments.
- `noise-N-seed-S/queries.jsonl`: every request, full first-page response,
  relevant IDs, metrics, continuation pages, and all ranked IDs within budget.
- `noise-N-seed-S/catalog/`: the synthetic catalog and manifest.
- Variant directories: independently derived normalized FTS indexes, when used.

The first page defaults to five records. All variants inspect the same maximum
of 50 ranked candidates before grouping; `--candidate-budget` can lower that
number. Record counts can differ because matching algorithms differ. A grouped
candidate uses one display slot, which is the behavior being tested.

Group Recall@k counts distinct labeled proposition groups recovered on the first
page. MRR@k measures the position of the first relevant group. Queries with no
positive label are excluded from these means; expected-empty violations and
forbidden-group hits are reported separately. Duplicate proposition slots measure
alias crowding. Detailed results additionally show within-budget recall, page
termination, complete statement preservation, and deterministic replay using a
JSON-round-tripped index descriptor.

`next_offset: null` means the experimental candidate horizon is exhausted, not
that the corpus contains no further facts. Goal candidate counts are already
bounded by production's top-500-per-token-chunk candidate generation. The lab's
pagination wrapper is **not** a new production harness feature. Descriptor replay
is **not** a test of File Planner recovery or dynamic imports. Those have separate
tests in `test_retrieval_recovery_controller.py` and the import test modules.

## What the numbers establish

These are authored diagnostic cases. Improvements show that a particular
mechanism behaves better on those cases, not a measured increase in theorem
applicability or proof completion. Keep unresolved and regressing cases in the
report. A combined variant is not an independent held-out evaluation.

Times include experimental wrappers; baseline enumeration can invoke production
queries and database verification repeatedly. Index construction is recorded
separately. Do not interpret the timing comparison as a production speedup.

After a strategy survives these diagnostics, the next evaluation should replay
sanitized real targets against the facts visible at the original time, with
independent relevance evidence. A subsequent same-model, same-budget generation
comparison can measure proof completion. Neither stage is run automatically by
this laboratory.

## Regression checks

```bash
python3 -m unittest regen.tests.current.test_premise_retrieval_lab
```

The tests check baseline fidelity, budget and pagination boundaries, label
isolation, complete statements, alias grouping, and stale-index rejection.
