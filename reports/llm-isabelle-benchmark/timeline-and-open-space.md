# LLM × Isabelle/seL4 — timeline, contributions, results, and the open space

Branch: `bridge-consumer-trace`. Date: 2026-06-17.
Consolidates deep-research passes (wb3y7bdx2, wxmp6ttrc) + the per-paper landscape.
Companion files: `related-work-landscape.md` (per-paper detail), `two-theses-positioning.md`.

**Caveats**: (1) 2026 works carry forward-dated arXiv IDs (2602/2603/2604.*) and are
very recent preprints — numbers/venues may shift. (2) Scores are **NOT directly
cross-comparable**: substrates differ (math/AFP vs seL4 vs Coq vs Verus) and pass@k /
solve-count / replay metrics differ. Compare *within* a substrate only.

---

## 1. Timeline

| # | Date | Work | Venue | Substrate | Task | Headline result |
|---|---|---|---|---|---|---|
| 1 | 2021 | **PISA** (Portal-to-Isabelle) | — | AFP/HOL | infra + extraction | 2.49M datapoints; gRPC REPL |
| 2 | 2022 | **Thor** | NeurIPS'22 | AFP + miniF2F | LM + hammer | PISA 39→**57%** |
| 3 | 2022 | **Draft-Sketch-Prove** | ICLR'23 | miniF2F | informal→formal sketch | miniF2F SOTA (its time) |
| 4 | 2023-03 | **Baldur** | FSE'23 | AFP (PISA) | whole-proof gen + self-error repair | 47.9% / **65.7%** (+Thor) |
| 5 | 2023-03 | **Magnushammer** | ICLR'24 | AFP + miniF2F | neural premise selection | PISA **59.5%** vs SH 38.3; +Thor **71%** |
| 6 | 2024-01 | **Selene** | ACL'24 | **seL4** (340) | synthesis | GPT-4 **27.06%** |
| 7 | 2024-06 | **FVEL / FVELER** | NeurIPS'24 D&B | **seL4/l4v** (29k) | env + corpus; synthesis | +17.4% SV-COMP (transfer) |
| 8 | 2025-10 | **CoqDev / Adapt** | arXiv | **Coq** commits (1720) | **repair-after-change** | maintenance benchmark |
| 9 | 2026-02 | **AutoReal-Prover** | arXiv | **seL4** (660) | synthesis (CoT-trained) | **51.67%** |
| 10 | 2026-03 | **Stepwise** | "OSDI'26" | **FVELER** held-out | synthesis (stepwise+tree search) | **77.6%** (seL4 SOTA) |
| 11 | 2026-03 | **ExVerus** | arXiv | **Verus/Rust** | **repair** (counterexample) | +38% over SOTA |
| 12 | 2026-04 | **PROMISE** | arXiv | **l4v** (223) | synthesis (training-free + retrieval) | 223-thm benchmark |

Substrate shift: **AFP/math (2021-24) → seL4 (2024→)**. Task: **synthesis everywhere,
except CoqDev/ExVerus = repair (other assistants)**.

---

## 2. What each did (problem → contribution)

**Phase I — make LLMs prove in Isabelle at all (AFP):**
- **PISA**: no ML interface to Isabelle → built a gRPC REPL + 2.49M-point AFP extraction. The geology everything stands on.
- **Thor**: pure LM is weak at routine closing → **LM + Sledgehammer hybrid**, learns *when to hammer* (39→57%).
- **Draft-Sketch-Prove**: LLMs draft informal but not formal proofs → **informal draft → formal sketch → hammer fills gaps**.
- **Magnushammer**: premise selection is the hammer bottleneck → **transformer retrieval** for premises (beats Sledgehammer; +Thor 71%, 4× fewer params).
- **Baldur**: step-proving is costly → **whole-proof generation in one call + a repair model** for the model's OWN failed attempt (statement+failed-proof+error). First "repair" flavor — but self-error, not code-change.

**Phase II — move to real systems (seL4) and push synthesis:**
- **Selene**: all prior eval is AFP/math → **first project-level seL4 benchmark**; revealed real-systems proof is *much harder* (27% vs AFP/miniF2F rates).
- **FVEL/FVELER**: no large real-verification Isabelle corpus + code↔proof not connected → an **interactive C→AutoCorres→Isabelle→LLM environment** + a **seL4-derived corpus**; showed seL4 fine-tuning *transfers* to general C-verification. (Used seL4 as training fuel; evaluated transfer, not seL4.)
- **AutoReal-Prover**: seL4 synthesis too low → **CoT-trained prover over seL4's 10 proof categories** (51.67%, ~2× Selene).
- **Stepwise**: coarse generation ignores Isabelle's proof state + FVELER's held-out split was never used → **new Isabelle REPL exposing fine-grained state + stepwise generation + tree search**; **activated FVELER as held-out eval** (77.6%, SOTA).
- **PROMISE**: trained provers are costly + contamination-prone → **training-free** proof-state search + structured retrieval, with a target-isolated 223-theorem benchmark.

