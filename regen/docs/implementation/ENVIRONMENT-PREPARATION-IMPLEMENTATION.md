# 环境准备与冻结：实现说明

状态：基于本分支 Semantic Base 代码的静态核对；本次未重新运行 Isabelle 或 campaign。
目标设计见 [总体设计](../design/REPO-LEVEL-PROOF-HARNESS.md)，总体入口见 [README](../../README.md)。
历史运行数字统一保存在 [实验记录](../history/REFINE-CREFINE-EXPERIMENT.md)，不作为当前分支已通过验收的证据。

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
facts 的使用在 theory 完成时按当前代码审计，不能把 pool membership 本身当作实际使用。
目标政策不要求完整恢复 `simp/wp` 等 tactic 的全部 premise 引用；现有审计的阻断条件与
该政策是否一致仍需按代码核对，不在本次直接移除审计。

## 3. 代码定位

以下均为本分支相对路径；使用函数名定位，避免旧 worktree 绝对路径和易漂移的行号。

| 责任 | 文件与入口 |
|---|---|
| 冻结 | [environment_freeze.py](../../preparation/environment_freeze.py) `main()`；[refine_campaign.py](../../preparation/refine_campaign.py) |
| 配置 | [refine-semantic-base-campaign.json](../../config/refine-semantic-base-campaign.json) |
| 提取目标 | [contract_extractor.py](../../preparation/contract_extractor.py) `extract_theory()`、`public_package()` |
| 声明片段裁剪 | [retrieval_utils.py](../../infrastructure/retrieval_utils.py) `declaration_statement()`，Contract Extractor 与旧分析入口共用 |
| Selector | [regen_extract.py](../../preparation/regen_extract.py) `is_contract()` |
| Command 分组 | [contract_command_inventory.py](../../preparation/contract_command_inventory.py) `command_occurrences()`、`build_inventory()` |
| 默认 core | [semantic_base_builder.py](../../preparation/semantic_base_builder.py) `initial_retained_command_ids()` |
| 渲染与 shells | [semantic_base_builder.py](../../preparation/semantic_base_builder.py) `render_theory()`、`materialize_workspace()` |
| Isabelle build | [semantic_base_builder.py](../../preparation/semantic_base_builder.py) `run_isabelle_build()` |
| Baseline / 反事实 | [command_necessity.py](../../preparation/command_necessity.py) `accept_green_baseline()`、`necessity_fixed_point()`、`decide_counterfactual()` |
| Runtime 隔离 | [environment_preparation.py](../../preparation/environment_preparation.py) `materialize_sanitized_l4v()` |
| Semantic Base seal | [environment_gate.py](../../preparation/environment_gate.py) `build_semantic_base_seal()`、`validate_semantic_base_seal()` |
| Context export | [premise_export.py](../../preparation/premise_export.py) `run_export()`、`parse_fact_records()`、`build_context_effect_disclosure()` |
| Catalog | [premise_catalog.py](../../preparation/premise_catalog.py) `build_snapshot()`、`write_database()`、`verify_catalog()` |
| Downstream gate | [environment_gate.py](../../preparation/environment_gate.py) `build_environment_gate()`、`validate_environment_gate()` |
| Runner 绑定 | [proof_session_runner.py](../../execution/proof_session_runner.py) `environment_context()`、`thin_environment_gate()` |
| Retained impact | [retained_semantic_impact.py](../../analysis/retained_semantic_impact.py) `audit_theory()` |

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

- [test_contract_extractor.py](../../tests/current/test_contract_extractor.py)
- [test_environment_preparation.py](../../tests/current/test_environment_preparation.py)
- [test_environment_gate.py](../../tests/current/test_environment_gate.py)
- [test_premise_export.py](../../tests/current/test_premise_export.py)

静态入口核对和结构回归不继承历史 campaign PASS。当前代码中的 gate 绑定检查、目标词法提取
和拟议的最终 typed contract identity 验证不是同一件事；是否满足全部目标合同须通过新的验证。

声明片段裁剪已合并为共享函数；其词法行为未变，不能代替 Isabelle parsing。该模块已纳入
`frozen_input_files`，新 campaign 需冻结更新后的代码与文档；已有 campaign manifests 不改写。

## 5. 统一准备入口、下游数据流与恢复

