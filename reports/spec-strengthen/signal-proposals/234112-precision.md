# Signal proposal — P-slot (mode: precision)

> LLM-proposed, deterministically calibrated, **propose-only**. Review then
> hand-write into `spec-strengthen/scripts/spec_slot_hints.py` if promotable.

Full run archived alongside: `234112-precision.prompt.txt` (exact input fed to the LLM) · `234112-precision.raw.jsonl` (claude -p NDJSON — the discovery process, every thinking/text event).

Mode: **precision** — the signal should fire (demote) on losses, NEVER on wins.

## Verdict: ⚠ NEEDS REVIEW

| metric | value |
|---|---|
| losses fired (demoted) | 8/12 (recall 0.667) |
| **wins fired (regression — must be 0)** | **2** |
| regression-free | False |
| predicate errors | 0 |
| misfired wins | ['some_get_page_info_umapsD_no_valid_objs', 'decode_inv_wf_no_real_cte'] |

## Structural signature
When the proof body contains `fastforce` or closes with `by blast`, the tactic is a goal-search oracle that silently discharges any reachable subgoal — including ones that depend on the dropped premise — without ever naming it in the proof text. Textual absence of the dropped head is therefore *unreliable evidence of redundancy* in this setting. Premises consumed by the quantifier-level obligations that fastforce/blast finds (e.g., inside an unfolded `*_def`, or in a residual wp obligation after `hoare_gen_asm`) leave no textual fingerprint. This structural property of the proof closure predicts LOSS: the premise looked unused but was silently load-bearing.

## Why it's novel (not an existing signal)
None of the 6 existing signals look at the PROOF CLOSURE TACTIC itself. Signal 1 (unused-premise) flags textual absence regardless of how the proof closes — this new signal specifically overrides that evidence when the closing tactic is an opaque search oracle. Signal 6 (rule-precondition dependency) requires a named invoked rule whose preconditions are known; this signal fires on the automation method alone, with no knowledge of rule preconditions. Signals 2–5 concern the premise's structural position in the statement or its op class, not the proof method. This signal is purely about the proof text's final tactic being a search strategy that can find hidden proof obligations invisibly.

## Expected effect
Fires on — and demotes — exactly 5 losses: retype_region_no_cap_to_obj_no_mdb (apply fastforce), retype_region_no_cap_to_obj_no_pspace (apply fastforce), unique_table_refsD_no_left_lookup (by blast), valid_table_capsD_no_asid_hyp (cases ptr, fastforce), cap_refs_in_kernel_windowD_no_lookup (cases ptr, fastforce). Does not fire on any of the 15 wins — none use fastforce or by blast; wins that use automation use named simp lemmas, wp chains, induction, or by clarsimp/simp.

## Proposed predicate (for calibration; review before promoting)
```python
def proposed_signal(feat):
    import re
    proof = feat.get('proof', '') or ''
    # fastforce is a search oracle that hides implicit premise consumption
    if re.search(r'\bfastforce\b', proof):
        return True
    # 'by blast' at proof close is the same oracle in term-mode proofs
    if re.search(r'\bby\s+blast\b', proof):
        return True
    return False
```

## Promote?
- If regression-free AND it catches a meaningful share of losses → integrate the
  pattern into `scan_p` (a new demote rule, mirroring rule-precondition), add a
  `lb_*`/evidence field, re-run the detector on the labeled lemmas to confirm,
  then commit.
- If it misfires on wins → reject or tighten; the hard gate is **zero wins
  demoted**.
