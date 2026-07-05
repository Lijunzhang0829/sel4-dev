# 0028-set-mrs-machine-state-frame-lemma

| Field | Value |
|---|---|
| Pattern | G |
| Key | `G:Ipc_AI:set_mrs:machine_state` |
| Variant | seL4-source PR (rule 5 full record) |
| Branch | spec-strengthen |
| Date | 2026-06-08 |
| File | `verification/l4v/proof/invariant-abstract/Ipc_AI.thy` |
| Verdict | **trial_failed** (semantic FP — no apply) |
| Walls | baseline=112260 ms · trial=FAILED · apply=N/A |

## Attempted patch

See `range-patch.patch.txt`. Insertion was after `set_mrs_vms[wp]`
(L1881):

```isabelle
lemma set_mrs_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_mrs t b m \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wp set_mrs_thread_set_dmo)
```

## What went wrong

`check-theory.sh --patch` reported:

```
*** Failed to finish proof:
*** goal (2 subgoals):
***  1. \<And>c. \<lbrace>\<lambda>s. P (machine_state s)\<rbrace> thread_set ...
```

Two subgoals open after `wp set_mrs_thread_set_dmo`:

1. `thread_set` subgoal — would need a `thread_set_machine_state[wp]`
   lemma (does not exist in the proof tree).
2. (implicit) `do_machine_op storeWord` subgoal — would NOT be
   discharge-able because `storeWord` mutates `machine_state.memory`.

## Root cause — semantic FP

`set_mrs` is the IPC message-register writer. Its definition
(`set_mrs_def2`) decomposes into:
- per-register TCB updates via `thread_set` (machine_state-preserving)
- per-overflow-register memory writes via `do_machine_op (storeWord …)`
  (mutates `machine_state.memory` — therefore **not**
  machine_state-preserving)

The strengthening claim `set_mrs preserves any P (machine_state s)`
is **semantically false at the abstract spec level** — for predicates
P that read `machine_state.memory`, the equation `P (machine_state
s) = P (machine_state s')` fails after a `storeWord` write.

This is unrelated to proof-tactic engineering; no tactic could
prove a false claim.

## Detector / preflight gap exposed

`spec_frame_gap.py`'s mechanical preflight only catches:
- existing identical lemma (`preflight_failed:direct`)
- crunch-derived companion that already proves it
  (`preflight_failed:crunch`)

It does NOT inspect the op's definition body for **calls into
`do_machine_op`** with non-pure machine actions. That third
gate would have caught this candidate before patch generation.

**Suggested follow-up** (not implemented in this experiment):

Add to `spec_frame_gap.py`:

```python
# Pre-gate: refuse machine_state framing on ops that invoke
# do_machine_op with a write-class machine action.
if field == "machine_state":
    op_def = locate_definition(op)  # parse set_mrs_def / similar
    if "do_machine_op" in op_def and \
       contains_machine_write(op_def):  # storeWord, storeWord_64, ...
        return ("preflight_failed:do_machine_op",
                f"{op} writes machine_state via do_machine_op")
```

## Acceptance gate trace

| Gate | Result |
|---|---|
| 1. check-theory.sh --patch | ✗ trial failed (proof body did not close) |
| 2. spec_impact verdict | N/A (gate 1 blocked) |
| 3. trial wall | N/A |
| 4. parent SKILL hard rules | N/A |

## Notes / follow-ups

- Ledger event: `trial_failed` recorded automatically by the
  framework; no `applied` event.
- Adjacent candidates on the same op (still in the Tier 1 list
  for Ipc_AI) — `set_mrs:domain_index`, `set_mrs:domain_time`,
  `set_mrs:arch_state` — are NOT affected by this storeWord
  issue (those fields are independent of `machine_state.memory`).
  They remain candidates if tackled separately.
- This is the **first semantic-FP** observed in the G family
  workflow. Worth a playbook entry: "if op's def contains
  `do_machine_op <write_action>` and the field is
  `machine_state`, the claim is false even if mechanical
  preflight passes."
