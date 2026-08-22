# Refine/CRefine Repo-Level Regeneration Experiment Plan

Status: current Setting B implementation and execution plan

Last updated: 2026-07-30

This file is the operational handoff for implementing and running the current
repo-level proof-regeneration experiment. It does not replace the skill or the
normative harness protocol.

## Contents

1. Source-of-truth order
2. Experimental claim
3. Model-visible and forbidden information
4. Authority model
5. Current implementation status
6. Implementation work packages
7. Next executable milestone
8. Generation and evidence policy
9. Stop conditions
10. Fresh-conversation checklist

## 1. Source-of-truth order

Read and apply the documents in this order:

1. `.claude/skills/isabelle_prover_regen/SKILL.md`: research question,
   oracle, evaluation, and contamination rules;
2. `.claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md`:
   normative Setting B roles and M0-M12 artifact contracts;
3. this file: implementation order, present status, and next action;
4. `regen/HARNESS-EVOLUTION.md`: historical reasons for harness decisions;
5. `regen/STEPWISE-CODEX.md`: M7 backend details only.

If this plan conflicts with the skill or session protocol, the earlier document
wins. Update all three when a deliberate protocol change is accepted.

## 2. Experimental claim

Fix the abstract specification, Haskell design specification, C implementation,
parent sessions, exact refinement contracts, and mechanically classified
zero-contract Support theories. Remove every human command from contract-owning
Proof theories except the exact contract declarations. Generate complete Refine
Proof theories, including generated imports, theorem statements, attributes,
order, and proofs; seal Refine; then generate CRefine against that exact
generated Refine heap.

This is Setting B. It claims repo-level contract-proof regeneration across two
sessions with fixed Proof file slots and frozen Support. It does not claim to
regenerate the Support library or module partition. Setting A, where the model
also creates file slots and contract ownership, remains deferred.

The benchmark boundary is the two-session generated corpus. A theory is a
generation/sealing unit and a theorem is an execution unit; neither is an
independent headline benchmark.

## 3. Model-visible and forbidden information

For a Proof theory, the model may see:

- file identity and every exact local corres/ccorres/connect contract;
- fixed endpoint and frozen parent/Support declarations exposed through legal
  context or premise records;
- declarations retrievable from previously sealed, order-legal generated Proof
  theories, plus current-file green nodes; a retrieved owner becomes an actual
  ancestor only after its candidate import passes Session Planner checks;
- current-generation Isabelle residuals, attempt history, and graph records.

It may not see:

- original imports or bodies of the M1-classified, contract-owning Proof
  theories in the active generation target;
- helper names, statements, roles, order, proofs, consumers, dependency graph,
  interface inventory, or export plan taken from those classified targets;
- facts reachable only through human Refine/CRefine heaps or source aliases;
- unsealed, red, abandoned, future, or sibling-generation facts.

This is a target-scoped non-disclosure rule, not a claim that the model sees no
human-written proof anywhere in l4v. Frozen Support theories and proof sources
outside the active classified target scope may remain legal repository
material, as in normal proof engineering. Human source from the active target
may be inspected only after a result is frozen in a separate comparison
workspace. Such artifacts are comparison-contaminated and cannot be fed back
into generation.

## 4. Authority model

- **Session Planner:** emits one immutable evidence-grounded layered Proof-file
  generation list, tracks pending/generating/sealed/failed/skipped/retry and
  frozen-Support publication state, enforces generated import acyclicity, and
  owns Refine-to-CRefine staging. It does not plan theorems. M4 may bind the M3
  snapshot identity but does not inspect premise records to order files.
- **File Planner:** receives one file and all its contracts, then emits a DAG
  containing only generated theorem nodes: fixed contract roots, obligation
  theorems, and helper leaves. It emits exact statements and edges, not proofs
  or premise nodes.
- **Premise Database:** indexes checked frozen and generated declarations with
  provenance and publication state. It retrieves at node start and from
  residuals. It does not invent nodes or approve graph changes.
- **Lemma Prover:** proves one certified node and may change only its proof body.
  It cannot change statements or add nodes.
- **Graph Revision Agent:** is the only model authority that may recommend graph
  mutation from hash-bound red-node evidence. Deterministic certification still
  commits or rejects the transaction.
- **Assembler/Sealer:** receives proof-time candidate imports already approved by
  the Session Planner, retains and minimizes those justified by actual successful
  dependencies, checks the full theory, audits attributes/provenance/transcripts,
  freezes the generation, and publishes declarations only after a seal.

