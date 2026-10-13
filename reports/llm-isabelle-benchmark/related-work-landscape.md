# LLM × Isabelle/seL4 — related-work landscape (per-paper)

Branch: `bridge-consumer-trace`. Date: 2026-06-17.
Source: deep-research passes wb3y7bdx2 (pass 4) + wxmp6ttrc (frontier), adversarially
verified. **Caveat**: the 2026 works carry forward-dated arXiv IDs (2602/2603/2604.*)
and are very recent preprints (some v1/v2, not yet peer-reviewed at a stable venue);
exact numbers/venues may shift in camera-ready.

Every seL4 work below does **forward proof SYNTHESIS**. None does refinement-proof
**repair** (corres/ccorres), **strengthening**, or **code-change-then-fix** — that
niche is unoccupied.

---

## A. The seL4/l4v line (the substrate that matters)

### FVEL / FVELER — Lin, Hu et al., NeurIPS 2024 Datasets & Benchmarks (arXiv:2406.14408)
*"FVEL: Interactive Formal Verification Environment with LLMs over Theorem Proving"*
- **Substrate**: the FOUNDATIONAL one. Extracts Isabelle from real seL4/l4v via
  `C-Parser → SIMPL → AutoCorres → Isabelle`. Dataset **FVELER** = a fork of
  `seL4/l4v` (`FVELER/l4v_FVEL`, GPL v2); `sel4_extraction/` mirrors l4v's structure.
- **Scale**: 758 theories, ~29,125–29,304 lemmas, ~200,646–201,498 proof steps,
  dependency depth 31–73 (max path **156**). Split by lemma into
  train / val / test (1,077) / **test-hard (852)** (test-hard from independent sessions
  SysInit / SysInitExamples / LibTest).
- **Tasks** (forward synthesis only): (1) **Statement Prediction** (S0: LLM generates a
  lemma as the formal spec of the code), (2) **step-wise Proof Generation** (S_i:
  generate proof steps until a whole proof, with Isabelle error feedback).
- **Method/eval**: fine-tune LLMs on FVELER, then **evaluate on EXTERNAL** Code2Inv
  (133) + SV-COMP (1,000). Headline: Llama3-8B-FT **69→81 (+17.4%)** on SV-COMP;
  Mistral-7B 75→84 (+12%). **No pass@k on the seL4 test split in the paper.**
- **Contamination**: none.
- **Leaves open**: seL4 as held-out *eval* (the split is defined but never scored);
  repair / strengthening / perturbation; refinement (corres/ccorres) as a task.

### Selene — arXiv:2401.07663, ACL 2024
- **First project-level automated proof benchmark on seL4.** 340 sampled seL4 theorems.
- **Task**: forward synthesis (whole-proof gen), Isabelle validation.
- **Method**: evaluates **GPT-3.5-turbo + GPT-4** (closed → eval, not fine-tune).
- **Result**: **27.06%** best (GPT-4) — the baseline later works surpass.
- Earliest true held-out seL4 eval (Jan'24, predates FVEL). Synthesis, not repair.

### AutoReal-Prover — arXiv:2602.08384, Zhang/Zhao et al., Feb 2026
- **Substrate**: seL4 "Important Theories", **660 held-out theorems**, 10 proof categories.
- **Task**: forward synthesis — "automatically synthesizes a complete Isabelle proof script".
- **Method**: CoT-trained prover (7B per memory); **eval theorems' proof steps excluded
  from CoT training** (target-level held-out).
- **Result**: **51.67% (341/660)**; beats Selene 27.06%.
- **Contamination**: target-level held-out only (no pretraining decontamination).
- Synthesis, not repair/strengthening/refinement.

### Stepwise — He et al., arXiv:2603.19715 ("OSDI 2026")
- **Substrate**: **FVELER** (29,125 theorems; train 26,081 / val / **test 1,077 /
  test-hard 852**). The canonical FVEL follow-up that **activates FVELER's latent
  held-out split** (turns it from training-only into an eval target).
- **Task**: forward synthesis, reframed as **stepwise proof-state transitions + tree
  search** (1.7B/7B per memory).
- **Method**: a **new Isabelle REPL** exposing fine-grained proof states + automation
  (Sledgehammer / Nitpick / QuickCheck); fine-tune on train, eval on held-out.
- **Result**: up to **77.6%**; head-to-head beats Selene 5.6%, FVEL 7.8%, standalone
  Sledgehammer 40.3%. (Current synthesis SOTA on seL4.)
- **Contamination**: test/val splits are **RANDOM** (only test-hard session-isolated)
  → pretraining contamination from public l4v plausible & **unaddressed**.
- Synthesis, not repair.

### PROMISE — arXiv:2604.05399, Apr 2026
- **Substrate**: a **223-theorem benchmark** (100 P1 + 100 P2 + 23 P3) from real l4v
  sessions (lib/Monads, invariant-abstract, infoflow, sep-capDL, access-control).
- **Task**: forward synthesis, "reframes proof generation as a **stateful search over
  proof-state transitions**"; structured retrieval (per memory: PROMISE = structured
  retrieval).
