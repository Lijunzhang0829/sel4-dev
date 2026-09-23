# Single-theory batch File Planner

`python3 -m regen.execution.proof_session_runner batch-file --run-dir RUN --theory THEORY`

This opt-in path keeps one continuing File Planner responsible for a theory's graph,
retrieval, auxiliary lifecycle, proof batches and response to checker feedback.
It does not start parallel File Planners. The frozen File Planner model generates
both graph edits and proof bodies. The legacy per-node runner remains available.

Actions include `observe`, `query`, `read_facts`, `read_queries`, `read_attempts`, `read_proofs`, `read_diagnostic`, `read_proof_state`, `resolve_symbols`, `submit_batch`, `finish` and `pause`.
A submitted graph is a patch: omitted nodes and contract mappings remain intact.
One proof batch may contain a helper and its consumers, or independent contracts.
Dependencies determine declaration order. All proposed proofs are checked together
with the accepted prefix; the checker never assumes an unproved target.
When all new or changed declarations are proved by the same batch, its complete native proof check and audit also certify those declarations. Changes to unproved declarations still require a separate declaration check, which is not proof evidence.

Fixed contracts and accepted proof declarations are immutable. An unproved generated
node may be removed with a reason, provided all consumers are repaired in that
same patch. Accepted auxiliaries remain in the checked prefix. A failed batch
accepts neither graph edits nor proofs; the next request receives the candidate
and native diagnostics. Atomic rejection intentionally does not salvage a
successful prefix from a failed batch: the planner may resubmit a smaller batch.

The acceptance journal binds exact generated source, logs, graph and proof order.
Every model request is charged before calling the provider, with audited artifacts
under `theories/THEORY/batch-requests/`. Retrieval results and requested accepted
proofs persist across calls. The default has no revision/request count cap;
`--max-requests N` checkpoints after N additional actions without resetting costs.
Provider and Isabelle timeouts remain operational safeguards.

`finish` checks the complete theory, performs semantic/dependency audits and builds
the generated session before sealing. Local accepted contracts and sealed contracts
are reported separately. A paused or checkpointed controller is not a successful
seal. The run mutation lock prevents overlapping controllers in the same run.

Changing implementation requires a new run identity. Use `fork_continuation` to
carry a verified existing commit chain and its cumulative costs into that identity;
never rewrite old bindings. Continuation evidence distinguishes reused accepted
proofs from newly generated proofs.

After the first live run, proof feedback was separated from query feedback.
`last_proof_feedback` survives later retrieval; `recent_actions` is navigation, while
`query_memory` and bound read tools retain query arguments and results. Optional string `notes` carries the model's
concise working summary. Startup recovers these observations from the same run's
audited request artifacts without accepting any uncommitted proof.

## External premise retrieval and proof examples

Current-theory GREEN nodes stay in the controller graph and acceptance journal;
`read_proofs` exposes their full proofs. The batch controller's external `query`
no longer includes these nodes or accepts `scope: local`. Other runners retain
their existing local recall API.

