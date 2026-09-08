# 环境准备与冻结：实现说明

状态：基于本分支 Semantic Base 代码的静态核对；本次未重新运行 Isabelle 或 campaign。
目标设计见 [Harness Design](UNIFIED-PROOF-HARNESS-DESIGN.md)，总体入口见 [README](README.md)。
历史运行数字统一保存在 [实验记录](REFINE-CREFINE-EXPERIMENT.md)，不作为当前分支已通过验收的证据。

## 1. 实际输入与保留边界

可信 preparation 读取指定架构下属于 Refine session 的原始 theories、ROOT 元数据、父 sessions、
程序／规格和冻结配置。目标选择与删除范围分开：selector 选出最终 contracts，但整个 Refine
scope 都进行 command 级处理，没有“无 contract 的 theory 整体保留”的规则。

Public contract packages 保留目标名称、净化后的 statement、contract-owned attributes、必要
context 和 owner 分组。Python 提取结果是文本接口，不应称为 Isabelle internal proposition。
原始 source locations 和完整 command spans 留在 private inventory，不提供给模型。

| 内容 | 默认处理 |
|---|---|
| Fixed contract | 原 command 从语义 theory 移除；目标陈述进入 public package 和独立诊断 shell |
| 普通 helper/theorem | statement、attributes 和 proof body 随 command span 一并移除 |
| 独立证明注册 | ordinary-proof 分类中的 `declare/notes/lemmas/crunch/method` 等移除 |
| Semantic core | 按 command 类型白名单选取；保留原文及导出的事实和 automation effects |
| Mixed/certificate candidates | 默认排除；不因名字看似必要就自动补回 |
| 结构 | 保留必要 scope，以及语义 theory 原 header/imports 和保留 command 的顺序 |
| Parents | Frozen 外部基础；不计为生成证明 |

Builder 的 internal-proof-syntax 检测会拒绝被检测出证明语法的 core span。这是当前词法检查，
不是“保留 command 不含任何人类认证能力”的证明。即使没有显式 proof body，语义 command
也可能导出可用于证明的 facts，其来源和使用需要单独披露。

原 imports 仍用于 Semantic Base 构建，public packages 不包含原 proof import graph。
不能将“模型 package 不含 imports”写成“准备过程不保留 imports”；模型和 proof 工具对语义
workspace 的可见边界仍是待明确项，不能仅凭 public package 推断全通道隔离。

## 2. 构建、选择与验收

```text
Environment Freeze
  -> Contract Command Inventory + public packages
  -> 固定 semantic-core selection
  -> sanitized runtime + Semantic Base + diagnostic shells
  -> Isabelle build / elaboration
  -> Semantic Base seal
  -> premise export + Context Effect Disclosure + catalog
  -> downstream environment gate
```

运行副本的 materializer 移除 `proof/refine` 下除 `base` 和 `README.md` 外的内容。原仓库不被
删除，只有可信 preparation 可以使用原始证明层；下游不得通过历史、缓存、其他副本或检索访问它。
该函数的扫描范围不应被夸大为已经验证全部可能的访问渠道。

默认从 `SEMANTIC_CORE_COMMANDS` 选择 semantic candidates；不在其中的 mixed/certificate
候选不进入 baseline。`command_necessity.py` 支持两种不同证据：

- 整体 baseline 验收：语义环境及所有 exact contract shells 构建通过；不声明逐 command 最小性。
- 可选删除反事实：移除一个 command 再构建，PASS 则可移除，语义／elaboration failure 则保留，
  timeout 或 infrastructure failure 记为 unresolved。

不能由 baseline PASS 推出每个保留 command 都不可删除，也不能由 shell PASS 推出原始目标已证。
诊断 shell 以 `oops` 结束，不发布目标 theorem。实际 theory/contract/command 数量来自本次
inventory，不把历史的 41/21/287/500/59 写成所有运行固定常量。

