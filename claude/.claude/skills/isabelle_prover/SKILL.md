---
name: isabelle-prover
description: "Strengthen seL4 formal verification — spec, proof, Haskell, C. Session-scoped with $100 per-session budget cap. Proof-level targeting required for productive runs."
---

# Isabelle/HOL Strengthen — v6

Make the seL4 kernel stronger by improving its Isabelle specifications,
proofs, Haskell executable spec, or C implementation. "Stronger" means:
proofs catch more real bugs, run faster, cover more invariants, or the
code is cleaner — **and still verifies**.

Each invocation of this skill targets **one Isabelle session**
(Access / AInvs / InfoFlow / Refine / CRefine / …). Budget cap is
**$100 per session**.

## Command

`/isabelle:strengthen <target> [--types spec,proof,haskell,c,all]`

## The four types (intent only — no prescribed recipe)

- **spec** — tighten theorem statements / specifications (`.thy` under `spec/`, `proof/invariant-abstract/`)
- **proof** — make proofs faster, smaller, or more maintainable (`.thy` under `proof/`)
- **haskell** — improve the Haskell executable spec (`.hs` / `.lhs` under `spec/haskell/`)
- **c** — improve the kernel C while keeping CRefine green

"Improvement" is your judgment call — speed, robustness, clarity,
coverage. Pick what the target rewards.

## Five hard rules

### 1. No direct edits

You do not open a `.thy` / `.hs` / `.c` / `.h` file and write to it.
Every change flows through a patch file, verified by:

```
bash "$ISA_SCRIPTS/check-theory.sh" <file> <session> --patch <patch>
```

Commit with `--apply <patch>` (only after a clean `--patch` run for
that exact patch).

### 2. No bypassing the prover

Do not introduce `sorry`, `oops`, new `axiomatization` / `axioms`, or
proof-mode toggles that silence errors. Do not call `isabelle build`
directly to "confirm" a change — `check-theory.sh` is the gate.

### 3. Two logs per run

Both files are append-only JSONL under `logs/`, one record per line.

**`logs/attempts-<run_id>.jsonl`** — one line per `check-theory.sh`
invocation (every attempt — baseline, trial, apply — not just
successes):

```json
{"ts":"<ISO-8601>","source":"agent|auto","kind":"baseline|patch|apply","target":"<file-or-proof>","session":"<name>","verdict":"pass|fail|timeout","wall_ms":12345,"notes":"<short free text>"}
```

When the call carried a patch, `check-theory.sh` also fills these optional
fields on the auto record: `patch_sha` (first 12 hex of sha256 of the
patch text), `lines_added`, `lines_removed`, `patch_bytes`. On a
successful `--apply`, an extra `kind:"apply"` auto record is written that
additionally carries `verify_ms` (the wall_ms of the prior verification
recurrence) and `patch_path` (relative to the workspace, pointing to the
persisted patch under `logs/patches/<run_id>/<sha>.patch`).

The script auto-appends a `source:"auto"` record for every call. You may
add `source:"agent"` records with richer notes but must not duplicate
what the auto record already captured beyond adding insight.

**`logs/impact-<run_id>.jsonl`** — one line per APPLIED change (what moved):

```json
{"ts":"<ISO-8601>","scope":"<file-or-proof>","session":"<name>","before_ms":42100,"after_ms":31800,"delta_ms":-10300,"delta_pct":-24.5,"patch_sha":"<first 12 hex of sha256(patch)>","notes":"<what changed>"}
```

`before_ms` / `after_ms` are whole-file check-theory times you already
ran; both should appear as pass records in `attempts-<run_id>.jsonl`.

**Auto-derived siblings (do not write yourself — `run.sh` produces these
after you exit):**

- `logs/impact-<run_id>-enriched.jsonl` — every impact record joined with
  attempts by `patch_sha`, gaining `lines_added`, `lines_removed`,
  `patch_path`, `attempts_to_apply`.
- `logs/per-target-<run_id>.jsonl` — one line per target file with
  attempt counts, file delta sums, LOC delta, and a token / USD share
  attributed from the stream-json transcript (each assistant turn bound
  to the most recent `check-theory.sh` / `proof-timing.sh` target seen
  in its Bash tool_use; tokens before the first such call land in
  `_recon`).
