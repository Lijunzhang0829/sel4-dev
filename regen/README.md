# Semantic Base 上的仓库级证明生成

本分支在 command 级 Refine Semantic Base 上探索仓库规划与文件内证明生成。
文档区分现有调度／证明实现与拟议仓库规划扩展。没有移植旧 planning 开发线的生产代码，
也没有因文档更新改变运行配置或声明新的证明结果。

## 分支与迁移来源

- 分支：`semantic-base-repository-planning`。
- 已提交祖先：`regen-refine-semantic-base` 的 `1621408828ed81f419ceedb0dc4fe5ee16939ff3`。
- 工作区基础：`4de882fe2cd6d6f23c058d10d079beb0abeb11c6`，保存 semantic-base 工作区的
  tracked changes 与非 ignored 新文件；不包含 ignored 数据、构建产物或嵌套仓库工作区。
- 文档来源：`session-planner-global-proof-plan` 的 `bacfdb56ff8af51cc4b6363b90db32a971910271`。
  来源仅限下列五个文件的已提交版本，不包括当时未提交的 `UNIFIED-PROOF-HARNESS.md`。
  旧 planning 与 runtime-audit 分支已删除；此处分支名仅记录来源，不能作为 checkout 指令。
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

文档职责分开：

- 上表中的 Experiment、Design、Substrate 是拟议扩展，不声明已经接线。
- [环境实现说明](ENVIRONMENT-PREPARATION-IMPLEMENTATION.md)、
  [Session 调度实现](SESSION-PLANNING-IMPLEMENTATION.md) 与 Module Implementation 描述静态代码入口。
- [总体导航](REPO-LEVEL-PROOF-HARNESS.md) 只索引流程，不另设一套冲突合同。
- [实验记录](REFINE-CREFINE-EXPERIMENT.md) 集中保留历史 campaign 结果，不是当前生产 PASS。
- `HARNESS-EVOLUTION.md` 是历史日志，旧路径／决策不作为当前代码或规范入口。

代码链接均使用相对当前文档的路径；定位使用函数名，不沿用旧 worktree 绝对路径或旧行号。
文档中的拟议行为不会自动覆盖代码；历史证据需要重验后才能支持当前运行。

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

当前 `session_planner.py` 仍只做 contract-based scheduling；File Planner 仍使用
fixed-contracts-only 输入。Planning Substrate、全局 obligation/helper 规划与 helper 撤销待接线。
当前 `run-session` 的 traversal complete 也不等于新的完整 Session Seal。

本分支没有因文档更新而获得新的 Isabelle build、模型生成或完整 seal 证据。
