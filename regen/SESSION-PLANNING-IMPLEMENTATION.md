# Repo / Session 级规划：实现说明

本文档对应总 harness 的第 2 个模块。它说明 Session Planner 的需求、输入边界、
规划规则、artifact bundle、runner 绑定和当前生产结果。总体流程见
`REPO-LEVEL-PROOF-HARNESS.md`。

## 1. 实现目标

Session Planner 只解决一个问题：

> 在固定 Refine contract-owning theory slots 的条件下，给出完整、可执行、可审计的
> theory 生成顺序和 session 进度表。

它不设计 theorem statements、helpers、proof bodies、imports 或 automation
attributes，也不根据 Premise Catalog 的具体 records 猜测 theorem 依赖。文件槽和
contract ownership 已固定，所以该模块是 dependency-aware generation scheduling，
不是 session architecture synthesis。

## 2. 输入边界

Session Planner 的合法输入：

- verified environment gate；
- 21 个 public contract packages；
- 287 个 exact contract statements；
- theory/file slot 与 contract IDs；
- Premise Catalog snapshot identity 和 record count；
- frozen scheduling policy 与 budget。

禁止输入：

- raw human Refine theory source；
- human imports 或 theory DAG；
- human helper names、statements、roles、order 或 proof decomposition；
- individual premise records、retrieval results 或 statistics；
- stock human Refine heap；
- human comparison result 或旧 campaign schedule。

Premise Catalog 在这里仅用于确认生成阶段将拥有一个合法 fact service。具体事实在
单 node proof 时检索，不用于安排文件顺序。

代码定位：

- [gate and policy validation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:48)
- [generation task extraction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:90)
- [planner payload](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:139)
- [prompt construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:164)
- [frozen policy](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/config/session-planning-policy.json)

## 3. LLM 与确定性代码分工

### 3.1 LLM 只提出 candidate edges

生产调用使用 audited `gpt-5.6-sol`。这是官方 GPT-5.6 flagship model ID；
`codex-5.6` 不是可用模型 ID，曾导致一次 400 provider failure。模型调用在只读、
无工具、无插件、无 repo source 的 clean runtime 中运行。

模型输出只包含两类候选：

- `contract-composition`：较小 correspondence 可以组合进较大 correspondence；
- `operation-call`：contracts 表面显示 callee 应优先于 caller。

每条候选必须列出 provider、consumer、edge type、confidence、contract IDs、
shared symbols 和 reason。

### 3.2 Validator 决定候选是否合法

Validator 检查：

- endpoint 必须是当前 21 个 theory slots；
- provider 与 consumer 不能相同；
- citations 必须覆盖两端且不能引用第三个 theory；
- shared symbol 必须作为完整 token 出现在两端 cited contracts；
- symbol 不能只是 `corres`、`lemma` 等 generic token；
- confidence、edge type、schema 和总数必须满足 frozen policy；
- 重复边只能保留一条。

模型不能决定最终 schedule。无证据或低质量边被记录为 rejected evidence。

代码定位：

- [model JSON parser](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:204)
- [symbol grounding](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:218)
- [candidate validator](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:229)

## 4. 分层与顺序规则

两种 edge 的语义刻意不同：

- accepted `contract-composition` 是 strong edge，决定 layer depth；
- accepted `operation-call` 是 weak edge，只决定同层 serial priority。

strong graph 或 weak graph 出现 cycle 时，deterministic code 计算 strongly connected
components，将 cycle 折叠并在 evidence 中披露。没有 strong edge 时，所有 theories
处于同一层；这表示 contract 表面没有给出足够强的 composition evidence，不表示
theories 独立。

每一层内部先遵守 weak edges，再用确定性 tie-break 形成 serial fallback。同层可以
在未来并行，但 runner 首先按 serial fallback 运行，以验证资源和跨文件发布行为。

代码定位：

- [SCC computation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:339)
- [layer depth](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:382)
- [weak ordering](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:408)
- [layer construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:439)

## 5. Transcript 生命周期

一次逻辑 Session Planner 调用可以有多个物理 provider attempts，例如 model name
错误、网络失败或 timeout。每个物理 attempt 使用独立 prefix：

```text
session-planner
session-planner--call-001
session-planner--call-002
```

失败 transcript 永不覆盖。Planner 只复用最新一个满足以下条件的 attempt：

- provider call 完成；
- transcript 可封存且没有 tool events；
- exact prompt 匹配；
- model 与 frozen policy 匹配。

如果没有合格 attempt，`--allow-external-call` 才会分配新的 prefix。这样一个逻辑
decision 不会被计成多个成功 decisions，但所有失败原因仍保留用于审计。

代码定位：

- [prefix allocation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/llm_transcript.py:47)
- [transcript reconciliation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/llm_transcript.py:87)
- [completed transcript selection](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:68)
- [audited artifact validation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/llm_transcript.py:465)
- [planner entry point](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:736)

## 6. 输出 bundle

Session Planning 生成五类证据：

1. `dependency-evidence.json`
   保存 accepted/rejected candidates、strong/weak edges、SCC 与解释。
