# 仓库规划：输入、产物与实现边界

当前默认入口为

[repository_planner.py](../../planning/repository_planner.py) 和
[theory_plan.py](../../planning/theory_plan.py)。它消费上一个模块完成并验证的
环境数据流，生成仓库证明方案和文件投影。目标 setting 见
[总体设计](../design/REPO-LEVEL-PROOF-HARNESS.md)，环境准备见
[环境实现](ENVIRONMENT-PREPARATION-IMPLEMENTATION.md)。

## 1. 输入交接

```text
Environment Preparation 的 gate + Environment-Dataflow/environment-index.json
  -> 校验 gate、dataflow、完整 contracts 和冻结 policy
  -> 按冻结上下文预算组织合法 planning input
  -> 模型提出生成 theories、固定目标分配、共享辅助意图、theory 前驱及优先级
  -> 结构校验；失败反馈规划器，在冻结次数内重试
  -> repository-plan + file-generation-list + files/*.json
  -> 后续 File Planner 精化与 Isabelle statement/proof 检查
```

输入读取与验证使用 `environment_dataflow.validate_dataflow()` 和 `resolve_ref()`，不另建
catalog，不读取 private inventory、原始 Refine sources、原 imports 或准备恢复日志。

| 上游产物 | 当前用途 |
|---|---|
| `Environment-Freeze/campaign-manifest.json` | 验证 campaign 身份，取得 prompt/time budgets |
| `Environment-Freeze/frozen-inputs/…repository-planning-policy.json` | 验证实际使用的 policy 内容与冻结文件哈希一致 |
| `Environment-Preparation/environment-gate.json` | 验证真实环境、catalog、packages 和 runtime 身份 |
| `Environment-Dataflow/environment-index.json` | 验证 dataflow 身份及逐条公开数据的一致性 |
| `contracts.jsonl` | 全部固定目标，保留 exact statement、context、attributes 与原 owner |
| `premises.jsonl` | 合法父 session 和 retained semantic facts；选中记录实际展示给模型 |
| `owners.json` | 目标来源分组；不限制最终生成文件划分 |

所有 contracts 必须完整进入 prompt，超预算直接报错，不静默截断目标。Premises 按 contract
及 context 的词法 token 重合度、稳定 ID 排序，在剩余字符预算内装入完整记录。超过空间的
记录跳过，输入保存 included/omitted 数量、所用算法和实际记录；这不是固定 top-k。
没有额外查询调用或隐藏输入。相同环境、policy 和预算确定性地产生相同 payload。

这里已经实现合法记录的规划输入组织，**没有实现完整语义符号解析**。词法候选不证明定义
关联，未展示某条 premise 不表示合法环境不存在该事实。全部 catalog 仍由环境模块保留。
后续若增加语义解析，复用同一环境接口，并提供清楚错误描述，无需另建复杂诊断状态机。

## 2. 模型提出什么

模型输出 `schema_version=3`，包含 `ordering_rationale` 和 `theories`。仓库图的节点是
生成 theory；不再输出以 contract/helper 为节点的全局证明 DAG。

| 字段 | 含义 |
|---|---|
| `name`、`purpose`、`grouping_reason` | 生成 theory 的身份、目标及分组理由；原 owner 仅作 provenance |
| `contract_ids` | 固定目标分配，代码复制完整 statement/context/attributes，不接受替代命题 |
| `auxiliary_intents` | 可选共享辅助结果意图，含唯一 `id`、`intent` 和 `for_contracts`；不是精确证明节点 |
| `suggested_predecessors` | 直接 theory 前驱，每条包含 `theory` 和非空 `reason` |
| `premise_ids` | 本次展示的合法事实 ID |

所有字段必填，列表可以为空。允许跨原 owner 重组、拆分和辅助专用 theory；每个 theory
至少包含一个 contract 或辅助意图。精确辅助命题、上下文和局部调度依赖由 File Planner
决定。共享意图既不是已证 premise，也不是跨文件 helper 承诺。

前驱只表达可调整的规划顺序，不自动成为 imports、局部证明依赖或必须使用的引用。
零辅助意图、零前驱边可以接受，但 prompt 要求分组与排序理由解释该选择；不设置数量配额。
非空理由只经过结构校验，不表示已确认其数学合理性。

## 3. 校验与顺序

`theory_plan.normalize()` 检查：

- 全部冻结 contract IDs 恰好出现一次，无遗漏、重复或额外目标。
- Theory 名称和辅助意图 IDs 合法且唯一；同文件 contract 声明名称不冲突。
- 辅助意图服务于已知固定目标；所有 premise 引用均来自实际展示的合法记录。
- Theory 前驱已存在、非自身、无重复且整体无环，每条边有非空理由。
- 拒绝局部节点 DAG、精确辅助 statement、proof、imports 等额外字段。

