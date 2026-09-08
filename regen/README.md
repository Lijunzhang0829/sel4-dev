# Semantic Base 上的仓库级证明生成

本分支在 command 级 Refine Semantic Base 上探索仓库规划与文件内证明生成。
当前任务是选择性迁移设计文档；没有移植 `session-planner-global-proof-plan` 的生产代码，
也没有因文档迁移而改变运行配置或声明新的证明结果。

## 分支与迁移来源

- 分支：`semantic-base-repository-planning`。
- 已提交祖先：`regen-refine-semantic-base` 的 `1621408828ed81f419ceedb0dc4fe5ee16939ff3`。
- 工作区基础：`4de882fe2cd6d6f23c058d10d079beb0abeb11c6`，保存 semantic-base 工作区的
  tracked changes 与非 ignored 新文件；不包含 ignored 数据、构建产物或嵌套仓库工作区。
- 文档来源：`session-planner-global-proof-plan` 的 `bacfdb56ff8af51cc4b6363b90db32a971910271`。
  来源仅限下列五个文件的已提交版本，不包括该分支未提交的 `UNIFIED-PROOF-HARNESS.md`。
- 原 semantic-base 分支、暂存区和工作区保持原状；基础快照将其 staged/unstaged 内容合并为
  新分支的文件版本，原暂存状态仍保留在原 worktree。

## 文档导航与筛选结果

| 文档 | 保留内容 | 本次不迁入的内容 |
|---|---|---|
| [Environment Planning Substrate](ENVIRONMENT-PLANNING-SUBSTRATE-PLAN.md) | contract 中心的语义视图、符号解析、可用性、文件投影 | frozen Support 输入、放宽源隔离的 planning-first profile、旧运行数量与效果断言 |
| [Proof Architect Experiment](UNIFIED-PROOF-ARCHITECT-EXPERIMENT.md) | 全局规划与局部细化、helper 复用、基于原始 contracts 的评价 | helper 必须有 replacement 才能退出、固定 top-k、强制双 session、未经验证的外部项目类比 |
| [Harness Design](UNIFIED-PROOF-HARNESS-DESIGN.md) | 目标身份、合法事实、局部迭代、完整构建与发布 | theory 级 Support/Proof 分类、全仓库回修系统、复杂 intent witness、未经讨论的固定预算 |
| [Module Implementation](UNIFIED-PROOF-MODULE-IMPLEMENTATION.md) | 模块职责、证据与后续验证清单 | 来源分支的 PASS/FAIL、测试计数、失效代码路径与旧目录布局 |
| 本 README | 分支来源、文档入口、未决项 | 不适用于当前平铺模块布局的运行命令 |

这些迁移文档是本分支的仓库规划设计草案。现有
[环境实现说明](ENVIRONMENT-PREPARATION-IMPLEMENTATION.md) 和
[session planning 实现说明](SESSION-PLANNING-IMPLEMENTATION.md) 用于理解基础快照，
其中历史运行结果、绝对路径和实现状态没有在本次重新验证。
旧 [总体说明](REPO-LEVEL-PROOF-HARNESS.md) 的强制 Refine→CRefine 流程不自动成为本草案的要求。
文档中的拟议行为不会自动覆盖代码：代码现状、已讨论方向和未决项分别标记。

## 当前边界

使用整个 Refine scope 的 command 级筛选；目标是 selected contracts，普通人类辅助 lemma
及其证明和独立自动化注册移除。保留语义 command 的附带事实与自动化效果必须披露。
生成模块不得通过源码、历史、缓存、其他副本或检索接触被删除的证明层。

初版采用一次仓库规划、文件内迭代、完整 theory 验收后跨文件复用。允许撤销不再需要的
未证明 helper 是拟议改动，尚不宣称已实现。当前实现范围是 Refine；独立 CRefine 和联合
任务是否纳入首版，留待确定。

尚待讨论：冻结 Architecture 与动态 Exact Graph 的关系；最终实验 session 范围；
Semantic Base 保留原 imports 与模型不获得人类 import graph 的准确边界；统一预算和停止规则。
本次不预先决定这些问题，也不迁入来源文档与之冲突的规则。

本分支没有因迁移文档而获得新的 Isabelle build、模型生成或完整 seal 证据。