Upstream and retained semantic facts remain in the verified SQLite catalog.
Imported sealed generated facts remain in the per-theory `sealed-premises.json`,
filtered by frozen imports. Publication now includes `context`, optional
`proof_body`, `proof_sha256` (the journal's canonical JSON digest), and
`proof_commit_id`. These are derived from accepted commits, not model summaries.
Legacy records without proof evidence remain usable as facts but not proof examples.
The database and old frozen experiment artifacts are not migrated in place.

In addition to exact/definition/search queries, the controller accepts:

```json
{"action":"query","request":{"goal_query":true,"context_node_id":"actual-node-id","purpose":"helper","scope":"all"}}
```

The harness takes the full statement and context from the accepted graph. It does
not trust model-supplied proof state text and does not claim to have a live REPL
state. Upstream ranking reuses FTS5/RRF, with optional `use_vectors: true` over an
existing verified lexical vector index. Imported sealed facts use lexical cosine;
the two source ranks are interleaved (their numerical scores are not comparable).
At most five results are returned with an immutable query/target/result audit.

`purpose: proof_example` searches only certified imported sealed generated proof
examples. Optional `proof_hint` supplies an approach or candidate fragment for
lexical matching over theorem name, statement, context and proof body. Upstream
proof bodies are not currently stored, so upstream-only example retrieval returns
no matches. A returned proof retains its original context and dependencies; it is
an example, not a standalone proof guaranteed to work in the new goal.

Reference inspected locally: Stepwise `seL4-prover/utils/repl.py:hammer_facts`,
`AutoIsar/Auto_Isabelle.ML:get_facts_from_state`, and
`seL4-prover/provers/treesearch_prover.py` undefined-fact repair. That path uses
Sledgehammer/MaSh relevance from goal/hypotheses, and Levenshtein distances between
fact names to repair undefined references. This implementation borrows target-led
fact selection; its proof-text lexical search is an extension, not a claimed
reimplementation of MaSh or a learned proof-strategy similarity model.

## Incremental native batch checks

New execution identities enable `IsabelleChecker.batch_incremental` by default.
`batch_checker.py` / `batch_checker.ML` keep a real checked prefix in one Isabelle
process. A trial executes only the appended proof declarations, with the same
context wrappers and declaration order as full rendering. No unproved goal is
introduced through `sorry`. New facts are consolidated and audited for skip-proof,
oracle and new-axiom dependencies before a GREEN result is returned.

A successful trial remains pending. Only a later call whose authoritative old
state exactly matches that pending prefix sends `commit` to the worker. Failed
trials leave the base state unchanged. A different committed prefix or changed
imports rebuilds the worker. Restart reconstructs it from accepted real proofs;
the volatile worker is never recovery authority. Unsupported legacy syntax or
worker failure closes the worker and falls back to a complete cold check.

Evidence retains a reconstruction of the full candidate source, plus prefix and
delta digests, the audited new fact names, command logs, preparation time and trial
time. This full source is an audit artifact, not a claim it was all re-executed on
every warm trial. For `audited-batch-worker-v2`, Isabelle atomically publishes a
structured audit before publishing GREEN. It binds the worker/request, theory,
imports, exact executed prefix and delta, and the complete new-fact list. Commit
validation independently reconstructs and checks those values and the embedded
artifact hash. stdout is diagnostic only: delayed audit log lines cannot reject
an otherwise valid v2 result. Historical v1 commits retain their original marker
validation. Missing/malformed v2 evidence triggers infrastructure fallback, never
acceptance from a bare GREEN response. Full audit text stays in durable evidence
and is excluded from compact model feedback. `execution_wall_ms` includes prefix preparation and the trial;
`wall_ms` measures the trial only. Whole-theory seal checks/audits/build remain cold.
A changed declaration proved in the same batch is certified by that batch; a separate
check remains necessary when any changed declaration is left unproved. Concurrent File Planners/workers are not added by this change.

Validation artifacts: `reports/analysis/incremental-batch-check-20260921/`.
Generic native tests cover rollback, same-batch helper/consumer and restart. The
case replay uses only already accepted StatePrimitives proofs and grants no new
formal acceptance. Active older experiments retain their frozen implementation;
using this checker requires a new execution identity.

## Persistent observation memory and bounded input working sets

Normal execution and restart recovery share the pure `apply_feedback` reducer.
Recovery restores query results, requested proofs, notes and JSON-error feedback
from charged requests. Matching a later accepted proof body never upgrades an
older RED or checked-but-uncommitted attempt. A feedback file is not acceptance.

`target_memory` indexes the latest proof attempt for each target, including removed
helpers. Its diagnostic is explicitly labelled as an excerpt when incomplete;
`read_attempts` returns the complete original candidate and native diagnostic by
charged request number. These observations never change the accepted graph.
`query_memory` expands repeated groups and the eight most recent distinct groups;
`query_index` covers all groups, with `read_queries` loading their complete entries.
Legacy query groups are advisory and cannot supply cache hits without a bound
identity. No decision-count limit is imposed.

New queries bind request, target statement/context, frozen run, accepted proof
prefix, database file identity and sealed-catalog content. A cached response is
reused only when its immutable feedback bytes match the hash recorded in committed
history. Missing or changed artifacts trigger normal retrieval. Every reuse still
has a charged model decision and its own audit. Progress feedback distinguishes
repeated queries, new record IDs and actual proof submissions; graph-only edits
are not counted as proof progress.

The input expands at most 24 recent recalled facts plus explicitly requested facts;
all complete statements/contexts remain in the journal and are available via
`read_facts` using the lightweight full index. This is a presentation working set,
not a retrieval or proof-attempt budget. Detailed attempt/query read responses are
expanded for the next decision and can be loaded again later.

The journal caches verified prefixes only in process memory. Restart requires full
validation. Existing-file identity changes invalidate the prefix; append validates
new evidence; missing commits remain errors. Shared caches require an explicit
validation namespace bound to runtime, package and validation implementation.
Continuation now carries verified transitive sealed providers, preserving their
actual seals and accepted proof evidence. Old runs remain immutable.

Read-ID errors identify the invalid values and may suggest unambiguous exact IDs.
Suggestions never replace a request automatically: the model must retry with valid
IDs. Type and count errors identify their field separately. Empty theory journals
no longer occupy shared validation-cache capacity; a live journal retains its own
verified prefix after shared-LRU eviction, with unchanged invalidation checks.

## Timing for fresh comparisons

Each charged request writes `timing.json` with disjoint `charge`,
`input_and_prompt`, `model`, `action`, `commit` and `progress` wall intervals.
Successful batch `continue`, successful `finish`, pauses and exceptions are covered.
`model_attempts` includes transport retries. LLM and native checker event durations
are subsets of these intervals and must not be added a second time.
Initialization writes a latest summary and a unique archived record under
`initialization-timings/`; restarts retain earlier startup costs. Publishing timing
files is excluded from their own totals, and telemetry failure does not change
proof acceptance or replace the original controller exception.

## Candidate timeout isolation

`batch-file --candidate-timeout 120` limits one proof batch's execution and audit;
`--check-timeout` independently controls graph checks, prefix startup and final
seal checks. The immutable batch policy records both. This does not limit planner
decision counts. The ML transaction returns explicit timeout evidence; the Python
watchdog grants 10 seconds for response delivery. A timed-out candidate cannot
be committed and is not automatically rerun through the cold checker. Genuine
worker infrastructure failures may use cold checking under the candidate limit;
that temporary limit is restored before graph or seal operations. Full source,
logs and measured elapsed costs remain in the attempt evidence.

## Exploration before mathematical pause

The planner prompt explicitly permits uncertain proof candidates and new helper
obligations. Native checking, not pre-submission confidence, decides acceptance.
Missing recalled bridge lemmas or proof examples alone are not a pause reason.
The planner may test a graph-only decomposition, a separate helper proof, or a
helper/consumer batch; no removed historical target proof is required. Mathematical
pause explanations should reference concrete attempts, native feedback and considered
decompositions; external blockers should identify the missing input or tool failure.
This is guidance, not a new action-count cap or a hard-coded helper quota. The
existing pause API still checks a nonempty reason; GREEN and fixed-contract checks
are unchanged. Prompt changes require a new implementation-bound continuation.

Explicit `read_attempts` and `read_queries` selections persist across other actions
until the next successful read of the same kind replaces them (at most four records
per read). This lets proof feedback and subsequently retrieved facts coexist.
Failed reads preserve the previous selection. Request-number watermarks prevent
older observation replay from overwriting newer selections; recovery uses the same
feedback application path. Full selected diagnostics are not truncated. This does
not import any additional proof authority.

## First reliability batch (2026-09-22)

- Diagnostics longer than 12,000 characters become explicitly incomplete excerpts.
  Full bound feedback/source/log evidence stays in the request artifacts. Use
  `read_diagnostic` with a historical `request_number`, diagnostic key `path`,
  character `offset`, and `limit` (1–12,000) to retrieve exact pages. The response
  gives total length, digest, and next offset. Reads verify the original journal
  feedback digest; this is not arbitrary filesystem access.
- Identical latest feedback is referenced through `last_proof_feedback.feedback`
  instead of repeated. The complete prompt is checked against an 800,000-character
  local input budget before calling the backend. This is not an iteration budget.
  Mandatory statements/propositions/proofs are never silently cut to fit. If they
  still exceed the limit, the controller records an infrastructure stop.
- `lifecycle.json` records request phase, backend attempts, audited returned turns,
  action completion and check verdict. `model_attempts` is a backend attempt count,
  not billing or proof of a remote provider invocation; failed dispatch may be
  unknown. A preflight rejection has zero backend attempts. `timing.json` preserves
  disjoint phase durations. Termination records use the acceptance journal's latest
  state, then refresh progress and publish `execution-stop.json` and a stop event.
  Startup failures without a journal are recorded as `startup-failure.json`.
- A checker bound to a frozen repository runtime caches up to 32 GREEN declaration
  certificates in memory. Exact rendered source, imports, frozen implementation/run
  identity, runtime settings and filesystem identities for heaps, Isabelle, l4v,
  semantic workspace and generated providers must match. Failed declarations are
  not cached; proof failure does not invalidate a matching declaration certificate.
  Cache hits retain original source/log evidence and record original native timing;
  current native wall time is zero, while environment lookup remains action time.
  Restart creates an empty cache. New declarations still receive whole-graph
  elaboration; affected-node incremental checking remains future work. Candidate
  proof checks and full theory seal are not bypassed.
- Frozen embedded source/log bytes are verified and preserved when evidence is
  frozen again. Mutable checker filenames cannot replace a previous certificate's
  source or log during later retries.

Validation artifacts: `reports/analysis/file-planner-first-batch-20260922/`.
The native smoke uses only the synthetic declaration `lemma cache_probe: "True"`;
no target proof was provided and no File Planner experiment was restarted.

## Workspace, intermediate recall and batch route feedback (2026-09-22)

- `observe` accepts 1–8 complementary read requests (one per read kind). Reads are
  independently reported; successful fact/attempt/proof/query selections survive
  another item failing and later queries. `workspace` optionally pins focus IDs,
  recalled fact IDs and a route summary. All propositions remain available through
  evidence-bound reads; focus changes affect expansion, not graph/proof authority.
- `read_proof_state` reports indexed diagnostic residuals with explicit completeness
  and native before-failed-command state when available. BatchWorker now exports
  native goals, local assumptions, failure command/line and successfully executed
  commands through XML. The full snapshot is kept in checker evidence; read views
  show marked excerpts. Diagnostic residuals after a failed method are NOT the same
  as the native state before that method. Missing snapshots are not reconstructed.
- Query syntax: `proof_state_query: {request_number, goal_index, origin?}` with
  `context_node_id` naming a submitted node. `origin` defaults to `diagnostic`, or
  can be `before_command`. The historical proposal and feedback must match journal
  digests; the caller cannot provide its own goal text. Incomplete or ambiguous
  goals are rejected. A multi-node residual needs a unique native failed-node
  observation. Deleted draft helpers can supply their original owned locale
  context; declarations changed in the current graph are rejected as stale.
- Intermediate recall uses the existing upstream SQLite/sealed recall restrictions
  and optional vectors; it changes the query target, not premise trust. It provides
  candidates, not proof of applicability. `resolve_symbols` accepts 1–16 plain term
  identifiers in one native lookup of the frozen imports and supplied node context.
  It reports constants/types/abbreviation expansions, not theorem existence.
  An unresolved term does not establish that a lemma is absent; use the existing
  `query` with `mode=exact`, then `read_facts` for recalled records.
- `consumer_feedback` separates planned consumer dependencies, trials after helper
  GREEN, and unknown actual proof use. It shows each pending contract's last trial
  and elapsed decision count. The model may test a consumer or explain continued
  decomposition; there is no mandatory helper quota or retry limit.
- `batch_opportunities` lists ready independent nodes and ready helper/consumer
  groups, exact candidate/failure signature repetition and known model/action costs.
  Old records lacking these measurements are marked by coverage, not assigned zero
  inferred cost. A shared batch's costs are counted once, not once per node.
- `node_results` distinguishes audited GREEN, completed-but-uncommitted, failed,
  not-reached, declaration-rejected and unknown. A successfully executed lemma in
  a RED batch is not a separately audited theorem. Atomic batch acceptance remains.
- `submit_batch.reuse_proofs` selects historical request numbers and node IDs. The
  harness verifies original artifacts, expands exact proof bodies and missing
  selected helper declarations, then performs ordinary preparation and full proof
  checking. It never copies a success flag. Explicit new proofs may be submitted
  alongside reused bodies. Already GREEN, duplicate and declaration-drift selections
  are rejected. The model response and expanded proposal are separately preserved.

Historical validation: combined reads at 55–56; residual recall after 95; native
state export during 94; consumer gap at 129; per-node outcomes and independent
rechecking of the completed helper from RED batch 112. Tests are isolated and do
not commit these replay results into the original experiment.

## 2026-09-22 interaction and check simplification

`observe` accepts 1–8 independent read-only items including queries, with repeated
kinds allowed (such as two diagnostic pages). Each item has its own index/result;
query audits are `fact-selection-01.json`, `fact-selection-02.json`, etc. All items
see the same accepted snapshot. They cannot use an ID that a preceding item has
just returned. Same-kind selections are combined before the feedback is committed.
Query cache entries verify the entire enclosing feedback artifact as well as the
selected item. Recovery preserves all successful observations without graph/proof changes.

`prepare_prompt` applies `planner_context.project_context`: identical complete
facts, proofs, candidates and feedback are expanded once, with explicit in-input
references elsewhere. Matching target previews reference that complete attempt;
unique older previews remain labelled. No statement, proof or context is shortened.
The saved `input.json` now contains the actual model-facing projection. Full history
and original feedback remain unchanged. Instructions describe tools and proof
boundaries without requiring another full read of an already visible attempt.

Declaration coverage is recomputed by the acceptance validator from old/new
statements, names and contexts. Only changes all included in newly proved nodes
can use `proof-batch-declarations-v1` instead of a separate graph check. The native
candidate source, prefix/delta and new-fact audit must still match. RED batches
accept nothing. Continuation can inherit declaration authority from the verified
origin chain, bound to its tip and final declaration digest. Final seal remains a
complete theory check, audit and session build.

The current provider remains one audited JSON decision per model call. Grouped
observations reduce forced round trips but do not create a persistent tool-using
model session. A future session adapter needs an explicit tool whitelist and
call/result evidence bindings; removing `no_tool_events` alone is insufficient.

Validation: `reports/analysis/file-planner-speed-20260922/`. Historical request145
passed with identical proof source and no separate declaration check. Its new
worker startup is accounted separately; cold end-to-end time is not compared to
a historical warm candidate. Historical95 grouped retrieval and decision113
input projection are offline/tool replays, not evidence of new proof generation.

## Runtime visibility

The batch entry now configures `runner-events.ndjson` and `runner-status.json`,
including a 30-second runner heartbeat and explicit monitor termination. A
heartbeat means the monitor is alive, not that model reasoning or proof acceptance
has progressed. Per-request `timing.json` and `lifecycle.json` retain phase changes.

Codex provider stdout/stderr are written to transcript artifacts while the call
runs. Each call owns a process group. The existing total timeout, SIGINT, SIGTERM
or SIGHUP saves partial output and non-sealable terminal metadata and cleans up
that group. No iteration budget or short no-output timeout was added. SIGKILL or
host failure requires external process supervision. Console errors no longer
precede durable event writes or turn success into a BrokenPipe failure; a blocked
console pipe can still require a redirected log or external supervisor.

The outer Codex conversation is a separate process. Repository runner heartbeats
cannot repair its stalled model request or frontend. Use the independent read-only
session watcher documented in `CODEX-SESSION-WATCH.md` for visibility outside that
conversation; it diagnoses lack of observable progress, not a network root cause.


## 2026-09-23：失败复用、草稿编辑及通信恢复

`planner_efficiency.py` 对确定性 RED 做同上下文复用：身份绑定冻结 run（含环境/实现）、旧/新 graph+proofs+accepted_order、imports、两级检查 timeout 与增量模式。只有历史 journal 绑定的 proposal/feedback 可被读取；timeout、worker故障、GREEN均不进入此复用。反馈保留原check与reused_request，当前步骤仍收费记账，但不会执行native检查或产生GREEN。`retry_check` 非空理由可显式重试。旧记录没有 identity 时不自动猜测缓存资格。

`submit_batch.proof_edits` 支持以历史 request_number/node_id/proof_sha256 选择草稿，顺序执行old/new唯一匹配替换。展开后保存完整proposal和原始model-proposal，正常进行命令安全、声明、DAG及完整证明检查。不会缓存任意局部证明状态或将completed_uncommitted当作GREEN。真正需要独立保存的子证明仍通过有完整声明/上下文的helper节点；Isar命名have/show可以用于组织单节点内部证明。

`next_proof_observation` 自动投影最近候选hash、失败命令、最多16000字符的小型完整诊断目标。被截断的诊断绝不标记为完整；较大状态继续用带request身份的读取接口。`repeated_failed_routes` 提供最近重复路线的请求索引，不增加尝试上限。

模型调用保留原有最多两次尝试；能由meta/stderr确认的timeout或传输中断在重试前退避10秒，连续失败后记录recoverable provider_transport暂停，保存所有已计费请求、部分输出和GREEN。再次运行同一冻结run时继续计数。非传输异常仍报infrastructure_error。暂停不是自动无限重试；runner状态和execution-stop明确展示原因。

验证产物：reports/analysis/file-planner-efficiency-20260923。历史回放与新实验隔离；读取历史候选的监督程序只做机械编辑还原，不向新模型提供VERO特定证明。


## 2026-09-23：多结论节点的封存审计

一个命名lemma可以包含多个shows结论，节点仍表示完整声明及其全部定理。两个审计ML生成器统一使用Proof_Context.get_thms，等待全部目标完成后对完整列表计算Thm_Deps.thm_deps与has_skip_proof，拒绝空列表。不能用get_thm或hd丢弃后续结论。依赖输出仍保留canonical_name与fact_index，现有节点级汇总格式不变。

对应历史故障为stateprimitives-astra-efficiency-20260923第84步semantic-audit；修复不增加File Planner权限、不修改任何GREEN命题或证明。验证记录位于reports/analysis/seal-multifact-fix-20260923；合成负例的sorry只存在隔离测试theory，生产执行仍禁止。


## 2026-09-23：验收证据与工具恢复

v2审计证据、term查询能力和紧凑反馈规则见上文。底层worker清理不会因stop文件写入失败而跳过进程回收；若容器清理失败，保留atexit注册与重试能力。provider仅对已识别的瞬态故障自动退避重试一次，其他错误保持原始诊断、已登记费用与GREEN状态，进入既有故障处理，不无条件再次发送。

验证包含已知故障步骤和seed=20260923的随机历史步骤，原证明/上下文保持相同；原生回放、故障注入与模型决策重跑分别标识。详细报告：`reports/analysis/harness-reliability-20260923/REPORT.md`。