模型 `theories` 数组顺序是同时 ready 的 theory 之间的明确优先级，前驱约束优先于数组。
代码用稳定拓扑排序产生 `theory_order` 和 `theory_depths`，用 `priority_order` 保留原数组。
例如数组为 `[Zeta, Alpha, Foundation]`，而 Zeta 以前驱 Foundation 为条件，则执行顺序是
`[Alpha, Foundation, Zeta]`。没有前驱时保持原数组顺序，不再按名称重排。
这是允许多个父节点的 DAG，不要求是树；结构验证不证明所建议路线一定可行。

## 4. 输出交接

默认输出目录为环境目录下的 `Repository-Planning/`。所有规划产物不覆盖已有不同内容。

| 文件 | 内容与消费者 |
|---|---|
| `planner-input.json` | 本次完整实际输入，绑定 dataflow、policy 和环境身份 |
| `planner-proposal.json` | 通过校验的模型原始 JSON |
| `repository-plan.json` | schema 3：theory DAG、理由、优先级、固定目标原文及 plan ID |
| `file-generation-list.json` | schema 4 的生成文件列表，包含每个 projection 路径和 payload hash |
| `files/<GeneratedTheory>.json` | 后续 File Planner 的输入投影：schema 3：完整 contracts、辅助 intents、直接 theory 前驱及理由、优先级和合法 premises；无局部 DAG |
| `repository-planning-audit.json` | 输入／plan／prompt／final 身份、检查范围与 transcript 引用，最后写入 |
| `transcript/repository-planner-*` | 每次调用实际 prompt、response、raw events 和审计 metadata |
| `attempt-*-rejection.json` | 无效方案的具体诊断和 transcript 引用 |

文件投影保留 contract 的原 owner 字段用于追溯；外层 `theory/file_slot` 表示新生成文件。
两者不能混用。投影允许空 contracts 列表，以承载只服务于其他 contract 的辅助意图。
下游可信入口为 `repository_planner.load_file_projection(environment_root, planning_output, theory)`：
先核对冻结 policy、准备数据流与完整规划 transcript/bundle，再返回对应投影；不访问原始证明源。

`repository-plan` 的状态为 `proposed`；文件列表明确 `next_module=file-planning`、
`execution_ready=false`，投影为 `requires-file-planning-and-Isabelle-checks`。
规划通过不表示节点 GREEN，也不表示生成事实已发布。本阶段不创建假的 proof progress ledger。

Runner 已通过 `repository_execution.py` 消费此投影格式，创建自己的运行输入、进度与提交日志。
旧 schema 校验器仍拒绝把新列表当成旧 schedule；Runner 入口按 artifact type 显式分派。
辅助节点、撤销、GREEN 上下文及发布恢复见
[证明执行实现](PROOF-GENERATION-IMPLEMENTATION.md)。初始规划仍不产生可直接发布的证明事实。

## 5. 运行、重放与恢复

默认 policy 为 schema 3。新环境可通过 Environment Freeze 冻结政策；已有 GREEN 环境也可
通过独立 Planning Freeze 升级规划政策，绑定原环境而不重建 Semantic Base 或修改旧 manifest。
普通运行仍要求政策匹配原环境冻结；独立规划使用新输出目录和 `--freeze-planning`：

```bash
python3 -m regen.planning.repository_planner \
  --environment-root reports/regen/live-environment-002 \
  --output reports/regen/repository-planning-v3-001 \
  --freeze-planning --allow-external-call
```

gate 和 dataflow 默认使用上一模块的标准路径，也可分别通过 `--environment-gate`、
`--dataflow` 显式指定。`--output` 可改变规划输出目录。默认模型、结构校验重试次数由
[repository-planning-policy.json](../../config/repository-planning-policy.json) 冻结；当前最多三次逻辑尝试。
提供 schema 2 或 3 时，即使直接调用 Python API，也必须验证 policy 对应冻结文件快照。

结构无效的方案反馈给同一仓库规划器，不交给 GRA；GRA 属于后续证明执行阶段。每次逻辑
尝试使用独立 transcript 前缀；provider 失败不会把失败输出当成有效方案。再次运行会按
实际 prompt/model 匹配已完成的 audited transcript，复用成功响应或继续其后的结构重试。
不覆盖旧失败记录。需要新调用时仍要求 `--allow-external-call`。

```bash
# 已有日志时重建缺失的规划产物；不允许新外部调用
python3 -m regen.planning.repository_planner \
  --environment-root reports/regen/environment-run-001

# 纯验证：缺失或漂移均报错，不补写投影、不调用模型
python3 -m regen.planning.repository_planner \
  --environment-root reports/regen/environment-run-001 --verify-only
```

