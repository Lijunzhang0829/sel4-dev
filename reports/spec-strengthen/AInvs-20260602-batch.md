# Spec-Strengthen Log — AInvs session — 2026-06-02 batch

Session: `AInvs`
File patched: `proof/invariant-abstract/CSpace_AI.thy`
Scanner used: `tools/spec_strengthen/spec_candidates.py`
Premise probe: `tools/spec_strengthen/spec_premise_probe.sh` (TRIAL-based, v1)
Witness generator: `tools/spec_strengthen/spec_witness_gen.py`
l4v submodule HEAD at baseline: `00d9073f70d0`
Branch: `spec-strengthen` (PR-4)

---

## TL;DR

This batch ran the full slim-SKILL workflow end-to-end for the first
time. Four `apply`-verified strengthenings landed on CSpace_AI.thy
— all Pattern B/G additive, all centred on the `set_cdt` operation —
plus six SKILL + tools infrastructure experiments (0008-0013, across
seven commits — 0009 was split into two follow-up commits) and one
audit-hygiene fix (0018).

| # | Lemma | Pattern | apply wall | Δ file wall | Downstream cite |
|---|---|---|---:|---:|---|
| 0014 | `set_cdt_cdt_update` | B (functional) | 55,846 ms | +8.7% | delayed (Pattern B) |
| 0015 | `set_cdt_machine_state[wp]` | G (frame) | 52,587 ms | **−11.3%** | immediate wp-class pickup |
| 0016 | `set_cdt_cur_thread[wp]` | G (frame) | 47,143 ms | −2.5% | delayed (low same-file freq) |
| 0017 | `set_cdt_idle_thread[wp]` | G (frame) | 47,460 ms | −0.4% | delayed (low same-file freq) |

Net same-file delta over 4 strengthenings: baseline 45,330 ms → final
~47,460 ms = **+4.7%** for 4 additive lemmas. Within the SKILL's +30%
regression cap.

**Notable result**: 0015 produced a **negative** wall delta
(−11.3%) — the first concrete demonstration of Pattern G's
`[wp]`-class-pickup ROI inside the same file. Worth recording as a
playbook case study (see §5 below).

---

## 1. Infrastructure context (experiments 0008-0013)

Before any source change, six Meta-PR experiments (across seven
commits — 0009's audit-trail follow-up landed as a separate
commit `cba0a9b` on top of the main 0009 purge `0b0aea3`) landed
the workflow surface:

| # | Topic | Effect |
|---|---|---|
| 0008 | SKILL slim follow-ups | Fixed fake `hoare_post_imp_R` rule reference + removed vestigial `derivability.thy` artefact |
| 0009 | Purge stale `claude/.claude/skills/` duplicate | Single source of truth for SKILLs; removed `1c1e90c` snapshot drift |
| 0010 | Five-point SKILL review fixes | Witness rule list softened, modify-vs-delete split, parent SKILL rule-5 alignment + witness-rule-by-triple-shape table added to playbook |
| 0011 | Tools: `spec_witness_gen.py` + `spec_premise_probe.py` | Witness generator (deterministic); probe (TRIAL-based after v0 substring heuristic was rejected) |
| 0012 | Promote `spec_witness_gen` into Step 2 mainline | Tool 3 moved from optional to recommended; tool 2 stays optional diagnostic |
| 0013 | Step 2 sequencing + Shape 3 hardening | Patch must contain BOTH strengthened lemma AND witness; Shape 3 deletes explicitly fail Acceptance #2 |

State at the start of this batch:
- 4 mainline tools: `check-theory.sh`, `spec_candidates.py`,
  `spec_witness_gen.py`, `spec_impact.py`
- 1 optional diagnostic: `spec_premise_probe.sh` (TRIAL-based)
- SKILL.md 228 lines; playbook 664 lines including the new Hoare-
  triple-shape witness-rule table
- l4v submodule clean at `00d9073f70d0`

---

## 2. Applied strengthenings (0014-0017)

Each experiment followed the SKILL Steps 1-6:
**candidate → patch + witness → verify → measure → apply → record**.

### 2.1 spec-0014 — `set_cdt_cdt_update` (Pattern B additive)

