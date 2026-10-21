# 单 theory 持久源码工作区

日期：2026-09-25。默认入口：`parallel-workspace --file-planner-interface document`。

## 目标与依据

固定功能/证明目标已给定时，让持续 agent 直接编辑产物、执行验证、读取反馈再修订。
无需同时维护一套模型生成的局部证明依赖图；文件顺序和 Isabelle 上下文决定可引用成果。
这是一条适合当前任务的工程路线，不宣称对所有代码/证明生成任务最优。

机器 B 本次读取的实现：
`/data/zljj/vero-runs/refine-incremental-sol-001/code/src/vero/isabelle/slots.py`
明确把 helpers 定义为普通文件声明，不独立调度；`checker.py:evaluate` 提取已填写槽，
验证 hygiene，渲染候选源，再按模块验证。这里只参考基础设施代码，未引入历史目标证明。
我们保留既定 imports、固定 contract、每 theory 单写入和 seal/integration 标准。

代码生成相关资料：[Agentless](https://arxiv.org/html/2407.01489v2) 的定位/修复/验证分工，
[CodePlan](https://arxiv.org/pdf/2309.12499) 的实际检查反馈驱动修订。它们研究已有仓库修改，
不提供“源码工作区必然比证明图快”的结论，性能仍需同目标实验比较。

## 模型看到的流程

`read_theory → edit_theory → check_theory → 反馈/继续编辑 → finish`

操作通过 `proof_action(theory, proposal)`：

```json
{"action":"edit_theory","base_sha256":"当前文档hash","edits":[{"old":"唯一旧文本","new":"新文本"}]}
{"action":"check_theory","source_sha256":"保存后的hash","reason":"此次修改"}
{"action":"finish","source_sha256":"成功检查且未再编辑的hash"}
```

也可用 `source` 替代 `edits` 保存完整文件。模型可连续保存多个草稿再检查，不每次编辑
都跑 Isabelle。不提供图节点/消费者/dependencies/策略 routes 的维护要求。

固定目标在文件中以受管注释标记，Isabelle 声明和证明仍为源码：

```isabelle
(* REGEN-BEGIN {"id":"contract-id","role":"contract","context":[]} *)
lemma target: "True"
(* REGEN-PROOF *)
oops
(* REGEN-END *)
```

helper 同样是带唯一 ID、role=helper、可用 context 的 lemma 区块，可以插入、删除、
重排和改写。固定 contract 不能删除或修改声明/上下文；header/imports 固定。
context 元数据与实际 context/end 包裹必须一致。空证明用单独的 oops 表示；它不导出
定理。用户提供的 sorry、axioms、任意 ML 和隐藏顶层命令继续拒绝。

## 持久状态和验证

- `theories/<slot>/document/draft.thy` 是当前工作文件，失败不会把它恢复成旧版本。
- `document/versions/<sha>/source.json` 保留版本，`document/edits/` 绑定实际模型工具调用。
- 保存只验证格式、目标边界、名称和命令卫生，不宣称语句合法或证明成功。
- 检查时派生内部声明表，自动设置文件顺序，所有计划依赖边为空；不让模型补齐 DAG。
- 未完成槽不进入证明检查源。新 helper 若仍未填写，会通过声明检查验证，但仍非前提。
- 全部已填写的证明按文件顺序检查。无变动的已接受前缀可复用；改写/删除/重排已接受
  成果则复验所有当前已填写证明。消费者引用了已删除 helper，必须由 Isabelle 拒绝。
- `document` 接受事件独立重建候选并验证旧状态、源 hash、imports、完整 checker 证据；
  任一失败不替换已接受版本。历史 GREEN 记录仍在 journal，但不是当前文档有效性的捷径。
- seal 要求当前草稿 hash 等于成功检查的 hash、无未完成槽，然后执行原完整检查、
  语义审计、session build。required_imports 的循环/就绪检查、并行槽和最终集成 gate 保持原有职责。

内部仍使用既有声明索引和 journal，以支持事实检索、跨 theory 读取和历史 replay；
旧 `graph/nodes` 字段是 journal/checker 的兼容存储格式，不代表运行一套局部 DAG。
`declaration_index.py` 独立构建/校验声明表；源码解析没有 dependencies/consumers，
仅在底层兼容编码中补空字段。正常初始化和候选生成不调用旧 `build_graph`；旧版
document 证据重建单独保留兼容路径，不重写历史 hash。文档顺序不是实际定理引用关系，
后者由 Isabelle 审计。模型读取时不展示兼容边字段、图所有权和路由；原始证据仍完整落盘。
旧 graph 模式的不可修改 GREEN 规则保持原义；只有具备整版复验证据的 document 事件
可替换当前已接受文件。环境不增加冻结或额外故障恢复协议。

## 跨 theory 调度与接口边界

仓库规划产出 required_imports；Python 协调器从这份真实 imports 关系计算就绪状态，
检查循环并等待上游 seal，然后启动各 theory 的持续 File Planner。list_workspace 是
状态读取接口，不是 agent 启动其他 File Planner 的调度接口。不额外要求 agent 维护
一份重复的调度图。协作组、优先级建议不转为局部证明依赖。

源码模式仅允许 edit_theory/check_theory/finish 与原只读检索、诊断动作；旧 submit_batch、
submit_check 和 prepare_reuse 的图补丁/闭包导入路径在该接口拒绝。跨 theory 复用使用
list_generated/read_generated 获取源码，模型复制或改写普通 helper 区块后重新检查。
读取其他 theory 返回已接受的只读声明/证明快照，不为其他 theory 创建草稿或写入。
不计算局部 consumer_feedback 或基于计划边的 batch_opportunities。

兼容边界：旧 graph CLI 仍用于历史回放/旧接口；公共 proof_batches、checker、journal、
检索仍读取兼容声明编码。新源码模式调用 `check_declarations`，原生标签为
`Declaration_Check_vN`；旧 `check_graph` 及原标签仅用于历史接口/旧 checker 适配。
权威 journal 内 `graph_check` 等字段暂不迁移，公开反馈展示为 `declaration_check`。
此轮不为改名重写历史数据。

## 实验边界与待评估事项

实现变化后必须新建 run，仓库计划可沿用。旧并行实验 20260925-002 已受控停止并保留
证据，不在旧 run 中切换接口。本次单测和 native 小例只验证接口与安全边界，不能认定
Refine 完成率/耗时已经提升。

后续比较同一 theory 的达到 GREEN/seal 时间、token、检索次数、重复提交文本体积、
整版复验次数和前缀复用收益。全量复验是修改早期成果的正确性代价，不强制限制修改，
也不把“修改次数少”当作优化目标。跨 theory 动态改 imports 不在此次范围。

## 本次验证记录

- 当前测试集：735 项通过（其中源码工作区 11 项），覆盖持久编辑、陈旧 hash、固定目标
  保护、批量顺序、旧证明改写、失败不提交及当前草稿 seal gate。
- 真实 Isabelle 小例：原文件 GREEN（11.593s）；删除仍被引用 helper 后 RED（12.245s）；
  修正消费者后 GREEN（11.504s）。两次修改均执行 full 验证，非沿用旧 GREEN。
- 证据：`reports/analysis/theory-document-native-20260925/result.json` 及各 case 的 evidence。
  这些是合成接口案例，不是 Refine target 成功率实验。

### 声明索引解耦后的复验

- 最终完整回归：741 项通过，33.904s；源码工作区专项 17 项。覆盖独立声明校验、
  新工作区初始化不调用旧图构建器、旧文档证据回放、拒绝图修改入口及原验证边界。
- 真实 Isabelle 合成小例：初始 GREEN 12.583s；删除仍被引用 helper 后 RED 12.762s；
  修复消费者后 GREEN 12.320s。后两次 full 复验。该耗时不构成性能改善结论。
- 证据：`reports/analysis/declaration-index-native-20260925/` 下 result.json、各 case
  evidence.json、python-tests.log、validation-summary.json。
- 原 `parallel-theory-document-astra-20260925-001` 在修改前受控停止，日志/成果保留，
  原因记录于 operator-stop.json；未在代码变化后继续旧实验，也未启动新 Refine 模型实验。

## 接口命名与并发一致性复核（2026-09-25）

当前默认源码模式的公共名称统一如下；实际 contract/helper ID 和 Isabelle 文本不改名。

| 含义 | 新源码接口/派生产物 | 历史兼容编码 |
|---|---|---|
| 声明与结果 | entries、entry_id、entry_results | nodes、node_id、node_results |
| 检索上下文/证明读取 | context_entry_id、entry_ids | context_node_id、node_ids |
| 声明检查 | check_declarations、Declaration_Check_vN | check_graph、Graph_Check_vN |
| 当前声明索引 | declaration-index.json、document_revision | active-graph.json、graph_revision |
| 辅助成果计数 | checked_auxiliaries | checked_generated_nodes |
| 工具观察记忆 | observation_memory.py、observation_* | async_observations.py 导入别名、async_* |

`document_interface.py` 对公开参数/反馈做结构化映射，旧 selector 同时提供而冲突时拒绝；
不改证明正文、ID、源 hash 或原始证据。读取跨 theory source_ref 可以按原公共格式传回。
新的 progress.json 是可重建的文档视图，权威仍为 acceptance journal；旧记录不会迁移。
源码函数采用 entry/index、initial_index/accepted_declarations/check_document/validate_index。
共享 checker、batch acceptance 和历史 schema 中仍有 graph/node 字段；其用途是兼容读写。

run 名由 `--run-id` 指定，没有自动 graph/node 前缀要求。新例使用
`parallel-theory-document-astra-YYYYMMDD-NNN`，原实验 ID、历史目录和证据文件不改名。
本次复核没有启动新的模型生成实验。

单 File Planner 所有数学/检索/编辑调用都持有 theory 局部串行锁直到结果返回。
observe 中的多个读取也是顺序执行。CheckExecutor 内部线程派发用于不同 theory 共用
执行池与持久任务收据，不代表同一 agent 可在等待期间开始另一个操作。历史状态查询/
取消接口仅供外层兼容控制，源码模型无需轮询。

跨 theory 由协调器线程池派发，run 排他锁阻止第二个外部 runner，不阻塞内部各 theory。
`max_parallel_theories` 是 File Planner 上限；native_slots 是 Isabelle 同时检查上限；
resident_limit 也可能减少 native 有效并发。required_imports 未 seal 时该 theory 等待。
默认 native_slots=1，因此两个 agent 运行不等于两次 Isabelle 同时运行。
list_workspace 分别返回 assigned_writer_count=1、max_parallel_theories 和 native_slots；
已移除原先在并行模式仍固定 worker_count=1 的误导字段。


本轮验证：完整 Python 回归 **757 项通过（36.603s）**；默认 document 并发专项 3 项、
命名/接口与证据绑定专项均通过。真实 Isabelle `Declaration_Check_v2` 声明检查 GREEN
（11.642s，proved_contracts=0）。证据位于
`reports/analysis/document-interface-native-20260925/`：python-tests.log、result.json、
evidence.json、validation-summary.json。没有启动新模型生成实验或迁移旧历史记录。