## 5. Current implementation status

### Normative design complete

- Setting B visibility and contamination boundary;
- Session Planner, File Planner, Premise Database, Lemma Prover, Graph Revision
  Agent, assembler/sealer responsibilities;
- generated-theorem DAG roles and helper-leaf discipline;
- M0-M12 input/output chain;
- file failure/skip continuation and partial-session reporting;
- generated Refine heap handoff required before CRefine.

### Existing code/evidence to reuse carefully

- `regen/contract_milestone.py`, `regen/theory_trace.py`,
  `regen/llm_transcript.py`, and `regen/run_theory_generation.sh` contain useful
  contract, audit, generation-ID, and transcript machinery, but must be checked
  against the current Setting B boundaries before reuse.
- `regen/regen_incr.py` and ArchAcc_R/Schedule_R work contain node proving,
  retrieval, residual triage, and lifecycle lessons. Their hard-coded plans,
  import masks, helper policies, human-derived interfaces, and old generation
  IDs are legacy and are not production inputs.
- Earlier session-DAG sealed theories and support-only generations are harness
  development evidence, not a current Setting B Refine checkpoint.

### Stepwise-Codex checkpoint

- Official Stepwise checkout: `tools/seL4-proof-search`, commit
  `3cb5e3ba48f6998696f6c721ab382217b76dc21a`, unchanged internally.
- Adapter: `regen/stepwise_codex.py`; runners:
  `regen/setup_stepwise_codex.sh` and `regen/run_stepwise_codex.sh`.
- Smoke generation: `20260728T172744Z-stepwise-codex-smoke`.
- Statement: `lemma stepwise_codex_smoke: "rev (rev xs) = xs"`.
- Result: one Codex call, Isabelle-accepted `by simp`, followed by a clean
  `Stepwise_Codex_Smoke` session build.
- Limitation: the relay returns only the final answer and may still read files.
  The run is `sealable_generation=false`; Stepwise-Codex is not yet a legal
  production Refine/CRefine backend.

### Not implemented end to end

- generic M5 File Planner schema and M6 statement/graph certifier;
- production-sealable M7 transport and unified backend result schema;
- independent M8 Graph Revision Agent transaction path;
- generic M9/M10 theory assembly, sealing, publication, and generated heap chain;
- complete Refine and CRefine campaign traversal and final evaluation.

### Current Setting B bootstrap

Branch: `regen-refine`.

Current campaign: `sel4-refine-setting-b-c55be4ed1c2f8c1b`.
M0-M4 are complete and reconstruction-verified. Campaign `70faa08a3822a1f9`
preserves the preceding M4 audit/replay weakness: its schedule was data-complete,
but verifier review showed that self-consistent artifact tampering and invalid
ledger state could pass. Campaign `6e0d35cf78b48cb5` is the short-lived M0-M3
development predecessor created before M4 began invoking the full M3 publication
verifier. Neither predecessor was rewritten or promoted.

#### Program and artifact map

| Milestone | Main program | Frozen configuration/policy | Tests | Primary evidence |
|---|---|---|---|---|
| M0 | `regen/refine_campaign.py` | `regen/config/refine-setting-b-campaign.json` | `regen/scripts/test_refine_campaign.py` | `M0/campaign-manifest.json` |
| M1 | `regen/refine_contracts.py` | M0 manifest plus `regen/scripts/regen_extract.py:is_contract` | `regen/scripts/test_refine_contracts.py` | private manifest, public packages, classification, forbidden inventory, and audit |
| M2 | `regen/refine_m2_preflight.py`, `regen/refine_m2_isolated.py` | M0/M1 identities, exact forbidden inventory, legal cache policy | M2 preflight/isolated tests | sanitized workspace, denial probes, elaboration, and `M2/m2-gate.json` |
| M3 | `regen/refine_m3_premises.py` | verified M2 gate and certified initial Support set | `regen/scripts/test_refine_m3_premises.py` | raw exports, SQLite database, snapshot, audit, and gate |
| M4 | `regen/refine_m4_session.py`, post-freeze `regen/refine_m4_compare.py` | fixed policy, public contracts, normalized Support availability, verified M3 publication | M4 session/comparison tests | dependency evidence, transcript, layered list, bound ledger, reconstruction gate, and isolated comparison |

