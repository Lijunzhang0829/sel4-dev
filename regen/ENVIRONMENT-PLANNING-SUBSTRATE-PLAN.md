# Environment Planning Substrate Plan

状态：选择性迁移的设计草案，尚未接入本分支代码。
来源：`session-planner-global-proof-plan@bacfdb5` 的同名文档。
环境基础：本分支 `4de882f` 的 command 级 Semantic Base，非 theory 级 frozen Support。

## 目标与范围

在现有环境准备结果与仓库规划之间增加只读、确定性的语义视图。它回答某个 contract
引用什么符号、对应哪些合法定义或事实、缺少的信息属于什么情况。它不决定证明路线、
helper、contract 聚类或依赖边。

当前 [session_planner.py](session_planner.py) 和
[调度配置](config/session-planning-policy.json) 禁止 individual premise records；
[file_planner.py](file_planner.py) 也仅接收 fixed contracts。本计划是对现有输入政策的扩展，
尚未实现，不能将当前行为描述为已消费语义视图。

首个接入点是仓库规划输入；同时生成并验证文件投影，后续 File Planner 是否消费这些投影
另行接线。实现可以复用现有有效环境产物，不为数据投影重复构建 Isabelle，前提是输入身份
和有效性已验证。不能通过放宽源隔离来简化开发。

## 输入边界

| 输入 | 允许内容 |
|---|---|
| Public contract packages | 冻结目标、types/context 信息、目标名称及 owner 分组 |
| Environment gate / Semantic Base seal | 实际使用的合法环境身份与可用性 |
| Premise Catalog | frozen parents 和 `retained_semantic_export`，包括合法定义方程及自动生成事实 |
| Context-effect disclosure | 保留语义 command 导致的事实集合和 automation membership |
| Generated publication snapshot | 如运行阶段需要，已完成并合法发布的生成事实；单独绑定快照 |

不得读取 private command inventory 的原始 proof spans、已删除 helper 的名称和陈述、原始证明
顺序、人类 proof graph、原始 Refine heap 或隐藏历史。`excluded` 诊断不能成为泄漏被删除
lemma 名称和 statement 的旁路。

Semantic Base 构建内部当前保留原 theory headers/imports。这不授权 substrate 将这些原始
imports 当成人类证明调度建议。允许呈现的是生成运行实际需要的合法环境接口；两种依赖的
完整边界仍待讨论。

## 产物设计

建议输出以下逻辑产物；路径和 schema 在实现时确定，不视为现有 CLI 合同。

| 产物 | 内容 |
|---|---|
| `environment-index.json` | contract 集合、环境 seal、catalog 身份、产物引用及 coverage |
| `declarations.jsonl` | 可公开声明的稳定 ID、精确陈述、owner、source class、attributes、provenance |
| `session-context.json` | 全部 contracts、符号解析结果、定义关联、合法事实引用与诊断 |
| `files/<file-slot>.json` | 同一语义视图对 contract owner 分组的确定性投影 |

不得把待证 contract 的 statement 视为可使用的 theorem。初始 substrate 不包含未来生成事实；
后续可用性变化产生新的视图或独立增量，不覆盖初始输入身份。当前 theory GREEN facts 在
attempt 构造时加入，不混入初始 Semantic Base catalog。

每个 contract 的符号解析应有以下一种结果：

| 状态 | 含义 |
|---|---|
| `available` | 在当前合法环境中唯一解析并可使用 |
| `unresolved` | 无法从合法输入恢复对应声明 |
| `excluded` | 合法元数据足以说明权限不允许；不暴露隐藏内容 |
| `blocked` | 合法已知接口尚不可用，例如生成依赖尚未发布 |
| `ambiguous` | 合法候选不唯一，保留候选引用及原因 |

优先使用 canonical/qualified name，再使用 owner-aware 解析。`_def`、`.simps`、`.cases`、
`.induct` 等命名约定只用于发现候选，最终关联必须有合法 catalog 或 prover 元数据支持。
字符串相似或名称后缀不能自行证明语义关系。

所有目标和解析状态必须保留，长声明可按稳定 ID 去重、分批或按需展开。输入组织规则应可记录
和重建；本次不从旧分支复制固定 top-3、128 条或 optional top-k 规则。

## 验证与增量落地

1. 从现有 public packages、环境 gate 和 catalog 构建纯数据视图。
2. 检查 exact-once contract coverage、声明引用、source classes、排序和投影一致性。
3. 接入仓库规划 payload，保存实际展示的记录和展开过程。
4. 用小型合法环境验证 producer→projection→planner 的交接；后续再接入 File Planner。

反例至少覆盖：遗漏 contract、未来事实标为可用、隐藏 proof 内容进入记录、目标被当作已证事实、
错误 catalog binding、无法解释的歧义。结构测试不冒充 Isabelle 语义验证。

观察指标包括符号解析覆盖、输入规模、规划修订、helper 采用和最终 contract 完成度。
没有冻结对照实验时，只报告可行性与观测，不声称环境视图提高了成功率。
