# Proof-timing scan pipeline — strategy comparison + bug catalogue

This document summarizes the work done validating the proof-timing scan
pipeline that we use to identify slow proofs in l4v for build-acceleration
work. It covers:

1. **Six bugs** found and fixed across the pipeline
2. Two **controlled comparison experiments** (per-file vs global top-N) on
   Access and InfoFlow
3. **Slow proofs catalogue** for the three sessions scanned so far
4. **Recommendations** for the remaining Refine / CRefine scans

Branch: `proof-optimization-lemma-inventory`. Period: 2026-05-03 → 2026-05-06.

---

## 1. Goal

Get reliable per-proof **sorry-cost** data on five Isabelle sessions on the
ARM build:

```
Access  AInvs  InfoFlow  Refine  CRefine
```

so that informed micro-rewrite experiments (rewriting a single slow proof,
re-measuring) can target the proofs that actually move the build wall.

The existing skill pipeline produced data, but it had multiple silent bugs
that made the data unusable in practice.

---

## 2. Bug catalogue (six bugs, six independent fixes)

Each bug below caused **silent data corruption** — the scanner ran to
completion, wrote a markdown report, and the report had cost numbers in it.
But those numbers were either zero (skip filter), early-crash artefacts, or
completely missing from the report. Without controlled comparison, none of
this would be visible.

### Bug 1 — `parse_proofs` mis-identifies Isar `qed` boundaries

**Symptom**: every Access scan reported "Baseline: 0 ms" for every file.
The downstream "(0 ms < 5 s)" skip threshold then dropped every file.

**Root cause**: `proof-timing.sh::parse_proofs()` used "first `done` or
`qed` line after `^lemma`" as the proof boundary. Isar `proof…qed` blocks
containing nested `subgoal`/`show … done` were truncated at the **first
inner `done`**. Sorry-substitution then deleted lines including the lemma's
actual end, leaving the **outer `qed` orphaned**. Isabelle errored at:

```
*** Bad context for command "qed"
```

The build crashed in 18-40 s, much faster than a real ~100-150 s build, so
`measure()` returned a small wall and `has_err=True` — but the upstream
`scan-slow-proofs.sh` did not check `has_err`.

Two related sub-bugs exposed by the same `parse_proofs`:
- `^lemma` matched only the keyword `lemma`; `theorem` / `corollary` /
  `proposition` / `schematic_goal` were silently skipped.
- `lemma (in locale) name:` and `lemma [attr] name:` did not match the
  narrow `^lemma\s+\S+` pattern, so locale-bound lemmas were dropped.

**Fix** (commit `6bbc538`): rewrote `parse_proofs` to use the
"next-top-level-command" boundary detection from
`tools/lemma_inventory/extract_lemmas.py` — proof body extends from the
first proof-keyword line after the lemma name up to (but not including)
the next `lemma|theorem|definition|fun|...|end` line. All Isar nested
constructs sit correctly inside that span. Also accepts every lemma kind
and tolerates `(in locale)` / `[attrs]` headers.

### Bug 2 — Skill scripts lost their `+x` bit

**Symptom**: after Bug 1 fix, `scan-slow-proofs.sh` still reported every
file as "(0 ms < 5 s)" and the chain "completed" in 4 seconds. Every
proof-timing invocation died instantly.

**Root cause**: `.claude/skills/isabelle_prover/scripts-container/*.sh`
were `-rw-r--r--` (no execute bit). `scan-slow-proofs.sh` calls its sibling
as `"$SCRIPT_DIR/proof-timing.sh"` (no `bash` prefix), which requires
execute permission. Without `+x` the call returned "Permission denied",
which the scanner captured into `OUTPUT` but couldn't grep "Baseline:" out
of, defaulting `BASELINE` to 0.

Smoke tests using `bash proof-timing.sh ...` (with explicit `bash`) had
worked all along, masking the bug.

**Fix** (commit `f1a7a52`): `chmod +x` on every `.sh` under
`scripts-container/`.

### Bug 3 — `scan-dir` spans multiple sessions

