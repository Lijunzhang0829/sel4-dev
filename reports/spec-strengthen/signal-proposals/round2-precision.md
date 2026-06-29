# Signal proposal — P-slot (mode: precision)

> LLM-proposed, deterministically calibrated, **propose-only**. Review then
> hand-write into `spec-strengthen/scripts/spec_slot_hints.py` if promotable.

Full run archived alongside: `234112-precision.prompt.txt` (exact input fed to the LLM) · `234112-precision.raw.jsonl` (claude -p NDJSON — the discovery process, every thinking/text event).

Mode: **precision** — the signal should fire (demote) on losses, NEVER on wins.

## Verdict: ⚠ NEEDS REVIEW

| metric | value |
|---|---|
| losses fired (demoted) | 16/30 (recall 0.533) |
| **wins fired (regression — must be 0)** | **3** |
| regression-free | False |
| predicate errors | 0 |
| misfired wins | ['some_get_page_info_umapsD_no_valid_objs', 'decode_inv_wf_no_real_cte', "gts_wf'"] |

## Structural signature
Proofs that contain hypothesis-consuming or hypothesis-restructuring tactics — specifically `hoare_gen_asm`, `supply`, `erule allE`/`ballE`/`allEI`/`ballEI`, `erule (N) impE`, `strengthen`, `unfolding X_def`, `bspec`, or `rule ccontr` — predict LOSS because each of these tactics explicitly operates on a specific hypothesis in the proof context (not via search): `hoare_gen_asm` extracts a named predicate from the Hoare precondition; `supply` surgically patches the lemma set (typically because something the proof relied on has gone missing); `erule allE`/`bspec` instantiate a universally-quantified hypothesis with a concrete term; `erule impE` destructs an implication from context; `strengthen` applies monotone precondition strengthening against the current hypothesis set; `unfolding X_def` syntactically expands a definition in the goal/context, revealing hidden dependency on hypotheses that were opaque to the head-name scan; `rule ccontr` initiates contradiction and typically needs the premise in the derived absurdity. If the dropped premise supplied the key context hypothesis for any of these tactics, the tactic call fails — even though the dropped head name never appears textually in the proof.

## Why it's novel (not an existing signal)
Signal 7 lists 'no strengthen/rule_tac' as an exclusion condition that removes a BOOST — it does not actively DEMOTE. This signal does actively DEMOTE, and covers hoare_gen_asm, supply, erule allE, bspec, unfolding X_def, and rule ccontr which appear nowhere in signals 1–7. The tried-and-rejected signal targeted unconstrained search tactics (blast/fastforce/auto); every tactic in this signal is directed and structural — each explicitly names a proof-context slot it operates on, making the hypothesis-absence failure deterministic rather than search-dependent. Signal 5 inspects the STATEMENT syntax (\<lbrakk>...\<rbrakk>); signal 6 inspects INVOKED rule dependencies; this signal inspects PROOF tactic vocabulary for patterns that structurally require a present hypothesis.

## Expected effect
Fires on ~14 of 30 losses while firing on 0 of 15 wins. Specific losses caught: retype_region_no_cap_to_obj_no_mdb and retype_region_no_cap_to_obj_no_pspace (hoare_gen_asm); suspend_unlive'_no_valid_mdb, suspend_unlive'_no_valid_objs, suspend_unlive'_no_tcb_at (supply); valid_table_caps_ptD' and valid_table_caps_ptD_no_cap (erule allE + erule (1) impE); valid_vspace_obj' (erule allEI ballEI); cap_insert_ap_invs_no_tcb_valid (strengthen); unique_table_refsD_no_left_lookup (unfolding unique_table_refs_def); lookup_pt_slot_pte_no_pspace_aligned, lookup_pt_slot_pte_no_pd_at, lookup_pt_slot_ptes_aligned_valid_no_ekm (bspec); vs_cap_ref_master_no_base (rule ccontr). None of the 15 wins use any of these tactics.

## Proposed predicate (for calibration; review before promoting)
```python
def proposed_signal(feat):
    import re
    proof = feat.get('proof', '') or ''

    # hoare_gen_asm: moves a named predicate from Hoare pre into the local context
    if re.search(r'\bhoare_gen_asm\b', proof):
        return True

    # supply: locally patches simp/wp lemma sets; signals missing hypothesis machinery
    if re.search(r'\bsupply\b', proof):
        return True

    # erule allE / ballE / allEI / ballEI: eliminates a universal-quantifier hypothesis
    if re.search(r'\berule\b[^\n]*\b(allE|ballE|allEI|ballEI)\b', proof):
        return True

    # erule (N) impE: eliminates an implication from the hypothesis context
    if re.search(r'\berule\s*\(\d+\)\s+impE\b', proof):
        return True

    # strengthen: explicit monotone precondition strengthening against current hyp set
    if re.search(r'\bstrengthen\b', proof):
        return True

    # unfolding X_def: syntactic expansion tactic (not simp) that exposes hidden deps
    if re.search(r'\bunfolding\b[^\n]*_def\b', proof):
        return True

    # bspec: instantiates a bounded-universal hypothesis with a specific term
    if re.search(r'\bbspec\b', proof):
        return True

    # rule ccontr: proof by contradiction; typically needs dropped premise in absurdity
    if re.search(r'\brule\s+ccontr\b', proof):
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
