# 环境准备与冻结：实现说明

本文档对应总 harness 的第 1 个模块。它用于快速定位环境构造的需求、实现路径、
关键 gate 和生产证据。总体模块关系见 `REPO-LEVEL-PROOF-HARNESS.md`。

## 1. 实现目标

环境模块需要同时满足四个要求：

1. 固定本轮实验真正使用的 repository、l4v、policy、模型和预算；
2. 给模型公开 exact contracts，但不公开原始人类 proof body、helper 分解或 import graph；
3. 构建一个 Isabelle 可以实际加载的 Semantic Base，而不是只生成 JSON 声明；
4. 给下游提供一个唯一 gate，绑定 Semantic Base、contract packages 和 Premise Catalog。

实验不要求把 retained semantic command 内部的所有 certificate 重新证明。普通
refinement theorem/helper layer 被删除；必要 semantic commands 原样保留，其产生
的 facts 和 automation effects单独披露并在生成完成后统计实际使用。

## 2. 输入与信任边界

可信准备阶段可以读取：

- 原始 41 个 Refine theory sources；
- frozen parents、specification、libraries 与 tools；
- campaign config 与 contract selection policy；
- l4v ROOT/session metadata。

模型和下游 proof 模块只能得到：

- 21 个 public contract packages；
- sealed Semantic Base heap/workspace；
- verified Premise Catalog；
- environment gate。

sanitized runtime 删除原始 target Refine theories，只保留 `proof/refine/base` 和
生成环境需要的固定上游内容。原始 sources 仍可供可信 extraction 和独立 human
comparison 使用，但不进入模型运行环境。

## 3. 实现流程

### 3.1 Freeze campaign

Environment Freeze 从 campaign config 构造稳定 manifest，记录代码版本、l4v
版本、冻结输入文件、策略、模型和预算。`--verify` 会重新计算所有冻结输入并拒绝
drift。

代码定位：

- [environment_freeze.py](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_freeze.py:26)
- [refine_campaign.py](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/refine_campaign.py)
- [campaign config](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/config/refine-semantic-base-campaign.json)

输出：`Environment-Freeze/campaign-manifest.json`。

### 3.2 Extract contracts and inventory commands

Contract Extractor 识别 fixed contracts，保留 contract name、精确 proposition、
contract-owned attributes 和最小 enclosing context。它删除 proof body 与 proof
context hints，并生成公开 package。

Command Inventory 对每个识别出的 declaration/structure owner span 记录 occurrence
ID、source order、kind、source span、role 与 proof-syntax 标记。这里的 7,102 个
span 是顶层 declaration/structure owner spans，不是每一个 `by/apply/qed` token。

代码定位：

- [contract extraction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/contract_extractor.py:388)
- [public package construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/contract_extractor.py:451)
- [command inventory](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/contract_command_inventory.py)
- [inventory entry point](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_preparation.py:109)

输出目录：`Contract-Command-Inventory/`。

注意：`environment_preparation.py inventory --output` 接收模块目录，不是最终 JSON
文件名。canonical private manifest 位于
`Contract-Command-Inventory/private/command-inventory.json`。

### 3.3 Select retained semantic commands

生产 baseline 使用确定性的 semantic-core seed：

- 删除 ordinary proof commands；
- 删除 59 个 mixed/certificate candidates；
- 保留 500 个 semantic-core commands；
- 要求该选择与全部 287 contract shells 一起 Isabelle GREEN。

逐 command fixed-point counterfactual 仍保留为可选 minimality diagnostic，但不是
当前 production gate。baseline seal 明确记录
`fixed_point_complete=false` 和 `command_minimality_claimed=false`，不能把可用
Semantic Base 表述为 command-minimal。

代码定位：

- [baseline acceptance](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/command_necessity.py:159)
- [optional fixed point](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/command_necessity.py:48)
- [decision classification](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/command_necessity.py:28)

输出目录：`Command-Selection/`。

注意：`command_necessity.py --output` 同样接收模块目录，canonical ledger 位于
`Command-Selection/command-necessity-ledger.json`。

### 3.4 Materialize sanitized runtime and Semantic Base

Runtime materializer 复制 l4v 的合法部分，删除目标 Refine proof source，并记录
删除项与 overlay identity。Builder 按 source order 重新渲染 41 个 theory slots，
blank 被删除 command 的字符以维持 source location，清理空 scopes，并生成 21 个
checker theories，合计 287 个 exact contract shells。

每个 shell 只检查 declaration 能否在 Semantic Base 中 parse/elaborate，结尾使用
`oops`，因此不会发布 contract theorem。Isabelle build 使用网络关闭的 container，
显式记录 image identity、完整 build command、log、heap 和 wall time。

代码定位：

- [sanitized runtime](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_preparation.py:31)
- [initial retained set](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/semantic_base_builder.py:80)
- [theory rendering](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/semantic_base_builder.py:116)
- [workspace and shells](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/semantic_base_builder.py:202)
- [Isabelle build](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/semantic_base_builder.py:293)

输出：`Runtime/` 与 `Semantic-Base/`。

### 3.5 Seal Semantic Base and disclose effects

Semantic Base seal 重新检查 campaign、inventory、selection ledger、workspace files、
sanitized runtime、heap identity 和 GREEN build。Seal 不只相信 runtime manifest：
它独立扫描实际 `Runtime/l4v/proof/refine/`，并重新核对 campaign ID、seal ID、
base session 与 base import；发现原始 target Refine theory 即拒绝。

