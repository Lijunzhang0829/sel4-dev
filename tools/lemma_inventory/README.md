# lemma_inventory

Source-level inventory of every lemma-like declaration in the l4v ARM build,
plus a structural diff between two inventory snapshots.

The intended use is **before/after accounting** when we modify proof sources
to speed up the build: take a baseline snapshot, edit theories freely
(restructure, parallelise, split), then diff. Anything that changed identity
(removed lemma, new `sorry`, statement-text drift) shows up explicitly.

Inventory is a *necessary* but not *sufficient* check. It detects "did the
named lemma still exist with the same statement", which is the part that's
trivial to break by accident during refactoring. **Real soundness is established
by `isabelle build` succeeding with `quick_and_dirty=false` and zero new
`sorry`/`oops`** — see [the validation invariants](#validation-invariants) below.

## Contents

| file | purpose |
|---|---|
| `parse_roots.py` | Parse all l4v `ROOT` files → session DAG + `theory→session` map. |
| `extract_lemmas.py` | Scan one `.thy` file → list of lemma records. |
| `build.py` | Walk the l4v tree, extract every lemma, store in SQLite + write summary. |
| `diff.py` | Compare two SQLite snapshots, print structural diff in markdown. |

## Quickstart

```bash
# 1. Build a baseline snapshot (run once on the canonical sources):
python3 tools/lemma_inventory/build.py \
    --l4v verification/l4v \
    --out reports/inventory/baseline.db \
    --summary reports/inventory/summary.md

# 2. After editing .thy files, build a "candidate" snapshot:
python3 tools/lemma_inventory/build.py \
    --l4v verification/l4v \
    --out /tmp/candidate.db \
    --summary /tmp/candidate_summary.md

# 3. Diff (exit code 1 if any blocking change is detected):
python3 tools/lemma_inventory/diff.py \
    reports/inventory/baseline.db /tmp/candidate.db \
    --out /tmp/diff.md
```

## What gets recorded

For every `lemma` / `theorem` / `corollary` / `proposition` / `schematic_goal`
/ `lemmas` declaration found, we store:

| field | meaning |
|---|---|
| `kind` | one of the keywords above |
| `name` | identifier (or `<anon@LINE>` for unnamed lemmas) |
| `attributes` | parsed `[simp]`, `[wp]`, `[corres]`, `in:locale_name`, … |
| `theory`, `line` | source location |
| `statement_norm` | statement text after collapsing whitespace and stripping comments |
| `statement_sha256` | SHA-256 of `statement_norm` (the diff identity) |
| `body` | proof script text (only used to detect `sorry`/`oops`) |
| `has_sorry` | true if the body contains `sorry`, `oops`, or `sledgehammer` |
| `is_anonymous` | true if no name |
| `session` | owning Isabelle session (resolved from ROOT files) |

The diff key is `(session, theory, name)` for named lemmas. Same name in
two theories of the same session is *legal Isabelle* (e.g.
`transferCaps_corres` lives in both `Ipc_R.thy` and `Tcb_R.thy`); we treat
them as distinct entities. Anonymous lemmas key on `(theory, line, kind)` and
will false-match across edits — that's a known limitation and the reason the
inventory tracks anonymous count separately.

## Scope filter

The build is `L4V_ARCH=ARM` (see `docker-compose.yml`), so the inventory only
walks ARM-relevant sources. Excluded:

- arch-specific subtrees that aren't ARM: `AARCH64`, `ARM_HYP`, `RISCV64`, `X64`
- non-build subtrees: `camkes`, `tutorial`, `EVTutorial`, `doc`, `.git`
- auto-generated theory files under `**/build/**/generated/**`

Six theories under `misc/` and `tools/proofcount/` are scanned but unassigned
to any session — these are utility files not declared in any ROOT.

## Heuristic limitations (read before trusting)

The extractor is regex-based. It does **not** invoke Isabelle's outer-syntax
parser. Known things it gets approximately right but not perfectly:

1. **Statement boundaries**. The "statement" is taken as everything between
   the lemma name and the first line whose first token is a proof keyword
   (`apply`, `proof`, `by`, `using`, `unfolding`, `done`, `qed`, `oops`,
   `sorry`, …). Statements that span unusual constructs (e.g. `if … then
   apply …`) may include too much/too little; the SHA will still be stable
   across runs but the captured text may differ from what Isabelle parses.

2. **Anonymous lemmas** (`lemma "stmt" by simp`) get a synthetic name
   `<anon@LINE>` keyed on file+line+kind. They will appear as
   removed+added on any line shift in their file. Only ~630 in total
   (≈1.7%); ignore them in diff noise.

3. **Implicit dependencies**. `[simp]`, `[wp]`, `[intro]`, `crunch`-derived
   facts and `declare` statements influence proof success but are NOT
   captured as edges. The inventory only sees the lemma block itself.
   This is the main reason the diff cannot replace `isabelle build`.

4. **`crunch` macros** appear as a single `crunch`/`crunches` invocation;
   the dozens of facts it generates are not enumerated. (`reports/` has
   the same limitation since the heap-log timing BLOB also attributes
   crunch time to the macro invocation.)

5. **`lemmas`** (fact bundle) declarations have no proof, so `has_sorry`
   is always false and the "statement" is the right-hand-side fact
   expression. Useful for tracking renamed bundles but not for
   correctness gating.

## Output schema

`reports/inventory/baseline.db` is a SQLite file with three tables:

```sql
CREATE TABLE sessions (
  name TEXT PRIMARY KEY, parent TEXT, session_dir TEXT,
  imported_sessions TEXT, root_file TEXT
);

CREATE TABLE theories (
  path TEXT PRIMARY KEY, session TEXT,
  total_lemmas INT, total_sorry INT
);

CREATE TABLE lemmas (
  theory TEXT, line INT, kind TEXT, name TEXT,
  statement_sha256 TEXT, statement_norm TEXT,
  attributes TEXT,                    -- JSON-encoded list
  has_sorry INT, is_anonymous INT, session TEXT,
  PRIMARY KEY (theory, name, line)
);
```

Indices on `name`, `session`, and `statement_sha256` for fast lookups.

## Validation invariants

When using inventory diffs to gate proof-source edits, the invariants are:

| check | source | gate |
|---|---|---|
| no removed lemmas | `diff.py` exit code | hard block |
| no new `sorry` / `oops` | `diff.py` exit code | hard block |
| every changed-statement reviewed | `diff.py` markdown report | manual ack |
| every affected session builds | `isabelle build -c <session>` | hard block |
| heap fingerprint chain still consumed by downstream sessions | next session's build success | hard block |

The first three live in this tool. The last two require running Isabelle
(see top-level `compile.sh` and the `isabelle_prover` skill).

## Round-trip evidence

`reports/inventory/round_trip_demo.md` is the diff produced by perturbing one
.thy file with three deliberate edits (rename a lemma, replace its proof
with `sorry`, tweak another lemma's statement) and rebuilding the inventory.
It demonstrates that the diff catches all three categories and collapses
incidental line-shift noise.