当前统一入口为 `environment_preparation.py prepare`。它复用现有 producer、seal 和 gate，
将阶段串成可恢复流水线；不增加第二套 contract selector 或 premise catalog。
单独的 `environment_freeze.py` 仍只生成／验证 manifest，旧的细粒度子命令也仍可用，
但它们不自动纳入新流水线的检查点日志。需要恢复能力时使用下面的统一入口。

从仓库根目录执行；`--environment-root` 必须是新的空目录，路径在初始化后固定：

```bash
python3 -m regen.preparation.environment_preparation prepare \
  --repo . \
  --config regen/config/refine-semantic-base-campaign.json \
  --environment-root reports/regen/environment-run-001
```

当前实现范围是 Semantic-Base / Refine / ARM；其他范围明确报错。完整准备需要本工作区的
`verification/l4v`、`verification/isabelle` 和可用的本地 Docker image。如需要已生成规格补充，
通过 `--generated-overlay <目录>` 指定；overlay 的内容也绑定到初始化记录。

| 顺序 | 阶段 | 输出与交接 |
|---|---|---|
| 1 | `freeze` | `Environment-Freeze/campaign-manifest.json`、`config.json` 和 `frozen-inputs/` 的实际文件副本 |
| 2 | `inventory` | private command inventory 与 public contract packages |
| 3 | `runtime` | `Runtime/l4v/` 和 sanitized manifest |
| 4 | `toolchain` | `Toolchain/identity.json`，固定 Docker image ID 和 Isabelle 安装内容身份 |
| 5 | `build` | `Semantic-Base/workspace/`、`heaps/`、`build-result.json` 与真实 build log |
| 6 | `selection` | 基于通过的 exact-shell baseline 生成 `Command-Selection/command-necessity-ledger.json` |
| 7 | `seal` | `Semantic-Base/semantic-base-seal.json` |
| 8 | `export` | `Premise-Export/` 中的 raw exports、records、disclosure 和 export log |
| 9 | `catalog` | `Premise-Catalog/premise-snapshot.json` 与 `premises.sqlite3` |
| 10 | `gate` | `Environment-Preparation/environment-gate.json`，兼容现有 Runner 的默认入口 |
| 11 | `dataflow` | `Environment-Dataflow/environment-index.json` 及公开数据流 |

`--until freeze`、`--until inventory` 等可在指定阶段后停止。只有全部阶段完成且数据流再次
校验通过，统一入口才返回 `ready=true`。Baseline 失败或导出失败时保留诊断，不继续发布 gate。
初始化绑定实际 l4v 内容，包括非 Git 跟踪文件；消耗原始输入的阶段完成后再次检查身份。
构建和导出使用固定 image ID，并检查 Isabelle 内容未漂移。

### 下游数据接口

统一索引记录 schema、campaign/gate/dataflow 身份、记录数量，以及相对环境根目录的文件路径、
SHA-256 和字节数。模型侧只需公开数据；gate、runtime 路径与恢复日志由可信运行器处理。

| 文件 | 消费方式 |
|---|---|
| `contracts.jsonl` | 每行一个冻结 contract，保留 exact statement、context、attributes 和来源 owner；明确 `proof_status=unproved`、`usable_as_premise=false` |
| `premises.jsonl` | 每行一个合法初始 premise，保留稳定 ID、名称、陈述、source class、attributes 和已有定义方程字段；绑定 gate，标为可用 |
| `owners.json` | 原 contract owner 的分组索引，方便按目标聚合；不限制最终生成 theory 划分 |

输出使用字段白名单，不包含 private inventory、被删除 helper、原证明 imports、source locations
或恢复路径。它是已有合法记录的确定性投影，**尚未实现 Planning Substrate 的符号解析与语义关联**，
仓库规划已接入这些数据流；File Planner 消费生成文件投影仍待实现。下游可以通过
`environment_dataflow.validate_dataflow(root, gate_path, index_path)` 验证 gate 和逐条数据的一致性。

### 复用上游 heap

`prepare --upstream-heaps <目录>` 可指定已有的合法上游缓存。目录应保留
`polyml-5.9.1_x86_64_32-linux/<session>` 和 `log/<session>.db` 布局。
[upstream_heaps.py](../../preparation/upstream_heaps.py) 对当前 Refine/ARM 的上游 session
使用明确白名单；拒绝目标 Refine/CRefine、后代、未知文件、符号链接和没有构建数据库的 heap。
本接口不是任意 session 的通用依赖发现器；扩展 setting 时需要更新白名单。