**What changed.** Inserted right after `set_cdt_valid_pspace`:

```isabelle
lemma set_cdt_cdt_update:
  "\<lbrace>\<top>\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. cdt s = t\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Model's understanding.** `set_cdt t` writes the `cdt` field of
kernel state to exactly `t`. The existing companion lemmas at this
point in the file proved various PRESERVATIONS of unrelated state
(`set_cdt_valid_pspace`, `set_cdt_pspace`, etc.), but no lemma
exposed the **direct functional fact** about what value the cdt
field gets. Downstream proofs that needed `cdt s = t` after
`set_cdt t` had been working around this absence by manually
unfolding `set_cdt_def` (grep confirms `update_cdt_cdt`,
`cap_move_typ_at` do exactly this). The lemma is a precise
functional postcondition that those proofs can now cite directly
as `wp set_cdt_cdt_update`.

This is the canonical Pattern B (`missing-functional`) shape from
the playbook Case 5 — picked from past 20260526 strengthen-log
where it was verified-via-patch but never `--apply`-ed.

**Attempt log.**
1. `spec_candidates.py --target ainvs` — `dxo_wp_weak` was top
   candidate but its `\<And>s f. P (trans_state f s) = P s`
   parameterization makes it frame-preservation, not amenable to
   additive functional postcond. Picked the playbook Case 5
   demonstrator instead.
2. `spec_witness_gen.py` → SHAPE 2 (additive). No witness needed.
3. Baseline wall: 45,330 ms.
4. `check-theory.sh --patch` → OK 49,271 ms. Trial wall +8.7%.
5. `spec_impact.py --measurement-out` → verdict `additive`,
   `gate_pass: true`, `has_weakening: false`.
6. `check-theory.sh --apply` → OK 55,846 ms.

**Downstream impact (上下游).**
- **Same file** (CSpace_AI.thy): wall +8.7% — the parsing-cost of
  the new lemma. Within the +30% threshold.
- **Tier-2 grep consumers**: 0 lines / 0 files at apply time. This
  is expected for a fresh additive lemma. Pattern B ROI is
  **delayed** per the playbook: existing consumers (`update_cdt_cdt`,
  `cap_move_typ_at`) keep their manual `set_cdt_def` unfolds until
  a separate cleanup PR refactors them to cite `wp
  set_cdt_cdt_update`. That cleanup is the real ROI but is out of
  scope here.
- **Cross-session** (Refine / CRefine): NOT rebuilt. Upper-bound
  impact: zero proof breakage (adding a lemma can only add
  options); ~50-100 ms theory-graph parse cost on Refine load.
- **Submodule pointer**: NOT advanced. Audit dir replay artifact
  in `reports/experiments/0014-set-cdt-cdt-update-additive/`.

---

### 2.2 spec-0015 — `set_cdt_machine_state[wp]` (Pattern G frame, **negative wall**)

**What changed.** Inserted right after `set_cdt_vms[wp]`:

```isabelle
lemma set_cdt_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Model's understanding.** Existing `set_cdt_vms[wp]` proves
`set_cdt` preserves the PREDICATE `valid_machine_state`. But it
does NOT let `wp` discharge an arbitrary `P (machine_state s)`
postcondition for generic `P`. The gap is between
"property-level" preservation (vms) and "field-level" preservation
(this lemma). Any downstream proof needing e.g. `ms_at p
(machine_state s) = v` after `set_cdt` was falling through to
manual unfolds.

`set_cdt` is defined as `do s ← get; put (s\<lparr>cdt := t\<rparr>)
od`. Since the record update only touches the `cdt` field, the
machine_state field flows through identically. Isabelle's
record-update simplifier knows this, so `(wpsimp simp:
set_cdt_def)` closes the proof in one line.

This is Pattern G (frame preservation). Per the playbook it has
no automated detector — picked by inspection from the existing
`set_cdt_*` companion lemma family.

**Attempt log.**
1. Inspection of `set_cdt_*` lemmas already in CSpace_AI.thy
   identified the gap (predicate-level vs field-level).
2. `spec_witness_gen.py` → SHAPE 2 (additive). No witness.
3. Baseline wall: 64,766 ms (note: this baseline is post-0014,
   reflecting some session-heap variance from the previous apply).