Related documentation and status records are
`.claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md` for the
normative protocol, `regen/regen-campaign-status.md` for the active continuation
point, and entries 125-127 of `regen/HARNESS-EVOLUTION.md` for M4 scheduling,
the first PASS boundary, and reconstruction hardening.

The M1/M2 programs use `tools/lemma_inventory/parse_roots.py` to interpret
`proof/ROOT`, assign `.thy` files to Refine, and derive Support eligibility.
The parser is now an independently content-hashed M0 frozen input and has a
regression test for quoted session names and bare theory-list entries.

#### M0: freeze one reproducible campaign

**Purpose.** M0 converts the experimental choices and trusted-source identity
into one immutable, content-addressed campaign. Later stages must reject changed
inputs instead of silently continuing under the old campaign ID.

**What the program does.** `regen/refine_campaign.py`:

1. validates Setting B, ARM, Refine-before-CRefine staging, budgets, completion
   criteria, model/backend identities, trust boundary, contract policy, and the
   concrete forbidden-path policy;
2. records the top-level repository revision and whether its worktree is clean;
3. independently records the nested `verification/l4v` Git revision and checks
   that `proof/ROOT` and `proof/refine` are clean;
4. hashes every explicitly frozen protocol/program input, including the skill,
   session harness, M0-M4 programs, audited transcript backend, M4 scheduling
   policy, contract selector, ROOT parser, and `proof/ROOT`;
5. canonicalizes the manifest payload, derives the campaign ID from its SHA-256
   hash, and writes it with conflict-rejecting immutable semantics.

M0 has no clock field, randomness, or model call. A dirty top-level worktree is
allowed only under the recorded policy: every declared M0 input is content
hashed, and M1 separately hashes every trusted theory source it reads.

**Commands.** From the repository root:

```bash
python3 regen/refine_campaign.py
python3 regen/refine_campaign.py \
  --verify reports/regen/refine-setting-b/sel4-refine-setting-b-c55be4ed1c2f8c1b/M0/campaign-manifest.json
```

**Output and result.** The sole M0 artifact is:

`reports/regen/refine-setting-b/sel4-refine-setting-b-c55be4ed1c2f8c1b/M0/campaign-manifest.json`

Its payload hash is
`c55be4ed1c2f8c1bc39c028baf8c182c4f8f4895d2c735dec4dd2a92e9c7fc0f`.
It freezes top-level revision `fe8336af70b3caf8f7f4dcc99f5e1e4e66f15ce5`,
nested l4v revision `00d9073f70d064b31b4b1fad8a0e7086bb258007`, and
records both required l4v paths as clean. The first run created the manifest;
an identical rerun returned `unchanged`; `--verify` passed after checking the
payload hash, campaign ID, current revisions, scoped cleanliness, campaign
configuration, and every frozen file's size and hash. M0 performed zero model
calls.

#### M1: extract the fixed contract surface

**Purpose.** M1 is the trusted preparation stage that may read original human
Proof theories. It extracts only the fixed contract interface needed by later
generation, classifies Refine theories mechanically as Proof or Support, and
creates the private source deny-list needed by M2. Original human proof
implementation is not published to the model-facing packages.

**What the program does.** `regen/refine_contracts.py`:

1. verifies the M0 manifest and frozen inputs before reading trusted sources;
2. discovers the ARM theories assigned to session `Refine` by `proof/ROOT`;
3. applies frozen selector `operation-corres-v1`, implemented by
   `regen/scripts/regen_extract.py:is_contract`, to operation-style
   `_corres`, `_ccorres`, and `_corresK` declarations;
4. records each selected contract's exact sanitized declaration, normalized
   statement hash, owner theory/file slot, source location, contract-owned
   attributes, and minimal enclosing semantic context in a private manifest;
5. classifies a theory as Proof iff it owns at least one selected contract;
   otherwise it is frozen Support;
6. publishes one package per Proof theory containing only file identity and its
   selected contract declarations, without human imports, proof bodies, helper
   information, or trusted source locations;
7. records full source hashes for all discovered theories and creates an exact
   private forbidden-source inventory for every classified Proof theory;
8. checks unique IDs, exact-once private/public contract coverage, package count,
   source hashing, forbidden fields, visibility assertions, and leaked proof
   commands/comments before accepting the extraction.

