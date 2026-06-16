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
- **(b) sweep**: rewrote **33 scripts** under `tools/seL4-proof-search/Isa-Repl/**`
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