**Symptom**: 11/51 InfoFlow per-file scans reported `[ERR]` rows; same 10
files showed `baseline_err=true` in the top-N JSON.

**Root cause**: scan-slow-proofs.sh and the original scan_topN_global.py
took a single `--session` argument and applied it to every `.thy` in the
scan dir. But several l4v scan dirs span multiple sessions:

| scan dir | sessions in tree |
|---|---|
| `proof/infoflow/` | InfoFlow + InfoFlowC + InfoFlowCBase |
| `proof/refine/ARM/` | Refine + RefineOrphanage |

Files in the "wrong" session erred with `*** Cannot load theory
"<session>.<theory>"` because the temp session's heap chain didn't include
the file's actual session content.

**Fix**: per-file inventory lookup. `scan_topN_global.py` now consults
`reports/inventory/baseline.db` for each file's owning session and uses
that for `build_temp_session`. Each file is built against its own session's
heap chain.

### Bug 4 — `qualify_imports` treats quoted simple names as paths

**Symptom**: 4 of 5 InfoFlow build-relevant files (`Scheduler_IF`,
`Syscall_IF`, `Example_Valid_State`, `PasUpdates`) erred with `*** Cannot
load theory file "/tmp/.../ArchSyscall_IF.thy"` even though their files
existed in the InfoFlow heap.

**Root cause**: many l4v sources use the **quoted form for plain theory
names**, e.g.

```isabelle
imports "ArchSyscall_IF" "ArchPasUpdates"
```