`verify_bundle()` 从合法输入和已审计 transcript 重新计算全部 plan / file projections，
验证前后身份一致。普通重跑可补回缺失 JSON，已有不同内容则拒绝覆盖；`--verify-only`
不补写产物。audit 最后发布，中断在半套输出时可从匹配 transcript 继续补齐。
输出目录文件锁防止两个规划进程并发写入。恢复需要环境产物和完整 transcript；环境产物
本身丢失时先使用上一个模块的恢复机制。

恢复后的 frozen policy 可显式使用
`--policy <环境目录>/Environment-Freeze/frozen-inputs/regen/config/repository-planning-policy.json`，
不要求去原始证明 source 查找输入。

## 6. 兼容与验证范围

`legacy/session_planner.py` 中旧 schema 1 的 candidate-edge、SCC、原 owner 排序与 ledger 函数仍用于
已有实验和测试；只有显式使用相应冻结旧 policy 才进入旧 CLI 流程。旧 schema 不声称具备
上述新能力。新旧文件列表的 artifact type 与文件名不同，不隐式转换。

[test_repository_planner.py](../../tests/current/test_repository_planner.py) 使用环境准备实际产物，
覆盖上下游身份绑定、多个生成文件、非叶辅助节点、auxiliary-only theory、目标不变、非法
引用、循环、预算、错误反馈、投影漂移和 transcript 恢复。只替代外部模型进程；实际
transcript 生成、hash/no-tool 审计及 bundle 重建均执行。测试不是一次真实模型规划实验。

默认 v3 的 proposal/plan/projection 版本为 3，file list 为 4；input/audit 封装继续使用版本 2。
历史 policy v2 保留原 prompt、节点图规范化和 bundle 重放路径；旧产物不转换、不改写。
既有 `live-environment-002` 的规划仍是 v2，新默认 policy 不能冒用其冻结哈希。

[test_theory_plan.py](../../tests/current/test_theory_plan.py) 覆盖 v3 直接 theory DAG、数组优先级、
非法输入、零边/零意图、辅助专用投影、审计重放和 Runner 初始化。连同独立冻结改造，全套 356 项测试通过。
这些测试使用模型替身；真实模型规划结果见下文，完整证明 campaign 尚未执行。

## 7. 独立规划冻结与调用前准备

[planning_freeze.py](../../planning/planning_freeze.py) 保存 `Planning-Freeze/manifest.json` 及代码快照，
绑定原 environment manifest、gate/dataflow 身份、规划政策和既有预算；freeze ID 进入实际模型
输入并通过 input hash 绑定方案、审计和下游投影。它不宣称旧环境执行过新政策。
已有输出不能替换政策；生成时 live 代码变化要求新输出目录。恢复/下游读取校验冻结字节，
不要求当前政策文件仍为旧版本；CLI 默认读取该规划的冻结政策，显式 `--policy` 则必须匹配。

```bash
# 只验证并保存合法输入，不调用模型
python3 -m regen.planning.repository_planner \
  --environment-root reports/regen/live-environment-002 \
  --output reports/regen/repository-planning-v3-001 \
  --freeze-planning --prepare-only
```

`--prepare-only` 与 `--allow-external-call` / `--verify-only` 互斥，输出 `prepared`，不生成
已接受方案。完整回归现为 356 项通过，包含新政策绑定、环境不变、代码漂移拒绝、恢复及
准备后继续调用的测试。用户确认授权后，真实 v3 模型调用已完成并通过审计。

## 8. 已完成的真实 v3 规划

`reports/regen/repository-planning-v3-001` 复用 `live-environment-002`，独立冻结政策与代码，
未重建 heap。输入为 287 contracts 和 31 条预算内合法 premises；gpt-5.6-sol 一次调用约
538 秒，首次结构验证通过，no-tool 审计通过。

产出 18 theories、18 辅助意图、48 条 theory 建议边，覆盖全部固定 contracts；辅助意图
关联 96 个不同目标。3 个 theories 合并了多个原 owner，没有辅助专用 theory。
`QUALITY-REVIEW.md` 提供完整顺序、理由和仅供展示的传递约简 DAG，`quality-review.json`
提供派生统计；这些审阅文件不是额外运行状态权威。

Plan ID：`repository-plan:2f71aca32c9b333c7f8b`。固定命题不变和结构通过不意味着证明通过；
精确 helper 命题、局部图和 Isabelle 验收仍属于下一模块。

真实环境下完整 bundle 重放及全部 18 份 File Planner 投影消费通过，结果保存在
`handoff-audit.json`；该检查不调用 File Planner 模型或 Isabelle。
