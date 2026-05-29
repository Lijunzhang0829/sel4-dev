---
name: isabelle-prover
description: "Strengthen seL4 formal verification. Dispatcher — routes to type-specific sub-skills for proof / spec / haskell / c."
---

# Isabelle/HOL Strengthen — dispatcher

Top-level skill for the seL4 verification chain. Pick the sub-skill that
matches your target type:

| Target | Sub-skill | Domain |
|---|---|---|
| `.thy` under `proof/` (build wall optimization) | **`isabelle_prover_proof`** | Lemma tactic optimization driven by CSTR-2 DAG |
| `.thy` under `spec/abstract/`, `proof/invariant-abstract/` | **`isabelle_prover_spec`** | Tighter postconditions / invariants |
| `.hs` / `.lhs` under `spec/haskell/` | **`isabelle_prover_haskell`** | Haskell Design Spec + downstream re-verification |
| C source in seL4 kernel | **`isabelle_prover_c`** | C implementation + CRefine maintenance |

The rest of this file describes the **common contract** all four sub-skills
share. Sub-skills only specify their workflow / strategies / type-specific
tools — they inherit everything below.

## Three hard rules (all sub-skills)

### 1. No direct edits
Every change to `.thy` / `.hs` / `.c` / `.h` goes through a patch file
verified by `check-theory.sh --patch` and applied by `--apply`. Never write
to source files directly.

### 2. No bypassing the prover
No new `sorry` / `oops` / `axiomatization`. Never call `isabelle build`
directly — `check-theory.sh` is the only verification gate.

### 3. Per-file wall is the truth
A patch is worth applying only when:
- `check-theory.sh --patch` returns `OK`, AND
- File wall drops by the threshold set by the sub-skill (see sub-skill —
  proof type uses a tiered 5%/20% threshold depending on patch scope).

No JSONL accounting / cost-model scoring required. Sub-skills may keep
type-specific run logs in `reports/<layer-or-type>/`.

### 4. Heap volatility — never pkill an active Isabelle build

`isabelle build` writes session heaps incrementally. Killing the process
(SIGKILL / SIGTERM) leaves the target heap in an inconsistent state —
subsequent invocations report `Unfinished session(s): X` even though
the heap file may exist. Recovery requires a **full session rebuild**
(Refine: ~1h 17min wall, ~2h 22min CPU; CRefine: longer).

Rules of engagement:
- **Don't `pkill` java / isabelle processes** to clear a stuck tool. Wait
  for the session lock to release, or use `flock -w <timeout>` to time-bound
  the wait.
- **Orphan check-theory processes**: if a previous run was killed, the
  `/tmp/isabelle-session-<NAME>.lock` file may persist. Check with `ls
  /tmp/isabelle-session-*.lock` inside the container before assuming the
  session is free.
- **If a heap is genuinely corrupted**, rebuild explicitly:
  ```
  docker compose exec -T l4v bash -c \
    "L4V_ARCH=ARM /workspace/verification/isabelle/bin/isabelle build -v -b \
     -d /sel4-project/verification/l4v <SESSION>"
  ```
  Budget hours, not minutes.

## Common tools

| Tool | Purpose |
|---|---|
| `$ISA_SCRIPTS/check-theory.sh <file> <session> [--patch p] [--apply p]` | Single-file verification + apply. The only verification gate. |
| `$ISA_SCRIPTS/goal-at.sh <file> <line> <session>` | Inspect proof state at a line. |
| `$ISA_SCRIPTS/sledgehammer.sh <file> <line> <session>` | Find `by (metis …)` reconstruction. |

**Serial only**: these share the session heap lock. Concurrent runs fail
with exit 4.

## Patch format

```
<start_line> <end_line>
<replacement text spanning one or more lines>
---
<start_line> <end_line>
<replacement text>
```

Multiple hunks separated by `---`. Line numbers refer to the **original**
file. Patches go in `/workspace/logs/<descriptive-name>.patch` (rw-mounted
on host); never `/workspace/patches/` (not mounted).

## Session mapping (path-prefix rule)

| Path | Session |
|---|---|
| `proof/refine/` | `Refine` |
| `proof/crefine/` | `CRefine` |
| `proof/invariant-abstract/` | `AInvs` |
| `proof/access-control/` | `Access` |
| `proof/infoflow/` | `InfoFlow` |
| `proof/drefine/` | `DRefine` |
| `proof/bisim/` | `Bisim` |
| `spec/abstract/` | `ASpec` |
| `spec/cspec/` | `CSpec` |

## References (read on demand)

| When | Read |
|---|---|
| Tactic-by-goal-shape cheatsheet | `references/tactic-patterns.md` |
| Sledgehammer details | `references/sledgehammer-guide.md` |
| Eisbach (`wp`, `wpsimp`, `hoare_vcg`) | `references/eisbach-patterns.md` |
| Structured Isar proofs | `references/isar-patterns.md` |
| Refinement (`corres`/`ccorres`) | `references/refinement-proofs.md` |
| C lifting via AutoCorres | `references/autocorres-guide.md` |
| Common error → fix | `references/compilation-errors.md` |
| Type-by-type strengthen guide | `references/strengthen-guide.md` |

References, scripts, and shared assets live under this directory
(`isabelle_prover/`). Sub-skills reference them by relative path.