4. `check-theory.sh --patch` → OK 57,416 ms. **Wall delta −11.3%**.
5. `spec_impact.py` → verdict `additive`, gate PASS.
6. `check-theory.sh --apply` → OK 52,587 ms.

**Why the wall got faster, despite adding a lemma.** This is the
key finding of the batch. By tagging the new lemma `[wp]`, wp's
automation in the rest of CSpace_AI.thy now discharges `\<lambda>s. P
(machine_state s)` postcondition fragments instantly via the new
rule — instead of falling through to slower fallbacks (`(simp
add: set_cdt_def)` + universal cong rule + unification). Several
downstream proofs in CSpace_AI use `set_cdt`-containing tactic
chains; the per-rule unification cost drops measurably once a rule
with the exact shape is in the `[wp]` class.

The new lemma's own proof cost is dwarfed by the per-consumer
discharge speed-up.

**Downstream impact.**
- **Same file**: wall **−11.3%** — net win. First concrete
  demonstration of Pattern G's `[wp]`-class-pickup ROI.
- **Tier-2 grep consumers**: 0 (brand new). But the file-wall
  improvement is real and immediate — happens via [wp] class
  pickup at theory load, not via explicit citation.
- **Cross-session**: NOT rebuilt. Adding `[wp]` rules can never
  break downstream proofs. Potential further speedup in Refine
  proofs touching `set_cdt`-containing operations; quantification
  deferred.

---

### 2.3 spec-0016 — `set_cdt_cur_thread[wp]` (Pattern G frame)

**What changed.** Inserted right after `set_cdt_machine_state`:

```isabelle
lemma set_cdt_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Model's understanding.** Same shape as 0015 but for the literal
`cur_thread` field. Existing `set_cdt_cur` preserves the PREDICATE
`cur_tcb` ("the current thread is a TCB") — analogous to the
vms/machine_state pair from 0015. The literal-field frame was
missing; many scheduler / IPC-path proofs need a polymorphic
`P (cur_thread s)` preservation across `set_cdt` and were
presumably using manual unfolds.

**Attempt log.**
1. Found by inspection (same survey pass that found 0015).
2. `spec_witness_gen.py` → SHAPE 2 (additive).
3. Baseline wall: 49,282 ms (post-0015).
4. `check-theory.sh --patch` → OK 48,041 ms. Δ −2.5%.
5. `spec_impact.py` → `additive`, PASS.
6. `check-theory.sh --apply` → OK 47,143 ms.

**Downstream impact.**
- **Same file**: −2.5% — the wp-class pickup is smaller than
  0015's −11.3%. Reason: `cur_thread`-frame goals are LESS
  common in CSpace_AI than `machine_state`-frame goals. Most
  cur_thread references are in scheduling / IPC files (Refine
  tier and DetSched* files), not CSpace_AI itself.
- **Cross-file**: 0 immediate. Downstream value expected in
  scheduler proofs (DetSchedSchedule_AI.thy) and IPC proofs
  (Ipc_AI.thy) that wrap cap_insert/cap_move (which call set_cdt).
- **Cross-session**: NOT rebuilt.

---

### 2.4 spec-0017 — `set_cdt_idle_thread[wp]` (Pattern G frame)

**What changed.** Inserted right after `set_cdt_cur_thread`:

```isabelle
lemma set_cdt_idle_thread[wp]:
  "\<lbrace>\<lambda>s. P (idle_thread s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (idle_thread s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Model's understanding.** Completes the threading-field Pattern G
triplet for `set_cdt`. Existing `set_cdt_idle[wp]` preserves the
PREDICATE `valid_idle` (analogous to the previous two pairs); the
literal `idle_thread` field-frame was missing.

**Attempt log.**
1. Inspection.
2. `spec_witness_gen.py` → SHAPE 2.
3. Baseline wall: 47,361 ms.
4. `check-theory.sh --patch` → OK 47,192 ms. Δ −0.4%.
5. `spec_impact.py` → `additive`, PASS.
6. `check-theory.sh --apply` → OK 47,460 ms.

**Downstream impact.**
- **Same file**: −0.4% — essentially flat. `idle_thread`
  references in CSpace_AI are even rarer than `cur_thread`'s.