初始化 recipe 绑定缓存来源路径及逐文件哈希/大小；freeze 将缓存复制到
`Environment-Freeze/upstream-heaps/` 并建立检查点，build 从冻结副本初始化工作 heap。
原缓存不被构建进程修改。Isabelle 仍执行普通增量 build，验证源文件、选项和父依赖，
失效缓存仍会重建；存在 heap 不等于通过当前环境验证。未提供此参数时从空目录构建。
冻结后恢复不依赖原缓存目录或原容器。更改缓存接入代码后应创建新环境，不能覆盖旧 manifest。

2026-09-08 的实际运行先用净化 runtime 对缓存执行 `build -n -b BaseRefine`，返回 0；
随后 `live-environment-002` 直接构建 Semantic_Base（33 分 26 秒通过），没有重建其父 session。
Premise 导出用时 2 分 52 秒；完整环境 gate/dataflow 已通过，输出 287 个 contracts、21 个原 owner 分组及 84,281 条合法 premise 记录。
前次 `live-environment-001` 已按用户要求停止并删除未完成 build 输出，诊断及已完成检查点保留。

### 日志与恢复规则

可信准备目录 `.preparation/` 权限为 `0700`，包括：

- `events.jsonl`：初始化 recipe、阶段开始／完成／失败、错误类型与诊断、检查点引用及恢复事件。
  事件带顺序、前序哈希和自身哈希，追加时 flush/fsync。
- `checkpoints/`：已完成阶段的实际文件副本及逐文件哈希、模式与目录清单；覆盖 runtime、workspace、
  heaps、catalog、gate 和公开数据流。不是只有文件名或哈希的“恢复计划”。
- `incomplete/`：重试或显式恢复时移入的中断／漂移产物，保留原 build log，不覆盖旧失败证据。

日志与检查点共同恢复；单个文本日志不能凭空恢复缺失文件。检查点保留完整文件副本，会占用
与产物相应的额外磁盘空间。私有 inventory 和冻结输入副本也在恢复范围内，整个 `.preparation/`
仅供可信 preparation，不能提供给证明 agent。

```bash
# 核对已完成阶段，补回缺失产物，从第一个未完成阶段继续
python3 -m regen.preparation.environment_preparation resume \
  --environment-root reports/regen/environment-run-001

# 不调用模型、Docker 或 Isabelle；从检查点恢复已完成产物
# 已漂移的普通文件／目录先保留到 incomplete/，再恢复
python3 -m regen.preparation.environment_preparation restore \
  --environment-root reports/regen/environment-run-001

# 单独验证下游数据流与环境 gate
python3 -m regen.preparation.environment_preparation verify-dataflow \
  --environment-root reports/regen/environment-run-001
```

恢复时先验证检查点内容；缺失或损坏则明确失败。未完成阶段不能根据残留文件推断通过，
续跑会保留残留产物后重新执行该阶段。普通 resume 遇到已有文件漂移会停止，显式 restore
才进行上述恢复。截断的最后一条日志可由 restore 保存后裁去；完整记录哈希错误不会自动修补。
运行级文件锁防止两个准备进程并发修改同一环境。

已完成环境的产物可在不读取原始证明仓库的情况下恢复，并重新通过 gate/dataflow 校验。
尚未完成的步骤要续跑，仍需匹配冻结身份的原始输入、当前 harness 与外部工具链。当前恢复
限定原环境绝对路径；不重建 Docker image、Isabelle 安装或 Git checkout，也不把恢复等同于
重新执行真实 prover。源目录／检查点中的符号链接明确拒绝，避免恢复时引入外部文件别名；
Isabelle 工具链允许指向普通文件的链接并记录目标内容，目录链接须先物化。

实现见 [environment_pipeline.py](../../preparation/environment_pipeline.py)、
[environment_journal.py](../../preparation/environment_journal.py)、
[environment_dataflow.py](../../preparation/environment_dataflow.py)。
新增 [test_environment_pipeline.py](../../tests/current/test_environment_pipeline.py) 覆盖真实本地 producer
到 gate 的交接、无源恢复、中断重试、身份漂移、检查点损坏、日志截断、锁和公开数据隔离；
外部 Docker/Isabelle 调用使用测试替身，不声称真实 Semantic Base 已构建通过。
