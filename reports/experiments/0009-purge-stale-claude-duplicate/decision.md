# spec-0009 — purge stale `claude/.claude/skills/` SKILL duplicate (Meta PR record)

| Field | Value |
|---|---|
| **Variant** | Meta PR (rule 5 simplified — no source change to verification/l4v) |
| **Branch** | `spec-strengthen` |
| **Date** | 2026-06-02 |
| **Verdict** | applied |

## Scope

User 2026-06-02 spotted that the spec SKILL still referenced
`tools/critical_path/` and Pattern A/B/C taxonomy — but **not** at
`.claude/skills/isabelle_prover_spec/SKILL.md` (which was correctly
slimmed in [[0006]] / [[0008]]). The stale references lived in a
parallel tracked tree at `claude/.claude/skills/`.

This dir bundles the de-duplication.

## What changed

### Removed

The 4 SKILL.md files under `claude/.claude/skills/` (frozen at
their pre-slim state since commit `1c1e90c` "trim main to
project-framework + skills + heap-records only", 3 days old):

```
D claude/.claude/skills/isabelle_prover_c/SKILL.md
D claude/.claude/skills/isabelle_prover_haskell/SKILL.md
D claude/.claude/skills/isabelle_prover_proof/SKILL.md
D claude/.claude/skills/isabelle_prover_spec/SKILL.md
```

The operational `.claude/skills/` tree (the one Claude Code reads at
session start) is now the single source of truth.

### Cleaned up live references that no longer resolve

`run.sh` lines 46-56 (pre-edit) had a `if [ -d
"${SCRIPT_DIR}/claude/.claude/skills/isabelle_prover/scripts" ]`
conditional that picked between two ISA_SCRIPTS locations. The
`claude/.claude/skills/isabelle_prover/` parent never existed in
the tracked tree (only the 4 sub-skill SKILL.md files were ever
tracked under `claude/.claude/`; no `isabelle_prover/scripts/`
sibling was). So the conditional was dead code — always fell
through to `.claude/skills/isabelle_prover/scripts`. Collapsed to
the unconditional assignment.

`.claude/skills/isabelle_prover/scripts/_dx.sh` lines 19-20
contained a layout-example comment using the obsolete
`<REPO_ROOT>/claude/.claude/skills/...` path. Rewritten to the
current `<REPO_ROOT>/.claude/skills/...` shape.

### NOT touched

- `claude/.claude/skills/isabelle_prover_proof/reference/` —
  untracked work-in-progress from another agent (per memory
  `proof-skill-resets.md`). Out of this branch's scope; left in
  place.
- Historical `reports/experiments/0002-*/patch.diff` and
  `reports/experiments/0006-*/patch.diff` — these contain
  `claude/.claude/` references as part of past PR records. Per
  `reports/experiments/README.md` Lifecycle, experiment dirs are
  permanent records (survive even reverts).

## Why this duplicate existed

Commit `1c1e90c` "trim main to project-framework + skills +
heap-records only" snapshotted the SKILLs onto `main` at a nested
path (`claude/.claude/...`) — apparently as a stable
"installed-skills snapshot" separate from the working-tree
`.claude/` location used by Claude Code. Since then the
working-tree `.claude/skills/isabelle_prover_spec/SKILL.md` was
heavily revised (taxonomy redesign → slim → 0006/0008 audit
fixes), but the snapshot was never re-synced. Drift made it
actively misleading: a reader landing on the snapshot would follow
references to `tools/critical_path/` (now `tools/spec_strengthen/`)
and to Pattern A/B/C labels (now retired).

Per user instruction "彻底删掉 claude/.claude/skills/ 这棵副本",
single-source-of-truth wins over snapshot-redundancy.

## Why meta-PR variant

- No `.thy`/`.hs`/`.c`/`.h` under `verification/l4v/` modified.
- Pure documentation/scaffolding cleanup.
- Smoke tests below replace measurement.json.

## Smoke tests

All ran 2026-06-02 on a clean spec-strengthen working tree after
the edits:

1. **No live (non-history) refs to `claude/.claude/` remain**
   ```
   grep -rn 'claude/\.claude' . \
     | grep -v -E '^\./(\.git|verification/l4v|reports/experiments/000[0-9])/'
   ```
   ✓ empty result. Only matches are inside historical experiment
   PR records (0002, 0006) which are immutable.

2. **No live refs to `tools/critical_path` in skill/tools/template**
   ```
   grep -rn 'critical_path' \
     .claude/skills/ tools/spec_strengthen/ \
     reports/experiments/_template/ reports/experiments/README.md run.sh
   ```
   ✓ empty result.

3. **`run.sh` still parses** — `bash -n run.sh` exits 0. ✓

4. **`spec_candidates.py --target ainvs --limit 1`** — still
   scans 4740 Hoare-triple lemmas under invariant-abstract and
   emits the expected dxo_wp_weak top row. The tool tree
   (`tools/spec_strengthen/`) is unaffected by the duplicate
   purge. ✓

## Notes / follow-ups

- After this commit, the `claude/.claude/skills/` tree still
  physically exists on disk but only contains the untracked
  `isabelle_prover_proof/reference/` dir owned by another agent.
  No tracked files remain under `claude/`.
- If the proof-agent's reference/ dir eventually moves to its
  proper home under `.claude/skills/isabelle_prover_proof/`, the
  `claude/` parent dir can be safely `rmdir`-removed. Not blocking.
- The memory file `proof-skill-resets.md` notes the proof SKILL
  "每轮被还原成基线". If that reset mechanism reads from the now-
  removed `claude/.claude/skills/isabelle_prover_proof/SKILL.md`,
  it will need rewiring to the `.claude/` location. Out of scope
  for this PR — the spec SKILL has no such reset behavior.
