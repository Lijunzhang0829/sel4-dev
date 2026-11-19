# Ours 与 Baseline 生成实现的优劣比较

本文整理 ours（结构化契约 + File Planner / Lemma Prover + 细粒度 manager）与
baseline（VERO / official-Codex 共享 harness，直接编辑 slot 源文件 + 单 agent）
在生成阶段的实现差异与优劣，供论文方法对比与实现选择引用。范围限于
Isabelle / seL4 仓库级证明生成。

## 1. 两条设计路线

- **ours**：把生成拆成“File Planner 出 statement / proof DAG，Lemma Prover 逐节点证明”，
  输入是 JSON 契约对象，native check 到节点/完整 theory，并带有 manager / 线索管理。
- **baseline**：把冻结 contract 渲染成 `Vero_*.thy` 的 `VERO START/END` slot，
  单个 agent 直接编辑 slot body，再由共享 checker 独立评估。

## 2. 总体对照

| 维度 | ours（JSON graph + File Planner/Lemma Prover） | baseline（slot 源文件编辑 + 单 agent） |
| --- | --- | --- |
| 输入形态 | 契约对象、proof DAG、premise ID | 渲染后的 `Vero_*.thy`，直接改 slot body |
| 规划 | File Planner 出 statement/DAG，planner 可审查/修订 | agent 自己选顺序、helper，写 plan.json 或直接干 |
| 模型自由度 | 受 schema 约束，但证据可查询 | 几乎自由，可写任意合法 Isar |
| native 检查 | node + complete-theory + command gate + automation audit | per-declaration + batch audit + Vero_All |
| 反作弊 | skip/oracle/axiom + automation context + retained-fact 使用 | skip/oracle/axiom + 精确 batch 身份 |
| 失效粒度 | proof graph + prefix cache，按传递依赖失效 | 曾出现 whole-theory invalidation、forced cold checks |
| 状态/线索 | compact manager、增量日志、证据 ledger | 曾出现 67MB/59GB 写放大、轮询失控、无增分重试 |
| 可归因性 | 每节点独立身份、evidence、native preflight | 以 slot/module 为粒度，单 agent 推理较难细粒度归因 |
| 工程成本 | 高，schema 多、阶段多、adapter 多 | 低，单工具链、共享 checker |

## 3. ours 的优势

1. **可审计、可归因**
   每个 generated node 有独立 ID、exact statement、native preflight、依赖与自动化审计，
   适合论文证据，也便于细粒度失败归因。
2. **反作弊面更宽**
   在 baseline 的 skip/oracle/new-axiom 之上，增加 command gate、automation context
   审计、retained Semantic Base facts 使用量化。
3. **运行效率更稳**
   细粒度失效、prefix 缓存、增量日志、有界查询，对治 baseline 日志中暴露的
   whole-theory 冷检、状态写放大和轮询失控。
4. **owner-blind 更彻底**
   canonical 输入完全删除 spec 到 theory 的归属关系，模型侧不泄漏原始架构先验。
5. **规划可修正**
   plan review / revision decisions / advisory proof directions，避免“规划一次超时后低效重来”。

## 4. ours 的劣势

1. **复杂度高**
   阶段多、schema 多、adapter 多，维护和扩展成本明显更高；新 benchmark 要适配
   File Planner / Lemma Prover。
2. **输入不是真实 theory 文本**
   模型看到的是 JSON 契约，看不到渲染后的完整 theory 上下文；某些局部 Isar/上下文细节
   需要额外查询，token 和交互成本可能更高。
3. **过度结构化可能压制探索**
   DAG/schema 要求先声明再证明，对“直接写一个复杂 Isar proof”的自由度有约束。
4. **阶段间协调开销**
   File Planner 与 Lemma Prover 分离带来交接、证据回填、GRA 修订等额外机制。

## 5. baseline 的优势

1. **简单直接**
   模型直接读写真实 Isabelle 源文件，所见即所得；单 agent 无跨阶段协调。
2. **灵活**
   slot 内几乎可写任意合法 Isar/局部 helper，适合探索性证明和快速原型。
3. **检查/审计路径干净**
   `tool.py` 共享，`checker.py` 的 native 声明审计已经很强；工程链路短，容易复现和审计。
4. **移植成本低**
   换 benchmark 主要是渲染 benchmark.json + 准备 premises，不必重写 File Planner / Lemma Prover。

## 6. baseline 的劣势

1. **粗粒度失效与状态失控**
   WORKLOG 明确记录 whole-theory invalidation、67MB/59GB 写放大、excessive polling。
2. **缺乏线索/证据管理**
   出现 952 次无增分尝试、超过六次 native checks、大量重复冷检，候选和反馈没有被持久复用。
3. **规划/修正弱**
   plan 是单次文件输出，失败后没有结构化 review/revision；出现过 PLAN 超时、
   生成成绩低于既有 fresh 结果。
4. **归属可见风险**
   benchmark 中 contracts 带 `module`（原始 owner），即使可以重分组，也更容易泄漏架构先验。
5. **单 agent 不透明**
   目标选择、helper 设计、检索策略都集中在一个 agent 的自由文件编辑里，
   细粒度归因和成本归属更难。

## 7. 结论

- ours 在“可信、可审计、可归因、大规模稳定运行”上更强，代价是更高的复杂度和更低的探索自由度。
- baseline 在“简单、灵活、易移植、快速实验”上更强，代价是运行管理和线索复用弱，
  且更容易出现结构性低效和归属泄漏。
- 若目标是论文主方法，ours 的审计和 manager 是优势；若目标是公平 baseline 对照，
  baseline 需要先补齐最低限度的 manager / clue 管理，否则其成绩会被工程低效拉低。
- 推荐组合：检查/审计策略统一，且 ours 策略不小于 baseline；输入形态保留各自方法特征，
  从而同时满足“方法有差异”和“信任底线一致”。

## 8. 证据来源

- baseline 运行日志：[WORKLOG.md](vero-isabelle/WORKLOG.md)
- baseline 待办与已知限制：[TODO.md](vero-isabelle/TODO.md)
- ours 生成规划：[file_planner.py](regen/planning/file_planner.py)、
  [proof_graph.py](regen/planning/proof_graph.py)
- ours 执行与审计：[proof_session_runner.py](regen/execution/proof_session_runner.py)、
  [isabelle_node_checker.py](regen/execution/isabelle_node_checker.py)
- baseline 生成与检查：[runner.py](vero-isabelle/src/vero/isabelle/runner.py)、
  [checker.py](vero-isabelle/src/vero/isabelle/checker.py)
