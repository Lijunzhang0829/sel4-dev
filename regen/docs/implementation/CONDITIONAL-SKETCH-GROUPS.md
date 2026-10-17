# Conditional helper groups and incremental document checks

The opt-in `sketch-guided-v1` adds native structural screening, candidate snapshot
ranking and selective probes. Its complete design, limits and small native test
are described in [Guided sketch decomposition](GUIDED-SKETCH-DECOMPOSITION.md).
The default and historical `sketch-reviewed-v1` behavior below remains unchanged.

New parallel document launches default to `sketch-reviewed-v1`. The generic
repository agent uses the same strategy unless its frozen configuration specifies
another strategy. `baseline` and the older strategies remain explicit comparison
options; frozen application bundles and historical journals keep their identities.

The new policy is enforced by the interface. It does not perform AND/OR route
optimization or rely on `helper_purposes` explanations.

## Review and execution gates

`sketch-reviewed-v1` adds three runtime gates to local-demand observations:

1. Close the executable parent sketch with `check_sketch`.
2. Call `probe_helpers` for the pending leaves. Each uses its own unchanged
   committed prefix, no sibling assumptions, and a ten-second candidate budget.
   A saved proof is tried; an `oops` leaf gets a basic `by simp` probe. Native
   prefix preparation has its separate existing limit. Probes use discarded
   transactions, create no accepted facts/checkpoints, and grant no budget.
3. Call `review_sketch` bottom-up, then prove selected helpers normally.

`probe_helpers` takes the current `source_sha256` and optional `entry_ids`.
Omitting the selection probes all pending leaves. A failure or timeout is
diagnostic evidence, not a proof that the leaf is false or difficult. A missing
legal dependency must be identified from the result, not temporarily admitted.

Each `review_sketch` takes `source_sha256`, `target_entry_id`, the exact current
native `receipt_id`, and one review per helper. For example:

```json
{
  "helper_id": "h1",
  "use_indices": [0],
  "domain_scope": "local",
  "premises": "local",
  "conclusion": "local",
  "generalization": "none",
  "burden": "isolated",
  "reduction": "Separate the range arithmetic from the state update.",
  "probe_id": "native probe receipt digest"
}
```

`use_indices` must cover every native observation of that helper in the parent
receipt. The three demand comparisons are `local` or `generalized`. Any
generalized comparison requires `generalization=induction` or `reuse` and an
explanation in `reduction`. A leaf requires `burden=isolated`, a nonempty
explanation of the reduced reasoning burden, and a matching native probe.
`burden=unchanged` is rejected. A nonleaf requires `burden=delegated` and a
current `child_review_id`; an accepted helper uses `burden=proved`. Ordinary
helper checks reject missing or stale reviews, including after interface or
child-decomposition changes. Prefix extension may reuse earlier evidence;
changes to its original legal prefix invalidate it.

The generating agent supplies these mathematical judgments. The interface
enforces coverage, evidence binding and the workflow; it does not certify their
truth or prove that a helper is easier. Operation count/statement length is not
a sound difficulty measure. No review produces GREEN or makes an unproved
helper available as a premise. Both the controller and ordinary `check_theory`
path use the same strategy flags, including the earlier local-demands policy.

Native consumer/probe/formal failures appear in `read_theory.workflow.pending_failures`.
Before modifying a related decomposition, call `diagnose_failure` with its
`failure_id`, a classification (`script`, `known_fact`, `mathematical`, or
`candidate_timeout`) and a concrete `plan`. For script/known-fact diagnoses,
proof-only edits and use-selector corrections remain allowed, but interface or
helper-group changes require a changed-proof native retry. Renaming/removing a
helper or reclassifying the same failure does not bypass this requirement.
If the retry leaves a different mathematical obstacle, diagnose its new failure
before revising the decomposition. Formal GREEN clears the pending failure;
partial/uncommitted successes and `not_reached` do not. Unrelated parent goals
may continue. Infrastructure/preparation failures are not proof diagnoses.
A repaired conditional consumer clears only its earlier conditional failure;
it cannot clear a failed real proof or establish parent GREEN.

Receipts live under `document/workflow`; they preserve probes, reviews,
diagnoses and costs. Historical strategies and frozen applications remain
available without silently adopting these gates.

## Observe local demands before generalizing

