# l4v proof-repair distribution — preliminary exploration

> Empirical grounding for the **co-evolution / repair** topic
> (`.claude/skills/isabelle_prover_coevolve`). Measures how proof repair
> actually happens in the real l4v git history, to set the task shape.
> Date: 2026-07-01.

## Method

- Repo: `verification/l4v`, upstream `github.com/seL4/l4v` (blob:none partial
  clone, unshallowed). Full non-merge history = **5629 commits, 2014–2024**.
- File-level tree data was available for **694 recent non-merge commits**
  (2023-01-25 → 2024-07-01 — the AArch64-verification window, the
  co-evolution-densest period; a good first sample, but re-measure on a wider
  window before publishing).
- Each changed file is bucketed into an **artifact layer** (`spec/abstract`,
  `spec/design`, `spec/haskell`, `spec/cspec`, `spec/machine`, …) or a
  **proof session** (AInvs = `invariant-abstract`, Refine, CRefine, Access,
  InfoFlow, DRefine, …). Per-commit we compute: touches proof?, touches
  artifact?, #proof sessions (cross-layer), #proof files (fan-out).
- Reproduce: `python3 reports/repair/classify_commits.py <git-log-dump>`,
  where the dump is
  `git log --no-merges --name-only --format='@@C@@%x09%H%x09%ci%x09%s' HEAD`.

## Findings

### 1. Repairs happen in commits SEPARATE from the change

| Category | Share of 694 |
|---|---|
| touch `proof/` | **52.0%** |
| touch an artifact (`spec/`) | 14.7% |
| **co-change** (artifact + proof in one commit) | **4.5%** |
| proof-only | 47.6% |
| artifact-only | 10.2% |

**76%** of artifact-only commits are followed by a proof-only commit within 3
commits. → the (change, repair) unit is a **commit pair/sequence**, not a
single commit. `C_a` (artifact new, proof old) is the natural "post-break"
state; the following proof-only `C_p` is the human reference fix.

### 2. Most repairs are small; a heavy tail is large

Fan-out (#proof files per proof-touching commit): median **1**, 56% touch a
single file, 86% stay within a **single session**. Tail: p90=10, p99=144,
max=259.

| #sessions | share of proof-touching commits |
|---|---|
| 1 (single-layer) | **86.4%** |
| 2 | 6.6% |
| 3 | 1.7% |
| 4–7 (cross-layer cascade) | ~5% (≥4-session ≈ 19 commits / 18 months ≈ 1/month) |

### 3. CRefine is the dominant repair site

Session hit counts (a commit may hit several): **CRefine 180 · Refine 149 ·
AInvs 79** · InfoFlow 21 · proof-other 21 · Access 19 · DRefine 15.
→ the real maintenance tax is in CRefine; but method development should start
on AInvs/Refine (lighter, avoids the CRefine heap-init + OOM wall).

### 4. Triggers are abstract / design / haskell, not raw C

Among co-change commits, the artifact layer changed: abstract **42%** ·
design(hs→isa) 35% · haskell 32% · machine 29% · other-spec 13% · **cspec/C
10%**. C enters via design regeneration, so model the "change" as
abstract/design/haskell edits.

## Archetype seed commits (first benchmark instances)

**L1 — clean single-session co-evolution (2–6 files, "sync/match C"):**
- `0e8048b4` aarch64 aspec+ainvs: sync user_vtop check with C
- `df5e1611` aarch64 machine+ainvs: update clearMemory to match C
- `c4390d8e` aarch64 spec+proof: update armvVCPUSave to match C

**L3 — cross-session cascade:**
- `e89813ec` proofs: updates for monad refactor — 155 files / 7 sessions
- `d5fa6043` proof: update (non-x64) for physBase-dependent defs — 25 files / 5 sessions

**Big single-session sweep (breadth, likely shallow-but-many):**
- `1f068023` crefine: update for new ccorres cong rules — 86 files
- `d87f5e13` crefine: update for no_name_eta — 42 files

## Change-type taxonomy (drives the synthetic perturbation generator)

Observed real change types, to be reproduced as decontaminated synthetic
perturbations: `sync/match-C`, rename (lemma / method / constant), `update for
new cong/simp rules`, abstraction introduction (e.g. `physBase`), monad/def
refactor.

## Consequences for the task (see the skill for the full design)

1. Task unit = **commit pair** (`C_a` → `C_p`), not a single broken lemma.
2. **L1 (single-session) covers ~86%** of real repairs — start there, it is
   the bulk of the tax, not a toy.
3. Measure **breadth (fan-out) and depth (per-lemma proof-delta) separately** —
   a 155-file sweep may be easy-but-many.
4. Value target = **CRefine**; method development = **AInvs/Refine**.
5. Benchmark = **commit-pair replay** (ecological) + **synthetic perturbation**
   (decontaminated, taxonomy above).

## Data limitation

File-level analysis covers the 694-commit / 18-month window with local tree
data; the full 5629-commit / 10-year history has metadata only (a full blob
fetch would be needed for full-history file-level distribution). The window is
co-evolution-dense (AArch64 port), so it is a strong first sample, but widen it
before publication.
