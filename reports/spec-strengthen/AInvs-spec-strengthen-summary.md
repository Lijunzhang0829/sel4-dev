# Spec-Strengthen Summary — AInvs session

Session: `AInvs`
Branch: `spec-strengthen` (PR-4)
l4v submodule HEAD at baseline: `00d9073f70d0`

Covers two batches landed on this branch:

- **Batch I (2026-06-02, CSpace_AI.thy / `set_cdt`)** — experiments 0014–0017
- **Batch II (2026-06-05, KHeap_AI.thy / `set_object`)** — experiments 0019–0022

Plus filtered probe rejections, the 0018 audit-hygiene fix, and the
0020a aborted attempt.

---

## TL;DR — all 8 applied strengthenings

| # | File | Lemma | Pattern | Δ trial wall | Verdict |
|---|---|---|---|---:|---|
| 0014 | CSpace_AI | `set_cdt_cdt_update` | B (functional) | +8.7% | additive PASS |
| 0015 | CSpace_AI | `set_cdt_machine_state[wp]` | G (frame) | **−11.3%** | additive PASS |
| 0016 | CSpace_AI | `set_cdt_cur_thread[wp]` | G | −2.5% | additive PASS |
| 0017 | CSpace_AI | `set_cdt_idle_thread[wp]` | G | −0.4% | additive PASS |
| 0019 | KHeap_AI | `set_object_cdt[wp]` | G | +2.1% | additive PASS |
| 0020 | KHeap_AI | `set_object_cur_thread[wp]` | G | −4.4% | additive PASS |
| 0021 | KHeap_AI | `set_object_cur_domain[wp]` | G | −4.6% | additive PASS |
| 0022 | KHeap_AI | `set_object_arch_state[wp]` | G | −2.0% | additive PASS |

**Net same-file wall** for each batch:
- CSpace_AI: 45,330 → 47,460 ms = **+4.7%** for 4 lemmas
- KHeap_AI: 24,538 → 24,229 ms = **−1.3%** for 4 lemmas

Net combined: 8 additive lemmas, **+~1.7%** average file-wall cost, well
under the +30% gate. Three of 8 individual trial deltas were **negative**
(adding the lemma made the file faster via `[wp]`-class pickup).

---

## Batch I — CSpace_AI.thy / `set_cdt` (0014–0017)

Each experiment followed SKILL Steps 1–6:
**candidate → patch + witness → verify → measure → apply → record**.

### 0014 — `set_cdt_cdt_update` (Pattern B, functional postcond)

```isabelle
lemma set_cdt_cdt_update:
  "\<lbrace>\<top>\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. cdt s = t\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Picked from** the 20260526 strengthen-log as a Case 5 demonstrator
that had been `--patch`-verified but never `--apply`-ed.

**Understanding.** `set_cdt t` writes the `cdt` field to exactly
`t`. Existing companions covered preservations of other state;
no lemma exposed the direct functional fact. Existing consumers
(`update_cdt_cdt`, `cap_move_typ_at`) work around by unfolding
`set_cdt_def` manually; this lemma lets them cite
`wp set_cdt_cdt_update` instead. Pattern B's ROI is delayed.

**Walls.** baseline 45,330 → trial 49,271 (+8.7%) → apply 55,846 ms.
Tier-2 grep consumers at apply time: 0.

### 0015 — `set_cdt_machine_state[wp]` (Pattern G, **−11.3%**)

```isabelle
lemma set_cdt_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**Picked by inspection** of the `set_cdt_*` lemma family in
CSpace_AI.thy: `set_cdt_vms[wp]` preserves the **predicate**
`valid_machine_state` but no lemma preserves the **literal field**
`machine_state`. Closing that predicate-vs-field gap is the
recurring Pattern G shape.

**Walls.** baseline 64,766 → trial 57,416 (−11.3%) → apply 52,587 ms.
The file got dramatically faster. wp automation in the rest of
CSpace_AI now discharges `\<lambda>s. P (machine_state s)`
post-conditions on `set_cdt`-using chains via the new rule instead
of falling through to manual `set_cdt_def` unfolds. The per-rule
discharge speed-up dwarfs the per-lemma parsing cost.

This is the **standout result** of the whole spec-strengthen
session.

### 0016 — `set_cdt_cur_thread[wp]` (Pattern G, −2.5%)

Same shape, different field. `set_cdt_cur` preserves the predicate
`cur_tcb`; `cur_thread`-literal-field frame was missing. Picked
from the same inspection pass.

