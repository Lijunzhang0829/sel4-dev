# Official Codex experiment launcher

Use `official_codex_deploy.py` for new shuffled-binomial runs. Historical
experiment directories are frozen evidence, not launcher templates to edit.
This harness invokes the official CLI once; it does not change agent search,
transport, model reasoning, or retry strategy, and does not restart it.

From the repository root, choose a fresh directory and run these stages:

```sh
python3 c-algorithms/binomial/tools/official_codex_deploy.py prepare \
  --output c-algorithms/binomial/experiments/NEW-RUN-ID --budget-seconds 28800
python3 c-algorithms/binomial/tools/official_codex_deploy.py preflight \
  --output c-algorithms/binomial/experiments/NEW-RUN-ID
python3 c-algorithms/binomial/tools/official_codex_deploy.py start \
  --output c-algorithms/binomial/experiments/NEW-RUN-ID
```

Preparation and preflight do not call a model. No duration default exists.
The profile is gpt-6-astra/low, 4 CPU / 16 GiB without extra swap,
worker/native_slots/resident_limit=1. Input/audit separation and the common
seven-service proof harness remain as before.

The mandatory preflight uses the actual evaluator, actual checker account,
and actual Isabelle backend with umask 027. A synthetic True proof and a synthetic False contract under an owned context must both pass their positive/negative native checks and integration. New benchmarks default to owned-context-outcomes-v1; scope is not a launch option. It also replays a synthetic response twice:
one response and 12 tokens must be attributed exactly once, and an interrupted
tail must remain unknown. It tests the real independent-checker creation path,
not just the agent's online check command. Starting without a passing receipt
or with changed frozen runtime/configuration files is rejected.

The supervisor records invocation_started before launching Codex and records
invocation_finished on success/interruption. Accounting errors no longer
silently accumulate in a hidden warning list; they block the run explicitly.
Live health includes observed usage. The independent evaluator changes UID
before creating/rendering its source, uses a checker-owned private directory,
and receives the candidate through an explicit root:checker 0640 handoff.
The final health record is terminal; controller exit status propagates agent
or evaluator process failure. Independent checker failure never certifies a
proof. An incomplete repository remains incomplete even when the evaluator
process exits successfully.

The historical 20261107-001 authentication refresh failure is a separate
unresolved issue. These fixes concern final-evaluation permissions and token
attribution; they do not establish that a copied login will remain valid for
an eight-hour experiment. Original run code, costs and results are unchanged.

Validation on 2026-11-08: six regression tests plus 24 existing usage/metrics
tests passed. A real Docker/Isabelle2026 diagnostic under umask 027 passed
checker-owned rendering, native proof check, integration and accounting probe.
It used only temporary synthetic files; model calls were zero. No fresh
experiment was started.


## VERO-style baseline

The same deployer/runtime now accepts `prepare --method vero` with an explicit duration. Use `--method vero` on preflight/start as well to check the frozen method identity. This selects the existing disclosed `intervaltree/baselines/vero/PROMPT.txt` and records method=vero in configuration/telemetry. `official-codex` remains the default for prior callers. Both methods share task rules, checker identities, native adapter, resource enforcement, clock, final evaluator and token gates; only the disclosed task prompt and method metadata differ. This is the agreed seven-service VERO-style instruction baseline, not the historical custom VERO retry/planner runner. No historical deployment is rewritten.

Both profiles currently allow generation to consume the full cumulative allowance; final evaluation runs only if time remains. A successful preflight is not a completed final evaluation of an experiment. Reserving evaluation time must be a declared common configuration change for both methods.

## Operation-level timing

New preparations freeze `observation_profile=official-otel-metadata-v1` and use
preflight schema `official-codex-owned-context-v3`. The same observer applies
to both baseline methods. `official_codex_observer.py` receives the official
CLI's OTLP/HTTP JSON logs, spans and metrics on loopback; it is not a provider
proxy. Prompts/answers/guardian text are disabled, tool-result content is capped
at zero, and the receiver persists only allowed timing/status/identity metadata.

Records are in `/results/operation-observation/events.jsonl`; live/final health
reports observation coverage. Separate client request operations, stream
events, tool execution, context compaction, native attempts and server-reported
inference/overhead aggregates. A completed reasoning item is not a first-token
timestamp. Aggregated metrics must not be attributed to individual requests
without a matching identifier. Histogram aggregation temporality and window timestamps are retained; repeated cumulative exports must not be summed as separate requests. Missing fields remain unknown. A budget kill
can prevent the final telemetry batch from flushing.

`agent_operation_timeline.py` exports retrospective rollout/attempt boundaries
without proof or reasoning text. It retains overlapping operations: a model
response may be pending while a background native check runs. Historical runs
without request/stream export cannot be retroactively assigned exact inference
or network times. Do not refund a silent interval without evidence.

No new model experiment was used to validate this addition. Unit/HTTP/privacy
fixtures, actual pinned CLI config parsing and the combined real native
preflight passed; request/stream event coverage still needs confirmation during
a future authorized model run.

Source dependency: the VERO checkout must include the default owned-context checker and cumulative budget clock commits. Both methods use that same backend; older frozen experiment copies are unchanged.
