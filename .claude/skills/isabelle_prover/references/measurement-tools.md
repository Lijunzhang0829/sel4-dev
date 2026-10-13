# Measurement & timing tools — which one for what (READ before timing anything)

Hard-won from the search-reduction experiment. The wrong timer silently produces
**cold-start-inflated, high-noise numbers** that flip verdicts (a measured "+16% faster" was
really **−76% slower**; a "91% / 25 s" win was really 55% / 3.4 s). Pick the tool by ROLE.

## ⚠️ Bottom line first: there is NO stable per-line build timer

Every available timer is too noisy to pin a per-line speedup precisely:
- **in-REPL wall-clock**: cold-start up to **4.5×**, py4j IPC, GC → up to **58 %** run-to-run noise.
- **stock `command_timings`**: stores **`elapsed` only (no cpu)**, and elapsed is wall-time that
  **varies 30–60 % across builds** (same original line measured 150.6 / 188.7 / 242.0 s under
  different parallelism). A patched-build "+N %" is NOT reproducible to better than ~30 %.
- **IsarLite `isar timing`**: BROKEN (`missing-json`).
- **per-command CPU**: not recorded by Isabelle anywhere accessible.

So: **report CORRECTNESS (build-green, reproducible) confidently; report SPEED only as a
direction + rough band ("~20 %, build-verified, corroborated across methods"), never a precise
reproducible figure.** A precise number would need per-command cpu (absent) or a controlled
many-reps fixed-load harness. For a fair stock comparison, rebuild orig AND variant in the SAME
config (`-o threads=1`, serial) BACK-TO-BACK — never compare a golden parallel orig to a serial
variant (that confound gave a spurious "19.2 %"; matched serial gave 23.3 %).

## The tools and what they actually measure

| Tool | Mechanism / basis | Measures | Provenance |
|---|---|---|---|
| **Isa-REPL** (`tools/seL4-proof-search/Isa-Repl`, `time_static_path`) | scala-isabelle JVM + py4j; **Python `time.monotonic()` around a step** | wall-clock of (tactic CPU **+ py4j IPC + JVM GC/cold-start**) | internal wrapper over published scala-isabelle (citable base) |
| **golden `command_timings`** | read from heap log DB `/root/.isabelle/heaps/*/log/<SESSION>.db`, table `isabelle_session_info` | Isabelle's OWN per-command **elapsed** from the real production build | **stock Isabelle** — reproducible |
| **stock `isabelle build`** (patched) + command_timings | rebuild the session, re-read command_timings | Isabelle's own elapsed for the variant, in a real build | **stock Isabelle** — reproducible |
| **IsarLite** (`isar timing`, `IsarLite-runtime`) | prover-internal `command_timing`, per-lemma, reps | prover-internal per-line elapsed/cpu (no IPC) | **internal, unpublished** — NOT for paper numbers |
| **check-theory.sh `--patch`** (use `check_theory_selfqual.sh`) | `isabelle process -l SESSION -T patched`, ~5 min, no rebuild | does the patched theory **build green** (correctness) | stock Isabelle — reproducible |

## Rules (the experiment learned these the hard way)

1. **Correctness verdict = stock build only.** `check-theory.sh --patch` (correctness) or a full
   `isabelle build`. The in-REPL reach-B (same state B) is a *fast pre-filter and CAN
   false-positive* (signature collision on long wrapped goals) — never report a rewrite as
   correct without a stock build. Splice B_sig-aware: a CLOSING line appends `done`; a
   MID-PROOF line (leaves subgoals) replaces only the command span, NO `done`.

2. **Reported speedup = stock command_timings, NEVER Isa-REPL wall-clock.** The original line's
   elapsed is free from the golden heap DB. The variant's elapsed needs a rebuild (expensive —
   see cost below). Isabelle's elapsed is its own measurement, immune to py4j/Python-wall.

3. **Isa-REPL / IsarLite are internal inner-loop accelerators** for *finding* candidate
   rewrites fast (reach-B search, cheap per-lemma timing). Fine to use; their wall-clock is a
   *rough screen only*, never a reported number, and IsarLite must not back a paper claim.

4. **Cold-start & noise discipline** (if you must time in-REPL): the FIRST run of a tactic is
   **2–4.5× inflated** (Poly/ML JIT, simpset cache, lazy theory load). Always: discard the
   warm-up run, take the **median of ≥5 reps**, and measure an **A/A noise floor** (time the
   original twice) — only claim a speedup if `(orig−variant) > 2×(A/A spread)`. Heavy InfoFlow
   lines showed up to **58 %** run-to-run noise; below that, "wins" are not real.

5. **command_timings stores `elapsed` only** (no cpu). Elapsed carries a parallel-scheduling
   component — build with `-o threads=1` for a cleaner near-serial number.

## Costs / gotchas (so you don't rediscover them)

- A stock variant rebuild is **~5 s for the heap-check but ~88 min if the patched theory is
  EARLY in its session's import chain** (everything downstream re-checks). `measure_build_elapsed.py`
  reads golden orig elapsed for free; the variant rebuild is the expensive part — reserve it for
  the FINAL headline winners, not routine screening.
- **Heap isolation is fragile**: redirecting `ISABELLE_HEAPS` did NOT fully prevent the build
  from writing the golden session DB (it got clobbered; the isolated DB lacked
  `isabelle_session_info`). A patched build can pollute the golden heap — the source is restored
  but the heap goes stale and self-heals only on the next clean `isabelle build <SESSION>`.
- **IsarLite `isar timing` returns `missing-json`** even after `pip install -e
  /workspace/IsarLite-runtime` — a deeper failure for these l4v lemmas (lemma-span / session
  rename / collect-timeout). It is the *intended* cheap per-line build-context timer; fix it if
  you want cheap timing, but it can't back a published number anyway.
- **Build verifier self-qualified-name bug**: `check-theory.sh` renames `theory FinalCaps →
  Tmp_xxx` but leaves body refs `FinalCaps.foo` dangling → even the *unpatched* theory fails to
  build (false REJECT). Use `check_theory_selfqual.sh` (renames body self-quals too); fixes
  FinalCaps / theories that qualify their own constants.

## Recommended pipeline (correct + reproducible + affordable)

```
FIND candidates   → Isa-REPL reach-B + reduce_agent           (internal, fast; not reported)
SCREEN speed      → in-REPL median/drop-warmup/A-A noise floor (rough; flag <2σ as unconfirmed)
VERIFY correct    → check_theory_selfqual.sh --patch            (stock; reproducible)   ← gates every claim
REPORT speed      → golden command_timings (orig) + one stock isabelle build (variant)  ← only for headline winners
```