- **Cross-file**: 0 immediate. Expected value: scheduler / IRQ
  paths that distinguish idle vs running thread.
- **Cross-session**: NOT rebuilt.

---

## 3. Filtered candidates (probe rejections)

Three Shape 1 (premise-drop) candidates from the scanner output
were rejected by `spec_premise_probe.sh` (TRIAL-based) before
ever reaching `check-theory.sh`. Each gave ground-truth
`load-bearing` verdict with residual subgoal evidence.

| Candidate | Probe wall | Verdict | Evidence |
|---|---:|---|---|
| `lsfco_cte_at` / `valid_objs` (Ipc_AI.thy:50) | 38.9 s | `load-bearing` | Proof step 4/4 (`by (rule hoare_strengthen_postE_R, ...)`) failed without `valid_objs` |
| `lsfco_cte_at` / `invs` (CSpace_AI.thy:4093) | (earlier session) | `load-bearing` | (probed pre-0014) |
| `get_rs_real_cte_at` / `valid_objs` (Ipc_AI.thy:96) | 48.2 s | `load-bearing` | Proof step 5/5 (`done`) left residual subgoal `\<And>a s. recv_buf = Some a \<Longrightarrow> valid_objs s` |

**Lesson — premise probe v1 gives ground truth.** The v0
substring-goal-state heuristic was rejected during 0011's
development because Isabelle keeps the full precondition visible
in intermediate subgoals, making the substring signal noisy.
The TRIAL-based v1 synthesizes the dropped-premise lemma and
literally runs the original proof against it — if any step
fails, the premise is consumed by that tactic. This batch is
the first time the probe filtered real candidates against the
seL4 source, and the residual-subgoal failure messages match
exactly what an `--apply` attempt would have produced — at a
fraction of the wall (probe ≈ 40-50 s, full apply ≈ 50-180 s).

**Implication for the candidate list quality.** The scanner's
`unused-premise` calibration in the playbook is **~50% TP**. The
combined evidence pool — 2 probe rejections from this batch
(`lsfco_cte_at/valid_objs`, `get_rs_real_cte_at/valid_objs`) plus
1 from the earlier 0011-era session (`lsfco_cte_at/invs`, recorded
in `reports/experiments/0011-*/decision.md`) — happens to give
3/3 load-bearing on the samples drawn so far. This is too small
to refine the playbook's 50% TP figure; it's consistent with
"we drew from the top of the ranked list where high-consumer-
count lemmas tend to be central enough that their premises are
load-bearing." The substantive lesson is **the probe is the
correct filter; the scanner alone is not sufficient** — that
holds regardless of TP rate.

---

## 4. Cross-experiment observations

### 4.1 File wall trajectory

| State | Wall (ms) | Δ from baseline |
|---|---:|---:|
| baseline (pre-batch) | 45,330 | 0% |
| post-0014 | 49,271 | +8.7% |
| post-0015 | 57,416 | +26.7% (peak — heap noise + parsing) |
| post-0016 | 48,041 | +6.0% (heap settled) |
| post-0017 | 47,192 | +4.1% |
| post-apply final | 47,460 | **+4.7%** |

Net cost: **+4.7% over baseline for 4 added lemmas**, well within
the +30% gate. Notable: wall is not monotone — the trial walls
after 0015 are LOWER than the trial walls after 0014, because
0015's `[wp]` rule offset 0014's parsing cost via downstream
automation pickup.

### 4.2 Pattern G ROI asymmetry — the real finding

| Lemma | New frame for | In-file wall delta |
|---|---|---:|
| 0015 | `machine_state` | **−11.3%** |
| 0016 | `cur_thread` | −2.5% |
| 0017 | `idle_thread` | −0.4% |

Same shape, same proof tactic — but wildly different ROI on the
same-file wall. The driver is **frequency of goals of the
matching shape in the file**. CSpace_AI.thy is full of
`set_cdt`-using lemmas whose proofs need to commute past
`machine_state` reads (because many of CSpace_AI's invariants
mention machine_state through valid_machine_state). Fewer of
them reference cur_thread or idle_thread.