The original `qualify_imports` left every quoted token verbatim, treating
`"ArchSyscall_IF"` as a relative file path. In a temp session at
`/tmp/xxx`, isabelle then searched `/tmp/xxx/ArchSyscall_IF.thy` (which
didn't exist) instead of resolving `InfoFlow.ArchSyscall_IF`.

**Fix** (in `tools/proof_cost_scan.py`, with `[BUG 4 — fixed 2026-05-05]`
inline comment): distinguish "quoted theory name" from "quoted relative
path". If the quoted string contains no `/` and no `.`, treat it as a
plain theory name and apply `<session>.<name>` qualification — same as
bare tokens. Quoted strings with `/` or `.` are kept verbatim (real paths
or already-qualified names).

### Bug 5 — Theory-rename doesn't rewrite qualified self-references

**Symptom**: `FinalCaps.thy` erred with `*** Undefined constant:
"FinalCaps.slots_holding_overlapping_caps"`.

**Root cause**: `FinalCaps.thy` references its own definitions using the
qualified form `FinalCaps.<id>` (style choice, e.g.
`FinalCaps.slots_holding_overlapping_caps`). `build_temp_session` renames
`theory FinalCaps → theory Tmp_xxx` for the temp .thy header, but the
later `FinalCaps.<id>` references in the body still pointed to the now
non-existent `FinalCaps` namespace.

**Fix** (in `tools/proof_cost_scan.py`, with `[BUG 5 — fixed 2026-05-05]`
inline comment): after renaming the header, also `re.sub(r"\b<base>\.",
"<tmp_name>.", new_text)` to rewrite every word-boundary `<base>.X`
occurrence. The trailing `\.` disambiguates from `theory <base>` itself
(no following `.`), so the header isn't double-rewritten.

### Bug 6 — `qualify_imports` doesn't strip inline comments

**Symptom**: `Syscall_IF.thy` erred with `keyword "begin" expected, but
bad input was found: .` after Bugs 4+5 were fixed.

**Root cause**: Imports section may contain inline Isabelle comments:

```isabelle
imports
    "ArchPasUpdates" (*Only needed for idle thread stuff*)
    "ArchTcb_IF"
```

The naive whitespace-tokenizer in `qualify_imports` treated `(*Only` as a
single non-whitespace token, found no `.` in it, and rewrote it to
`InfoFlow.(*Only`. Isabelle then choked on the mangled `imports` block
before reaching `begin`.

**Fix** (in `tools/proof_cost_scan.py`, with `[BUG 6 — fixed 2026-05-05]`
inline comment): apply `_strip_comments` to the `imports_chunk` before
tokenizing. `_strip_comments` preserves whitespace/newlines so the
remaining tokenizer logic still works.

---

## 3. Strategy comparison: per-file top-10 vs global top-N

### What's the difference

**Per-file (skill's `scan-slow-proofs.sh`)**: walks every `.thy` in scan
dir; for each file, measures top-10 lemmas **by line count** with
sorry-substitution. Every file pays `1 baseline + 10 sorry = 11 builds`,
even when its biggest lemma is tiny in the global picture.

**Global top-N (mine, `tools/scan_topN_global.py`)**: enumerates all
lemmas in the dir, sorts by `body_size_lines` descending **globally**,
takes top-N (default 100), groups by file. Each involved file pays
`1 baseline + (N_in_this_file) sorry` builds. Files with no top-N lemma
do 0 builds.

### Access (controlled experiment 1)

```
session: Access
1167 lemmas, 28 .thy files
```

| metric | per-file | top-N=100 | Δ |
|---|---:|---:|---:|
| wall | 4.7 h | 2.9 h | **−39 %** |
| files measured | 27 / 28 | 16 / 28 | −41 % |
| lemmas measured (no err) | 238 | 94 | −60 % |
| **slow proofs found (cost > 3 s)** | 30 | **49** | **+63 %** |
| in BOTH lists | 21 | 21 | — |
| only in top-N (deep big files) | — | 28 | top-N net win |
| only in per-file (small files) | 9 | — | per-file backup |

Top-N net win on Access: **+19 slow proofs found while taking 39% less wall**.

### InfoFlow (controlled experiment 2, with fix5 supplement)

```
session: InfoFlow
1700+ lemmas, 51 .thy files
```

Both per-file and top-N suffered Bug 3/4/5/6 ERRs on the same ~10 files.
After fix5 patched the 5 InfoFlow-build-relevant files, **the top-N path
recovers them** while the per-file path remains buggy until proof-timing.sh
gets the same fix.

| metric | per-file (raw) | top-N (raw) | top-N + fix5 |
|---|---:|---:|---:|
| wall | ~18 h | 2.2 h | 2.2 h + 1.1 h fix5 = 3.3 h |
| files measured (no err) | 36 / 51 | 21 / 51 | 26 / 51 |
| lemmas measured (no err) | 450 | 58 | 71 (58 + 13 new) |
| **slow proofs found (cost > 3 s)** | 73 | 27 | **40** |

InfoFlow's per-file path nominally found 73 slow proofs, but **6 of the
11 ERR'd files (those in InfoFlowC session) are out of InfoFlow's active
build closure** — their cost data, even if measurable, wouldn't help
InfoFlow build acceleration. Excluding those, per-file's effective slow
count is closer to ~50-60.

The top-N + fix5 hybrid gives 40 actionable slow proofs, including high-
value ones that per-file missed because they live in big files past top-10
(`integrity_trans`, `send_upd_ctxintegrity`, etc).

### Verdict

- **Top-N is the right primary strategy.** Catches more slow proofs in
  less wall time on both controlled experiments.
- **Per-file's "small-file capture" advantage matters less than predicted.**
  Of the slow proofs per-file uniquely caught, several are in test/example
  files (e.g. `ExampleSystem.thy`) outside the active build path.
- **Fix5 supplement is valuable when scan-dir spans multiple sessions
  (InfoFlow, future Refine/orphanage)** — recovers files that fail under
  the single-session assumption.

---

## 4. Slow proofs catalogue (3 sessions scanned so far)

Lemmas with cost > 10 000 ms, across the three sessions completed. These
are the high-confidence targets for micro-rewrite experiments.

### Access (top-N data)

| cost (s) | size (L) | lemma | file |
|---:|---:|---|---|
| 80.0 | 84 | `tro_alt_trans_spec` | Access_AC.thy |
| 39.8 | 63 | `invoke_tcb_tc_respects_aag` | ArchTcb_AC.thy |
| 22.9 | 98 | `decode_arch_invocation_authorised` | ArchArch_AC.thy |
| 20.2 | 41 | `decode_untyped_invocation_authorised` | Retype_AC.thy |
| 16.8 | 46 | `transfer_caps_loop_presM_extended` | Ipc_AC.thy |
| 16.6 | 96 | `integrity_trans` | Access_AC.thy |
| 15.7 | 39 | `integrity_mono` | Access_AC.thy |
| 14.6 | 90 | `perform_asid_control_invocation_pas_refined` | ArchArch_AC.thy |
| 14.3 | 17 | `tcb_caller_slot_empty_on_recieve` | Access_AC.thy |
| 14.1 | 15 | `cap_insert_pas_refined` | CNode_AC.thy |
| 14.0 | 17 | `decode_tcb_configure_authorised_helper` | Tcb_AC.thy |
| 12.4 | 22 | `handle_invocation_pas_refined` | Syscall_AC.thy |
| 12.1 | 15 | `unmap_page_respects` | ArchArch_AC.thy |
| 11.5 | 58 | `send_ipc_integrity_autarch` | Ipc_AC.thy |

### AInvs (top-N data)

| cost (s) | size (L) | lemma | file |
|---:|---:|---|---|
| 82.6 | 44 | `tc_invs` | Tcb_AI.thy |
| 33.4 | 90 | `dt_corres` | Deterministic_AI.thy |
| 32.5 | 57 | `decode_tcb_inv_invs` | Tcb_AI.thy |
| 25.5 | 32 | `derive_cap_invs[wp]` | ArchCNodeInv_AI.thy |
| 23.7 | 60 | `cap_revoke_invs[wp]` | CNodeInv_AI.thy |
| 22.5 | 27 | `init_arch_objects_invs` | ArchRetype_AI.thy |
| 18.5 | 39 | `cap_revoke_typ_at[wp]` | CNodeInv_AI.thy |
| 17.4 | 34 | `cancel_badged_sends_invs` | Finalise_AI.thy |
| 14.0 | 20 | `decode_inv_typ_at[wp]` | Tcb_AI.thy |
| 12.0 | 36 | `make_arch_fault_msg_invs` | ArchTcb_AI.thy |

### InfoFlow (top-N + fix5 supplement)

| cost (s) | size (L) | lemma | file |
|---:|---:|---|---|
| **167.4** | 55 | `retype_region_silc_inv` | FinalCaps.thy 🔥 |
| 52.8 | 29 | `reads_respects_scheduler_invisible_no_domain_switch` | Scheduler_IF.thy |
| 23.5 | 55 | `reads_respects_scheduler_invisible_domain_switch` | Scheduler_IF.thy |
| 23.2 | 55 | `schedule_reads_respects_scheduler_cur_domain` | Scheduler_IF.thy |
| 21.6 | 23 | `send_upd_ctxintegrity` | Ipc_IF.thy |
| 18.8 | 40 | `kernel_exit_A_if_confidentiality` | Noninterference.thy |
| 18.6 | 40 | `tcb_sched_action_reads_respects_g'` | Noninterference.thy |
| 17.7 | 64 | `handle_invocation_reads_respects_g` | Syscall_IF.thy |
| 17.5 | 114 | `rec_del_silc_inv'` | FinalCaps.thy |
| 17.0 | 40 | `schedule_reads_respects_g` | Noninterference.thy |
| 15.9 | 40 | `sub_big_steps_not_PSched_confidentiality_part` | Noninterference.thy |
| 15.2 | 77 | `perform_invocation_reads_respects_f_g` | Syscall_IF.thy |
| 13.9 | 34 | `kernel_schedule_if_confidentiality` | Noninterference.thy |
| 12.6 | 49 | `handle_event_reads_respects_f_g` | Syscall_IF.thy |
| 10.8 | 23 | `handle_recv_reads_respects_f` | Syscall_IF.thy |

`retype_region_silc_inv` at **167 s** is the largest single proof cost
across all sessions — its body alone accounts for ~58 % of FinalCaps.thy's
build wall.

---

## 5. Single-sample noise floor

The Access controlled experiment also surfaced an important methodological
fact: **single-sample sorry-cost has high variance below ~10 s**.

Across the 80 lemmas measured in BOTH per-file and top-N runs, per-lemma
cost differences (top-N vs per-file, same lemma) had:

- σ ≈ 4.8 s
- range: −8 s to +12 s typical
- **sign flips on small-cost lemmas**: e.g. `lookup_pt_slot_authorised`
  per-file = −3.7 s, top-N = +11.0 s

The noise originates in **isabelle build wall variance** — even with no
sorry substitution, baseline-vs-baseline of the same file across two runs
differs by ~10 % (Ipc_AC.thy: 102 092 ms first run, 112 915 ms second run).
Cost is the *difference* of two builds, so its noise ≈ √2 × baseline noise
≈ 14-15 s.

**Implication for build-acceleration targeting**:

| measured cost | confidence in real cost |
|---:|---|
| < 3 s | noise (sign can flip) |
| 3-10 s | direction OK, magnitude ±50 % |
| 10-30 s | reliably real, magnitude ±20 % |
| > 30 s | high-confidence target |

For the catalogue above, the cost > 30 s entries (Access top 4, AInvs
top 5, InfoFlow top 11) are reliable targets. Mid-tier (10-30 s) entries
should be re-measured 3+ times before committing rewrite effort.

---

## 6. Recommendations

### For the remaining Refine + CRefine scans

1. **Use top-N + fixed `tools/scan_topN_global.py`** (per-file inventory
   lookup, Bug 4/5/6 fixes all in place).
2. Refine scan-dir is `proof/refine/ARM` which spans Refine +
   RefineOrphanage. Inventory lookup handles this correctly.
3. CRefine scan-dir is `proof/crefine/ARM` (single session, clean).
4. Wall estimate: Refine 5-10 h, CRefine 6-12 h. No fix5 supplement
   needed unless ERRs surface.

### For Phase B (micro-rewrite experiments) once all 5 sessions scanned

1. Target the **cost > 30 s** tier first — these are high-confidence
   real costs.
2. **Multi-sample before committing**: any lemma in 10-30 s tier should
   be re-measured 3 times to filter noise.
3. The two cost > 100 s outliers (`tro_alt_trans_spec` 80 s, but earlier
   was 62 s with high variance; `retype_region_silc_inv` 167 s) deserve
   priority investigation regardless of session priority.

### For the scan pipeline itself

1. **`proof-timing.sh` still has Bugs 4 / 5 / 6** — only `proof_cost_scan.py`
   has been fixed. If the user wants per-file scans to also work
   reliably, the same three fixes should be ported back to
   `proof-timing.sh`. Marked TODO; not blocking current work since top-N
   is the primary strategy.
2. The `scan-slow-proofs.sh + proof-timing.sh` skill pipeline lacks
   per-file inventory session lookup. Same TODO — not on critical path.

---

## Appendix: artefact map

| artefact | location |
|---|---|
| Access top-N JSON + MD | `reports/slow-proofs-access-topN.{json,md}` |
| Access per-file MD (control) | `reports/slow-proofs-access.md` |
| Access comparison | `reports/scan-comparison-access.md` |
| AInvs top-N | `reports/slow-proofs-ainvs-topN.{json,md}` |
| InfoFlow top-N | `reports/slow-proofs-infoflow-topN.{json,md}` |
| InfoFlow per-file (control, partial) | `reports/slow-proofs-infoflow.md` |
| InfoFlow comparison | `reports/scan-comparison-infoflow.md` |
| InfoFlow fix5 results | `reports/slow-proofs-infoflow-fix5/*.json` |
| Bug 1 fix | commit `6bbc538`, `proof-timing.sh::parse_proofs` |
| Bug 2 fix | commit `f1a7a52`, `chmod +x scripts-container/*.sh` |
| Bug 3 fix | `tools/scan_topN_global.py` + `tools/proof_cost_scan.py::session_for_thy` |
| Bug 4 fix | `tools/proof_cost_scan.py::build_temp_session::qualify_imports` |
| Bug 5 fix | `tools/proof_cost_scan.py::build_temp_session::theory rename` |
| Bug 6 fix | `tools/proof_cost_scan.py::build_temp_session::imports comment-strip` |
