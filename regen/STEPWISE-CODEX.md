# Stepwise-Codex Adapter

This adapter reuses the official Stepwise `TreeSearchProver` and `IsaREPL.jar`
while replacing its fine-tuned `/generate_batch` model with the repository's
Codex relay (`gpt-5.5`). It accepts an exact lemma statement that is
already present in a theory file, steps Isabelle to that statement, and searches
for a checking sequence of proof commands.

The official checkout is kept unchanged under `tools/seL4-proof-search`.

## Boundary

Required input:

- one exact `lemma`/`theorem` statement;
- the theory file containing it;
- owner session and session root;
- one harness generation ID.

Output is `GREEN` with the Isabelle-accepted command list, or `RED` with search
logs. Every Codex prompt, relay response, parsed tactic list, run configuration,
and result is stored below `reports/regen/stepwise-codex/` by default.

The current relay API returns only the final agent answer, not the raw Codex
item stream. Consequently the adapter marks runs `sealable_generation=false`
and `model_tool_audit=unavailable-from-current-relay-api`. These runs are valid
backend-development evidence but cannot seal a production repo-level generation.
The relay agent is read-only but can still inspect repository files, so a
prompt-only ban on file access is not a sufficient leakage barrier for Refine or
CRefine. Production use must wait until the relay exposes and audits tool events
or the adapter switches to the existing no-tool-audited Codex CLI path.

## Run

Install the pinned container dependencies once with `regen/setup_stepwise_codex.sh`.

The l4v Docker container is the supported execution environment:

```bash
regen/run_stepwise_codex.sh \
  --generation-id 20260728T000000Z-example \
  --theory-file /workspace/path/Target.thy \
  --session-root /workspace/path \
  --session HOL \
  --theory-name Target \
  --lemma-name target_lemma \
  --statement 'lemma target_lemma: "..."'
```

The statement must occur exactly in the theory source. Earlier commands are
executed strictly; the adapter disables Stepwise's normal replacement of prior
proofs by `sorry`. A final whole-theory check remains mandatory before a node or
file can become a production premise.

Default Codex search budgets are deliberately smaller than the paper's
fine-tuned 128-way sampler: width 8, two states per round, eight rounds, depth
16, and eight final hammer leaves. All are CLI options.

## Verified smoke run

Generation `20260728T172744Z-stepwise-codex-smoke` received the target below.
At generation time the fixture contained `oops`, not the eventual proof, so the
model could not copy `by simp` from that file.

```isabelle
lemma stepwise_codex_smoke: "rev (rev xs) = xs"
```

The single Codex call returned `by simp` and `by (induct xs) simp_all`. Stepwise
tried the first command; IsaREPL reported proof completion. The materialized
`regen/fixtures/Stepwise_Codex_Smoke.thy` then passed a complete
`Stepwise_Codex_Smoke` session build. Prompt, raw response, parsed candidates,
Stepwise log, and result remain under the generation path.

The adapter also hardens four runtime assumptions in the public code without
editing its checkout: it loads relative tactic inventories from the Stepwise
working directory, passes Isabelle's bundled JDK explicitly, requires a valid
session `ROOT`, and waits for the Java REPL port instead of relying on a fixed
one-second sleep. Permanent relay errors use HTTP 422 so Stepwise's normal 502
retry policy does not repeat an unchanged bad request six times.

## Harness role

This is an M7 single-theorem backend. It consumes one certified theorem node and
may use only its current Isabelle proof state and legal context. It does not add
helpers, change statements, choose file order, or revise the theorem DAG. A hard
node returns RED evidence to the harness; only the Graph Revision Agent may
authorize a split or a new generated theorem node.

## Dependencies

The repository already contains the official 36 MB REPL JAR. The downloaded
runtime packages are isolated under `.stepwise-codex/container-site-packages`:
`treelib`, `python-Levenshtein`, `rustworkx`, and their small transitive
dependencies (`six`, `rapidfuzz`, and `numpy`). No vLLM, Ray, OpenAI client, or
fine-tuned checkpoint is required.
