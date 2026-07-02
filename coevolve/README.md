# coevolve — fork-① harness (localizer + gate, calibrated on real cases)

Skill: `.claude/skills/isabelle_prover_coevolve`. This dir is the fork-①
deliverable: **agent-driven break localization**, calibrated against real
l4v co-change commits (human fix `C_p` = ground truth).

## Layout

| Path | What |
|---|---|
| `scripts/case_extract.py` | Deterministic: split a co-change commit into `delta_artifact.diff` (trigger) + `delta_proof.diff` (**held-out** human fix) + `ground_truth.json` (per-lemma: `statement_evolved`/`body_changed`/kind incl. `relocated`) |
| `scripts/localize_agent.py` | Agent half: mechanical context (changed identifiers incl. hunk-context enclosing defs; arch-filtered grep candidates) → `claude -p` → predicted work-list + full transcript. Sees ONLY the artifact half. |
| `scripts/calibrate.py` | Score prediction vs ground truth: file P/R, lemma recall, statement-evolve flag accuracy |
| `cases/<commit>/` | 15 real L1 co-change seeds (AArch64 window), each a self-contained bundle |

Requires `claude` CLI (runs on the machine that has it; B currently lacks it
— run localizer on A or install CLI here). `case_extract`/`calibrate` run
anywhere (py3.8+).

```bash
python3 scripts/case_extract.py --l4v verification/l4v --commit <hash> --out cases/<hash>
python3 scripts/localize_agent.py --l4v verification/l4v --case cases/<hash> --model sonnet
python3 scripts/calibrate.py cases/<hash>
```

## Calibration state (2026-07-01, model=sonnet, 4 rounds on df5e1611 + 0e8048b49)

| Seed | fileP | fileR | lemR | evoAcc |
|---|---|---|---|---|
| df5e1611 (clearMemory match-C) | 0.50 | **1.00** | **1.00** | 0.0 |
| 0e8048b49 (user_vtop ≥→>) | 0.50 | **1.00** | 0.0* | – |

\* GT lemmas here are human-ADDED helpers — name-matching additions is
unfair; calibrate.py should split added vs modified (open item).

**File-level localization works** (recall 1.0 both). Precision 0.5 =
maintainer-plausible extra files. evoAcc is the open front.

## Failure→fix taxonomy (each calibration failure became a deterministic fix)

1. Isabelle `\<lambda>` in JSON reply → invalid escape crash ⇒ tolerant
   backslash repair before `json.loads`.
2. Change inside a definition BODY → zero identifiers extracted ⇒ also parse
   the hunk-context header (`@@ ... @@ definition NAME`).
3. Blind agent guessed by file-name similarity (Decode_A→Decode_AI; human
   fixed ArchArch_AI) ⇒ prompt rule: breakage follows REFERENCES, not names.
4. Deep static reasoning concluded "proof survives" and returned an empty
   work-list, while the human DID edit ⇒ prompt reframed **maintainer-aligned
   (semantic tracking), not merely compiler-forced**.
5. Agent listed inspected-but-fine files in work_list ⇒ schema: work_list =
   edits only; `cleared` is a separate key.
6. 41 candidates × unbounded tool exploration → 600s timeout ⇒ deterministic
   **arch filter** (AArch64-only change cannot break ARM/RISCV64/X64 files;
   41→~8 candidates) + inspect-budget (≤6 files) + 900s.

## Open items (next session)

- calibrate.py: split added vs modified lemmas in lemma-recall.
- evoAcc: feed candidate lemma STATEMENTS into the prompt; adjudicate
  "agent says survives / human edited" cases with the build oracle
  (`check-theory` on the reconstructed break) — that also gives mode=build.
- Gate agent (evolution-vs-weakening): build calibration set from the
  statement_evolved pairs in `cases/*/ground_truth.json` (positives) +
  synthetic weakenings (negatives).
- Sweep all 15 seeds once localizer prompt stabilizes; then wire into the
  repair driver (Step 2 of the skill's First cut).
