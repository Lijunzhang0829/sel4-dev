# Persistent shared proof workspace

2026-09-24: This document describes the implemented single-agent workspace baseline.
The current `parallel-workspace` command implements persistent agents per theory, a shared native resource pool and repository integration. Each File Planner executes its tools serially; different theories may run concurrently. This page describes the older `workspace` command only; see the [current end-to-end design](../design/PARALLEL-PROOF-PIPELINE.md).

The `workspace` command keeps one Codex app-server process and thread alive while
File Planner uses harness tools. A model turn can make many proof/retrieval calls,
switch between selected theories and read partial results before their theory seals.
Only one native worker is active at a time. The existing `batch-file` command and
its acceptance rules remain supported.

## Run and identity

Create a **new proof run** after code changes; a frozen old run cannot adopt the
new implementation. Existing accepted repository plans may seed a new run. To
change contract distribution, use the new planning protocol described in
[`REPOSITORY-COLLABORATION-PLAN.md`](../REPOSITORY-COLLABORATION-PLAN.md) and publish
its checked artifacts into a new plan directory first.

```sh
python3 -m regen.execution.proof_session_runner init \
  --run-dir <new-run> --run-id <new-id> \
  --dataset-root <prepared-dataset> --schedule <plan>/file-generation-list.json
python3 -m regen.execution.proof_session_runner workspace \
  --run-dir <new-run> --theory <selected-theory> --allow-external-call
```

Repeat `--theory` to allow switching across a selected group, or omit it for all
theories. Reference reads can see any theory in this run; mutations remain within
the selection. This is one File Planner with serial actions, not concurrent File
Planners. The frozen File Planner model and reasoning settings are used. Only
`auth.json` is copied into an ephemeral Codex home; user configuration and
workspace instructions are not loaded from that home.

The available tools are `list_workspace`, `read_theory`, `list_generated`,
`read_generated`, `read_attempt`, `prepare_reuse`, and `proof_action`.
`proof_action(theory, proposal)` accepts the existing File Planner actions,
including grouped observations, premise queries, helper graph patches, atomic
proof batches and `finish`. No iteration or helper-creation quota is added.
Read-only exploration of a theory is allowed before its hard providers seal.
Proof execution still requires its hard imports; advisory groups/reuse suggestions
never become available premises or blocking imports.

The provider timeout measures **inactivity waiting for protocol events**, excluding
the native-check callback. Native candidate/full-check timeouts stay separate.
Received actions are never automatically redispatched. If the model ends the turn
with work unfinished, the run reports `partial`; final prose is not a seal. A new
invocation can inspect and continue accepted work in the same new run, using a new
provider session with explicit context reads.

## Acceptance, audit and logs

Dynamic actions use the existing batch graph validation, declaration checks,
atomic native acceptance journal, semantic audit and final build. Fixed contracts
and accepted proofs remain immutable. A workspace focus may refer to a helper
created in the same batch; a failed batch retains its draft for inspection, without
accepting its helper. These changes do not weaken seal requirements.

Each session keeps one `workspace-sessions/<id>/session-transcript.jsonl`, provider
stderr, frozen policy and result/failure. Per-theory batch directories keep input
snapshots, expanded proposals, checks, feedback and timing as before. Per-action
call artifacts bind an actual dynamic tool request to a hashed byte prefix of the
session transcript instead of copying the whole transcript or claiming a new model
request. Timing distinguishes `dynamic_tool_call` (zero provider dispatches) from
legacy `model_request`; local action-source verification has its own
`tool_source_audit` phase. Provider usage is recorded at session level.

Stage changes and tool starts/finishes update `runner-events.ndjson` and
`runner-status.json`. There is no periodic heartbeat. Accepted state comes from
validated commits, not the progress log.

The provider assurance is **audited-tool-allowlist**, not OS-level exclusive file
isolation. Built-in tools, unknown dynamic tools, approval requests, duplicate call
IDs and malformed schemas terminate the session. Synthetic transports cannot
produce sealable evidence. Each accepted action binds the verified transcript
prefix; `finish` additionally audits the session so far, and terminal completion
is audited at exit. A subsequent unrelated session failure does not rewrite a
previously accepted proof.

## Rollout and evaluation

