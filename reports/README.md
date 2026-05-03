# Slow-commands reports — audit notes

These 18 markdown files are decoded from the heap-log `command_timings`
BLOBs of each Isabelle session's `.db` file (no measurement re-runs).
Coverage and known limitations:

## Sessions covered (18)

```
Access            CBaseRefine     DRefine         InfoFlowC        Refine
AInvs             CRefine         DSpecProofs     InfoFlowCBase    RefineOrphanage
BaseRefine        DBaseRefine     InfoFlow        SepDSpec
Bisim             DPolicy         SimplExport     SimplExportAndRefine
```

## Sessions NOT covered (2)

- **AutoCorresCRefine** — broken in l4v 13.0 ARM (`Refine_C.thy` import
  path mismatch). No heap, no DB. Excluded from `run_tests` on
  X64/RISCV64/AARCH64 already; we exclude on ARM via compile.sh.

- **CRefineSyscall** — heap exists (326 MB in container, 215 MB user
  override on host), but its `theory_timings`/`command_timings` BLOBs
  were **never committed** in the original build (per build_log.txt
  trailer "heap saved but theory_timings BLOB never committed").
  No timing data recoverable.

## Inheritance overlap (READ THIS BEFORE OPTIMIZING)

CBaseRefine inherits Refine's heap. Its `command_timings` records
re-execution of Refine theories during CBaseRefine's build (especially
during future-stage proof checking). So:

  cteInsert_corres aggregate elapsed:
    Refine session report        → 302.9s
    CBaseRefine session report   → 421.1s

Both are real, but for **optimization decisions on Refine.* lemmas
use the Refine session report as canonical** — that's the time recorded
when the lemma was first checked. The CBaseRefine number includes
re-check overhead.

Same pattern for:
- CRefine inherits CBaseRefine inherits Refine
- InfoFlowCBase inherits Refine + Access
- InfoFlowC inherits InfoFlowCBase + CRefine

## `<top-level>` entries

Commands at theory top level (definitions, instances, fun declarations,
crunch macros, etc.) before any `lemma`/`theorem` block are aggregated
under the `<top-level>` placeholder. These are real time costs but are
NOT lemma proofs — different optimization tooling needed.

Example: `DPolicy` rank-2 is `<top-level>` @ 14.9s — that's
non-lemma work in `Dpolicy.thy`.

## Per-command attribution

Each individual command (apply/by/simp/etc.) is mapped to its
enclosing lemma via byte-offset bisect. Edge cases:
- Commands inside `context`/`locale` blocks attribute to the last
  preceding `lemma` declaration in the file. Usually correct.
- `crunch`-generated lemmas appear as a single `crunch` command with
  cumulative time, not as individual generated lemmas.

## Data freshness

Reports reflect the original baseline build (~Apr 30 in our timeline).
After modifying any .thy file, the relevant session must be rebuilt
(`isabelle build -c <session>`) for the DB to be updated. Re-extract
with: `python3 extract_session_command_timings.py <session.db>
/workspace reports/slow-commands-<session>.md`.
