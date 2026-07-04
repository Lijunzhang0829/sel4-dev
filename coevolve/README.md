# coevolve — fork-① harness (localizer + gate, calibrated on real cases)

Skill: `.claude/skills/isabelle_prover_coevolve`. This dir is the fork-①
deliverable: **agent-driven break localization**, calibrated against real
l4v co-change commits (human fix `C_p` = ground truth).

## Layout

| Path | What |
|---|---|
| `scripts/case_extract.py` | Deterministic: split a co-change commit into `delta_artifact.diff` (trigger) + `delta_proof.diff` (**held-out** human fix) + `ground_truth.json` (per-lemma: `statement_evolved`/`body_changed`/kind incl. `relocated`) |
| `scripts/localize_agent.py` | Agent half: mechanical context (changed identifiers incl. hunk-context enclosing defs; arch-filtered grep candidates) → `claude -p` → predicted work-list + full transcript. Sees ONLY the artifact half. |
| `scripts/calibrate.py` | Score prediction vs ground truth: file P/R, lemma recall, statement-evolve flag accuracy |
| `cases/<commit>/` | 15 real L1 co-change seeds (AArch64 window), each a self-contained bundle |

Requires `claude` CLI (runs on the machine that has it; B currently lacks it
— run localizer on A or install CLI here). `case_extract`/`calibrate` run
anywhere (py3.8+).

```bash
python3 scripts/case_extract.py --l4v verification/l4v --commit <hash> --out cases/<hash>
python3 scripts/localize_agent.py --l4v verification/l4v --case cases/<hash> --model sonnet
python3 scripts/calibrate.py cases/<hash>
```

## Calibration state (2026-07-01, model=sonnet)

