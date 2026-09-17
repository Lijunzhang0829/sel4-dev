# 仓库规划实现

当前默认链路只支持独立的 Refine 实验。Refine 与 CRefine 是两份数据集；Refine 的输入、
premise 库、plan、运行状态和评价均不读取 CRefine，也不继承另一实验的生成结果。

## 1. 数据准备

入口为 [planning_input.py](../../preparation/planning_input.py)：

```bash
python3 -m regen.preparation.planning_input \
  --l4v verification/l4v \
  --contracts <prepared-contracts.jsonl> \
  --premises <broad-parent-export.jsonl> \
  --output <refine-planning-input>
```

准备模块直接消费已经去除 Refine 普通人类证明的 seL4 树，不创建或暴露 environment ID。
它输出：

- `repository-planning-input.json`：完整 contracts、紧凑 contract cards、ROOT session closure、
  Refine theory import graph、context profiles 和确定性 relation candidates；
- `parent-premises.sqlite`：只读父 session premise 库；
- `parent-premises-manifest.json`：记录数、内容身份和过滤政策。

旧 premise export 的 `owner_session` 实际可能是 theory 标签。准备模块不信任该字段，而是用
ROOT 搜索路径将 `owner_theory` 重新归属到 session。只保留 Refine 的严格上游 closure；
Refine/CRefine 来源以及无法确认归属的记录均排除，并在 `selection_audit` 中计数。
输入校验同时拒绝重复 contract、非 Refine contract、proof/source 字段和过大文本。

## 2. Planner 的受限接口

[repository_planner.py](../../planning/repository_planner.py) 的直接入口是 `--planning-input`。
Planner 不能读取任意文件，只能通过以下只读接口取得证据：

- `inspect_contracts`：按最多 32 个 ID 返回完整 statement/context；
- `search_parent_premises`：按合法 context profile 检索，单次最多 50 条；
- `inspect_parent_premises`：展开已返回 ID 的完整记录；
- `resolve_symbols`：在父 premise 索引中报告唯一、歧义或未找到；
- `validate_repository_plan`：由结构 validator 实现，不交给模型自报。

模型分两次工作。第一阶段只看紧凑 cards、关系候选和 session/import 摘要，必须把每个 contract
恰好放入一个 inspection batch，并提出最多 20 个 premise 查询。第二阶段只接收展开后的证据，
输出 theory 级方案。验证失败时只反馈短错误，不累积检索 transcript。

## 3. Repository plan

当前 proposal schema 为 4。它包含：

- evidence-backed `contract_relations`；
- 每个生成 theory 的完整 contract 分配和 context profile；
- `reuse_existing_fact` 或 `propose_auxiliary_intent` 两类 helper 决策；
- 显式 theory provider 边、ready tie-break priority 和简短理由。

Repository Planner 不得生成精确 helper statement、proof、tactic 或 contract-level DAG。
复用结论必须引用本轮已 inspect 的合法 premise ID；新的 helper 在本阶段仅为 intent。
[theory_plan.py](../../planning/theory_plan.py) 检查 contract exact-once、证据 ID、context profile、
theory DAG 和 priority，并生成 `repository-plan.json`、`file-generation-list.json` 与
`files/<theory>.json`。

## 4. Plan 身份与 revision

每次独立运行由 [plan_revisions.py](../../planning/plan_revisions.py) 创建一个稳定 `plan_id`。
权威历史位于：

```text
planning/<plan_id>/revisions/0001.json
planning/<plan_id>/HEAD.json
repository-plan.json                 # 当前投影
```

同一运行的 revision 递增并以 `parent_revision_id` 串联；`revision_id` 是不可变内容身份。
已经启动的 theory 绑定其 revision，后续 revision 不能改变或删除该 theory 及其 incident edges。
恢复沿用同一个 plan；新的独立运行取得新的 plan ID。普通更新不使用 `supersedes_plan_id`。

## 5. File Planner 与执行交接

File projection 只冻结该 theory 的 contracts、context profile、显式 provider theories、已检查的
父 premise IDs 和辅助 intents。File Planner 在 theory 第一次 ready 时运行一次，补出精确的
局部 statements 与 DAG。每个生成节点必须记录 semantic role、reasoning gap、intended
consumers，以及 premise evidence IDs 或明确查询。初始图经真实 statement elaboration 后提交为
revision 1；此后只有 Graph Revision Agent 能修订图。

直接 file list 使用 schema 5，并把每份 projection 绑定到同一个 plan revision。Runner 只导入
显式且已 seal 的 provider theories。`--max-parallel-theories N` 可并行执行 DAG 中同时 ready 的
theories；每个 theory 使用独立 checker 工作目录和 heap 副本。发布仍只发生在完整 theory seal
之后，生成 premise 不写入不可变父数据库。

## 6. 运行方式与兼容路径

```bash
python3 -m regen.planning.repository_planner \
  --planning-input <refine-planning-input/repository-planning-input.json> \
  --output <repository-plan-output> \
  --allow-external-call
```

`--prepare-only` 只验证并保存输入，不调用模型。`--planning-input` 不能与旧环境参数混用。
基于 environment gate/dataflow 的 schema 2/3 路径暂时保留，只用于已有冻结实验的兼容重放；
它不是新 Refine 实验的默认接口。schema 5 执行器读取数据集根目录的 `checker-context.json`
和 `checker-assets/`，不读取 environment gate 或 environment ID。`checker_context.py` 可把已有
不可变 toolchain/heaps 以链接方式一次性纳入该目录，避免复制大型资产；旧 schema 兼容代码
仍保留，但不再是新 Refine 运行的依赖。

主要测试为 `test_planning_input.py`、`test_plan_revisions.py`、`test_theory_plan.py`、
`test_repository_planner.py` 和 `test_repository_execution.py`。
