# Infra root-cure — l4v dual-mount path-fingerprint churn

Branch: `bridge-consumer-trace`. Date: 2026-06-16.

## Root cause
Same l4v mounted at TWO container paths (`/workspace/verification/l4v` and
`/sel4-project/verification/l4v`). Isabelle/scala-isabelle fingerprints sessions by
source-path string; the 29 prebuilt heaps were built for `/sel4-project`. Any tool
calling `_initializeRepl`/`isabelle build -d` with `/workspace` makes scala-isabelle
think sources changed → rebuilds the whole ASpec→…→target chain to a `/workspace`
fingerprint; the next `/sel4-project` tool flips it back → back-and-forth **churn** →
heaps "vanish", init hangs, socket/connection-refused. This was the root of the
multi-day instability and the `FAILED (8s)` check-theory crashes when an IsaREPL run
was concurrent.

## Cure — canonical path = `/sel4-project` (heaps already there, zero rebuild)
- **(a) persistence**: already in `docker-compose.yml` (`environment: L4V_DIR=
  /sel4-project/verification/l4v`); confirmed live in the running container; exec'd
  processes inherit it. The agents (`ab_agent`/`react_agent`/`poc_repl_server`)
  already default to `/sel4-project` via `L4V_DIR`.
- **(b) sweep**: rewrote **33 scripts** under `lemma-staticize/isa-repl/**`
  (+ `profiler/**`) that hardcoded `/workspace/verification/l4v` → now read
  `os.environ.get("L4V_DIR", "/sel4-project/verification/l4v")` (literal swap for the
  few non-os files / inline full paths). `isarepl_client.py` default also moved to
  `/sel4-project`. All 42 parse-clean; 0 remaining `/workspace` l4v code refs.

## Result
agents · swept scripts · container `L4V_DIR` · check-theory(env) all converge on
`/sel4-project`. No path-triggered rebuild. (Concurrency on one session still hits the
heap lock — keep runs serial.) `check-theory.sh` honors `L4V_DIR` (defaults to
`/workspace`), so host-side calls must ensure `L4V_DIR=/sel4-project/verification/l4v`
is set/inherited. Skill files untouched. See memory `l4v-dual-mount-heap-churn`.

## Hardening (round 2) — close the recurrence holes

The dual-mount itself stays (removing it needs a single-path remount + multi-hour
heap rebuild). Churn recurrence was still possible via two holes; both now closed:

**(c) check-theory default + a coupled logs bug.**
- `scripts-container/check-theory.sh`: `L4V_DIR` default changed `${REPO_ROOT}` (=
  /workspace) → `/sel4-project/verification/l4v`, so the gate never silently falls
  back to /workspace when the env isn't inherited.
- Coupled fix: `WORKSPACE_ROOT="${L4V_DIR%/verification/l4v}"` derived the logs root
  from L4V_DIR. Once L4V_DIR=/sel4-project (which only mounts verification/l4v),
  this pointed at a nonexistent `/sel4-project/logs` → **attempt-logging /
  candidate-ledger auto-append was silently disabled**. Decoupled: `WORKSPACE_ROOT=
  "$REPO_ROOT"` (logs live at /workspace, independent of the build path).

**(d) regression guard.** `tools/guard-l4v-path.sh` greps .py/.sh under tools/,
lemma-staticize/, spec-strengthen/ for the hardcoded /workspace l4v path and exits
1 with offenders. `--install-hook` wires it as a git pre-commit hook (installed).
The needle is split-quoted so the guard is immune to its own sweep.

**The guard immediately paid off**: it caught **11 `.sh` launchers** that the
.py-only sweep missed (bench*.sh, run_loop.sh, ablation*.sh, run_cand30.sh, and
`run_one_candidate.sh` — which passed a /workspace THY straight into ab_agent.py).
Swept: simple `THY=` lines → `${L4V_DIR:-/sel4-project/verification/l4v}`;
run_one_candidate.sh → literal /sel4-project. Final state: 0 hardcoded /workspace
l4v paths in any .py/.sh; guard green.
