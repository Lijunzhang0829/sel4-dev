# Unified Proof Harness Design — Semantic Base Profile

状态：本分支仓库规划扩展的设计草案，不表示下述全部合同已经实现。
来源：`session-planner-global-proof-plan@bacfdb5` 的同名文档，经筛选重写。
本次不改变基础快照中的 Python 代码、模型、预算或环境配置。

## 1. 任务输入与删除边界

冻结程序／规格、parent sessions、目标 selector、exact selected contracts、contract owner
分组和使用的语义环境。删除政策与目标选择分开：没有选中 contract 的 theory 也必须经过
command 级处理，不能自动整体保留。

| 对象 | 基础快照中的处理 |
|---|---|
| 目标 contract | 对外保留精确 statement、名称、attributes、必要 enclosing context 和 owner；原证明移除 |
| 普通人类 helper/theorem | 移除整个 command span，包括声明、attributes 和证明体 |
| 独立证明自动化命令 | `declare/notes/lemmas/crunch/method` 等 ordinary-proof 类随层删除 |
| 语义 command | 使用固定 semantic-core 白名单，原文保留；附带导出事实与自动化效果单独披露 |
| 混合／证书类 command | 默认不在核心；白名单片段若检测到 internal proof syntax 则拒绝 |
| 结构 | 保留语义 command 所需 scope，以及语义 theory 原 header/imports 和命令相对顺序 |
| Frozen parents | 外部可信基础，不作为本轮生成结果 |

当前接受规则可以是整个 semantic core 与全部 exact contract shells 构建通过，明确不声称
每个 retained command 都经过单独必要性证明或达到最小集合。逐项删除反事实是可选诊断。
shell 以 `oops` 结束，只验证目标 elaboration，不产生可供证明调用的目标 theorem。

原始 source 和完整 command inventory 仅供可信 preparation；生成模块不得通过文件、Git 历史、
缓存、heap、其他副本或外部检索接触被删除的证明层。只展示合法环境接口和本次生成证据。
本次静态文档迁移不宣称已验证所有访问通道的隔离。

保留原 imports 是 Semantic Base 当前构建事实；向 planner 暴露人类 proof import graph 并未
因此获得授权。用户希望仅保留 contract 分组，该目标与现有语义环境内部 imports 的关系需
单独澄清，不能在文档迁移时伪装为已完成。

## 2. 有限范围的生成流程

```text
冻结输入与 command 级 Semantic Base
  -> 合法 catalog + Planning Substrate
  -> 一次 Repository planning + 文件顺序
  -> 对每个 theory:
       绑定合法环境和已发布接口
       -> File Planner + statement elaboration
       -> proof / retry / local graph revision 循环
       -> 完整 theory replay + accumulated build + 合法 export
       -> publication
  -> 选定任务的完整构建与结果报告
```

初版限制跨文件重构，允许文件内开发、修改和迭代。已发布上游需要改变时，本轮明确阻断，
不静默修改旧 publication。失败后是否继续无依赖文件及总体停止规则，作为待冻结的运行政策，
本次不搬入来源文档中相互不同的调度规定。

## 3. 必须保持的正确性边界

- 原始 contract 命题、types、assumptions 与所引用的程序／定义语义不变。
- LLM 只能提出 statement、proof 和搜索方案；真实 prover 判定 elaboration 与证明成立。
- 待证命题、未证明 helper、未发布跨文件事实不能作为 trusted premise。
- 每次 attempt 的 prompt facts 必须属于实际 checker context；required predecessors 与
  optional retrieval 分开记录，检索不自动产生依赖或强制使用。
- 同文件 GREEN helper 使用前必须确实位于合法 checker prefix 中；跨文件使用必须绑定
  已验收的 publication 与实际 imports。
- GRA 不改变已 GREEN 的 statement、proof 或上下文；图修订不可让旧证据错误地继续生效。
  完整组装暴露不一致时停止发布并保存证据，本草案不预设复杂 Assembly 回修例外。
- 完整生成输出不得含 `sorry/oops` 等跳过证明方式，不引入未允许的 axiom/oracle。
- 最终验收检查完整 contract coverage、真实构建、依赖来源与输出身份，而非只相信自报状态。

## 4. Local helper 迭代

允许在未证明范围内创建、分解、替换 helper，并重新检查涉及的 statement 与 graph。
helper 的自然语言 intent 和 replacement 是否值得尝试可交由 Codex 对话监视，不要求复杂
consumer applicability witness；最终原始目标的真实证明不得省略。

拟议的 `withdrawn-unproved` disposition 用于撤销当前 theory 内不再需要的 generated helper：

1. 不能是原始 contract，不能已经 GREEN 或发布，不能涉及需修改的跨文件承诺。
2. 撤销后无悬空引用、无环，未 GREEN consumers 的目标不变，已 GREEN prefix 不变。
3. 显式记录移除的计划边、来源、原因、旧尝试和累计成本。
4. 剩余原始目标必须全部证明；不能因 helper 撤销提高原始目标的统计分母或抹去失败成本。

保留 active scope 中的 helper 仍需真实证明；合法撤销的 helper 不再阻断最终目标完成。
该 disposition 尚需代码实现，不能直接沿用旧“supersede 必须有非空 replacement”的 gate。

## 5. 关系、身份和证据

保留计划依赖、prover-observed dependencies、lineage 的语义区分。冻结 Architecture 与动态
Exact Graph 的对应方式尚未决定，本次不固定图数量、合并方式或 version schema。

无论最终图表示如何，至少记录：输入环境／contract 身份、规划来源、当前精确 graph、attempt
实际 facts、proof/checker 结果、接受的 proof revision、publication、失败与撤销历史。
恢复应读取已验证产物，不凭 live summary 宣告成功。缺少或失效的证据不能维持 GREEN。

模型调用记录实际输入输出；checker 记录 source、环境、command、结果及依赖证据。
统计保留语义事实的使用，避免把原基础的证明能力算作本轮新生成能力。
具体计时、存储和事务协议待单独确定，不机械复制旧固定 top-k、调用次数或大型 WAL/CAS 要求。

## 6. 验收与未决事项

分别报告证明目标覆盖与正确性、实验协议合规性、证据完整性。任一必需目标未完成时不得
宣称任务完整成功；模型自建 helper 的数量不是稳定完成度分母。

初版可针对 Refine 独立验收，不强制 Final Seal 包含两个 session。CRefine 独立／联合运行、
图对应关系、输入 imports 边界以及统一预算与停止规则仍需讨论。
原讨论第七点及其后的未决问题没有因本次文档迁移被视为已批准。