`sketch-local-demands-v1` retains the conditional group gate and requires
`use_sites` covering every helper in each pending group. `sketch-groups-v1`
remains available for historical comparison; its old receipts keep their exact
rendering and identity, and are explicitly reported as unobserved. Existing
frozen experiments do not silently switch strategy.

For a consumer proof such as:

```isabelle
proof -
  have z: "(n::nat) + 0 = n"
    using zero[where n=n] by assumption
  have i: "(m::nat) = m"
    using identity[where x=m] by assumption
  show ?thesis using z i by blast
qed
```

attach this group to `edit_theory`:

```json
{
  "target_entry_id": "parent-1",
  "helper_ids": ["identity", "zero"],
  "use_sites": [
    {"helper_id": "zero", "consumer_line": 3, "fact": "zero[where n=n]"},
    {"helper_id": "identity", "consumer_line": 5, "fact": "identity[where x=m]"}
  ]
}
```

`consumer_line` counts the exact parent proof body, starting at one; it is not
a theory-file line. Select a `using`, `from`, `with`, `apply` or `by` command
containing the exact reference. Split compound commands when the relevant
variables are introduced inside a method: an observation before that command
cannot inspect a context created later inside automation. Multiple sites can
select the same helper. Selectors must be updated after consumer edits.

The trusted conditional renderer inserts read-only native `ML_val` diagnostics
at those sites. Isabelle parses and evaluates each fact expression, including
`where`/`OF` attributes, then records the resulting propositions, pending goals,
local assumptions and chained facts. The saved draft and ordinary final proof
contain no instrumentation. A group with missing observations is rejected even
if a closure marker exists. Selectors and native observations are bound into
the conditional receipt; changing helper interfaces or the consumer invalidates
it as before. Full details are in `read_sketch`'s `helper_uses`; routine replies
report observation coverage. Temporary facts and diagnostics are discarded.

These records support comparison between the proposed helper and its local
demand. They do not automatically establish actual theorem dependency,
minimality, reachability or reduced difficulty. An implicit instantiation made
later by resolution/automation is not reconstructed: remaining schematic term
and type variables are reported explicitly. Use explicit fact instantiations
when the local instance matters.

The default workflow prefers the needed instance and conclusion under available
local premises. If a general helper stalls, specialize its objects/conclusion
or restore useful premises, revise all affected consumers and recheck the
sketches. A restored premise must be discharged in the consumer; assuming it in
the helper declaration does not establish it there. Generalization remains
allowed for induction or demonstrated reuse. Brief leaf probes and this
specialization policy remain model judgments, not a certified difficulty
ordering or an automatic mathematical rewrite. The reviewed strategy requires
their evidence and assessments through the execution gates described above.

## Propose and check a group

Use the ordinary `edit_theory` action to add helper declarations and the complete
consumer proof in the same `.thy` draft. Attach groups outside the source markers:
The minimal groups below describe the legacy interface; the default reviewed
strategy additionally requires use selectors, probes and reviews as above.

```json
{
  "action": "edit_theory",
  "base_sha256": "hash returned by read_theory",
  "source": "complete marked theory source",
  "sketches": [
    {"target_entry_id": "parent-1", "helper_ids": ["h1", "h2"]},
    {"target_entry_id": "parent-2", "helper_ids": ["h3", "h4", "h5"]}
  ]
}
```

Each group contains one or more helpers; there is no single-helper requirement
or fixed group-size quota. Each parent must have an executable proof body.
Helpers must appear before their parent; `helper_ids` follows declaration order.
Every unaccepted auxiliary, including
entries with role `theorem`, must belong to a group reachable from a fixed
contract. A difficult helper can become the parent of a nested group. Source
order excludes circular parent/helper relations. Accepted declarations remain
fixed; accepted proof bodies can be rechecked after edits.

Saving a draft checks these structural requirements. It does not certify the
mathematical decomposition. Invoke:

```json
{
  "action": "check_sketch",
  "source_sha256": "current saved hash",
  "target_entry_ids": ["parent-1", "parent-2"]
}
```

Omitting `target_entry_ids` checks all pending registered groups. Current valid
receipts are reused. An invalid or unknown selection fails explicitly.

The trusted checker renders the unchanged legal GREEN prefix available before
this consumer, the group's exact helper
declarations and the parent's exact consumer proof into a disposable native
transaction. Only the selected unproved helpers receive harness-generated
`sorry` bodies. The model cannot supply `sorry` in a saved draft or ordinary
proof. Native declaration processing preserves quantification, `fixes`,
`assumes`, `shows`, polymorphism, attributes and owned contexts without textual
proposition extraction. Already GREEN group members use their actual facts.
Other unproved declarations are never premises in this transaction.

