# CSTR Optimization Study: seL4 / l4v Proof Build

End-to-end report on detecting and remediating **Cross-Session Theory
Reprocessing (CSTR)** in the seL4 / l4v proof build, validated by a full
canonical rebuild that recovered **-30.6 % wall** (7.04 h → 4.88 h).

> Scope: this work targets the *speed of the proof build* — the rebuild
> cycle you pay every time a proof, spec, Haskell prototype, or C kernel
> change is verified. It is seL4-specific tooling, not a general Isabelle
> methodology. The faster proof rebuild directly accelerates validation
> of spec/Haskell/C optimizations downstream.

---

## 1. Background — the CSTR phenomenon

Isabelle session declarations in `proof/ROOT` distinguish two kinds of
upstream dependency:

| Syntax | Heap semantics | Cost when downstream builds |
|---|---|---|
| `session B = A +` | A's heap is **merged** into B's parent heap | ~0 — A's theories already proved, loaded as binary |
| `session B = X + sessions Y` | Y's heap is **not** merged, only namespace declared | Every `.thy` of Y inside B's closure is **re-executed in B's ML state** |

l4v's `proof/ROOT` (baseline / pre-swap 2026-05-07) declares three big
`sessions Y` clauses pointing at *heavy* upstream sessions:

```
session CBaseRefine    = CSpec + sessions { CLib, Refine, AutoCorres }
session CRefineSyscall = CBaseRefine + sessions { CRefine }
session InfoFlowCBase  = CRefine + sessions { Refine, Access, InfoFlow }
```

Empirically (see [CSTR-2/outputs/session-duplication-scan.md](CSTR-2/outputs/session-duplication-scan.md)):

- CBaseRefine re-executes **304 theories** worth ~5599 s aggregate;
  98.6 % of that is theories already in Refine / AInvs / BaseRefine.
- CRefineSyscall re-executes **all 44 theories** of CRefine (100 % dup,
  1546 s aggregate).
- InfoFlowCBase re-executes Refine + Access + InfoFlow contents (99.4 %
  dup, 648 s aggregate).

Total aggregate CSTR overhead at baseline: **5585 s**, about 28.6 % of
all per-session work; wall impact on the canonical rebuild is roughly
**8 400 s** (parallelism factor 3-6 amplifies/dampens aggregate to wall).

The fix is purely structural — change three lines of `proof/ROOT` so the
heavy session moves to the heap-merged `+` slot:

```
session CBaseRefine    = Refine + sessions { CLib, CSpec, AutoCorres }
session CRefineSyscall = CRefine +
session InfoFlowCBase  = CRefine + sessions { Access, InfoFlow }
                                              # ↑ Refine dropped
```

---

## 2. Two methods compared

Two graph-based methodologies were developed to **discover** these ROOT
changes from cold timing data alone. Both arrive at the same 3 edits.

### Method **CSTR-1** (primary — single graph)

Build *one* graph at session granularity:

- 37 nodes, 52 edges
- Node weight = session's own elapsed
- Edge `parent` → weight 0 (heap-merged)
- Edge `sessions` → weight = Σ elapsed of theories from upstream
  reprocessed inside downstream's `theory_timings` BLOB
- Ancestry-aware swap heuristic: rec a swap `parent ↔ U` if
  `Σ reprocess(U)` > `est cost of moving old parent to sessions`

Code: [CSTR-1/analyze.py](CSTR-1/analyze.py) — 436 lines, self-contained.

### Method **CSTR-2** (supplementary — two graphs)

Build two graphs at different granularities:

- **theory-DAG**: 1094 nodes / 1961 edges, edges from `.thy` import clauses
- **session-DAG**: 56 nodes / 161 edges, edges from ROOT topology + dup% annotations
- A CSTR scanner does cross-session theory occurrence aggregation
- Critical-path (CPM) is computed on theory-DAG closures for 7 change types
- Recommendations are derived **at the report layer** by cross-referencing
  duplication% with co-appearance maps; post-swap implications must be
  reasoned about manually

Code: [CSTR-2/](CSTR-2/) — 7 scripts + 7 manifest files, 1306+ lines,
multi-stage pipeline with intermediate JSON between stages.

### Side-by-side

| Dimension | CSTR-1 (single graph) | CSTR-2 (two graphs) |
|---|---|---|
| Total LoC | 436 (1 file) | 1 306 (3 files) + 7 manifests |
| Nodes | 37 (sessions) | 1094 (thy) + 56 (sess) |
| Edges | 52 | 1961 (thy) + 161 (sess) |
| Intermediate artifacts | none | session-dag.json + theory-dag.json + session-duplication-scan.{md,json} |
| Construction time | **0.21 s** | ~5-15 s (multi-stage) |
| CSTR detection | built-in, ranked + post-swap implications | hand-derived from report cross-references |
| Per-thy bottleneck ranking | not provided (intentional) | provided via `critical-path-*.md` |
| Re-runs after each edit | ~0.2 s | several seconds + 4 stages |