- `reports/session-baselines-<run_id>-after.md` and `-diff.md` — per-theory
  before/after table. **Only emitted when `RUN_SESSION_CLEAN_REBUILD=1`**
  (opt-in env var). Without that flag the heap-log timings would mix
  builds and produce spurious +400% rows.

The run-level `logs/metrics-<run_id>.jsonl` aggregate gains:

- `session_warm_check_before_ms` / `_after_ms` — `isabelle build` exit
  time when nothing needs to recompile (~7-15s typical). **Not** a
  verification-cost proxy — it's mostly Isabelle startup. Don't compute
  deltas from these and don't show them as "session got faster/slower".
- `session_clean_before_ms` / `_after_ms` / `_delta_ms` / `_delta_pct` —
  full-rebuild wall, only present when `RUN_SESSION_CLEAN_REBUILD=1`
  (~30 min – 2 h depending on session). This is the honest "did the
  session as a whole verify faster" signal.
- `sum_delta_ms` (already populated from impact records) — the cheapest
  honest session-level signal: sum of file-level wall changes the agent
  actually verified. Use this as the default session metric.

### 4. No fabricated attempts

Every `attempts-*.jsonl` record with `source:"agent"` MUST correspond to
a real `check-theory.sh` call that finished and returned a `wall_ms` you
read from its output. Do not write `wall_ms: 0, verdict: "fail"` as a
"I'm predicting this will fail" placeholder. Either run the tool and
record the true outcome, or write nothing. Auto-logged records from the
script are always tied to a real run.

### 5. PR-tracked mainline — every accepted patch reaches `main` via a PR

`main` and `baseline` are aggregation points, not development surfaces.
No direct push to either. Every patch that passes rules 1–4 above must
be committed to a **topic branch** and submitted as a **Pull Request**.

- **Topic branch per type**: experiments live on type-named branches —
  `proof-strengthen`, `spec-strengthen`, `haskell-mega-merge`,
  `c-strengthen`. Each branched off `baseline`.
- **Per-experiment record** under `reports/experiments/<NNNN>-<name>/`:

  | file | content |
  |---|---|
  | `patch.diff` | the exact source change |
  | `command.sh` | the measurement command (re-runnable) |
  | `measurement.json` | baseline wall + trial wall + delta, with `baseline_ref` pointing at `reports/golden-baseline/walls.json` |
  | `decision.md` | human-readable summary + verdict (`applied` / `rejected` / `inconclusive`) |

- **PR description** cites: the lemma/file changed, the matching
  baseline wall entry, the trial wall from `check-theory.sh --apply`
  output, and the experiment ID.
- **Workflow**: `baseline` (clean template) → topic branch → experiments
  → PR → `main` (accumulates verified results). `baseline` is refreshed
  from `main` only when a release milestone is hit.

Reason: every accepted modification has a PR + experiment record + a
measurable wall delta tied to the golden baseline. Heaps, container
state, and `/tmp` logs can be rebuilt; lost provenance can't be
reconstructed.

Everything else — how to pick targets, how many strategies to try per
candidate, how to decide when a change is "good enough", when to move
on — is your call. `run.sh` closes the loop after you exit by computing
Claude-side metrics (total USD, cache hit rate, turn count) and joining
them with the hit rate + impact-sum you wrote.

## Step 0 — Ensure proof-level data before spending on patches

**Observed across six prior runs: file-level baselines are insufficient.**
Agents who target WHICH FILES are slow, but then bulk-patch them without
proof-level data, regress on most files (Tcb_AC +160%, Syscall_AC +17%,
CNode_AC +7%, Access_AC no win). Agents who had proof-level cost tables
found 2 big wins on Access (−34.9%, −23.1%).

Before attacking anything, check for:

```
reports/slow-proofs-<session>-lowercase-or-dirname.md
```

(e.g. `reports/slow-proofs-access-control.md`,
`reports/slow-proofs-infoflow.md`).

**If it exists:** read it. Each entry ranks proofs by sorry-substitution
cost. Target proofs with cost ≥ 3000 ms. Below that, Isabelle's fixed
overhead dominates and the wins aren't measurable.