The sanitizer scans outer words outside Isabelle strings and cartouches. It
removes declaration-context `notes` and `includes` blocks while retaining later
`fixes`, `assumes`, `defines`, `shows`, `obtains`, `for`, and propositions. It
separately truncates proof tails beginning with `using`, `including`,
`unfolding`, `supply`, or proof commands, and removes `(is ...)`, nested block
comments, symbolic comments, and proof bodies. The audit still rejects any such
command or comment that survives sanitization.

The minimal enclosing context is part of the contract's semantic identity, not
human proof guidance. In this corpus, 278 contracts require the architecture
interpretation context and one contract has a nested context. Omitting these
commands makes otherwise unchanged propositions fail to elaborate; human
helper declarations and proof-context hints remain excluded.

**Commands.** From the repository root:

```bash
python3 regen/refine_contracts.py \
  --manifest reports/regen/refine-setting-b/sel4-refine-setting-b-c55be4ed1c2f8c1b/M0/campaign-manifest.json

python3 regen/refine_contracts.py \
  --verify reports/regen/refine-setting-b/sel4-refine-setting-b-c55be4ed1c2f8c1b/M1/private/contract-manifest.json
```

**Artifacts.** Campaign `c55be...` historically wrote 26 immutable artifacts.
The successor implementation writes the same 26 payload artifacts plus
`M1/artifact-seal.json`, which binds their exact paths, sizes, hashes, Proof
package count, and contract count:

- `M1/private/contract-manifest.json`: all contracts plus all 41 trusted source
  paths, classifications, and full-source hashes; private and not model-visible;
- `M1/private/proof-support-classification.json`: deterministic 21 Proof / 20
  Support classification and per-file contract counts;
- `M1/private/proof-source-forbidden-inventory.json`: the exact 21 original
  contract-owning Proof source paths that M2 must deny;
- `M1/support-inventory.json`: the 20 frozen Support theories and source hashes;
- `M1/public/proof-file-packages/*.json`: 21 model-facing file packages;
- `M1/audit.json`: deterministic extraction and leakage audit.
- `M1/artifact-seal.json`: one aggregate integrity record for the preceding 26
  payload artifacts; it is not a Proof-theory package.

All paths above are relative to:

`reports/regen/refine-setting-b/sel4-refine-setting-b-c55be4ed1c2f8c1b/`

**Result.** For ARM Refine, M1 discovered 41 theory slots, selected 287
contracts, and classified 21 theories as Proof and 20 as Support. The first run
created all 26 artifacts; replay reported all 26 as `unchanged`. The M1 verifier
rechecked all 41 source hashes and returned `verdict: pass`. `M1/audit.json`
returned `PASS`; an independent scan of all 287 public statements found zero
forbidden command/comment hits and zero declarations missing their
`corres`/`ccorres`/`corresK` relation. The combined focused M0-M4 plus
ROOT-parser suite passes 46 tests, including manifest stability, immutable-write conflict rejection,
nested-repository drift, proof-body source drift, exact-once coverage, observed
human-command leak shapes, `includes ... shows ...` proposition retention,
M2 public-package leakage rejection, clean contract-shell materialization, and
forbidden-source byte-copy detection.

**Boundary.** M1 is a trusted extraction and JSON-level leakage audit. It does
not prove that contract shells elaborate in Isabelle, does not construct an
OS-level sanitized workspace, and does not execute forbidden-source denial
probes; those are M2 responsibilities. It also does not publish a Premise
Database (M3), schedule files (M4), or invoke a model.

#### M2 preflight: sanitized workspace and elaboration gate

**Purpose.** M2 checks whether the public M1 packages can become a usable
generation workspace without exposing original contract-owning Proof sources or
stock target heaps. It is still an evaluation-integrity stage, not theorem
generation.

**Implementation.** `regen/refine_m2_preflight.py` verifies M0 and M1,
materializes a sanitized workspace from public packages, writes one contract
shell theory per Proof file slot, reruns public statement leakage checks, and
compares every workspace file against the private Proof-source forbidden
inventory by path and full-source hash. The private forbidden inventory remains
an audit input and is not copied into the model-visible workspace because it
contains forbidden source paths.

**Gate model.** M2 has three independent checks:
Preflight shell names such as `M2_Bits_R_Contract_Shell` are temporary
validation namespaces. They prevent Isabelle name/session collisions and
distinguish declaration shells from publishable generated theories; they are
not a source-isolation mechanism. Production generation keeps the fixed
Setting B file/theory slot name. Isolation comes from removed paths, controlled
mounts, heap selection, and executable denial probes.