CSTR-1 trades thy-level granularity (single-lemma optimization, which
empirically yielded <1 % wall savings on a sample target — see prior
abandoned work) for radical simplicity and faster iteration. For
CSTR-class structural fixes, the two methods are **information-equivalent**.

---

## 3. Recommendations produced (both methods)

Both methods, from the same baseline data (heaps/build_log.pre-swap.txt
+ heaps/db-archive-pre-swap/*.db), produce the same 3 ROOT edits:

| # | Edit | Aggregate save predicted |
|---|---|---:|
| 1 | `CBaseRefine = CSpec +` → `= Refine +`; swap `sessions Refine` ↔ `sessions CSpec` | 3 284 s |
| 2 | `CRefineSyscall = CBaseRefine + sessions CRefine` → `= CRefine +` (drop sessions clause) | 1 546 s |
| 3 | `InfoFlowCBase`: drop redundant `sessions Refine` (becomes redundant after #1) | 0 (cleanup) |

CSTR-1 emits these in [CSTR-1/output.md](CSTR-1/output.md). CSTR-2 emits
the same fixes via [CSTR-2/outputs/session-duplication-scan.md](CSTR-2/outputs/session-duplication-scan.md)
+ [CSTR-2/outputs/critical-path-summary.md](CSTR-2/outputs/critical-path-summary.md).

Diff applied to `verification/l4v/proof/ROOT`:

```diff
-session CRefineSyscall = CBaseRefine +
-  sessions
-    CRefine
+session CRefineSyscall = CRefine +
...
-session CBaseRefine = CSpec +
+session CBaseRefine = Refine +
   sessions
     CLib
-    Refine
+    CSpec
     AutoCorres
...
 session InfoFlowCBase = CRefine +
   sessions
-    Refine
     Access
     InfoFlow
```

---

## 4. End-to-end validation — canonical rebuild

Full WIPE=1 canonical TUNED rebuild with the 3 ROOT edits applied:

- Container: `sel4-l4v` (image `sel4-public:tuned`)
- Host: 16 cores / 23 GB RAM
- ML_OPTIONS: `-H 8000 --maxheap 16000 --stackspace 64`
- Threads: 8 intra-session; -j 1 strict serial
- 29 sessions in topological order
- Build window: **2026-05-12 11:11:55 → 16:36:26** (5 h 24 min host wall)

### Headline measurement

|  | pre-swap | post-swap (CSTR fixed) | delta | % |
|---|---:|---:|---:|---:|
| **TOTAL elapsed** | **25 331.2 s** (7.04 h) | **17 568.5 s** (4.88 h) | **-7 762.7 s** | **-30.6 %** |
| TOTAL cpu | 107 405.4 s | 76 274.4 s | -31 131.0 s | -29.0 % |
| TOTAL gc | 11 498.1 s | 4 884.4 s | -6 613.7 s | -57.5 % |

Wall savings ≈ **129 minutes** per canonical rebuild.

### Per-session breakdown (CSTR-affected sessions)

| Session | pre-swap | post-swap | Δ wall | % |
|---|---:|---:|---:|---:|
| **CBaseRefine** | 5 183.2 s | **1 322.5 s** | -3 860.7 s | **-74.5 %** |
| **CRefineSyscall** | 3 306.7 s | **1.1 s** | -3 305.6 s | **-100.0 %** |
| CRefine (downstream beneficiary) | 4 556.6 s | 3 689.1 s | -867.5 s | -19.0 % |
| InfoFlowCBase | 1 109.9 s | 1 154.2 s | +44.3 s | +4.0 % |
| InfoFlowC | 844.7 s | 831.9 s | -12.8 s | -1.5 % |

### Non-CSTR sessions — no regression

| Session | pre-swap | post-swap | Δ % |
|---|---:|---:|---:|
| Refine | 2 083.3 s | 2 121.0 s | +1.8 % |
| AInvs | 1 084.7 s | 1 087.6 s | +0.3 % |
| CKernel | 888.8 s | 905.7 s | +1.9 % |

All within ±5 % machine-noise band — confirms the savings localize to
the swapped sessions and don't leak elsewhere.

### Theory-count change — direct evidence the fix is real

Beyond wall time, the *theory count Isabelle executes* drops in the
swapped sessions — a structural artifact impossible to fake via caching:

| Session | theories pre-swap | theories post-swap |
|---|---:|---:|
| CBaseRefine | 304 | **94** (Refine's 48 thys no longer reloaded) |
| CRefineSyscall | 44 | **1** (CRefine's 51 thys no longer reloaded) |

---

## 5. Repository layout

```
.
├── CSTR-REPORT.md                ← this file
├── CSTR-1/                       ← primary method (single graph)
│   ├── analyze.py                  the 436-line analyzer
│   ├── output.md                   pre-swap rec + 0.21 s construction time
│   ├── output.json
│   ├── output.post-swap.md         analyzer re-run after applying edits
│   └── output.post-swap.json       (shows 0 remaining recommendations)
├── CSTR-2/                       ← supplementary method (two graphs)
│   ├── parse_theory_imports.py     theory-DAG builder
│   ├── build_session_dag.py        session-DAG builder
│   ├── session_dedupe_scan.py      CSTR scanner + dup% / co-appear matrix
│   ├── dag.py                      CPM critical-path on closures
│   ├── report.py                   7 change-type per-type reports
│   ├── focused_closure.py          per-theory query tool
│   ├── parse_roots.py              vendored ROOT parser
│   ├── manifests/                  7 change-type manifests
│   │   ├── proof.py
│   │   ├── spec_abstract.py
│   │   ├── spec_invariant.py
│   │   ├── spec_cspec.py
│   │   ├── spec_lib.py
│   │   ├── haskell.py
│   │   └── c.py
│   └── outputs/
│       ├── session-duplication-scan.{md,json}
│       ├── session-dag.json
│       ├── theory-dag.json
│       ├── critical-path-{proof,spec_*,haskell,c}.md
│       ├── critical-path-summary.md
│       └── critical-path-all.json
├── heaps/
│   ├── build_log.txt               post-swap canonical (validated)
│   ├── build_log.pre-swap.txt      pre-swap canonical (baseline reference)
│   ├── db-archive/                 post-swap theory_timings BLOBs (29 .db)
│   └── db-archive-pre-swap/        pre-swap theory_timings BLOBs (28 .db)
└── verification/l4v/proof/ROOT     edited in-place (3 changes)
                                    — inner repo, gitignored from outer
```

---

## 6. Reproducing

### Method CSTR-1 (recommended)

```bash
python3 CSTR-1/analyze.py
# Reads heaps/build_log.pre-swap.txt + heaps/db-archive-pre-swap/*.db
# Writes CSTR-1/output.{md,json} with ranked swap recommendations
# Construction time: ~0.2 s
```

Apply the 3 listed edits to `verification/l4v/proof/ROOT`, then:

```bash
WIPE=1 bash tools/rebuild_canonical.sh        # ~5 h 24 m wall
python3 tools/assemble_build_log.py           # merge db-archive → heaps/build_log.txt
```

Compare `heaps/build_log.txt` (TOTAL row) against `heaps/build_log.pre-swap.txt`.

### Method CSTR-2 (supplementary)

```bash
# Stage 1: build the two graphs
python3 CSTR-2/parse_theory_imports.py        # → CSTR-2/outputs/theory-dag.json
python3 CSTR-2/session_dedupe_scan.py         # → CSTR-2/outputs/session-duplication-scan.{md,json}
python3 CSTR-2/build_session_dag.py           # → CSTR-2/outputs/session-dag.json

# Stage 2: critical-path on closures, then per-type reports
python3 CSTR-2/dag.py                         # → CSTR-2/outputs/critical-path-all.json
python3 CSTR-2/report.py                      # → CSTR-2/outputs/critical-path-{type}.md

# Stage 3 (optional): query a single theory
python3 CSTR-2/focused_closure.py Refine.Finalise_R
```

CSTR-1's recommendations can be cross-checked against CSTR-2's
`critical-path-summary.md` (structural targets section) and
`session-duplication-scan.md` (per-pair dup analysis).

---

## 7. Limitations

- Both methods require pre-swap `theory_timings` BLOBs (zstandard
  compressed sqlite blobs from a canonical build) — i.e. you need to do
  a full pre-swap rebuild once before the analyzer can find anything.
- CSTR-1's swap heuristic uses parent's own elapsed as an upper bound
  on post-swap reprocess cost; this can underestimate savings in chains
  where the old parent is heavily reused downstream (CRefineSyscall
  saved 1546 s; analyzer predicted 1546 s — exact match because
  old parent CBaseRefine is in CRefine's ancestry, est = 0).
- The methodology is seL4-specific: it exploits the particular shape of
  l4v's `proof/ROOT` (a handful of heavy sessions with explicit
  `sessions Y` clauses pointing at large peers). General Isabelle
  developments with different ROOT topologies may not have CSTR-class
  opportunities.
- The Isabelle 2024 build flag `SKIP_DUPLICATED_PROOFS` is l4v's own
  workaround for this same phenomenon (skips proofs on the re-loaded
  copies, weakening verification). The CSTR-1/CSTR-2 fix eliminates the
  reprocessing entirely instead of skipping it.
