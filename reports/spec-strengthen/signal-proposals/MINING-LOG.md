# Detector signal mining log

Append-only record of each [spec_signal_miner.py](../../../spec-strengthen/scripts/spec_signal_miner.py)
round: what was mined, the deterministic calibration, and the **promotion
outcome** (the miner only PROPOSES; promotion is a human/LLM review gate). The
proposal artifacts sit next to this file.

---

## Round 1 — 2026-06-29 (after the AInvs ARM sweep)

Labeled set: **14 P-slot wins / 59 losses** from the local experiment archive.

### Precision mode → "unconstrained automation tactic" — ❌ REJECTED
[proposal](234112.md). Signal: a proof discharged by `blast`/`fastforce`/`auto`/
`force`/`crush` makes a text-absent premise unsafe to drop.
- Miner calibration: demotes 15/59 losses, **0 wins → reported PROMOTABLE**.
- **Promote-time re-verification on the live detector FAILED**: it demoted real
  wins outside the labeled set —
  - `gts_wf` (applied win) uses `blast` *embedded* mid-proof
    (`apply (erule allE, erule impE, blast)`), not a whole-goal discharge;
  - 2 of the 14 wins use `apply (fastforce simp: …)` (directed, with a simp-set).
- The "0 wins" was a **false positive**: (a) the miner's proof-feature
  extraction missed the `fastforce` in those wins, and (b) `gts_wf` (an
  `applied` win) is not in the experiment archive's `trial_passed` set, so the
  calibration never saw it. Bare automation (loss: `by blast`, `apply fastforce`)
  vs directed automation (win: `fastforce simp: …`) is **not cleanly
  separable** with the current features.
- **Outcome**: NOT gated on. `has_automation()` is kept and recorded on each
  hint (informational) pending a cleaner whole-goal-only re-mine.

### Recall mode → "compositional wp-chain idiom" — ✅ PROMOTED
[proposal](234112-recall.md). Signal: a Hoare proof that unfolds the op's own
`<op>_def` + uses `hoare_pre` + a pure wp/wpc/clarsimp chain with NO
`drule`/`frule`/`strengthen`/`hoare_gen_asm`/`rule_tac` is forward-driven —
each sub-op discharges its own preconditions via `[wp]`, so a bundled outer
premise (e.g. `invs`) is over-specified → BOOST.
- Miner calibration: boosts 4/5 under-ranked wins, **0 false-boosts on 20
  losses**.
- Promote-time re-verification on the live detector PASSED:
  - boosts `invoke_cnode_valid_pdpt_objs` (drop `invs`, **write-op** — recovers
    a win signal #4 wrongly demoted), `decode_unbind_notification_wf`,
    `get_simple_ko_valid_obj` → all now `high`;
  - does NOT fire on the ArchAcc `lookup_pt_slot_pte` losses (`drule` excludes
    them); `gts_wf` / `pd_at_asid_unique` unchanged.
- **Outcome**: promoted as [`is_compositional_wp()`](../../../spec-strengthen/scripts/spec_slot_hints.py)
  in `scan_p` (precedence: rule-precondition demote > comp-wp boost > write-op
  demote > differential).

### Lessons (about the meta-loop, not the signals)
1. **A miner "regression-free" verdict is bounded by its labeled set.** It means
   regression-free *on the archived candidates*. Promote-time re-verification on
   the LIVE detector against known wins (e.g. `gts_wf`) is mandatory — and it
   caught the automation regression. The propose-only + review discipline did
   its job.
2. **Calibration reliability ⊆ feature-extraction reliability.** The miner's
   `_proof_of` under-extracted some proofs → false confidence. Follow-up: harden
   the proof feature, and feed the calibration the FULL win set (incl. `applied`
   wins from the ledger, not only `trial_passed` experiment candidates).
3. **One clean signal per round is a good outcome.** Recall (boost) was cleanly
   separable; precision (automation) was not — and that's information.

### Reliability fixes (done, same day)
Both lessons-2 gaps are now fixed in the miner and verified:
- **`_proof_of` no longer truncates** — it joins everything after the 2nd quote,
  so a proof with its own quotes keeps all tactics (decode_inv_wf's `fastforce`
  is now visible).
- **Calibration sees the FULL win set** — `collect_ledger_wins()` reconstructs
  `applied`/older wins from source (e.g. `gts_wf'`), not only experiment
  `trial_passed` candidates.
- **Verified**: re-calibrating the automation signal with the fixed miner now
  reports `regression_free=False` (fires on 3 wins: `gts_wf'`, `decode_inv_wf`,
  `some_get_page_info_umapsD`) → **NEEDS REVIEW**, i.e. the miner would no
  longer have falsely cleared it. The calibration gate is now trustworthy.
- **End-to-end re-run** (`234112-precision.md`): the fixed miner independently
  re-proposed the same automation pattern and its own calibration flagged it
  `regression-free=False` (2 wins fired) → NEEDS REVIEW. The bad signal is now
  caught at calibration time, not only at promote-time.

### Process archiving (done)
Every miner run now archives the FULL process under `signal-proposals/<ts>-<mode>.*`:
`.prompt.txt` (exact LLM input), `.raw.jsonl` (claude -p NDJSON — every
thinking/text/result event of the discovery), `.md` (proposal + calibration).
The LLM's "how it found the signal" is now auditable after the fact, same
contract as spec_agent's `agent-raw.txt`. (Round-1's `234112.md` /
`234112-recall.md` predate this and have no `.raw.jsonl`.)

---

## Round 2 — 2026-06-29 (trustworthy miner; same data)

Same 14+1 wins / 59 losses, but with the reliable calibration + archiving, and
with `compositional-wp` (now signal #7) and the rejected `automation` signal
**both excluded from the prompt** so the LLM must find something genuinely new.

### Precision mode → "hypothesis-manipulating tactics" — ⚠ NEEDS REVIEW (not promoted)
[proposal](round2-precision.md) · process: `round2-precision.{prompt.txt,raw.jsonl}`.
Signal: a proof using `hoare_gen_asm`/`supply`/`erule allE|impE`/`strengthen`/
`unfolding`/`bspec`/`rule ccontr` predicts LOSS (each touches a specific context
hypothesis).
- Calibration (now trustworthy): demotes 16/30 losses, but **fires on 3 wins**
  (`gts_wf'`, `decode_inv_wf`, `some_get_page_info`) → `regression-free=False`.
- **Caught at calibration time** — no promote-time human catch needed.
- The LLM deliberated long (~370s, 400+ thinking events) and still only produced
  a broader cousin of the round-1 automation idea — and it regresses the **same
  gts_wf-family wins** (those wins use `erule allE/impE` etc. but are still
  droppable).

### Convergence reading (first evidence the precision space is near-exhausted)
Two consecutive rounds both gravitate to "the proof uses tactic X → demote", and
both regress the same gts_wf-family wins. The residual losses are not separable
from wins by mechanical **proof-tactic** features — they are approaching the
**trial's semantic floor** (only the prover decides). Per the termination
criterion (see [experiment-workflow.md](../experiment-workflow.md) / discussion):
no regression-free new precision signal on this data ⇒ precision mining is
(near-)converged FOR THIS SNAPSHOT. A held-out generalization test (not yet
built) would make this a hard verdict rather than a 2-round trend; and new
object-level data can re-open the space.