**Full 15-seed sweep (MICRO-AVG): fileP=0.22 fileR=0.38 lemR=0.58 evoAcc=0.57**
(n=14 scored, 1 JSON-parse skip). Per-case table in cases/*/calibration.json.

Honest read: the 2-seed-tuned prompt did NOT generalize.
- 6 seeds: EMPTY prediction (agent judges "proof survives" on constant-
  abstraction changes like physBase/pptrBase; human edited anyway).
- 1 seed: 22-file over-prediction (every mention of the_arch_cap; human: 1).
- 2 seeds died on identifier-extraction noise (type_synonym/defs captured as
  names) — deterministic bug, FIXED in localize_agent.py (keyword regex +
  stoplist), not yet re-swept.
- Where extraction was clean + candidates small: 71f5a8658 and 18b0cef0c
  scored 1.00 across the board; df5e1611/0e8048b49/c4390d8e7 fileR=1.00.

Conclusion: the agent oscillates between under- (survives) and over-
(every-mention) prediction; further prompt tuning would overfit the library.
**Next lever = the build oracle (mode=build): reconstruct the broken tree,
let check-theory adjudicate which breaks are real.**

## Failure→fix taxonomy (each calibration failure became a deterministic fix)

1. Isabelle `\<lambda>` in JSON reply → invalid escape crash ⇒ tolerant
   backslash repair before `json.loads`.
2. Change inside a definition BODY → zero identifiers extracted ⇒ also parse
   the hunk-context header (`@@ ... @@ definition NAME`).
3. Blind agent guessed by file-name similarity (Decode_A→Decode_AI; human
   fixed ArchArch_AI) ⇒ prompt rule: breakage follows REFERENCES, not names.
4. Deep static reasoning concluded "proof survives" and returned an empty
   work-list, while the human DID edit ⇒ prompt reframed **maintainer-aligned
   (semantic tracking), not merely compiler-forced**.
5. Agent listed inspected-but-fine files in work_list ⇒ schema: work_list =
   edits only; `cleared` is a separate key.
6. 41 candidates × unbounded tool exploration → 600s timeout ⇒ deterministic
   **arch filter** (AArch64-only change cannot break ARM/RISCV64/X64 files;
   41→~8 candidates) + inspect-budget (≤6 files) + 900s.

## Open items (next session)

- calibrate.py: split added vs modified lemmas in lemma-recall.
- evoAcc: feed candidate lemma STATEMENTS into the prompt; adjudicate
  "agent says survives / human edited" cases with the build oracle
  (`check-theory` on the reconstructed break) — that also gives mode=build.
- Gate agent (evolution-vs-weakening): build calibration set from the
  statement_evolved pairs in `cases/*/ground_truth.json` (positives) +
  synthetic weakenings (negatives).
- Sweep all 15 seeds once localizer prompt stabilizes; then wire into the
  repair driver (Step 2 of the skill's First cut).

## Build-oracle adjudication (2026-07-02, first batch — 4 cleanly-reverting seeds)

Reconstruction: current tree − Δ_proof (reverse-apply in an isolated
worktree) checked against the AARCH64 heap. `adjudicate.py`; per-case logs in
`cases/*/adjudication*.{json,log}`.

| seed | agent said | broken state | verdict |
|---|---|---|---|
| 83ddb4def | empty ("survives") | **RED** | agent wrong — compiler-forced break missed |
| 0e8048b49 | deep "survives" reasoning | **RED** | plausible-but-wrong; Isabelle refutes it |
| df5e1611 | statement need not evolve | **RED** | old stmt+proof does not build |
| 8f6373c7e | (over-predicted 22 files) | **GREEN** | human's edit was maintainer-CHOICE — GT itself is non-forced |

**Consequences (adopted):**
1. **Agent static "survives" judgments are not trustworthy** even when the
   reasoning cites exact lemma structure. In deployment the RED set comes FREE
   from the build — the agent's role is re-scoped: interpret each break +
   plan statement evolution + gate, NOT predict breakage. The 0.22/0.38
   localizer scores measured a capability deployment doesn't need.
2. **Dual-track ground truth from now on**: build-RED = forced set (P/R on it
   = localization ability); human-fix ∖ forced = choice set (recall on it =
   maintainer-alignment, softer). ≥1/4 of first batch was pure choice —
   no prior repair work build-cleans its ground truth.
3. Cost model: reverse-apply onto the CURRENT tree reuses today's heap —
   no per-case parent-era heap rebuild for cleanly-reverting seeds (4/8
   tested clean). Era-bucketing only needed for old seeds.

## Ground-truth distribution (15 co-change seeds — `gt-distribution.md`)

**11/15 involve statement evolution (L2), only 4 pure body-repair (L1).**
Lemma-edit mix: 35 added · 27 body-only · 13 stmt-evolved · 14 deleted —
the dominant human response is ADD new helpers + evolve statements, not
re-prove bodies. Selection-bias caveat: co-change commits may over-represent
L2 (statement changes force atomic commits); the pair-form benchmark will
give the unbiased distribution.

## Repair experiment — final scorecard (2026-07-03, Step-2 first cut)

3 build-adjudicated RED seeds, repair agent = claude -p sonnet, closed loop
(propose SEARCH/REPLACE → check-theory → feed error back, ≤3 rounds,
cumulative). Full transcripts in `cases/*/repair/`.

| seed | shape | verdict | rounds | vs human C_p |
|---|---|---|---|---|
| df5e1611 | crunches + decl-order relocation | **GREEN → SESSION-GREEN** (full AInvs AARCH64 build) | 1 (892s) | isomorphic, smaller (kept stmt; human also symbolized 3→word_size_bits = choice, build-proven non-forced) |
| 83ddb4def | obsolete-lemma repair | **GREEN → SESSION-GREEN** | 2 (581s) | **divergent & stronger**: human deleted the lemma; agent kept it, re-proved by inlining the deleted upstream defs |
| 0e8048b49 | boundary flip → stmt evolution + new helper | RED (unresolved) | — | agent 3× produced the correct semantic move (prop_tac `<`→`≤`, not_le→not_less — byte-identical to half of C_p) but was **never given a usable error**: goal dump >250 lines pushed `***` out of the harness tail window; with mid-file default windows it correctly said "break is outside my view" each time. Harness-attributed, not capability. tail→1200 fix landed; last attempt pending |

**Conclusion (fork-① capability question): the agent CAN repair.** 2/3
session-green with human-divergent-yet-valid fixes; the third blocked
exclusively by harness I/O (error truncation, argv limit, patch-parser `---`
mine, transport stalls at ~80KB prompts) — each failure became a
deterministic harness fix; zero failures attributable to proof reasoning.
Machine-side cost per success: ~10-15 min wall, 1-2 rounds.

## ⚠ RETRACTION & corrections (2026-07-03 evening audit)

1. **83ddb4def GREEN/SESSION-GREEN RETRACTED.** Offline audit of
   `final.patch` against the current tree showed the v3 driver computed
   hunk coordinates in BROKEN-file space while check-theory applies them in
   CURRENT-file space; the +7-line drift landed the edit on the UNRELATED
   lemma `pptrTop_le_ipa_size` (whose proof happened to still close under
   the substituted simp set → false GREEN, and the session build validated
   that accidental state). The agent's intended repair was never tested.
   Driver fixed (patch now computed vs the live current file); the agent's
   r1+r2 edits are being replayed and properly verified. df5e1611 is
   unaffected (v2 whole-file patch — coordinate-exact by construction).
2. **Container recreation wiped the AARCH64 heap** (image-baked heaps are
   ephemeral; timestamps reverted to the Apr-30 ARM originals) → every
   check after the recreation ran against the WRONG-ARCH heap, producing
   "Not a datatype constructor: VCPUSetTCB" artifacts. All 0e8048b49
   attempts of 2026-07-03 afternoon are void as capability data. Heap
   rebuilt; **heaps now also backed up to /workspace/heaps-backup/**
   (host-mounted, survives recreation; restore = cp back + or rebuild).
3. Harness-failure taxonomy grows to 8: (7) patch coordinate space,
   (8) ephemeral-heap recreation. The audit that caught #7 was triggered
   by a routine "should we re-verify on the new DAG" question — cheap
   text-level audits of applied patches are now a standing gate step.

## Reinstatement + final open item (2026-07-03 night)

- **83ddb4def REINSTATED**: replaying the agent's r1+r2 edits in broken space
  and emitting the patch in CORRECT current-file coordinates verifies
  **GREEN (68.6s)** — the inline-and-reprove repair is genuinely valid
  (`logs/replay-83ddb4def.patch`). Pending: downstream session build of this
  TRUE state (the earlier SESSION-GREEN validated the mis-anchored state).
- **0e8048b49 stays open, fully attributed**: its failure has NEVER emitted
  a capturable `***` block via stdout tail (since adjudication) — the error
  detail lives in check-theory's temp log, not the tail. Harness item #9:
  read the temp-log path instead of tailing stdout. The agent's final reply
  states precisely the two missing inputs (omitted-region text or an error
  line number); its semantic move (prop_tac `<`→`≤` + discharge-chain swap)
  has been correct in every round that had any signal. No capability failure
  on record for this seed — only I/O starvation.

Score at campaign close: **2/3 valid GREEN** (df5e1611 session-validated;
83ddb4def file-validated on true coordinates, session pending), 1/3 open
with 9-item harness taxonomy and zero reasoning failures attributed.

## 0e8048b49 final status (2026-07-04) + two new hard rules

Item #9's true root cause found and fixed: B ran an OLD check-theory whose
`grep|head` under pipefail died of SIGPIPE before printing any `***` —
every failure looked like a bare `FAILED` (blind repair). A-side had fixed
this; the fix is now ported (harness item #10: cross-machine script drift).

With errors finally visible, the next blocker surfaced: **baseline
(unmodified file) is RED on the rebuilt heap** (`Not a datatype
constructor: VCPUSetTCB`) while it was GREEN on the July-2 heap — two runs
of build_aarch64_heap.sh are NOT equivalent (suspect: stale cross-container
cmake config-build cache on the host mount, or partial build). All
0e8048b49 rounds remain void; **zero capability failures on record**.

New hard rules:
1. **Baseline-first**: repair_driver now refuses to run any seed whose
   unpatched baseline is not GREEN (INFRA-RED verdict, no claude spend).
2. **Heap-event discipline**: after ANY heap rebuild/container event, run a
   baseline check before experiments; back up good heaps to
   /workspace/heaps-backup immediately after validating them.

Next session: diff rebuild2 log vs the July-2 build log; clean config-build
cache and rebuild; baseline-test; then the (seventh, properly-fed) attempt.