**Phase III — the conceptual pivot to maintenance/repair (other assistants):**
- **CoqDev/Adapt**: all proof-LLM work is greenfield synthesis, but real cost is *maintenance* (proofs break on code change) → **first commit-history-mined "repair-after-change" benchmark** (Coq; 1720 theorems). The pivot the project rides — but Coq, no refinement.
- **ExVerus**: LLM Verus proofs fail → **counterexample-guided repair** (Verus/Rust).

---

## 3. Experimental results (within-substrate)

**AFP (Isabelle), pass@k / proof-check:**
- Thor 57% · Magnushammer 59.5% (vs Sledgehammer 38.3%) · Magnushammer+Thor **71%** · Baldur 47.9% whole-proof, **65.7%** +Thor.

**seL4 (Isabelle), held-out synthesis — the comparable progression:**
- Selene **27.06%** (GPT-4, 340 thms) → AutoReal **51.67%** (341/660) → Stepwise **77.6%** (FVELER test/test-hard).
- Stepwise head-to-head: beats Selene 5.6%, FVEL 7.8%, standalone Sledgehammer 40.3%.
- ~**3× improvement in ~2 years** on seL4 synthesis. The lane is crowded and fast.

**seL4 (transfer, not direct):**
- FVEL: fine-tune on FVELER → +17.4% (Llama3-8B 69→81) / +12% (Mistral-7B) on **external** SV-COMP. No pass@k on seL4 in the paper.

**Repair (other assistants):**
- CoqDev: maintenance benchmark (Coq). · ExVerus: +38% over SOTA (Verus/Rust, LCBench/HumanEval).

**Contamination health-check**: only target-level held-out (AutoReal/PROMISE) or
session-isolation (FVELER test-hard); **none** does pretraining-level
perturbation/decontamination on public l4v → the 51–78% rates **likely overstate**
genuine generalization.

---

## 4. The remaining open space

| Dimension | Status (2026-06) |
|---|---|
| seL4 as held-out eval for **synthesis** | **FILLED** (Selene/AutoReal/Stepwise/PROMISE) |
| Direct seL4 **pass@k** metric | **FILLED** |
| Real-systems substrate for Isabelle LLM | **FILLED** (seL4 replaced miniF2F) |
| **Refinement-proof REPAIR (corres/ccorres) on seL4** | **OPEN — unoccupied by anyone** |
| **Proof STRENGTHENING / spec-strengthen-then-repair** | **OPEN** |
| **Code-change-then-fix on seL4/Isabelle** | **OPEN** (CoqDev did it in Coq; ExVerus in Verus) |
| refinement structure as a **task dimension** | **OPEN** (synthesis works flatten it away) |
| **pretraining-level decontamination** for l4v | **OPEN + adversarial** (undercuts incumbents' numbers) |

**The single empty cell**: *seL4/l4v as a held-out LLM-evaluation benchmark for
refinement-proof REPAIR / strengthening after a code change* — published by no one.

Why it stays open and is hard to grab accidentally:
- **No reusable dataset**: FVELER/PROMISE are synthesis sets (statement→proof). Repair
  needs **(code_v1, proof_v1, code_v2, broken_proof) → fixed_proof** tuples that no seL4
  dataset provides. They must be *constructed* — via perturbation operators (the failure
  taxonomy: rename / add-case / swap-subop / alter-precond / alter-relation) or l4v
  git-history mining (CoqDev's method, ported to Isabelle).
- **Only seL4 supplies the task type**: corres/ccorres refinement repair exists nowhere
  else (CoqDev = Coq functional correctness; ExVerus = Verus).
- **Built-in decontamination advantage**: synthetic breaks make the (broken, fix) pair
  absent from pretraining — sidestepping the contamination flaw of all synthesis benchmarks.

## 5. Where the project sits

The arc — access → hybrid automation → whole-proof+self-repair → real-systems
substrate → synthesis SOTA → training-free → **maintenance/repair pivot** — has been
converging on repair, but the seL4/Isabelle/refinement instance is the one cell no one
has filled. The project already has the pieces in this exact cell: the bridge-repair
concept, the 0029 strengthen-and-repair exemplar (kernel-checked, 2-iteration repair
loop), the failure taxonomy (perturbation operators), and the consumption metric.
CoqDev is the template-and-contrast; FVEL supplies the refinement extraction; Baldur
supplies the LLM-repair shape. **Nobody has assembled them on seL4.**

**Urgency**: items 9–12 are Feb–Apr 2026 preprints; the repair cell could be filled by
an Isabelle port of CoqDev or a repair variant of PROMISE. Move while open.