- **Method**: **TRAINING-FREE**; scored by **whole-theory Isabelle replay** where the
  original target proof is never used.
- **Contamination**: target isolated into a temp theory, original proof never used
  (target-level); **pretraining contamination not addressed**.
- Synthesis, not repair. The closest competitor-BENCHMARK to anything the project would
  build — but synthesis-only.

---

## B. Proof-REPAIR / change benchmarks — exist, but NOT Isabelle/seL4/refinement

### CoqDev / "Adapt" — Lu et al., Purdue, arXiv:2510.25103, Oct 2025
- **CoqDev**: **1,720 theorems mined from real Coq commit histories**, modeling the
  **incremental development process** = a **proof-maintenance-after-change benchmark**.
- **Coq-only** ("confined solely to the Coq ecosystem"; Isabelle/Lean = future work).
- Mitigates GitHub leakage by **prioritizing newer projects**.
- → **THE template + contrast** for the project: proves "proof-maintenance-after-change
  benchmark" is a real, publishable shape — but it's Coq functional-correctness, **no
  refinement (corres/ccorres), no seL4**.

### ExVerus — Yang et al., arXiv:2603.25810, Mar 2026
- LLM **Verus/Rust proof REPAIR** via counterexample reasoning. **+38%** over SOTA on
  LCBench/HumanEval. 0 occurrences of seL4 / l4v / Isabelle / corres / refinement.

---

## C. The AFP lineage (Isabelle-LLM, but NOT seL4 — context)

- **PISA** (Jiang et al., *Portal-to-Isabelle*): the standard gRPC REPL substrate;
  **2.49M datapoints** (AFP+HOL), 95/1/4 split, **3,000 held-out test theorems**.
- **Thor** (NeurIPS 2022): LM + hammer; PISA **39→57%**; solved 8.2% of theorems
  unprovable by LM or Sledgehammer alone.
- **Magnushammer** (ICLR 2024): retrieval-based premise selection; PISA 59.5% vs
  Sledgehammer 38.3%; miniF2F 34.0% vs 20.9%; **+Thor SOTA 71%, 4× fewer params**.
- **Baldur** (First et al., FSE 2023): Minerva 8B/62B; whole-proof gen **47.9%**, +Thor
  **65.7%**; PISA/AFP (183K theorems, 6,336 test). Two tasks: whole-proof gen +
  **repair of the model's OWN failed attempt** (statement, failed proof, error) — NOT
  code-change repair. seL4 only as a motivating example.

---

## Summary table

| Work | Year | Substrate | Task | Eval/metric | Headline | Repair? |
|---|---|---|---|---|---|---|
| Selene | ACL'24 | seL4 (340) | synth | held-out, Isabelle check | 27.06% (GPT-4) | no |
| FVEL/FVELER | NeurIPS'24 | **seL4/l4v** (29k) | synth | fine-tune→**external** SV-COMP | +17.4% | no |
| AutoReal | Feb'26 | seL4 (660) | synth | held-out pass | **51.67%** | no |
| Stepwise | "OSDI'26" | **FVELER** held-out | synth + tree search | pass@k | **77.6%** (SOTA) | no |
| PROMISE | Apr'26 | l4v (223) | synth, training-free | whole-theory replay | benchmark | no |
| CoqDev/Adapt | Oct'25 | **Coq** commits (1720) | **repair-after-change** | — | — | **yes (Coq)** |
| ExVerus | Mar'26 | **Verus/Rust** | **repair** | LCBench/HumanEval | +38% | **yes (Verus)** |
| Baldur | FSE'23 | AFP | synth + own-error repair | pass@k | 65.7% | own-error only |

**The empty cell**: refinement-proof **repair / strengthening / code-change-then-fix on
seL4/Isabelle**. Filled nowhere. CoqDev shows the *shape* is publishable; only seL4's
refinement stack provides the corres/ccorres task type; FVELER/PROMISE are synthesis
datasets that **cannot be repurposed** (repair needs (code_v1, proof_v1, code_v2,
broken_proof) tuples no dataset has).