1. materialized workspace content contains no forbidden source copy/path/hint;
2. the execution environment mechanically denies forbidden source/heap reads;
3. every contract shell elaborates in Isabelle.

**Implementation.** `regen/refine_m2_isolated.py` copies a sanitized l4v tree
with the exact 21 classified Proof sources removed, dereferences and audits
symlinks, and constructs a content-identified parent-only Isabelle cache. It
derives eight initially eligible Support theories from their legal dependency
structure and builds them as `M2_Frozen_Support`; the other 12 Support theories
remain unavailable until their generated Proof dependencies exist. Contract
shells recreate the contract declarations in the legal frozen context required
for elaboration, including the relevant architecture interpretation. This
campaign does not attempt a separate per-file minimal-context construction.

The runner uses a network-disabled container. It mounts the sanitized l4v tree,
Isabelle installation, sanitized M2 workspace, and parent/Support heaps only;
it does not mount the host workspace or Docker socket. Parent, Support, and
contract-build caches are reused only after content and PASS-gate verification.
Each reuse record binds the source campaign ID, exact source M2 gate hash,
input-tree identity, and copied heap/log hashes.
Operational `created/unchanged` copy dispositions are deliberately excluded
from immutable identities so replay is stable.

The successor M2 gate binds the M1 artifact seal, preflight workspace manifest
and content audit, sanitized-l4v manifest, parent-cache manifest, isolation
results, elaboration results, and any cache-reuse records by relative path,
size, and SHA-256. The gate has a self-hash. M3 and M4 invoke this strict
verifier instead of trusting a standalone `"verdict": "pass"`.

**Historical result.** All three checks PASS for campaign
`sel4-refine-setting-b-c55be4ed1c2f8c1b`:

- 21 public packages, 21 declaration-shell theories, and 287 contracts are
  present; all leakage/copy/path/heap-hint counts are zero;
- all 21 classified Proof-source paths and both stock target heap paths fail
  executable `cat` probes, while two legal positive controls succeed;
- Isabelle accepts `M2_Contract_Shells` with return code 0. The final replay
  used a verified build database and completed in about 42 seconds; the source
  full shell build had already checked all 287 contracts.

Primary evidence is `M2/m2-gate.json`,
`M2/source-access-isolation-results.json`,
`M2/contract-elaboration-results.json`, and the content/cache identity
manifests under the final campaign directory. M2 publishes no contract-shell
facts.

#### M3: publish the initial Premise Database
Because the stricter gate and M1 aggregate seal are frozen-program changes,
`c55b...` remains historical bootstrap evidence. A successor campaign must
replay M0-M2 before the new gate format can be treated as production evidence.


**Purpose.** M3 turns the exact M2-green Isabelle context into a searchable,
versioned premise snapshot. It publishes only frozen-parent declarations and
the eight initially legal frozen Support theories. Classified human Proof
theories, contract shells, stock target heaps, generated theories, and local
green nodes are excluded.

**Implementation.** `regen/refine_m3_premises.py` loads
`M2_Frozen_Support.Corres` in the same isolated environment and uses Isabelle/ML
to enumerate static facts plus the effective `simp`, `wp`, and `corres` pools.
It writes facts incrementally with pure-ML hexadecimal encoding, avoiding both
per-fact ML/Scala calls and whole-library in-memory accumulation. Propositions
are rendered with markup disabled. M3 classifies Support ownership against the
M2-certified eligible-theory set and records the actually importable owner as
`M2_Frozen_Support.<theory>`.

Each published record includes canonical name, exact proposition, normalized
statement hash, owner theory/session, source class, effective automation
attributes, definition equations where applicable, publication state, and M2
provenance. The records are indexed in `M3/premises.sqlite3`; raw exports,
snapshot, audit, fact-dump result, and `m3-gate.json` remain immutable evidence.

The successor verifier treats raw exports as the reconstruction source. It
reparses `facts.tsv` and the automation pools using the M2-bound Support-owner
plan, reconstructs every canonical record, and compares all record fields with
SQLite. It recomputes `records_payload_sha256`, requires
`snapshot_id = "m3-" + records_payload_sha256[:20]`, and derives record and
source-class counts from those reconstructed records. Snapshot, audit, gate,
and database identities must all agree with that reconstruction.