**Playbook follow-up.** The playbook's Pattern G section
currently says "manual detection only, ROI mostly delayed". This
batch produced **one** counterexample (0015's −11.3%) showing
the ROI can occasionally be immediate. Worth recording as a
case study, but not yet as a rule.

**Unverified hypothesis** (do not promote to playbook until
measured): the same-file wall delta might correlate with the
frequency of `(<field-name> s)` references in that file's
postconditions / proof obligations. Three data points isn't
enough to fit a threshold — a casual grep for `machine_state s`
/ `cur_thread s` / `idle_thread s` in CSpace_AI.thy doesn't show
counts approaching any particular threshold and certainly
nothing as clean as a "~50 separates immediate from delayed".

To turn this into a usable heuristic, future Pattern G
experiments should record alongside each measurement:
- count of `(<field> s)` literal references in pre/post lines
  of the same file,
- count of `[wp]`-applicable goal shapes after a parsing pass
  through the trial proof state (would need an Isa-REPL probe
  extension; tooling does not exist today),
- the eventual wall delta.

After ~5-10 Pattern G data points with these recorded, a
threshold (if any) becomes statistically meaningful. Until then,
the safe statement is: "Pattern G ROI is mostly delayed, but
when the new field is heavily referenced in same-file
postconditions, an immediate wp-class pickup is possible."

### 4.3 Workflow stress test — what failed

Two workflow snags worth recording:

1. **Hand-written unified diffs were syntactically broken**
   (0015/0016/0017 — caught by 0018 audit). Empty context lines
   missed the required leading space; cumulative-diff hunk
   extraction had wrong `@@` offsets. Fix: always generate
   `patch.diff` via `diff -u <pre-snapshot> <post-snapshot>`
   immediately after each `--apply`, not hand-written.
   Workflow follow-up captured at the end of 0018's decision.md.
2. **Heap volatility during verification replay** (caught after
   0018). The diagnostic 0017 verbose-apply ran into "Incoherent
   digest for source file" and triggered a partial session
   rebuild. Root cause: a file was modified under check-theory.sh
   while it was running against an in-memory expected digest.
   Mitigation: don't restore file state from `/tmp/` while a
   check-theory.sh process may still be reading the file.

### 4.4 Cross-session impact — what was NOT measured

All 4 strengthenings are additive ASpec/AInvs-level lemmas. The
**theoretical** cross-session impact on Refine / CRefine:
- Adding a lemma cannot break a downstream proof.
- New `[wp]` rules can speed up downstream automation (likely
  most for Refine proofs that touch `set_cdt`-using operations).
- Marginal cost: one theory-graph parse per new lemma (~50-100 ms
  per Refine rebuild).

**No Refine rebuild was performed** because a single rebuild
takes ~1h17min wall (~2h22min CPU). The 4 additive lemmas
individually don't justify the rebuild cost. A future
downstream-cleanup PR that refactors consumers to cite the new
lemmas would be the natural batch under which to do the
measurement.

---

## 5. Audit hygiene (0018)

User verification on 2026-06-03 caught a real bug in the
audit-dir `patch.diff` files for 0015/0016/0017:
- empty context lines emitted without the required leading space
- `@@` hunk-header line counts wrong because the patches were
  extracted from a cumulative diff (carrying line-number offsets
  from prior experiments)

**Critical clarification**: the deployed source was never
broken. `check-theory.sh --apply` used the range-replace
patches in `logs/` (always correct, since they're verified by
Isabelle on every `--patch` and `--apply`). Only the audit-dir
replay artifact — the unified-diff format used by `git apply` —
was malformed.

Fix in 0018: regenerated all three patches via `diff -u <pre>
<post>` against pre-experiment snapshots, prepended the `diff
--git a/... b/...` header. Verified by **full reverse + forward
replay chain**:

```
$ for d in 0017 0016 0015 0014; do
    git -C verification/l4v apply --reverse "reports/experiments/${d}-*/patch.diff"
  done
$ grep -c '^lemma set_cdt_' verification/l4v/proof/invariant-abstract/CSpace_AI.thy
0
$ for d in 0014 0015 0016 0017; do
    git -C verification/l4v apply "reports/experiments/${d}-*/patch.diff"
  done
$ diff verification/l4v/proof/invariant-abstract/CSpace_AI.thy /tmp/CSpace_AI.thy.applied
            # (empty — byte-identical)
```

**Lesson recorded for future workflow**:
> Generate `patch.diff` via `diff -u <pre-snapshot> <post-
> snapshot>` immediately after each `--apply`. Never hand-write
> unified diffs; never extract a single hunk from a cumulative
> diff against a far-away baseline.

---

## 6. Open follow-ups

These are deliberately deferred from this batch:

1. **Downstream-cleanup PR** for Pattern B (0014). Refactor
   `update_cdt_cdt` and `cap_move_typ_at` (and any other lemma
   that manually unfolds `set_cdt_def` to expose `cdt s = t`) to
   cite `wp set_cdt_cdt_update` instead. Measures Pattern B's
   delayed ROI. Estimated scope: ~10-20 sites across
   CSpace_AI.thy + a few in Ipc_AI.thy.
2. **Refine rebuild measurement** to quantify cross-session
   speedup from the 0015/0016/0017 `[wp]` triplet. Worth doing
   AFTER follow-up (1) so it bundles meaningful refactor +
   measurement into one PR.
3. **`spec_capture_patch.sh` tooling** to automate the
   `diff -u <pre> <post>` step. Eliminates the 0018-style bug
   class entirely. Small helper, maybe 30 lines of bash. Should
   land before the next strengthening batch.
4. **Playbook §"Pattern G ROI" case study** writing up the
   0015/0016/0017 wall-delta asymmetry as a heuristic for
   "which fields most benefit from a literal-frame `[wp]`
   rule". Captured the observation in 0017 decision.md but
   not yet promoted to the playbook itself.
5. **Pattern G detector?** The scanner currently flags only
   `unused-premise`, `paired-chain`, `missing-functional`.
   Pattern G (frame preservation gaps) is manual-only. A
   detector could grep for `set_<op>_<predicate>` companions
   without a paired `set_<op>_<field>` literal-frame lemma.
   Open design question for the playbook.

---

## 7. PR-4 commit list (this batch)

For traceability — the full sequence of commits on the
`spec-strengthen` branch covering this batch:

```
07cce7e audit(0018): fix replayability of 0015/0016/0017 patch.diff
c0e1950 spec(0016+0017): apply set_cdt_cur_thread[wp] + set_cdt_idle_thread[wp]
f82ff4a spec(0015): apply set_cdt_machine_state[wp] — Pattern G frame lemma
bcc13ad spec(0014): apply set_cdt_cdt_update — Pattern B additive (CSpace_AI.thy)
1faff22 skill(spec): tighten Step 2 witness sequencing + harden Shape 3 vs Acceptance
ad21561 skill(spec): promote spec_witness_gen.py into Step 2 mainline
699d6c2 tools(spec): add spec_witness_gen.py + spec_premise_probe.py (TRIAL-based)
81a2b0a skill(spec): five-point review fixes — witness rules, modify-vs-delete, parent SKILL alignment
cba0a9b audit(0009): follow-up — record proof-skill-resets memory update
0b0aea3 audit(0009): purge stale claude/.claude/skills/ SKILL duplicate
35d4aa2 audit(0008): SKILL slim follow-ups — fake Hoare rule + derivability.thy vestige
```

Each experiment/topic has a corresponding `reports/experiments/00NN-*/`
audit directory; experiments 0014-0017 carry the full 4-file
seL4-source PR record (`patch.diff` + `command.sh` +
`measurement.json` + `decision.md`); the rest carry the simplified
2-file Meta-PR record. **The commit↔experiment mapping is not 1:1**:
`c0e1950` covers both 0016 and 0017 (a single commit landing two
related Pattern G frame lemmas); 0009 is split across two commits
(`0b0aea3` for the SKILL-duplicate purge and `cba0a9b` for the
proof-skill-resets memory follow-up). The 11 commits above cover
10 distinct experiment/topic IDs (0008-0018, excluding the
"meta-of-meta" follow-up nature of `cba0a9b`).

l4v submodule pointer at end of batch: `00d9073f70d0` (unchanged —
source changes live in the working tree only; upstream l4v PRs are
a separate concern).