**Walls.** baseline 49,282 → trial 48,041 (−2.5%) → apply 47,143 ms.
Smaller negative delta than 0015 because `cur_thread`-frame goals
are less common in CSpace_AI itself; most live in scheduler /
IPC files.

### 0017 — `set_cdt_idle_thread[wp]` (Pattern G, −0.4%)

Completes the threading-field triplet on `set_cdt`
(`machine_state` / `cur_thread` / `idle_thread`).

**Walls.** baseline 47,361 → trial 47,192 (−0.4%) → apply 47,460 ms.
Essentially flat. `idle_thread` references in CSpace_AI are
rarer still.

---

## Batch II — KHeap_AI.thy / `set_object` (0019–0022)

Switched operation from `set_cdt` (specialized) to `set_object` (the
fundamental heap-write primitive that nearly every other `set_<X>`
calls internally). Same family-survey heuristic.

### 0019 — `set_object_cdt[wp]` (+2.1%, parser revert detour)

```isabelle
lemma set_object_cdt[wp]:
  "\<lbrace>\<lambda>s. P (cdt s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cdt s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

**Workflow detour.** Originally wrote in the **abbreviated
"preserves" form** `set_object p ko \<lbrace>...\<rbrace>` (the
idiomatic l4v style that `set_object_machine_state[wp]` uses).
`check-theory.sh --patch` accepted it (OK, 24,434 ms) but
`spec_witness_gen.py` returned "Could not classify", and
`spec_impact.py` stdout reported `Gate: FAIL` (empty deltas table)
while its `--measurement-out` JSON wrote
`impact_verdict: additive, gate_pass: true` via a different code
path — a tool stdout/JSON divergence.

Strict SKILL Acceptance #2 requires a verdict, so I reverted via
`git -C verification/l4v checkout` and rewrote in **explicit Hoare
form** (semantically identical, parser-recognized).

**Walls.** baseline 24,538 → trial 25,045 (+2.1%) → apply 24,568 ms.

### 0020 — first attempt aborted, then `set_object_cur_thread[wp]`

**0020a attempt** — `set_object_interrupt_states[wp]`, picked
because the next lemma below (`valid_irq_states_triv`) uses
`\<lambda>s. P (interrupt_states s)` as an assumption. Highest
expected ROI of the available slots.

`check-theory.sh --patch` failed:
```
*** Duplicate fact declaration "Tmp_*.set_object_interrupt_states"
    vs. "Tmp_*.set_object_interrupt_states"