**Historical result.** M3 PASS in `c55b...` published snapshot
`m3-eedb806f991eeb648859` with 76,663
records from 382 owner theories: 74,893 frozen-parent and 1,770 frozen-Support
facts. Effective pool membership marks 135 records as `simp`, 3,788 as `wp`,
and 13 as `corres`. The SQLite database is about 71 MiB; raw facts and pools are
about 30 MiB and 4.7 MiB. Isabelle completed the dump theory in about 3:35
(about 4:14 total command time). A repeated publication command verifies all
gate, export, database, row-count, source-class, and publication-state hashes
and returns `replay: unchanged` without rerunning Isabelle.

This requires a successor replay before the stronger reconstruction can become
production evidence.

#### Superseded bootstrap evidence

Campaign `sel4-refine-setting-b-739cfcb1ec51da4e` records the earlier M2
workspace-only PASS and `Word_Lib` timeout. Campaigns created while repairing
context preservation, cache replay, and M3 export (`de6d...`, `44bb...`,
`498c...`, `a810...`, `b328...`, `d41f...`, `1777...`, `b515...`, and
`40ef...`) are immutable development evidence, not continuation points. The
latest completed predecessor is `c55be...`; no strict-gate successor is sealed.

Earlier campaigns through `sel4-refine-setting-b-360fdf9a7f7eb738` are
bootstrap-development evidence and may not be continued. In particular,
`360f...` had a false-negative M1 PASS because public packages contained human
proof-context commands; its immutable disposition records the contamination.

Campaign `sel4-refine-setting-b-89ad2fd1fbfa667e` was data-correct for its
frozen 287 contracts but had an uncovered generic `includes ... shows ...`
truncation boundary. The extractor fix is frozen into the current campaign;
the old campaign's disposition prohibits continuation.

## 6. Implementation work packages

### WP0: freeze the campaign (M0)

**Input:** repository revision, architecture, Refine/CRefine targets, Setting B,
contract selection rule, model IDs, budgets, forbidden paths, and success rules.

**Output:** immutable campaign manifest with hashes and one campaign ID.

**Gate:** rerunning manifest construction on unchanged inputs yields the same
hash. No model call occurs.

### WP1: extract and sanitize the contract layer (M1-M2)

**Input:** trusted original sources and WP0 manifest.

**Output:** private contract manifest; public Proof-file packages; frozen Support
inventory; sanitized workspace containing no human Proof imports or bodies;
workspace-content audit; mechanical source-access isolation results; and
contract elaboration results.

**Gate:** every selected contract occurs exactly once; each contract statement
hash matches trusted extraction; every Proof file exposes only identity and
contracts; forbidden source probes fail.

### WP2: publish legal premises (M3)

**Input:** checked frozen parents/Support and sanitized environment identity.

**Output:** versioned premise snapshot containing canonical declaration identity,
statement/hash, owner theory/session, attributes, defining equations, source
class, heap/generation provenance, and publication state.

**Gate:** zero original facts from classified contract-owning human Proof
theories; frozen Support facts remain legal; queries return only callable
records; every record resolves in the recorded context.

### WP3: schedule Refine files (M4)

**Input:** all public Refine Proof packages with exact contracts, normalized M2
frozen-Support availability/unlock facts, fixed scheduling policy, and a fully
verified M3 publication. Premise records and retrieval statistics are not inputs.

**Output:** immutable layered Refine file-generation list, mutable status ledger,
candidate dependency-evidence audit, and a complete one-call Codex transcript.

**Method and gate:** one audited Codex 5.5 call proposes only contract-citing
`contract-composition` and weak `operation-call` edges. Deterministic code
validates file/contract citations and complete identifier tokens, rejects
unsupported edges, collapses composition SCCs, derives layers, and emits the
final list. Contract composition determines layers. Within one layer, acyclic
operation-call edges are precedence constraints; operation cycles are collapsed
and reported. M2 `support-unlock` facts rank currently ready components. These
are scheduling evidence, not certified proof dependencies. All Proof slots and
contracts occur exactly once; no human Proof import graph or premise records are
used.

The ledger distinguishes eight initially published frozen Support theories from
twelve proof-dependent frozen Support theories. The latter remain unchanged
human Support input; they are checked and published only when their generated
Proof and Support prerequisites become available.

M4 itself does not allocate theorem-generation IDs. When execution later starts
a file/theorem generation, the runner creates the fresh generation ID, binds it
to that file's ledger entry, and changes only the mutable progress state. Later
proof, retry, seal, failure, or skip events update that ledger through validated
status transitions; they do not change the immutable M4 file list.

