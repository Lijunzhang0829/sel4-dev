# Semantic Base 上的仓库级证明生成

当前实现针对 Refine：删除普通人类证明层，在 command 级 Semantic Base 上生成固定 contracts。
环境数据流和仓库规划已接入，可输出生成 theory DAG、固定目标分配、共享辅助意图和文件投影。
File Planner / Runner 已接通投影消费、局部图及辅助节点失败撤销；完整仓库实验仍待执行。详细 setting、正确性与未决项
统一见 [总体设计](docs/design/REPO-LEVEL-PROOF-HARNESS.md)，本页只负责导航。

## 文档与代码入口

| 文档 | 唯一职责 | 主要代码 |
|---|---|---|
| [总体设计](docs/design/REPO-LEVEL-PROOF-HARNESS.md) | 研究目标、输入边界、生成合同、helper 政策与未决问题 | 目标设计，不声称全部实现 |
| [环境实现](docs/implementation/ENVIRONMENT-PREPARATION-IMPLEMENTATION.md) | command 选择、环境／catalog 构建与 gate | [environment_preparation.py](preparation/environment_preparation.py)、[semantic_base_builder.py](preparation/semantic_base_builder.py) |
| [规划实现与扩展](docs/implementation/REPOSITORY-PLANNING-IMPLEMENTATION.md) | 环境数据流到仓库方案、文件投影、重放与执行边界 | [repository_planner.py](planning/repository_planner.py) |
| [证明生成实现](docs/implementation/PROOF-GENERATION-IMPLEMENTATION.md) | node、GRA、组装、发布、日志与缺口 | [proof_session_runner.py](execution/proof_session_runner.py)、[isabelle_node_checker.py](execution/isabelle_node_checker.py) |
| [实验记录](docs/history/REFINE-CREFINE-EXPERIMENT.md) | 历史 campaign 结果，不作为当前 PASS | 运行数据需另行核对 |

[Kernel_Object_Access 单 theory 实验](docs/experiments/KERNEL-OBJECT-ACCESS-LAB.md) 仅完成交互式诊断及证明正确性检查，包含外层 Codex 直接编写 helper/证明，不满足无干预生成 setting。

[HARNESS-EVOLUTION.md](docs/history/HARNESS-EVOLUTION.md) 仅作历史追溯，旧路径与政策不作为现行入口。
代码链接相对当前文档；函数名用于定位。配置为
[refine-semantic-base-campaign.json](config/refine-semantic-base-campaign.json)。

新运行默认采用直接错误反馈及固定 GREEN 前缀复用；GREEN 仍须冷复核，最终 theory
验收不变。参见[单节点执行设计](docs/design/REPO-LEVEL-PROOF-HARNESS.md#单节点直接反馈与固定前缀检查)。

## 目录划分

| 目录 | 内容与边界 |
|---|---|
| `preparation/` | campaign 冻结、contract 提取、command 选择、Semantic Base 构建、premise 导出与环境 gate |
| `planning/` | Repository / File Planner、proof graph、premise 检索、automation 建议与局部 outline |
| `execution/` | Runner、Lemma Prover、Isabelle 检查、graph revision、组装诊断与可选 stepwise backend；当前 seal / publication 仍由 Runner 实现 |
| `analysis/` | retained semantic facts 的依赖与 automation 影响分析 |
| `infrastructure/` | 模型调用、transcript、模块日志、进度事件与共享文本处理 |
| `tools/` | 运行辅助入口、smoke / case 工具、stepwise 启动与依赖安装 |
| `config/` | campaign 与规划政策配置 |
| `fixtures/` | 测试 theory、ROOT 与请求样例 |
| `tests/current/` | 当前 Semantic Base 链路的回归测试 |
| `tests/legacy/` | 保留的旧实验链路测试 |
| `legacy/` | 旧 Refine ARM 分层实验、Mode R 增量生成、whole-theory 生成；详见 [说明](legacy/README.md) |
| `docs/design/` | 当前研究 setting 与目标设计 |
| `docs/implementation/` | 环境、规划、证明生成的实际实现与待实现扩展 |
| `docs/history/` | 历史实验、演进与延期决策记录 |

这是现有实现的职责整理，没有恢复已删除开发分支的代码。公共提取逻辑由
`preparation/regen_extract.py` 提供，旧链路也通过同一包导入，不另存副本。

## 运行与检查

从本工作区的仓库根目录运行，使用新的模块路径：

```bash
python3 -m regen.preparation.environment_freeze --help
python3 -m regen.preparation.environment_preparation --help
python3 -m regen.planning.repository_planner --help
python3 -m regen.execution.proof_session_runner --help
python3 -m unittest discover -s regen/tests -t . -p 'test_*.py'
```

证明执行的模型配置见 [proof-models.json](config/proof-models.json)：新 projection run
默认 Lemma Prover 为 `gpt-6-astra medium`，并冻结配置及 Codex 二进制哈希。
旧运行仍遵循原绑定；更换 CLI 或模型需要显式创建新的执行绑定。
配对诊断入口为 `python3 -m regen.tools.compare_proof_models --help`，
[本次结果](../reports/regen/model-comparison-001/REPORT.md) 不属于 campaign 验收。

辅助脚本位于 `regen/tools/`。不再需要把 `regen` 或旧 `regen/scripts` 加入
`PYTHONPATH`；原平铺 Python 路径已迁移，外部调用方应更新为上述包路径。
既有实验产物保持原样；源码路径和内容变化会改变冻结输入哈希，不能将旧 seal
当成对新布局的验证，后续真实运行需重新冻结环境。

环境准备现在有统一的 `prepare / resume / restore / verify-dataflow` 入口，运行步骤和
恢复边界见 [环境流水线说明](docs/implementation/ENVIRONMENT-PREPARATION-IMPLEMENTATION.md#5-统一准备入口下游数据流与恢复)。
完成后下游读取 `Environment-Dataflow/environment-index.json` 及 contracts/premises JSONL；
私有恢复日志和检查点不作为模型输入。

## 来源与合并记录

本分支 `semantic-base-repository-planning` 基于 `1621408`，`4de882f` 保存了 semantic-base
工作区快照；`f981a4b` 迁入设计文档，`c116eae` 核对了本地实现路径。来源提交是
`bacfdb56ff8af51cc4b6363b90db32a971910271`，来源分支已删除，不作为 checkout 指令。
迁移没有恢复旧 planning 开发线代码。

本次按内容职责合并，旧文件不再保留平行说明：

| 原迁入文件 | 合并去向 |
|---|---|
| `UNIFIED-PROOF-ARCHITECT-EXPERIMENT.md` | 总体设计：研究问题、两级规划、helper 与评价 |
| `UNIFIED-PROOF-HARNESS-DESIGN.md` | 总体设计：正确性、迭代和完成合同 |
| `ENVIRONMENT-PLANNING-SUBSTRATE-PLAN.md` | 规划实现文档：Planning Substrate 扩展章节 |
| `UNIFIED-PROOF-MODULE-IMPLEMENTATION.md` | 按环境、规划、证明生成三份实现文档分工；重复模块表删除 |
| `README.md` | 保留为本导航页 |

共享声明裁剪函数已合并到 [retrieval_utils.py](infrastructure/retrieval_utils.py)，提取器和旧分析入口复用它。
这次整理不新增 proof-generation 能力，不把文档、单测或历史结果当作新的 Isabelle seal 证据。

当前 repository file projections 的执行、提交恢复与 optional GREEN facts 接线见
[证明执行实现](docs/implementation/PROOF-GENERATION-IMPLEMENTATION.md)。
