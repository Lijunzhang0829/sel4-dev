# Block 4 — loop design: search adequacy ⭐ + gate validity

The loop is two decoupled phases per candidate:
1. **SEARCH** (`ab_agent.py`, JVM/Isa-REPL): reach the lemma, capture the
   pre-state `A_goal` and the target subgoal signature `B_sig` (by running the
   original tactic once), then search for a deterministic, audit-passing rule chain
   from A to B. Proof state at every step comes from Isa-REPL (`_step`,
   `_clone_tls`/`_focus_tls`, `signature`). Proposer = heuristic classifier OR LLM
   (haiku) over a file bridge. `TARGET_SUBSTR` focuses the search on only the
   DB-flagged slow line.
2. **GATE** (`gate.py`, after JVM exits): correctness via `check-theory.sh --patch`
   (real `isabelle process` build) + speed via `isar timing --lemma` orig-vs-patched.

This division is itself a fix: the OLD in-JVM timing (`time_run`, Isa-REPL
`time.monotonic`) baked ~6–8 ms IPC into every step and so **systematically biased
multi-step static paths as "slower"** — which made the old "no payoff" partly an
artifact. The gate removes that bias (prover-internal timing, no IPC).

## 4.1 Search adequacy ⭐ — the central credibility issue

NO-PATH means *the bounded search found no chain*. The bound: ATTEMPT_CAP=30,
DEPTH=24, TIME_CAP=2400 s. **But the LLM is slow (~30–96 s/call), so most LLM runs
hit TIME_CAP long before the 30-attempt cap.** Actual attempts (from
`runs/ab-<lemma>.json`):

| lemma | proposer | attempts | hit cap(30)? | depth of search |
|--|--|--:|--|--|
| make_zombie_invs' | LLM | 30 | ✅ full | thorough |
| update_valid_tcb' | heuristic | 30 | ✅ full | thorough |
| dmo_bind_ev / dmo_bind_ev' | heuristic | 30 | ✅ full | thorough |
| retype_region_silc_inv | LLM | 24 | ~ | moderate (2 targets) |
| empty_slot_pas_refined | LLM | 10 | ❌ | **shallow** |
| untyped_inc_n' | LLM | 10 | ❌ | **shallow** |
| checkCapAt_ccorres | LLM | 7 | ❌ | **shallow** |
| setupReplyMaster_corres | LLM | 7 | ❌ | **shallow** |
| invoke_untyp_invs' | LLM | 6 | ❌ | **shallow** |
| fastpath_enqueue_ccorres | LLM | 2 | ❌ | **very shallow** |

**This is the experiment's weakest link.** Of the 8 DB search-oriented candidates,
only 1 (make_zombie_invs') reached the full attempt cap; the rest tried 2–24. The
flagship `empty_slot_pas_refined` got 10 attempts (4 made progress, none closed).
`fastpath_enqueue_ccorres` got only 2. So for those, NO-PATH is **"the slow LLM
didn't find a chain in the time available"**, which is materially weaker than
"no chain exists."

Mitigating points (why the result still carries weight):
- The proposals were *on-target*: for `empty_slot` the LLM proposed exactly the
  right dest rules (is_transferable_all_children, sta_cdt_transferable,
  pas_refined_Control) and chained drules with `progress`, yet did not close in 10
  steps — suggesting the closure is not a short chain.
- The heuristic runs that DID hit 30 attempts (update_valid_tcb', dmo_bind_ev*,
  make_zombie_invs') are thorough and still NO-PATH.
- Structurally, a `fastforce dest: A B C` taking >100 s is doing large backtracking
  combination-search; a short deterministic equivalent is a priori unlikely.

But none of that *closes* the gap. To make the search-adequacy argument airtight
one would need: (a) a faster proposer so every candidate reaches the 30-cap (or
higher), and (b) deeper DEPTH, and ideally (c) re-running the shallow ones.

## 4.2 Gate validity

- **Correctness**: `check-theory.sh --patch` actually builds the patched theory
  with `isabelle process` — stronger than the audit (the audit only checks the path
  is automation-free; the build checks it *proves the lemma*). Sound.
- **Speed**: `isar timing --lemma` on original vs a written patched copy, both
  prover-internal. Sound above the noise floor.
- **Noise floor (added mid-experiment)**: `ex_tupleI` flipped ACCEPT(−40%) /
  REJECT(+25%) across two runs at 4–7 ms — proving ms-scale verdicts are noise.
  `GATE_MIN_MS=50` now returns INCONCLUSIVE below 50 ms. Good, but note it was a
  *reactive* fix; any pre-floor verdict in early logs is unreliable.
- **The gate was never exercised on a found path** (all 8 were NO-PATH), so its
  ACCEPT branch on a *real* static path is only validated on the toy `ex_tupleI`,
  not on a genuine optimization. If a future run finds a path, the ACCEPT side
  needs its own scrutiny.

## 4.3 Loop-design verdict
Architecture is **sound** (state-guided search + ground-truth correctness +
prover-internal timing, with the IPC bias removed). The decisive weakness is
**search depth under the slow LLM**: 7 of 8 target searches were below the attempt
cap, several drastically (2–10). So the loop reliably establishes "no *easily
found* static path," and for the 4 full-cap runs "no path within a thorough bounded
search" — but it does not establish "no path exists," especially for the 6 shallow
LLM candidates.