Context Effect Disclosure 是环境硬门和后续评估接口。它必须由 sealed Semantic
Base 的真实 Isabelle context 导出并达到 `status=complete`，覆盖全部 facts、
`simp/wp/cong/weak-cong`、classical intro/elim 以及可发现的 named theorem
collections；pending 或覆盖不全时 environment gate 必须拒绝 proof generation。
它不反向改变 command selection。Generated proofs 实际使用哪些 retained effects，
仍在每个 theory 完成后由 proof-term dependency audit 判定。

代码定位：

- [seal construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_gate.py:105)
- [workspace checks](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_gate.py:95)
- [seal writer](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_gate.py:341)
- [functional seal CLI](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_preparation.py:124)
- [generated-proof impact audit](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/retained_semantic_impact.py)

### 3.6 Export and publish Premise Catalog

Premise Export 在 sealed Semantic Base 上运行 Isabelle，导出 facts 与
`simp/wp/cong` 等 automation memberships。Records 区分 `frozen_parent` 与
`retained_semantic_export`，并保留 owner、statement、attributes 和 provenance。

Premise Catalog 从 raw records 构造 snapshot 与 SQLite database。`--verify` 会从
snapshot records 重建并比较 schema 的全部 13 列，包括 declaration/fact identity、
normalized statement hash、definition equations、publication state 与 provenance，
而不是只比较名称和 statement。

代码定位：

- [Isabelle exporter](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/premise_export.py:126)
- [record parser](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/premise_export.py:212)
- [snapshot construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/premise_catalog.py:87)
- [database writer](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/premise_catalog.py:123)
- [catalog verifier](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/premise_catalog.py:139)

输出：`Premise-Catalog/`。

### 3.7 Publish the downstream gate

最终 gate 绑定：

- campaign manifest；
- Semantic Base seal/heap/workspace；
- 21 个 public contract packages 与 287 contracts；
- Premise Catalog snapshot/database；
- sanitized runtime identity；
- completed Context Effect Disclosure 及其 exact raw-export hashes。

Runner 每次初始化都重新验证这些引用。只有
`proof_generation_allowed=true` 才允许进入 Session Planning 或 theory generation。

代码定位：

- [gate construction](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_gate.py:233)
- [gate validation](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_gate.py:302)
- [functional gate CLI](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/environment_preparation.py:137)
- [runner environment binding](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/proof_session_runner.py:294)

## 4. 当前证据状态

历史 campaign `sel4-refine-semantic-base-d6c26eb4fae33cab` 的 Semantic Base、
contract shells 和 Premise Catalog 数据仍可作为 baseline evidence，但它的
`context-effect-disclosure.json` 是 `pending`。在本次 gate 收紧后，该 campaign
不再是允许 proof generation 的 canonical environment；必须由 successor campaign
重新发布 completed disclosure、environment gate 和 Session Planning bundle。

旧 baseline 数据：

```text
campaign                 sel4-refine-semantic-base-d6c26eb4fae33cab
theories                 41
command owner spans      7102
fixed contracts          287
contract-owning slots    21
retained commands        500
group-removed commands   59
contract shells          287 GREEN
Semantic Base seal       semantic-base:78bbc099957cdc0adc04
Premise records          84219
Premise snapshot         premise-snapshot:f6d61b1ccffc07e0a0ad
Environment gate         environment:adf1ce7e8f207b0d5cdc
```

Premise records 分布：

- 83,145 条 `frozen_parent`；
- 1,074 条 `retained_semantic_export`。

旧实现曾让该环境通过 gate replay 并初始化 downstream smoke；新 verifier 会因
disclosure pending 明确拒绝它。扩展 exporter 已在该 sealed Semantic Base 的隔离
临时副本上真实 Isabelle PASS，导出 84,219 facts、13,497 simp rows、4,470 wp rows、
116 weak-cong rows及 89 个 named collections。该 smoke 验证实现能力，但不替代
successor campaign 的 immutable artifacts。

生产 artifacts：

[active environment artifacts](/home/lijun/seL4-docker-main/reports/regen/refine-semantic-base/sel4-refine-semantic-base-d6c26eb4fae33cab)

## 5. 验证与测试

主要测试：

- [contract extractor tests](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/scripts/test_contract_extractor.py)
- [environment preparation tests](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/scripts/test_environment_preparation.py)
- [environment gate tests](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/scripts/test_environment_gate.py)
- [premise export tests](/home/lijun/seL4-docker-main/.worktrees/regen-refine-semantic-base/regen/scripts/test_premise_export.py)

环境与 Session 相关的新增负向回归测试已通过，expanded exporter 也已真实 Isabelle
PASS。完整 successor campaign 尚未重放，因此当前不能宣称新 environment gate
production PASS。

## 6. 已知边界

- 当前 Semantic Base 是 contract-sufficient baseline，不声明 command-minimal；
- retained semantic commands 可能导出少量 human-certified proof power；
- Context Effect Disclosure 负责披露，theory seals 负责统计实际使用；
- public contract package 可包含净化后的最小 enclosing context opener，但不包含
  human proof setup；
- hash 和 ID 用于绑定同一 campaign 的输入输出与避免误接旧 artifacts，不作为
  对抗性安全模型。
