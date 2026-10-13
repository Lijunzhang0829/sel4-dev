# Block 6 — threats to validity + overall credibility verdict

## 6.1 Threats to validity (consolidated, ranked by severity)

| # | threat | severity | where | does it undercut the claim? |
|--:|--|--|--|--|
| T1 | **Shallow LLM search** — 7/8 targets below the 30-attempt cap (2–24); flagship 10, fastpath 2 | **HIGH** | block 4.1 | Yes — NO-PATH for those = "not found in budget," not "no path" |
| T2 | **Small, filtered sample** — only 8 lemmas looped; single-line, ≥10 s, frac>30% only | **HIGH** | block 3 | Yes — limits "anywhere in seL4" generalization |
| T3 | **search_frac is command-level, not intra-tactic** — a frac=1.0 fastforce can still be mostly simp | MED | block 2.2 | Partly — mitigated by the no-simp filter |
| T4 | **ACCEPT branch never exercised on a real path** — gate's speed-accept only seen on toy ex_tupleI | MED | block 4.2 | No (no path found), but means a future ACCEPT is unvetted |
| T5 | **Noise floor was a reactive fix** — pre-floor ms verdicts (e.g. ex_tupleI) were unreliable | LOW | block 4.2 | No (no >floor false ACCEPT survived) |
| T6 | **Lemma grouping heuristic** in DB scan — a few totals may mis-merge | LOW | block 2.2 | No (targets re-derived + spot-checked) |
| T7 | **Per-lemma speedup ≠ total-wall** — never tested (no candidate passed) | N/A here | block 1.3 | Would matter only if a speedup were found |

## 6.2 What IS credibly established
- **Measurement is sound** (T-none): prover-internal timing, validated to ~4% vs
  `isabelle process`; the old IPC-biased timer was removed.
- **The method finds nothing on the most promising targets**: across all 5 heavy
  sessions, on the genuinely search-oriented (no-simp) slow lemmas — the very class
  where static-ization should pay off — 8/8 NO-PATH.
- **The 4 full-cap (30-attempt) searches** (make_zombie_invs', update_valid_tcb',
  dmo_bind_ev, dmo_bind_ev') are thorough and still NO-PATH.
- **A clean mechanistic reason** holds independent of search budget: a >100 s
  `fastforce dest: …` *is* a large backtracking combination-search; even with the
  correct rules in hand (empty_slot: LLM proposed them) no short deterministic
  chain closed it.

## 6.3 What is NOT closed (the honest gap)
- For 6 of 8 targets the search was **shallow** (T1) → those NO-PATHs are weak.
- Only **8 lemmas** were looped, from a deliberately-narrow slice (T2).
- Therefore "**no static-izable speedup exists anywhere in seL4**" is **not
  proven**. What is proven is the narrower, still-valuable statement below.

## 6.4 Credibility verdict (graded)

| statement | evidence grade |
|--|--|
| "This A→B method + reliable gate is correctly built and measures truthfully." | **A** (validated) |
| "On the tested search-oriented slow lemmas, this method finds no static-izable speedup within budget." | **A−** |
| "A thorough (full-cap) search finds no static path on these classical lines." | **B** (only 4/8 reached full cap) |
| "No static-izable wall-time speedup exists anywhere in seL4." | **C** (strong suggestion, not proven; T1+T2) |
| "Even if found, it would move total build wall." | **untested** (T7; CSTR says probably not) |

## 6.5 What would raise the grade to A on the universal claim
1. **Faster proposer** (local model or batched calls) so every candidate reaches
   ≥30 attempts and higher DEPTH — directly kills T1. Priority #1.
2. **Re-run the 6 shallow targets** to the full cap (cheap once the proposer is
   faster).
3. **Widen the sample**: include 1–10 s tier and multi-line embedded search steps;
   run all 10 (not 8) search-oriented + a random sample of the MIXED 49.
4. **Exercise the ACCEPT path** at least once (e.g. construct a known-staticizable
   slow lemma) to validate the gate's speed-accept end to end (T4).

## 6.6 Bottom line
The experiment is **methodologically sound and the conclusion is directionally
well-supported**, but it is **over-stated as a universal negative**. Honest
phrasing of the result:

> "Across all seL4 sessions, on the slow, genuinely search-dominated classical
> lemmas most likely to benefit, a state-guided A→B searcher (with reliable
> in-prover timing) found no static-izable speedup — searches that ran to the full
> budget confirmed this, and the mechanistic reason (backtracking combination-search
> has no short deterministic equivalent) is consistent. The universal 'no payoff
> anywhere' remains a strong inference, limited by shallow LLM searches on 6 of 8
> targets and a sample of 8 lemmas."

The single highest-value follow-up is a **faster proposer** so the search-adequacy
threat (T1) is removed; everything else is secondary.