`conditional_closed` means that the parent's script closed under those temporary
admissions. It does not establish the truth or budgeted provability of a helper.
The result has `publishable=false` and `new_green=[]`. Replies contain compact
diagnostics and receipt IDs. Read exact native source, logs and bindings with
`{"action":"read_sketch","receipt_id":"returned receipt ID","view":"full"}`;
they are not broadcast with every group check. The native transaction
does not change the worker's `base` or `pending` state. Its temporary facts are
not published, indexed, imported or accepted into the proof journal.

Receipts are saved under `document/sketch-checks`; content-bound plan snapshots
and current certificates are stored alongside them. A certificate binds exact
helper statements, parent statement/context, consumer script, imports and the
original GREEN prefix. Filling a helper's proof does not change that interface.
Changing a statement or consumer expires the certificate. A changed original
prefix also expires it. Extending an unchanged prefix permits reuse of the
original conditional evidence; the final parent still receives ordinary native
checking in its actual environment.

## Prove selected updates

With either sketch strategy, ordinary checking rejects unaccepted helper proofs unless
their current group has a valid conditional receipt. This is a runtime gate,
independent of whether the model follows its instructions.

```json
{
  "action": "check_theory",
  "source_sha256": "current saved hash",
  "entry_ids": ["h1", "h2", "h3"],
  "reason": "prove the leaves of the checked groups"
}
```

The checker projects selected proof bodies plus **every accepted proof**, in
source order. Unselected failed or sketch-only proof bodies remain in the full
draft but are rendered as `oops` in this check projection. The projection is
saved with its native evidence; it never overwrites `draft.thy`.
New unselected auxiliary declarations stay draft-only, and changes to unselected
open declarations are deferred. Their type errors cannot block an independent
selected group. Fixed contracts and existing committed declarations are retained.

After the helpers are GREEN, select their parents, including several parents in
one action. Their actual consumer proofs must pass without temporary admissions.
Native proof audits and complete-theory seal/build checks keep their existing
requirements. Neither registering nor conditionally closing a group grants
proof status or replenishes the generation budget.

## Incremental scope

Document checking reuses the longest identical prefix of accepted proof entries.
Identity includes entry ID/name/role, statement, context and proof body. It checks
the suffix beginning at the first change, including affected later accepted
entries. It does not skip later proofs on the basis of guessed dependencies.

The worker records functional context checkpoints at complete entry boundaries,
and exposes them only after the containing native batch has passed its audit.
An exact prefix can be restored in the same worker. Conditional trials create
no audited checkpoints. Checkpoint restore never itself commits a proof.
The native audit receipt binds the restored prefix and executed suffix to the
ordinary journal acceptance evidence.

If the worker was evicted or has no matching checkpoint, it reconstructs the
legal prefix once. Imported environments still require their normal preparation.
Declaration elaboration may cover open declarations as well; the incremental
guarantee concerns proof execution, not eliminating every statement preflight.
Historical full-check evidence remains replayable through its original policy.

## Validation

`regen.tests.current.test_sketch_check` covers orphan/unchecked groups, multiple
helpers, nested groups, stale statements/consumers, prefix changes, unchanged
proof filling, selected multi-entry checks and the controller acceptance gate.
Existing document, worker, task and registry regressions cover compatibility.
`regen.tests.current.test_sketch_workflow` covers evidence-bound review gates,
unchanged burden, generalized demands, stale interfaces/receipts, recursive
delegation, failure diagnosis, changed-proof local retry and independent parents.

Run the native smoke with:

```bash
python3 -m regen.tools.sketch_native_smoke --output /tmp/sketch-native-validation
```

The smoke uses local Isabelle HOL and generic synthetic propositions. It runs
the production worker ML, renderers, native audits and document validators;
only its process transport and fixture environment are substituted. It verifies
a two-helper conditional group with native instantiated use-site observations,
restoring a legal local premise and rejecting an unsupported premise, actual
helper proofs, two parent updates in one
incremental batch, a suffix rewrite restored in the same worker, and a `False`
helper that conditionally closes a consumer but fails ordinary proof acceptance.
It uses no model calls and does not establish Refine repository completion or
improved benchmark success.