2. `refine-file-generation-list.json`
   保存 immutable layers、21 个 exact slots、contract IDs 和 serial fallback。
3. `session-planning-audit.json`
   检查 gate、policy、coverage、model、provider 与 transcript sealability。
4. `session-progress-ledger.json`
   初始化每个 theory 的 `pending` 状态，并作为 runner 唯一可更新的 canonical
   session ledger；记录 generation IDs、状态和真实 theory seal ID。
5. `transcript/session-planner.*`
   保存 exact prompt、raw provider stream、final JSON、stderr、metadata 与 tool audit。

整个 bundle 绑定 campaign、environment gate、contract package payload、policy、
planner input、proposal、dependency evidence 和 final file list。

代码定位：

- [progress ledger](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:485)
- [artifact construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:516)
- [file-list validation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:608)
- [bundle validation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:653)
- [immutable artifact writing](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/session_planner.py:693)

## 7. Runner 绑定

Runner 不接受一个孤立的 schedule 文件。初始化时它：

- 验证 environment gate；
- 验证 file list 的 sibling dependency evidence 与 planning audit；
- 检查 campaign、gate、package payload、policy 和 artifact identities；
- 逐个比对 21 个 file slots 与 287 个 contract IDs；
- 绑定 Semantic Base、Premise Catalog、sanitized runtime 与 contract package path；
- 原子更新 Session Planner 创建的 canonical progress ledger，不创建 `files.json`；
- 只把真实 theory seal 存在的 theory 标为 `sealed`，之后才发布 generated premises；
- `run-session` 按 immutable serial fallback 遍历，单 theory 失败后继续，最终产生
  `partial` 或 `complete` session 状态。

任何来自旧 campaign 或另一 package set 的 list 都会被拒绝。Planner 缺失时 runner
也不会按文件名自行猜顺序。

代码定位：

- [schedule entries](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/proof_session_runner.py:351)
- [schedule binding](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/proof_session_runner.py:385)
- [run initialization](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/proof_session_runner.py:412)

## 8. 当前证据状态

下面的 `d6c...` planning bundle 是旧 environment gate 下得到的历史规划结果。
其 contract-only edge 分析仍可评价，但由于上游 disclosure 是 pending，它不能继续
作为新 runner 的 canonical production input。Successor environment gate 发布后必须
重新运行 Session Planner，并由 runner 绑定新的 canonical ledger。

历史结果：

```text
campaign             sel4-refine-semantic-base-d6c26eb4fae33cab
environment gate     environment:adf1ce7e8f207b0d5cdc
model                gpt-5.6-sol
theory slots         21
contracts            287
candidate edges      17
accepted edges       14
rejected edges       3
strong edges         0
weak edges           14
layers               1
composition SCCs     0
operation SCCs       0
audit                PASS
replay               reconstructed-and-unchanged
downstream smoke     ready
```

14 条 accepted edges 都是 weak `operation-call` evidence。3 条 rejection 分别是两条
duplicate edge 和一条缺少两端 shared endpoint symbol 的 edge。模型明确说明未发现
足以声明 proof composition dependency 的 contract-only evidence，所以结果只有一层。

这是一份合法且保守的 schedule，但不是完整 theory dependency DAG。当前结论是：
仅凭 fixed contract surfaces 能稳定找到一部分 callee-before-caller priority，却不足以
恢复强分层关系。后续 theory generation 可使用 serial fallback；是否增强 Session
Planner 的输入或强边推断，应根据多个 theory 的实际 premise availability 和失败数据
再评估，不能回读 human import graph 来补答案。

当前 serial fallback：

```text
CSpace1_R
TcbAcc_R
ArchAcc_R
Interrupt_R
KHeap_R
Schedule_R
Syscall_R
Tcb_R
Arch_R
Bits_R
CNodeInv_R
CSpace_R
Detype_R
Finalise_R
InterruptAcc_R
IpcCancel_R
Ipc_R
Refine
Retype_R
Untyped_R
VSpace_R
```

生产 artifacts：

[active Session Planning bundle](/home/lijun/seL4-docker-main/reports/regen/refine-semantic-base/sel4-refine-semantic-base-d6c26eb4fae33cab/Session-Planning)

## 9. 验证与测试

主要测试：

- [Session Planner rules](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/scripts/test_session_planner.py)
- [environment and runner binding](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/scripts/test_environment_gate.py)

已验证：

- planner rule、ledger binding 与 continue-after-failure 回归测试通过；
- 历史 prompt/model/provider transcript 可封存，14/3 edge decision 可重放；
- 新 runner 已拒绝第二份 `files.json` 生命周期并绑定 canonical ledger；
- expanded environment gate 的 successor production replay 尚未完成；
- 旧 schedule/package/gate mismatch 会被拒绝。

## 10. 当前边界

- contract-only input 没有产生 strong composition edges；
- 同层不代表可立即安全并行；
- Session Planner 不管理 helper reuse 或 imports；
- session progress ledger 由 Session Planner 创建，runner `init` 绑定 run ID，之后
  `plan-file/run-file/run-session` 持续原子更新同一 artifact；
- 当前生产结果只覆盖 Refine，CRefine 必须等待完整 generated Refine seal。