**Caveat learned the hard way (Access run, 2026-04-25)**: sorry-cost is
*"how much faster does the file build if this proof is replaced by sorry"*.
For a leaf-but-widely-depended-on proof — say, a one-line lemma whose
statement is used by 50 later proofs — the sorry-cost can be 20+ s even
though the proof body itself takes 200 ms. Optimizing such a proof's
*tactic* yields zero. Before patching, sanity-check: open the proof body,
count its lines, look at its tactic calls. If it's a 1–3 line `by simp`
or `by (clarsimp simp: ...)`, the cost lives in **what depends on it**,
not what it executes — move to a different proof.

**If it's missing:** run

```
bash "$ISA_SCRIPTS/scan-slow-proofs.sh" <absolute-dir> <session>
```

This takes **1–3 hours of wall time** but is the single most valuable
input to your run. The report persists in `reports/` and is reusable
across all future strengthen runs on this session. In a $100 budget this
step costs ~$0 in Claude tokens (the wait is isabelle-side wall time);
skipping it tends to waste most of the remaining $100 on bulk patches
that regress.

Skip the scan only if: (a) the report already exists and is for the
current commit, OR (b) your target is a single specific lemma the user
named explicitly.

## Step 1.5 — Candidate generation (cheap, before every patch attempt)

For each slow proof you've decided to attack, run:

```
bash "$ISA_SCRIPTS/propose-tactic.sh" <file.thy> <line> [session]
```

Wall: ~50–500 ms (no Isabelle invocation, no session lock). Output is a
ranked list of substitution candidates with patch text already in
`check-theory.sh --patch` format. Static priors come from
`references/tactic-cost-priors.jsonl`; empirical update from this
session's `impact-*.jsonl` and `attempts-*.jsonl`.

Use the top candidate as your first patch attempt. If it regresses or
fails to verify, try the second. Three failed candidates in a row on one
proof = pivot per the commit-bias rules.

The proposer skips structural tactics (`rule` / `subst` / `induct` /
`cases`) and Eisbach methods (`wp` / `wpsimp` / `hoare_vcg_*`) — it will
print `out-of-scope: ...` and you've spent nothing. ATP reconstruction
(metis/smt) is out of scope in v1; see "Sledgehammer linearization"
section if you want to revisit that path manually.

Honor the proposer's `anti_flags`. The most important is
`proof_cost_ms < 5000` — small proofs lose from tactic reorder and the
v6 anti-pattern trap (Tcb_AC +160%, Syscall_AC +17%) is exactly this
case applied at scale. If every candidate the proposer returns has
`proof_cost_ms < 5000` flagged, target a different proof.

To bypass the proposer (A/B control arm), set
`STRENGTHEN_DISABLE_TACTIC_COST_MODEL=1` in your environment. The
proposer then prints a one-line note and exits; the rest of the skill
still works.

## Targeting hierarchy

In descending value:

1. `reports/slow-proofs-<session>.md` — proof-level sorry-cost.
   **Required** for productive patching; see Step 0.
2. `reports/session-baselines-<run_id>.md` — per-theory elapsed/cpu
   time, always emitted by `run.sh` at zero extra cost. File-level only.
3. Prior `logs/impact-*.jsonl` / `logs/attempts-*.jsonl` — what worked
   or failed in earlier runs (same session, different commit possibly).

Combine them: session-baselines tells you which **files** are hot;
slow-proofs tells you which **proofs inside hot files** are worth
attacking; impact logs tell you which **patterns** have worked before.

**Reading prior runs without being misled:**

- `impact.jsonl` records are point-in-time. `before_ms` was measured
  *before* that patch landed; if the patch is still in place, the file's
  current baseline is closer to `after_ms`. Do not chain before/after
  across runs without re-measuring.
- A prior `−1.4%` apply does not mean "this file is tapped out". It
  means one apply found one piece of low-hanging fruit; different
  patterns may still give much larger wins on the same file.
- A prior failure does **not** permanently blacklist a target. Read
  *why* it failed (search explosion? wrong tactic? broken imports?). If
  two prior runs failed with the same pattern, try a **different**
  pattern, not a retry.

## Commit bias — pivot, don't grind

Single-shot sessions reward fast compounding. Small wins land; "holding
out for a bigger win" tends to burn budget for zero. Defaults, not
rules — override with reason:

- Target individual slow **proofs** from the scan report, not whole
  files. Surgical wins (one proof's tactic changed) reliably land;
  bulk file-level `force → fastforce` sweeps regress on small/simple
  proofs that didn't need it.
- On a file with baseline ≥ 30 s, a patch that verifies at wall ≤ 95%
  of baseline is usually worth `--apply`. Don't loop 5+ variants hoping
  for a bigger number — commit and move on.
- If you've spent 3+ check-theory patch attempts on the same proof
  with no net speedup ≥ −3%, pivot to a different proof.
- Track your own `apply_success_rate`. If you've done 4+ patch/apply
  attempts and `applied_count` is still 0, stop exploring new patches
  — read your attempts log, identify the pattern that keeps failing,
  try something structurally different.
- Heavy tactic rewrites (batch `auto → fastforce`, bulk
  `force → fastforce`) can blow up search space and hang check-theory
  for 10+ minutes. If a patch runs 3× baseline without finishing, it's
  going to time out — kill it and try a surgical single-proof change.

## Budget pacing for the $100 cap

Rough allocation for a fresh session:

| Phase | Typical spend | What happens |
|---|---|---|
| Step 0 (scan if needed) | ~$0 in tokens | 1–3 h isabelle wall time, persistent output |
| Recon (read baselines + slow-proofs + priors, rank targets) | ≤ $5 | read-heavy, mostly cached |
| Targeted patches (drill 3–8 top slow proofs) | $30–60 | most of your budget |
| Reserve | $20–40 | retries, `goal-at` diagnostics, deeper patches |

Stop and reflect before continuing if:
- apply_success_rate < 30% after 5 patch attempts
- You've spent $40+ without an apply
- Every pass so far is a regression (≥ 100% of baseline)

Reading the existing attempts log and changing strategy is cheaper than
continuing to run patches that don't work.

### Diagnostic switch — when single-proof wins don't show up at session level

After every run with `applied_count > 0`, `run.sh` re-measures
`session_after_ms` and emits `reports/session-baselines-<run_id>-diff.md`.
If file-level deltas in `impact-*.jsonl` look good but
`session_delta_pct` in `metrics-*.jsonl` is ~0 or positive, an unmodified
proof has likely regressed via a shared lemma rewrite or import-chain
side-effect.

To localise it, re-run with `RUN_PROOF_TIMING_AFTER=1`:

```
RUN_PROOF_TIMING_AFTER=1 bash run.sh <target> ...
```

This re-runs `proof-timing.sh` on every touched file after the agent
exits and writes per-file `reports/proof-timing-<run_id>-<file>-after.md`.
Diff against the pre-run `reports/slow-proofs-<session>.md` to find the
proof whose `cost_ms` moved unexpectedly. Default off — it adds 1–5 min
per touched file to the wall time.

## Tools

| Tool | Purpose |
|---|---|
| `$ISA_SCRIPTS/check-theory.sh <file> <session> [--patch p] [--apply p]` | Single-file verification. The only gate. ~10–60 s depending on file + heap. |
| `$ISA_SCRIPTS/proof-timing.sh <file> <session>` | Per-proof cost via sorry-substitution on one file. |
| `$ISA_SCRIPTS/scan-slow-proofs.sh <dir> <session>` | Batch proof-timing over a directory. Run once per session; result persists in `reports/`. |
| `$ISA_SCRIPTS/goal-at.sh <file> <line> <session>` | Print the proof state at a specific line. Use when a tactic is opaque. |
| `$ISA_SCRIPTS/sledgehammer.sh <file> <line> <session>` | Run sledgehammer at the goal point — returns ATP-found `by (metis ...)` / `by (smt ...)` reconstructions. ~30–180 s. See "Sledgehammer linearization" below for when to use. |
| `$ISA_SCRIPTS/propose-tactic.sh <file> <line> [session]` | Cost-model proposer. Static priors (`references/tactic-cost-priors.jsonl`) + empirical update from impact logs. Ranks search-strength swaps, modifier-safety, depth-limit candidates with patch text. ~50–500 ms, no session lock, no Isabelle. See Step 1.5. |

**Serial only.** `check-theory.sh`, `proof-timing.sh`,
`scan-slow-proofs.sh`, and `sledgehammer.sh` all drive Isabelle on the
same session heap. Running two at once corrupts the heap and stalls both.
Each script holds a session-scoped file lock and fails fast with
**exit code 4** if another is already running on that session.
`goal-at.sh` is read-only and safe to run concurrently;
`emit-session-baselines.sh` only reads the DB and is also safe.

**Use `$ISA_SCRIPTS/<name>.sh` only — do not invent paths.** The runner
exports `ISA_SCRIPTS` to the host-side wrapper directory. Those wrappers
translate host paths into container paths and dispatch through `docker
compose exec`. There is also a sibling `scripts-container/` directory;
**do not call its scripts directly with host paths** — they expect
container-internal paths. A defensive shim will repair an accidental
call, but the canonical contract is: **always go through `$ISA_SCRIPTS/`**.

## Session mapping (auto-detected, override with intent)

- `l4v/proof/access-control/` → `Access`
- `l4v/proof/invariant-abstract/` → `AInvs`
- `l4v/proof/infoflow/` → `InfoFlow`
- `l4v/proof/refine/` → `Refine`
- `l4v/proof/crefine/` → `CRefine`
- `l4v/spec/**` → the session that imports the file (usually `ASpec` or above)
- `l4v/spec/haskell/**` → `ExecSpec` (regeneration handled by `strengthen-hooks/`)
- `seL4/src/**` → `CRefine` (regeneration handled by `strengthen-hooks/`)

If in doubt, pass your best guess — `check-theory.sh` fails fast with a
clear diagnostic.

## Patch format (what `check-theory.sh --patch` expects)

```
<start_line> <end_line>
<replacement text spanning one or more lines>
---
<start_line> <end_line>
<replacement text>
```

Line numbers refer to the **original** file. `check-theory.sh` applies
patches in reverse order internally — no manual line-offset arithmetic.

### Where to write the patch file

Use `/workspace/logs/<descriptive-name>.patch` for trial patches you pass
to `--patch`. The container has `/workspace/logs/` mounted rw on the host.
Do **not** write to `/workspace/patches/` — that path is *not* mounted and
the file will be invisible from outside the container.

When you `--apply`, `check-theory.sh` automatically copies the patch to
`/workspace/logs/patches/<run_id>/<sha>.patch` and records `patch_path`
in the auto-attempts log — no manual archiving needed.

### Wall-time ceiling (search-explosion guard)

`check-theory.sh` enforces a per-call wall ceiling via the
`CHECK_THEORY_TIMEOUT_S` env var (default **600 s**). If a patch's
verification exceeds this, the call returns verdict `timeout` (exit 124)
and stops the runaway. This catches the common failure mode where
`auto` / `fastforce` / `metis` without explicit rules opens an
unbounded search and burns 10–30 minutes per attempt.

If a normal patch takes more than ~3× the file's baseline wall, it is
*not* converging — kill and try a structurally different patch rather
than waiting it out. Override the cap explicitly with
`CHECK_THEORY_TIMEOUT_S=N bash $ISA_SCRIPTS/check-theory.sh ...` only
when you have specific reason to believe the proof is genuinely that
slow.

## Sledgehammer linearization (high-leverage when applicable)

When a slow proof's cost is dominated by undirected backtracking search
(`auto` / `fastforce` / `force` without enough hints), an ATP-found
`by (metis ...)` / `by (smt ...)` reconstruction often verifies 5–50×
faster — no search at verify time, just direct rule application against
explicitly named lemmas. The trade is more lines (each lemma named) for
much less wall, which fits the skill's "verify speed wins, line count
doesn't matter" priority.

**Workflow:**

1. From `slow-proofs-<session>.md`, pick a proof with sorry-cost ≥ 5 s
   whose tactic is `auto`, `fastforce`, or `force` (lookup the proof body
   with `goal-at.sh` to confirm).
2. Locate the proof's tactic line. Run:
   ```
   bash $ISA_SCRIPTS/sledgehammer.sh <file> <line> <session>
   ```
   Wall: 30–180 s (sledgehammer runs 4–5 ATPs in parallel; per-prover
   timeout default 60 s, override via `SLEDGEHAMMER_TIMEOUT_S`).
3. Output lists `Try this: by (...)` suggestions, sorted roughly by
   prover time. Pick the shortest plausible one (fewer named lemmas =
   smaller patch, easier to maintain).
4. Build a patch replacing the original tactic line with the suggested
   `by (...)` and verify via `check-theory.sh --patch`.
5. If wall ≤ 50 % of file baseline, `--apply`. (More aggressive
   threshold than tactic-swap because the speedup mechanism is direct,
   not heuristic — when sledgehammer wins, it usually wins big.)

**When NOT to use sledgehammer:**

- Sorry-cost < 5 s — sledgehammer's own 30–180 s wall outweighs the
  potential save.
- Proof already uses explicit `by (metis ...)` / `by (rule ...)` — it's
  already linearised; no further win.
- Proof is structural Isar (`proof - ... qed`) — sledgehammer attacks
  the goal at one point, won't help structural composition.
- Tight budget remaining (< $10) — each sledgehammer call costs
  ~30–180 s of session lock + an attempts.jsonl `kind:sledgehammer`
  entry, but no Claude tokens until you read its output.

**Caveats:**

- `metis` itself has a search component. A reconstruction with 20+
  named lemmas may still be slow. If verify fails to beat 50 % of
  baseline with one suggestion, try the next-shortest before giving up.
- `smt` reconstructions depend on the SMT solver being trusted — they
  are accepted in seL4 proofs but check more carefully on application.
- Sledgehammer auto-logs to `attempts-<rid>.jsonl` with
  `kind:"sledgehammer"`, and its full transcript persists at
  `logs/sledgehammer/<rid>/<file>_L<line>_<ts>.log` for evidence.

## Known strategies and anti-patterns (from prior runs)

**Patterns that have produced wins (high confidence):**
- Replacing `auto dest: X` with `fastforce dest: X` on a proof where
  sorry-cost ≥ 30 s. Twice in Access session yielded −23% and −35% on
  single proofs. Works because the expensive proof was doing deep `auto`
  search; `fastforce`'s early-bail structure skips the deepest branches.
- Replacing `auto elim!: <agg-elim>` with `auto elim: <agg-elim>` on
  proofs where the unsafe elim was driving exponential search.
  Validated on AInvs IpcCancel_AI (`blocked_cancel_ipc_invs`,
  `delta_sym_refs` rule): single-line change, **−24.4 % file wall**.
  Look for `elim!:` patterns whose argument is a "delta" / structural
  rule that recurses into itself.
- Sledgehammer linearisation — see dedicated section above. Most
  promising on Access (lots of unhinted `auto` / `fastforce`); not yet
  measured at scale.
- Merging consecutive `clarsimp` calls into one + adding specific
  `simp_thms` to collapse True conjuncts. Modest wins (~6%) but
  low-risk.

**Anti-patterns that keep failing:**
- Batch `force → fastforce` across a whole file without first checking
  per-proof cost. Every run that tried this got regression on at least
  one proof: Tcb_AC +160%, Syscall_AC +17%, CNode_AC +7%, InfoFlow
  Noninterference 380 s timeout. `fastforce` is not universally faster
  than `force`; on small simple proofs it reorders search and loses.
- Heavy `auto` replacements that do not name specific `dest:` / `intro:`
  rules. The open search space can hang check-theory for 10+ minutes.
- Retrying the same pattern after two failures on the same target.
  Different pattern > retry.

These are observations, not rules. New strategies are welcome; log them
in `impact.jsonl` `notes` with enough detail for future runs to learn.

> The patterns and anti-patterns above are also encoded in machine-
> readable form at `references/tactic-cost-priors.jsonl` and surfaced
> on demand by Step 1.5's `propose-tactic.sh`. When you confirm a new
> pattern via impact records, append a row to the priors file so
> future runs benefit from it without re-discovering. The proposer also
> reads each note's `delta_pct` empirically — patterns that win
> consistently in this session take over the ranking automatically.

## Optional technical references

Consult when useful:

- `references/strengthen-guide.md` — type-specific background
- `references/tactic-patterns.md` — tactic-by-goal-shape cheatsheet
- `references/sledgehammer-guide.md` — automated proof discovery
- `references/eisbach-patterns.md` — seL4 Eisbach methods (`wp`, `wpsimp`, `hoare_vcg`)
- `references/isar-patterns.md` — structured proof syntax
- `references/refinement-proofs.md` — `corres` / `ccorres` architecture
- `references/autocorres-guide.md` — C → HOL lifting
- `references/find-theorems-guide.md` — theorem search
- `references/compilation-errors.md` — common-error → fix lookup
