# spec-0013 — Step 2 witness sequencing + Shape 3 hardening (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

User 2026-06-02 reviewed the SKILL post-[[0012]] and flagged two
items needing tighter wording. Point 3 of the review was an
acknowledgement of the tool 2 framing (no action needed).

## Point 1 — Step 2 witness sequencing

**User**: current Step 2 says "partial patch (modified lemma only)
→ generate witness → paste into patch". Risk: an agent could
verify the partial patch via `check-theory.sh --patch`, see OK,
and conclude soundness — bypassing the witness gate.

**Fix**: appended a hard sentence after the witness-tool
paragraph:

> **The patch handed to `check-theory.sh --patch` in Step 3 MUST
> contain both the strengthened lemma AND its `<name>_old`
> witness** (shape 1). Do not verify the partial patch alone —
> a partial patch can pass Step 3 while leaving the soundness
> gate unenforced. The witness must sit immediately after the
> strengthened lemma in the patch hunk so Isabelle parses both
> in the same theory pass.

This makes the temporal order explicit:
  1. Write strengthened lemma → save partial patch
  2. Run `spec_witness_gen.py` on the partial patch → get witness
  3. Paste witness into the patch
  4. ONLY THEN run `check-theory.sh --patch <full patch>`

## Point 2 — Shape 3 vs Acceptance

**User**: Shape 3 (delete/disable) is correctly NOT a
strengthening, but current text only "hints" at this. Should be
harder: shape 3 fails Acceptance #2, and routing options should
be spelled out.

**Fix**: replaced the soft "this is a refactor, not a
strengthening" line with an explicit reference to Acceptance
gate #2 + two enumerated routes out:

> **Shape 3 is a refactor, NOT a spec strengthening** —
> `spec_impact.py` will emit a `removal` verdict, which **fails
> this sub-skill's Acceptance gate #2** (a strengthening verdict
> is required). Routes out:
>   (a) **Bundle with a shape-1 strengthening of the same lemma
>       family** in one patch — the strengthening carries
>       Acceptance; the cleanup rides along, recorded as a
>       non-gating component in `decision.md`.
>   (b) **Ship the deletion as a standalone refactor PR outside
>       this sub-skill** — still a seL4-source PR per parent
>       rule 5 (it touches `verification/l4v/`), but the
>       acceptance criteria are the refactor's own; this
>       sub-skill's strengthening gate does not apply.
> Do NOT submit a pure shape-3 patch through the spec-
> strengthening workflow expecting it to pass.

This closes a real loophole. Pre-edit, an agent reading "this
is a refactor, not a strengthening" could plausibly still try
to ship shape 3 through the sub-skill workflow and get rejected
late at gate #2 (wasting a check-theory.sh run + decision.md
draft). Now the rejection happens at workflow-shape selection
time.

### Route (b) honesty note

The original edit attempt said "route it as a standalone
meta/cleanup PR (parent SKILL rule 5 Meta-PR variant)" — but
the Meta-PR variant is for non-`verification/l4v/` changes
(skill docs / tools / infra). A `.thy` lemma deletion IS a
verification/l4v change, so Meta-PR doesn't apply. Corrected
before commit to say "standalone refactor PR" with
"acceptance criteria are the refactor's own" — honest about
the fact that parent SKILL doesn't currently have a named
"refactor PR" variant for seL4-source changes; routing
those is its own decision.

## Point 3 — Tool 2 framing

**User**: keep the current honest framing about
`spec_premise_probe.sh` — single-process doesn't save wall;
daemon mode is where time savings live; useful for diagnostic
residual subgoals.

**Action**: no change required. The current SKILL Optional
diagnostic block reads exactly that way:

> Per-probe wall ≈ 60-200s in single-process mode (same as
> `check-theory.sh --patch`); the value-add is the **residual
> subgoal text** printed on `load-bearing`, which
> `check-theory.sh` failure doesn't expose. Future daemon mode
> (shared JVM across many probes) is where the real speedup
> lives.

Preserved verbatim.

## What changed

`.claude/skills/isabelle_prover_spec/SKILL.md`:
- Step 2: +7 lines of "patch must contain BOTH" hard rule after
  the spec_witness_gen paragraph.
- Shape 3 bullet: +12 lines explicitly citing Acceptance gate #2
  and enumerating routes (a) bundle, (b) standalone refactor PR.

SKILL went 205 → 228 lines.

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- Pure SKILL contract-tightening.
- Smoke test below replaces measurement.json.

## Smoke test

Internal cross-reference check after the edits:

```
$ grep -nE 'shape 3|removal|Acceptance.*#?2' \
    .claude/skills/isabelle_prover_spec/SKILL.md
82:   - **Delete or disable an existing lemma** (shape 3) — only
87:     `removal` verdict, which **fails this sub-skill's
170:   `additive`. (`rewrite` / `noop` / `removal` alone is insufficient.)
```

- Shape 3 wording explicitly cites Acceptance gate #2 ✓
- Acceptance gate #2 explicitly lists `removal` as insufficient ✓
- Forward-reference (shape 3 → Acceptance) and the criterion
  itself (Acceptance enumerates accepted verdicts) are
  internally consistent.

All SKILL link targets still resolve (5/5 checked: playbook,
downstream-map, refinement-proofs, witness_gen.py,
premise_probe.sh).

## Notes / follow-ups

- A "named refactor PR variant" in parent SKILL rule 5 could
  formalise route (b) above. Out of scope for this sub-skill
  PR; if the project sees enough Shape 3 traffic to warrant
  it, raise on the parent SKILL surface.
- The 0013 patch leaves the existing witness-contract paragraph
  (the "Three patch shapes, three different soundness
  obligations" block) untouched — it correctly describes shape 3
  as "not a strengthening" already; the new hardening lives at
  the workflow-Step 2 level where the choice is actually made.