**Observed M4 result (`c55be...`):** the single audited Codex 5.5 call used a
154,600-character sanitized prompt, recorded no tool events, and returned 49
candidate edges. Twenty-four passed deterministic checks and 25 were rejected:
16 duplicate-edge reports, two self-edges, and nine cases with at least one
identifier not copied exactly from the cited contracts. No operation SCC,
precedence violation, or composition override was present. The accepted evidence
produces three layers over all 21 Proof files and 287 contracts. The immutable
list hash is `28b7d326db5b6d8f9d56ff1d92c7894f41bb8c3d57ae44c752f325f07d6886e7`.

M4 replay verifies the complete M3 publication, reconstructs the exact planner
payload and prompt, validates the sole transcript bundle, reparses the saved
final response, and rebuilds dependency evidence, list, audit, gate, and the
revision-zero ledger. The gate binds the initial ledger; later revisions must
replay through legal status events. Regression tests reject a rehashed
contaminated audit, a rehashed list missing a Proof file/contracts, and an
unrecorded status/revision.

This PASS certifies artifact reconstruction, legal inputs, transcript
sealability, edge citation checks, operation precedence, and exact coverage. It
does **not** certify a true or optimal Refine proof DAG. The post-freeze held-out
comparison agrees with 10/22 direct human Proof imports and 13/29 relations after
collapsing Support-only paths. This comparison is contaminated engineering
evidence, not Setting B ground truth, and is forbidden as future planner input.

### WP4: plan and certify one theory (M5-M6)

**Input:** the first pending file task selected by WP3, all its contracts, legal
environment declarations, optional premise search, and graph budgets.

**Output:** certified generated-theorem DAG with exact contract/obligation/helper
statements, roles, edges, consumers, rough strategies, graph hash, and executable
node order.

**Gate:** fixed contracts unchanged; no premise nodes; no cycles, wrappers,
duplicates, vacuous nodes, undeclared constants, missing guards, or inaccessible
dependencies. Isabelle declaration-shell checking succeeds.

### WP5: prove nodes with interchangeable M7 backends

Run two separately reported backends:

1. Codex whole-proof generation plus Isabelle-error repair baseline;
2. Stepwise-Codex proof-state tree search.

Both receive exactly one certified node plus green generated dependencies,
eligible premises, current context, strategy, attempts, and residual. Both return
a checked proof or structured RED result. Neither may change the graph.

For a retrieved cross-file premise, M7 first asks the Session Planner to approve
the owner as a candidate import using seal, provenance, generation-order, and
cycle checks. Isabelle then checks the attempt in that temporary context and M7
records what was actually used.

**Production gate:** model file/tool access is mechanically denied and logged,
or all tool events are exposed and pass the forbidden-source audit. Until this
gate exists, Stepwise results remain development-only.

### WP6: revise graphs from evidence (M8)

**Input:** one red node, graph hash, consumers, all attempts/residuals/frontier,
retrieval and duplicate evidence, proposed operation, and remaining budget.

**Output:** signed decision and, when accepted, a deterministically checked graph
transaction with a new hash and invalidation set.

**Gate:** contracts immutable; helper leaves have no generated children; parent
guards are retained; all consumers and dependencies type-check; green nodes are
immutable; no human proof/decomposition enters the request.

### WP7: assemble, seal, and publish theories (M9-M10)

**Input:** all green nodes and actual dependencies for one file generation.

**Output:** complete generated theory, the proof-time candidate-import record,
minimized final generated imports, clean build, attribute/dependency/leakage
audit, immutable transcript bundle, theory seal, and new premise snapshot.

M9 removes unused candidate imports, minimizes those supported by actual proof
dependencies, and clean-checks the complete theory. Candidate imports and final
imports are distinct artifacts.

**Gate:** zero bypass commands; every contract green; all explicit and implicit
premise use legal; every model call sealable; downstream child import smoke
succeeds. Only then may declarations enter the Premise Database.

### WP8: traverse and seal Refine (M11)

Continue through the complete M4 list. Record failed/skipped files and attempt
later entries when their legal premises exist. Retry source-linked generations
without changing the immutable list.

**Output:** either a clean sealed generated Refine heap and exact premise
snapshot, or an explicitly partial campaign. Never call a partial result a
Refine seal.

### WP9: generate CRefine and evaluate (M12)