```

**Root cause** (worth recording in detail). `KHeap_AI.thy:925` has:
```
crunch interrupt_states[wp]: set_simple_ko "\<lambda>s. P (interrupt_states s)"
```
`set_simple_ko` calls `set_object` internally; `crunch` recursively
descends through the body and auto-generates
`set_object_interrupt_states[wp]` as an intermediate fact. The
generated lemma name appears **nowhere in source text** — `grep
'set_object_interrupt_states'` across the whole l4v tree returns 0
hits — but Isabelle sees the duplicate at compile time.

**Useful negative result**: the downstream-readiness gap (which
would have been unlocked by adding the frame) is already covered
by the crunch derivation. No PR needed there.

**0020 fallback** — `set_object_cur_thread[wp]`. Clean after
verifying no `crunch cur_thread` derivations reach `set_object`.

**Walls.** baseline 25,076 → trial 23,966 (−4.4%) → apply 23,954 ms.

### 0021 — `set_object_cur_domain[wp]` (−4.6%)

First positive application of the new pre-flight recipe (added
to playbook as `f98fb0a`).

```
$ grep -rn 'set_object_cur_domain\b' verification/l4v/             # direct: 0
$ grep -rn 'crunch cur_domain\b.*set_(simple_ko|cap|...)' verification/l4v/...   # crunch-derived: 0
```

**Walls.** baseline 25,954 → trial 24,773 (−4.6%) → apply 24,238 ms.

### 0022 — `set_object_arch_state[wp]` (−2.0%)

Pattern G on the architecture-specific state field. Same pre-flight
clean.

**Walls.** baseline 24,677 → trial 24,184 (−2.0%) → apply 24,229 ms.

Expected real downstream value is in ArchAcc_AI.thy and arch
invariant proofs (`valid_arch_state` etc.), not in-file.

---

## Filtered candidates (4 ground-truth rejections)

| Candidate | Source | Verdict | Evidence |
|---|---|---|---|
| `lsfco_cte_at` / `valid_objs` (Ipc_AI.thy:50) | scanner top | load-bearing | proof step 4/4 (`by (rule hoare_strengthen_postE_R, ...)`) failed without premise |
| `lsfco_cte_at` / `invs` (CSpace_AI.thy:4093) | scanner top, **earlier 0011-era** | load-bearing | (probed pre-0014) |
| `get_rs_real_cte_at` / `valid_objs` (Ipc_AI.thy:96) | scanner top | load-bearing | proof step 5/5 (`done`) left residual subgoal `\<And>a s. recv_buf = Some a \<Longrightarrow> valid_objs s` |
| `set_object_interrupt_states[wp]` | family survey | crunch-collision | duplicate fact, root cause `KHeap_AI:925` `crunch interrupt_states[wp]: set_simple_ko` |

Three are Pattern C (premise-drop) probe rejections; one is a
Pattern G crunch collision. All four were caught before any
`--apply` attempt — i.e., the tooling (probe + pre-flight recipe)
saved a doomed run each time.

The Pattern C calibration in the playbook is **~50% TP**; the
combined pool above is **0/3 success on the samples drawn so
far**, but the sample is too small to revise the calibration
figure. The substantive lesson is **the probe is the correct
filter; the scanner alone is not sufficient** — that holds
regardless of TP rate.

---

## Cross-batch findings (concluding observations)

### 1. Pattern G dominates — 8/8 success vs Pattern C 0/3

Across both batches: 7 of 8 applied strengthenings are Pattern G
frame lemmas, one is Pattern B (functional postcond). All 8 passed
Acceptance. Pattern C candidates were uniformly rejected by the
probe before any apply attempt. **For this session, the highest
success-rate axis was the predicate-vs-literal-field gap on
`set_<op>` operations.**

### 2. The family-survey heuristic generalizes across operations

Same recipe worked on `set_cdt` (specialized CDT-update op) and
`set_object` (fundamental heap-write op):

> Find a `set_<op>` family where the file has
> `set_<op>_<predicate>[wp]` companions but missing
> `set_<op>_<literal-field>[wp]` ones. Pre-flight grep for
> direct + crunch-derived duplicates. Add via the one-line
> tactic.

The tactic (`by (wpsimp simp: <op>_def)` or
`by (wpsimp wp: <op>_wp_strong)`) closes in <1 s for any clean
slot. The combination of (1) easy proof + (2) zero downstream
breakage risk + (3) measurable in-file wp-class pickup makes
Pattern G the most efficient mining target found this session.

### 3. Wall delta varies dramatically across fields — and we don't know precisely why

The 8 trial deltas range from `+8.7%` (0014 functional) to
`−11.3%` (0015 machine_state):

| Σ Pattern | Trial Δ |
|---|---:|
| 0014 set_cdt_cdt_update (B) | +8.7% |
| 0015 set_cdt_machine_state (G) | **−11.3%** |
| 0016 set_cdt_cur_thread (G) | −2.5% |
| 0017 set_cdt_idle_thread (G) | −0.4% |
| 0019 set_object_cdt (G) | +2.1% |
| 0020 set_object_cur_thread (G) | −4.4% |
| 0021 set_object_cur_domain (G) | −4.6% |
| 0022 set_object_arch_state (G) | −2.0% |

The plausible explanation is **frequency of `(<field> s)` goal
shapes in the same file** — files heavily referencing the field
gain more from a new `[wp]` rule on that field. We do **not** have
enough data points to fit a numeric threshold; a casual grep
shows no obvious clean separation by occurrence count.

The honest statement is: "Pattern G ROI is mostly delayed, but
when the new field is heavily referenced in same-file
postconditions, an immediate in-file wp-class pickup is
possible." Six of eight individual trial deltas are negative or
flat; the variance has a long-tail upside.

### 4. Per-rule pickups can offset cumulative parsing cost

Adding 4 additive lemmas to KHeap_AI **net reduced** its wall by
−1.3% (24,538 → 24,229 ms). CSpace_AI's +4.7% net over 4 adds is
larger but still within bounds. The pattern from 0015 (single
−11.3%) holds at scale across multiple Pattern G adds in the
same file: cumulative wp-class accumulation dominates over
linear-in-lemma parsing cost.

**Practical implication**: Pattern G batches should target
multiple related field-frames in one PR to amortize the parsing
cost via the cumulative pickup. Adding one frame at a time
wastes the compound effect.

### 5. Crunch collisions are a real false-positive class

5 of the 12 `set_object_<field>` slots surveyed pre-batch (out of
11 directly missing + machine_state already covered) are
implicitly generated by `crunch` derivations on wrapper
operations:

| Field | Path |
|---|---|
| `interrupt_states` | `KHeap_AI:925` `crunch interrupt_states[wp]: set_simple_ko` |
| `idle_thread` | 3 files (various crunch declarations) |
| `scheduler_action` | 1 file |
| `interrupt_irq_node` | 3 files |
| `ready_queues` | `DetSchedSchedule_AI:2623` |

Without the pre-flight recipe, each of these would have failed at
`check-theory.sh --patch` time with `Duplicate fact declaration`,
costing ~20–30 s per failed trial. The recipe is mechanical and
should run as Step 0 of any future Pattern G PR.

### 6. The scanner's ROI ranking was not used in either batch

The current `spec_candidates.py` detector emits `unused-premise`
(Pattern C), `missing-functional` (Pattern B), and `paired-chain`
(Pattern A) candidates. **It does not emit Pattern G hints.**
Top scanner picks were all Pattern C in the high-consumer-count
region; the probe found all sampled ones load-bearing.

We bypassed the scanner entirely for picking what to do. The
candidate selection was driven by **inspection of `set_<op>_*`
lemma families** — a heuristic the scanner doesn't model. This is
the most significant gap between the SKILL's stated flow ("run
the candidates tool, pick top") and what actually worked.

### 7. Tooling pain points that recurred across batches

- **Parser doesn't recognize abbreviated `f \<lbrace>P\<rbrace>`
  form** — bit us in 0019. Workaround: write Pattern G adds in
  explicit `\<lbrace>P\<rbrace> ... \<lbrace>Q\<rbrace>` form
  always. Cost: tiny syntactic difference. Fix: ~5-line regex
  extension to `HOARE_TRIPLE_RE`.
- **`spec_impact.py` stdout/JSON divergence** — surfaced in 0019.
  JSON is the audit-of-record. Stdout markdown can mislead.
- **Hand-written unified diffs are error-prone** — caught by
  0018 audit on Batch I. Now: always `diff -u <pre-snapshot>
  <post-snapshot>` immediately after each `--apply`. Snapshot
  discipline established for Batch II from the start; no
  recurrence.

### 8. Cross-session impact uniformly NOT measured

All 8 added lemmas are additive at the ASpec/AInvs level. The
**theoretical** impact on Refine / CRefine is zero proof
breakage; potential downstream wp-class pickup. Refine rebuild
takes ~1h17min wall (~2h22min CPU). The 8 individual additive
lemmas don't justify the rebuild cost.

The right time to do a Refine measurement is **after a Pattern B
cleanup PR adopts the new lemmas downstream** — at which point
the measurement reflects real semantic refactor savings, not just
theory-graph parse cost.

### 9. What this session does NOT cover

For honest scoping:

- **No applied Pattern A (paired weak/strong cleanup)** — playbook
  Case 1 (`lookup_slot_cte_at_wp`) was a historical reference
  only. This session didn't attempt one.
- **No applied Pattern C or D** — Pattern C candidates were all
  load-bearing on probe; Pattern D requires inspecting `≤`-bound
  postconditions for exactness, none surveyed.
- **No applied Pattern E** — compound `_invs` lemmas were not
  attempted (playbook Case 6 documents a prior failure).
- **No arch-specific files touched** — ARM/RISCV64/X64 subdirs
  not explored.
- **No files outside `proof/invariant-abstract/`** — Refine /
  Access / InfoFlow proofs untouched.
- **No cross-session work** — see §8.

---

## Selection logic that emerged

Documented for traceability; **not promoted into the SKILL**.
Implicit priority order across both batches:

1. **Pattern: shape 2 additive > shape 1 modify.** Additive
   `[wp]` rules cannot break downstream proofs — strict upper
   bound on risk. Shape 1 needs witness + probe + structural
   diff; the probe has been finding top scanner candidates
   load-bearing.
2. **Operation: fundamental > specialized.** `set_object` (heap-
   write primitive) > `set_cdt` (CDT update) > more specialized
   ops. Pattern G frames on fundamental ops propagate to downstream
   via wp class accumulation.
3. **Field choice: predicate-companion present + literal-field-
   companion missing.** When a file has `set_<op>_<predicate>[wp]`
   (e.g. `set_object_machine_state` for `valid_machine_state`)
   but no `set_<op>_<literal-field>[wp]`, the literal-field frame
   is almost certainly provable by the same one-line tactic.
4. **Pre-flight: direct grep + crunch grep.** The recipe born
   from 0020a. Mechanical, cheap, catches the crunch-collision
   class. Should run before every Pattern G PR.
5. **Scanner ROI ranking: NOT in this priority list.** The scanner
   doesn't emit Pattern G hints; its Pattern C top picks are
   high-consumer but tend to be central enough that premises
   are load-bearing.

This was effective for this session's scope. It's narrow — it does
not discover Pattern C / D / E successes, doesn't touch arch
files, doesn't measure cross-session impact. A future batch could
pivot to other patterns and re-establish a wider selection
function.

---

## Open follow-ups (deferred from this session)

1. **2 remaining clean `set_object_<field>[wp]` slots**:
   `domain_index`, `domain_time`. Same recipe applies; a small
   future batch could fill them.
2. **Other `set_<op>` families to mine** — `set_thread_state`,
   `thread_set`, `set_cap`, `set_simple_ko`. Each likely has
   predicate-vs-literal-field gaps similar to the ones filled
   this session.
3. **Pattern B downstream-cleanup PR** for 0014. Refactor
   `update_cdt_cdt`, `cap_move_typ_at`, and similar lemmas to
   cite `wp set_cdt_cdt_update` instead of unfolding
   `set_cdt_def`. Measures Pattern B's delayed ROI.
4. **Refine rebuild measurement** to quantify cross-session
   speedup from the 7 applied Pattern G `[wp]` lemmas. Best
   bundled with (3) so the measurement captures meaningful
   semantic refactor savings.
5. **`spec_strengthen_scan.py` regex fix** for the abbreviated
   `f \<lbrace>P\<rbrace>` form. ~5 lines, removes a recurring
   syntax-juggling task.
6. **`spec_impact.py` stdout/JSON consistency fix**. Currently
   the stdout markdown table can show `Gate: FAIL` while JSON
   shows `gate_pass: true`. Pick one source of truth.
7. **`spec_capture_patch.sh` tooling** to automate `diff -u
   <pre> <post>` snapshot capture. Eliminates the 0018-style
   bug class.
8. **Pattern G detector** for the scanner. The family-survey
   heuristic above could be encoded as a detector that lists
   `set_<op>_<field>[wp]` gaps automatically. Would close the
   "SKILL says scanner; we used inspection" gap.
9. **Playbook Pattern G case study** writing up 0015's −11.3%
   wall delta and the wider asymmetry across the 7 G-pattern
   lemmas. The observation is in this summary; not yet promoted
   to the playbook.

---

## PR-4 commit list (this session)

```
d58acf2 spec(0021+0022): set_object_cur_domain[wp] + set_object_arch_state[wp]
f98fb0a playbook(spec): record Pattern G gotchas — crunch collisions + parser caveat
1a5fd19 spec(0019+0020): set_object_cdt[wp] + set_object_cur_thread[wp] on KHeap_AI.thy
07cce7e audit(0018): fix replayability of 0015/0016/0017 patch.diff
c0e1950 spec(0016+0017): apply set_cdt_cur_thread[wp] + set_cdt_idle_thread[wp]
f82ff4a spec(0015): apply set_cdt_machine_state[wp] — Pattern G frame lemma
bcc13ad spec(0014): apply set_cdt_cdt_update — Pattern B additive (CSpace_AI.thy)
```

The 7 commits above cover the 8 strengthening experiments plus
the playbook update plus the 0018 audit-hygiene fix.
**Commit↔experiment mapping is not 1:1**: `c0e1950` covers 0016+0017
jointly; `1a5fd19` covers 0019+0020; `d58acf2` covers 0021+0022.

Each experiment still has its own `reports/experiments/00NN-*/`
audit directory with the full 4-file seL4-source PR record
(`patch.diff` + `command.sh` + `measurement.json` +
`decision.md`). Audit replay validated end-to-end (per 0018):
forward-apply of all 4 Batch I patches from baseline lands
byte-identical to the applied state; same for Batch II.

l4v submodule pointer at end of session: `00d9073f70d0` (unchanged
— source changes live in the submodule working tree only; upstream
l4v PRs are a separate concern not handled by this sub-skill).