The first reuse path copies a helper closure and proves it in the consumer.
Direct imports of partially proved theories and immutable partial-theory snapshots
are not enabled. Thus a source may stay unsealed for reading/copying, but changing
the consumer's import set still requires a separately planned, checked new run.

First compare continuous versus legacy control on the same six-contract plan,
model and semantic parent with zero inherited proofs. Test regrouping separately:
move the five simple combinators to a combinator theory; explore
`setObject_other_corres`, endpoint/notification get and set contracts together in
a five-contract object-access group, separate from the remaining IPC protocol
contracts. These are experiment choices, not hard-coded planner rules. Preserve
all fixed statements and locale contexts and check the replacement import DAG.
Measure wall time, provider turns/tool actions, input/output tokens, native checks,
copied helpers actually used, accepted contracts and final seals separately.

Validation on 2026-09-23: `python3 -m unittest discover -s regen/tests/current -q`
passed **674 tests**; `git diff --check` passed. No provider request was made.

Implemented tests cover protocol/legacy compatibility, tool provenance, lifecycle,
reference integrity, red/green acceptance boundaries, workspace switching and
transitive sealed-premise visibility. Historical steps 34/46/59/71/92 now pass the
formerly failing new-helper focus validation in offline replay. This establishes
removal of a harness rejection, **not** proof success or a measured generation
speed improvement. No live provider or new Isabelle generation was run for this
implementation change; an end-to-end proof experiment remains necessary.

## Shared source API


This is a read-only source view of the current run, not an import or GREEN-transfer mechanism. Source theories need not be sealed. The existing target batch checker remains the only acceptance authority.

```python
workspace = SharedProofWorkspace(run_dir)
workspace.index(theory=None, query='', offset=0, limit=20)
workspace.read([source_ref, ...])
workspace.read_attempt(theory, request_number)
workspace.prepare_reuse(source_ref, target_state=state,
    target_node_id='target contract or helper id', mapping=None)
expand_reuse_helpers(proposal, state, workspace)
```

An index entry contains a `source_ref` with `run_id`, `theory`, `node_id`, `commit_id`, `node_sha256`, and `proof_sha256`. Use the exact reference returned. `read` returns complete source declaration, context and body, explicitly as reference-only. Query is literal case-insensitive text matching over generated names/statements, not an automatic ranker. Failure summaries include a total count and only the latest eight request IDs; full failed candidates are read explicitly by request number. Reads do not run Isabelle or models, repair HEAD pointers, or write source artifacts. Only frozen packages and verified acceptance commits under the given run are read.

`prepare_reuse` returns a bundle with a compact `reuse_request` receipt, `bundle_sha256`, `source_ref`, `target_theory`, `target_node_id`, `mapping` and `helpers`. The optional mapping maps **every source helper ID in the returned closure** to `{id: target_id, name: target_name}`; when absent, fresh deterministic IDs/names are generated. Source contract dependencies cannot be copied; inspect them as reference and adapt the candidate instead. The source context must equal the chosen target node context. No source fact becomes a trusted target premise before native checking.

Prefer putting the returned `reuse_request` directly in `reuse_helpers`; it carries source reference, target, mapping and bundle hash without repeating proof bodies. The full-bundle form below is also supported. Both forms are rebuilt from the source journal and compared before expansion.

Model action:

```json
{"action":"submit_batch", "reason":"Reuse checked helper source in this context",
 "reuse_helpers":[{"bundle": {"...":"entire prepare_reuse result bundle"}}],
 "proofs":{"target contract id":"candidate proof using copied helpers"}}
```

`expand_reuse_helpers` rebuilds each bundle from its immutable source commit, checks hashes and closure, and adds ordinary generated nodes/proof bodies to the same `submit_batch`. It returns `workspace_reuse_origin` for provenance. The controller should exclude that metadata when calling `prepare_batch`, retain it in the saved proposal/check/commit evidence, and call the usual `prepare_batch`/`check_batch`/journal acceptance. It must never append source proofs directly to accepted target state. Target consumers' graph dependencies are updated to include the copied root helper; already GREEN target consumers cannot be revised.

A failed attempt is available only when its proposal and feedback hashes are bound by a validated source commit. It is returned as `candidate_reference`, never as GREEN and never as a reuse bundle.
