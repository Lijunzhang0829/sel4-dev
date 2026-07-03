# Signal proposal — P-slot (mode: precision)

> LLM-proposed, **trained on TRAIN, validated on HELD-OUT**, propose-only. The
> signal is real only if it GENERALIZES (held-out regression-free + still
> catches). train-only success = OVERFIT = convergence evidence.

Full run archived alongside: `round3-precision.prompt.txt` (exact input fed to the LLM) · `round3-precision.raw.jsonl` (claude -p NDJSON — the discovery process).

Mode: **precision** — fire (demote) on losses, NEVER on wins. Split by
original lemma (~80/20), so a sibling drop never leaks into held-out.

## Verdict: ⚠ NEEDS REVIEW (fires on a train win)

| set | losses fired (recall) | wins fired (regression) | regression-free |
|---|---|---|---|
| TRAIN (gate; LLM saw this) | 17/44 (recall 0.386) | 2 | False |
| **HELD-OUT (generalization)** | 3/15 (recall 0.2) | 1 | False |
| _full set (reference)_ | 20/59 (recall 0.339) | 3 | False |

**Convergence scalar** = held-out demote-recall of this best signal =
**0.2**. Tracked across rounds: when it stays ≈0 (no generalizing signal
shrinks the held-out residual), the precision mining space is **exhausted for this
data snapshot**. misfired held-out wins: ["gts_wf'"].

## Structural signature
The proof body contains a standalone `erule` tactic or a standalone `elim` tactic (as a primary tactic in an apply step, not as the `elim!:` keyword-modifier inside a `clarsimp`/`simp` invocation). These tactics CONSUME a hypothesis from the goal by matching it against a named rule without explicitly mentioning the hypothesis by head name. When a premise is dropped, the implicit hypothesis it would have contributed is gone, so `erule`/`elim` fails to find a matchable hypothesis and the proof collapses — even though the dropped head never appears by name in the proof text. This is structurally distinct from `drule`/`frule` (which copy/transform rather than consume) and from signal-6 (which tracks named-rule precondition feeding). The pattern predicts load-bearing because the proof's implicit hypothesis-matching strategy silently depended on the now-absent premise.

## Why it's novel (not an existing signal)
Signal 1 (unused-premise) looks for the dropped head being ABSENT from proof text — a static name-occurrence check. Signal 6 (rule-precondition dependency) looks for drule/frule forwarding the head into a named rule's precondition. This new signal is orthogonal: it looks for `erule`/`elim` tactics that DESTRUCTIVELY consume some hypothesis by structural matching against a rule, without naming the hypothesis. The dropped premise provided that hypothesis; after the drop, the matching fails silently. Neither name-absence nor forwarding-chain reasoning covers this consumption-by-structural-match mechanism.

## Expected effect
Catches ~9 of 40 losses with 0 false positives on wins: valid_vspace_obj' (erule allEI ballEI), valid_table_caps_ptD x2 (erule allE / erule (1) impE / erule impE), cnode_cap_ex_cte x2 (erule cte_wp_at_weakenE / erule(2) valid_CNodeCapE), ifunsafe_tcb_update' (simp …, elim allEI), descendants_range x3 (apply (elim exE) + apply (erule(1) impE)). All 11 wins are spared: the one win that uses elim-like syntax (post_cap_delete_pre) uses only the `elim!:` modifier inside clarsimp, which the negative lookahead correctly excludes.

## Proposed predicate (for calibration; review before promoting)
```python
def proposed_signal(feat):
    import re
    proof = feat.get('proof', '')
    # erule consumes a hypothesis; never appears in any win
    if re.search(r'\berule\b', proof):
        return True
    # standalone elim (tactic), but NOT elim!: which is a clarsimp/simp modifier
    # elim!: is always followed by optional-whitespace + optional-! + optional-whitespace + colon
    if re.search(r'\belim\b(?!\s*!?\s*:)', proof):
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