Context Effect Disclosure 从真实 Semantic Base context 导出 facts 和 automation pools；
environment gate 要求 disclosure 完整并绑定 raw exports 与 catalog。生成 proofs 对 retained
facts 的实际使用在 theory 完成时审计，不能把 pool membership 本身当作实际使用。

## 3. 代码定位

以下均为本分支相对路径；使用函数名定位，避免旧 worktree 绝对路径和易漂移的行号。

| 责任 | 文件与入口 |
|---|---|
| 冻结 | [environment_freeze.py](environment_freeze.py) `main()`；[refine_campaign.py](refine_campaign.py) |
| 配置 | [refine-semantic-base-campaign.json](config/refine-semantic-base-campaign.json) |
| 提取目标 | [contract_extractor.py](contract_extractor.py) `extract_theory()`、`public_package()` |
| Selector | [regen_extract.py](scripts/regen_extract.py) `is_contract()` |
| Command 分组 | [contract_command_inventory.py](contract_command_inventory.py) `command_occurrences()`、`build_inventory()` |
| 默认 core | [semantic_base_builder.py](semantic_base_builder.py) `initial_retained_command_ids()` |
| 渲染与 shells | [semantic_base_builder.py](semantic_base_builder.py) `render_theory()`、`materialize_workspace()` |
| Isabelle build | [semantic_base_builder.py](semantic_base_builder.py) `run_isabelle_build()` |
| Baseline / 反事实 | [command_necessity.py](command_necessity.py) `accept_green_baseline()`、`necessity_fixed_point()`、`decide_counterfactual()` |
| Runtime 隔离 | [environment_preparation.py](environment_preparation.py) `materialize_sanitized_l4v()` |
| Semantic Base seal | [environment_gate.py](environment_gate.py) `build_semantic_base_seal()`、`validate_semantic_base_seal()` |
| Context export | [premise_export.py](premise_export.py) `run_export()`、`parse_fact_records()`、`build_context_effect_disclosure()` |
| Catalog | [premise_catalog.py](premise_catalog.py) `build_snapshot()`、`write_database()`、`verify_catalog()` |
| Downstream gate | [environment_gate.py](environment_gate.py) `build_environment_gate()`、`validate_environment_gate()` |
| Runner 绑定 | [proof_session_runner.py](proof_session_runner.py) `environment_context()`、`thin_environment_gate()` |
| Retained impact | [retained_semantic_impact.py](retained_semantic_impact.py) `audit_theory()` |

`environment_preparation.py` 提供 inventory、runtime、build、seal 和 gate 等 CLI 子命令，实际参数
以其 `main()` 为准。模块输出通常位于 campaign 目录内：

| 模块目录 | 主要产物 |
|---|---|
| `Environment-Freeze/` | `campaign-manifest.json` |
| `Contract-Command-Inventory/` | `private/command-inventory.json`、`public/contract-packages/` |
| `Command-Selection/` | `command-necessity-ledger.json` |
| `Runtime/`、`Semantic-Base/` | sanitized l4v、语义 workspace、build 结果和 seal 引用 |
| `Premise-Catalog/` | raw exports、snapshot、SQLite、Context Effect Disclosure |

这些是相对 campaign 的产物路径，不是代码位置，也不保证当前 checkout 已有对应运行数据。
`inventory --output` 和 `command_necessity.py --output` 接收模块目录，不能传成最终 JSON 文件名。

## 4. 验证范围

相关测试入口：

- [test_contract_extractor.py](scripts/test_contract_extractor.py)
- [test_environment_preparation.py](scripts/test_environment_preparation.py)
- [test_environment_gate.py](scripts/test_environment_gate.py)
- [test_premise_export.py](scripts/test_premise_export.py)

本次只核对文档与静态入口，不继承历史测试 PASS。当前代码中的 gate 绑定检查、目标词法提取
和拟议的最终 typed contract identity 验证不是同一件事；是否满足全部目标合同须通过新的验证。