Only after WP8 seals Refine, repeat WP0-M10 for CRefine against that exact heap.
Then measure contract coverage, build wall, proof LOC, theorem/helper count,
cross-file generated dependencies, reuse, fan-out, duplicate facts, tactic
brittleness, human-assist budget, and at least two memorization controls.

## 7. Next executable milestone

M0-M4 are complete for campaign `sel4-refine-setting-b-c55be4ed1c2f8c1b`.
The immutable three-layer schedule is suitable as a reconstruction-verified
execution priority with retry/skip support. It remains incorrect to call it the
true Refine dependency DAG; M5 must record premise-availability failures and may
continue later entries rather than treating M4 edges as proof dependencies.

Required milestone artifacts:

1. complete: immutable campaign manifest, independent l4v identity, frozen
   program/policy hashes, and concrete forbidden paths;
2. complete: 287 exact contracts, private 41-source inventory, exact 21-source
   deny inventory, 21 public Proof packages, and 21/20 Proof/Support split;
3. complete: sanitized workspace, 23 passing denial probes, two passing
   controls, legal cache identities, and 287 green declaration shells;
4. complete: M3 snapshot `m3-eedb806f991eeb648859` with 76,663 facts and a
   replay-verified SQLite retrieval database;
5. complete: immutable three-layer Refine file list and list hash;
6. complete: ledger initialized with every Proof slot, eight published/12
   blocked Support theories, and a gate-bound initial-ledger hash;
7. complete: computed audit evidence, complete transcript, exact prompt/final
   binding, and full reconstruction replay;
8. complete: M0-M4, comparison, and ROOT-parser suite passes 46 tests, including
   rehashed-artifact and invalid-ledger attacks;
9. complete: direct and Support-collapsed human-import comparisons are isolated
   under `M4-comparison/` and forbidden as future planner input.

M4 completion did not prove a theorem. The next milestone is WP4/M5-M6: consume
the first pending file task, plan only its generated theorem DAG, certify every
statement and edge, and preserve dynamic failure/skip/retry evidence.

## 8. Generation and evidence policy

- The first M5 attempt for a file receives a fresh generation ID covering M5
  through M9 and all planner, prover, revision, checking, and sealing calls.
- A retry or graph-changing replacement derived from a failed/abandoned attempt
  receives a new generation ID with a predecessor/source link; it never edits an
  old transcript.
- Record prompts, raw provider output/streams, final responses, stderr, model
  metadata, tool events, candidates, Isabelle logs, residuals, retrievals,
  graph hashes/transactions, timestamps, and artifact hashes.
- A successful Isabelle proof is not publishable when its model transcript or
  premise provenance is non-sealable.
- RED and partial results are retained as first-class evidence.

## 9. Stop conditions

Stop and report rather than silently relaxing the experiment when:

- a fixed contract cannot elaborate without a human Proof command;
- the sanitizer cannot prove human Proof source/heap isolation;
- a premise cannot be assigned legal provenance;
- a model transport permits unaudited file access;
- a planner requests human helper/interface/dependency information;
- graph revision would alter a contract, drop a parent guard, create a cycle,
  or exceed the frozen budget;
- CRefine would have to use the stock human Refine heap.

## 10. Fresh-conversation checklist

From `/home/lijun/seL4-docker-main`:

```bash
git branch --show-current
sed -n '1,460p' .claude/skills/isabelle_prover_regen/SKILL.md
sed -n '1,620p' .claude/skills/isabelle_prover_regen/references/SESSION_HARNESS.md
cat regen/REFINE-CREFINE-REPO-LEVEL-EXPERIMENT-PLAN.md
docker ps --filter name=sel4-l4v
curl -fsS http://127.0.0.1:18080/healthz
```

Expected branch: `regen-refine`.

Before Stepwise backend work only:

```bash
regen/setup_stepwise_codex.sh
PYTHONPATH=.stepwise-codex/site-packages:. \
  python3 -m unittest regen.scripts.test_stepwise_codex
```

The next implementation question is now narrower:

> Can M4 produce a deterministic, evidence-grounded layered Refine Proof-file
> execution list and a separate mutable progress ledger from public contracts,
> normalized Support availability, and a frozen scheduling policy?

M0-M4 pass in `70fa...`. Do not feed the post-freeze human comparison back into
generation. The immediate design decision is whether M5 should consume the
current list with dynamic failed/skipped/retry handling, or whether M4 deserves
a separately frozen schema/policy revision first.
