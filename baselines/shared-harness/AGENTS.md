# Repository-level Isabelle task

The task inventory is benchmark.json. Definitions, contract statements and contexts are fixed. Resolve each fixed contract by a checked proof or checked counterexample in its original owned environment. Choose proof order, auxiliary lemmas and repair strategy yourself.

Edit only the benchmark's permitted proof, auxiliary and import slots. Preserve fixed content and the slot grammar. An unfinished `oops` does not introduce a fact. Reuse legal parent facts and checked facts generated in this run. A negative theorem is not a proof of the positive target.

Use the common harness (the same tool entry and checker profile are supplied to VERO and official Codex):

- `python3 /backend/tool.py check MODULE_NAME`
- `python3 /backend/tool.py search 'query' --limit 20`
- `python3 /backend/tool.py progress`
- `python3 /backend/tool.py budget`
- `python3 /backend/tool.py check Vero_All` checks the complete generated import closure; an ok result alone does not mean unfilled targets were solved.

Read the actual fixed statements and checker diagnostics. New benchmarks use owned-context-outcomes-v1 by default. Refutation must prove the mechanically constructed negation of the universally closed contract, including all contract assumptions and parameters, conditional on the exact original owned environment. The trusted command exports this complete obligation outside the context; do not add premises or change context. This obligation does not require proving the environment globally. This certificate does not establish the environment or an actual deployment. Historical benchmarks without result_scope retain their frozen strict-global meaning. Failed proof search, a timeout, a model explanation or a concrete test alone is not a formal refutation.

Use only the supplied sanitized definitions, legal premise catalog and results from this run. Do not change trusted tools, declarations, specifications or semantic inputs. Do not access historical experiment solutions or reports. The common harness independently checks submissions and final integration; successful tool execution alone does not establish acceptance.

Report proved, refuted and unresolved targets separately. An artifact with refuted targets resolves obligations but does not verify all original positive specifications. Select your own working notes and intermediate checks; no particular proof-search procedure is prescribed.

## Budget and completion

The allocated time comes from the required `--budget-seconds` launch hyperparameter; there is no default. It is an effective normal-runtime budget: all normal experiment work counts, including planning, reasoning, helper work, retrieval, tool waiting, native checking, ordinary proof timeouts, theory builds/seals and repository integration. Lack of progress does not stop the clock. Only the trusted supervisor can exclude recorded explicit pauses, confirmed all-worker infrastructure interruptions and offline recovery. Overlapping excluded intervals count once. The agent cannot grant exclusions, change the clock or reset the allowance.

At startup run `python3 /backend/tool.py budget`, and query it again before deciding to stop. Follow `remaining_seconds` and `status`; there is no fixed calendar deadline because trusted excluded periods move the expected finish time. `projected_deadline_unix`, if present, is only an estimate assuming no further interruptions. Missing metadata or `accounting_unknown` is an infrastructure/accounting blocker, not zero time; preserve work and report it. A paused clock does not authorize uncharged work: the supervisor must suspend experimental work during an excluded interval.

Continue while effective time remains and any target is unresolved. Partial coverage, an unsuccessful proof attempt or a clean build of only filled slots is not a stopping condition. Choose your own approach. Stop after all targets have checked proofs/refutations and the full repository check succeeds, when the effective budget is exhausted, or when a genuine infrastructure blocker prevents further tool use. Report the stopping reason and observed remaining effective time; never claim resolution merely to satisfy this instruction.
