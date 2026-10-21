# spec-0006 — SKILL slim + tools refactor (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

A coordinated slim of the spec sub-skill, motivated by user
critique 2026-06-02:

> "现有方法使用 pattern + tier 给 spec 强化分类并引入脚本验证，
> 但太冗余 —— 转变为维护 Pattern 分类学，而不是系统性发现-验证-
> 落地 seL4 Spec Strengthening。skill 太长，应考虑重构。"

The refactor cuts SKILL.md from **409 → 137 lines** (-66%) and
reorganises tools from `tools/critical_path/` (misnamed; that dir
originally hosted CSTR-2 build-wall analysis) to a dedicated
`tools/spec_strengthen/` namespace.

## What changed

### `.claude/skills/isabelle_prover_spec/SKILL.md`

Slim rewrite. Removed the Pattern A/B/C/D/E/G taxonomy, Tier 1/2/3
hierarchy, ROI weights table, Per-pattern derivability tactic
template (4×6 = 24 cells), scanner reliability calibration, and
Experiment Calibration §9. All moved to the playbook.

New SKILL is a thin contract:
- Definition of stronger spec (`P_old ⟹ P_new ∧ Q_new ⟹ Q_old`).
- Witness lemma as the soundness gate (one form: `<name>_old` in
  the same patch, proved by a Hoare monotonicity rule).
- Workflow Steps 1-6.
- Acceptance gates (4 hard).
- Targets table.
- Anti-pattern (definitional change out of scope).
- Tools (3 one-line entries).
- References (4 cross-links).

### `tools/spec_strengthen/` (NEW DIR — renamed from `tools/critical_path/`)

| File | Status | Role |
|---|---|---|
| `spec_strengthen_scan.py` | Library only (CLI removed). | Detector functions (`detect_unused_premise`, `detect_missing_functional`, `detect_paired_chain`) + `parse_thy_lemmas` + `Lemma` / `Finding` dataclasses. Imported by the two user-facing tools. The `Finding.pattern` field is renamed to `Finding.kind` with descriptive string values. Internal Pattern A/B/C labels are dropped. |
| `spec_candidates.py` | NEW (replaces `rank_candidates.py`) | Step 1 entry. Emits **flat ranked** table (no Pattern A/B/C sections). Each row carries `name, file:line, session, consumers, suggested_move`. Internal `KIND_WEIGHT = {unused-premise: 5, missing-functional: 3, paired-chain: 1}` drives ranking but is hidden from output. |
| `spec_impact.py` | Slimmed. | Step 4 entry. Verdict ontology stays granular (per user review 2026-06-02 — SKILL Acceptance enumerates them). `unclassified-add` verdict **dropped** — all added Hoare-triple lemmas verdict to `additive`. NEW flag `--measurement-out FILE` writes simplified JSON matching `_template/measurement.json` schema for rule-5 audit bundles. |

`tools/critical_path/` directory removed (was misnamed for these
tools — `critical_path` is the CSTR-2 build-wall axis, not the spec
strengthening axis).

### `reports/experiments/_template/command.sh`

Updated tool paths from `tools/critical_path/` → `tools/spec_strengthen/`.
Replaced the standalone "Step 4.5 derivability check" placeholder
(removed from SKILL) with an automatic `spec_impact.py
--measurement-out` invocation that produces measurement.json in one
shot — eliminating the manual schema-mapping the old template required.

### `references/spec-strengthen-playbook.md` (RENAMED from patterns.md)

Becomes the catch-all for everything cut from SKILL.md:
- Taxonomy summary (Strengthening / Additive / Packaging)
- Pattern catalog (legacy A/B/C/D/E/F/G names with mapping table to
  new detector kinds; preserved for cross-reference with past
  strengthen-logs).
- Scanner reliability calibration table.
- ROI weight rationale (per-kind).
- Worked case studies (already present from prior commits).
- Empirical notes — Pattern F rejection, Pattern G manual-only.

### `.claude/skills/isabelle_prover/scripts-container/check-theory.sh`

Removed stale merge-conflict markers (`<<<<<<<`/`=======`/`>>>>>>>`)
that survived the PR-2 merge into spec-strengthen. With the markers
the script had a bash syntax error and could not run.

## Smoke tests (in lieu of measurement.json)

All ran 2026-06-02 on a clean spec-strengthen working tree:

1. **`spec_candidates.py --target ainvs --limit 8`**
   - Scanned 4740 Hoare-triple lemmas in invariant-abstract.
   - Top row: `dxo_wp_weak` (148 consumers × missing-functional weight 3 = 444 ROI).
   - Output: single ranked table, natural-language suggested moves,
     no Pattern letter exposed. ✓

2. **`check-theory.sh ... Finalise_AI.thy AInvs --patch
   logs/exp_smoke_C.patch`** (the unbind_maybe Pattern C +
   witness patch from prior smoke runs)
   - Returns `OK (29958ms)` ✓
   - Verifies both `unbind_maybe_notification_not_bound` (strengthened)
     and `unbind_maybe_notification_not_bound_old` (witness) in one
     pass. ✓

3. **`spec_impact.py ... --measurement-out /tmp/m.json`**
   - Verdict: `premise-weaken` ✓
   - SSS: 2.0 (2 premise conjuncts removed) ✓
   - Wall: +7.2%, gate ≤ +30% ✓
   - Witness: present (1 found) ✓
   - measurement.json generated with rule-5-schema fields:
     `{session, baseline_wall_ms, trial_wall_ms, delta_pct,
       wall_gate_pass, baseline_ref, session_rebuild_done,
       consumers_lines/files, impact_verdict, strength_score,
       witness_present, witness_advisory_pass, gate_pass,
       has_weakening}` ✓

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- No Hoare-triple soundness gate to run; smoke tests above are the
  verification.
- Matches parent SKILL rule-5 Meta-PR variant (patch.diff +
  decision.md, no command.sh/measurement.json required).

## Notes / follow-ups

- The candidates tool's `KIND_WEIGHT` table is hardcoded. SKILL
  hints externalising to `tools/spec_strengthen/kind_weights.json`
  if more tuning becomes needed. Not blocking.
- Pattern G (frame preservation) and D (loose-bound ≤ → =) still
  lack auto detectors. Playbook documents the manual recipes.
- `spec_coverage_matrix.py` was never created and has been removed
  from SKILL references (it lived in §3 / §7 of the old SKILL).
