# 统一 owner-free 数据集输入契约（Refine / intervaltree / c-algorithms）

状态：已实现。CRefine 后续以 profile 方式扩展。
本文定义独立数据集的目录布局、文件 schema、哈希绑定和
`preparation.json` receipt。Refine、intervaltree、c-algorithms 的 canonical
输入已统一为 owner-free 契约：contract 不再携带 `owner_theory` /
`owner_file_slot` / `normalized_statement_sha256`，`owner_groups`、
`session_graph`、`theory_import_graph` 为空。本文不改动已冻结产物，也不重跑
Isabelle 或 Docker。

## 1. 目标与范围

- 把 Refine 的 11 阶段环境准备产物，包装成与 `intervaltree_dataset.py` /
  `calg_dataset.py` 同构的独立数据集目录。
- 统一 owner-free 输入标准：canonical contract 完全删除 spec 到原 theory 的归属关系。
- 统一 canonical contract 字段白名单：`contract_id, name, kind, exact_statement,
  contract_owned_context, contract_owned_attributes, contract_owned_attributes_raw`。
- 统一 premise catalog 到 `premise_database.SCHEMA` 的 v2 格式。
- 收口 `preparation.json` 为一个可机器校验的统一 receipt。

非目标：本步不实现完整的 `refine_dataset.py prepare` 物化逻辑，也不处理
CRefine 的特殊语义（`init_refinement_C`、`value_type`、父定义 aliases、303+6
目标拆分）。

## 2. 目录布局

```text
<dataset>/
  checker-assets/
    l4v           -> <frozen-env>/Runtime/l4v          (symlink, read-only)
    semantic-base -> <frozen-env>/Semantic-Base/workspace
    heaps         -> <frozen-env>/Semantic-Base/heaps
    isabelle      -> <isabelle install>
  parent-premises.sqlite
  parent-premises-manifest.json
  premise-audit-context.json
  repository-planning-input.json
  checker-context.json
  preparation.json
```

私有映射单独存放，不进入数据集目录，也不进入任何模型 workspace：

```text
<private-map>/refine-private-map.json
```

约束：

- `checker-assets/*` 只做 symlink 绑定，不复制 l4v / workspace / heaps。
- 目录内 symlink 目标不得逃出冻结环境或 Isabelle 安装；目录级链接须先物化。
- 公开数据集不含 `frozen-inputs/`、`.preparation/`、private inventory、
  source locations、原 proof imports。

## 3. `repository-planning-input.json`（owner-blind canonical）

schema 沿用现有 `repository-planning-input`，但统一到匿名规范：

```json
{
  "schema_version": 1,
  "artifact_type": "repository-planning-input",
  "benchmark": {
    "id": "sel4-refine-arm",
    "target_session": "Refine",
    "architecture": "ARM",
    "independent_experiment": true,
    "cross_experiment_inputs": []
  },
  "contract_visibility": "owner-blind-v1",
  "contracts": [
    {
      "contract_id": "goal:<sha256-24>",
      "name": "<original lemma name>",
      "kind": "lemma",
      "exact_statement": "<frozen statement>",
      "contract_owned_context": ["context ... begin"],
      "contract_owned_attributes": [],
      "contract_owned_attributes_raw": "",
    }
  ],
  "contract_cards": [
    {"contract_id": "goal:<sha256-24>", "name": "<original lemma name>"}
  ],
  "owner_groups": {},
  "session_graph": {},
  "theory_import_graph": {},
  "upstream_session_closure": ["<parent sessions>"],
  "contract_relation_candidates": [],
  "context_profiles": [
    {
      "context_profile_id": "context:Refine:parents",
      "target_session": "Refine",
      "upstream_sessions": ["<parent sessions>"],
      "generated_import": "Semantic_Base.Semantic_Root"
    }
  ],
  "parent_premises": {
    "database": "parent-premises.sqlite",
    "manifest": "parent-premises-manifest.json",
    "record_count": 0,
    "record_ids_sha256": "<sha256 of sorted record ids>",
    "database_sha256": "<sha256 of sqlite file>",
    "query_interface": "proof-state-recall-v2"
  },
  "planner_interfaces": {
    "inspect_contracts": {"max_ids": 32},
    "search_parent_premises": {"max_results": 50},
    "inspect_parent_premises": {"max_ids": 50},
    "resolve_symbols": {"max_symbols": 64},
    "validate_repository_plan": {}
  },
  "planning_input_id": "owner-blind-input:<sha256-20>"
}
```

不变量：

- `contract_visibility == "owner-blind-v1"`。
- 每个 `contract_id` 以 `goal:` 开头，且全局唯一。
- 每个 contract 不得包含 `owner_theory`、`owner_file_slot` 或任何原始归属字段。
- `owner_groups == {}`、`session_graph == {}`、`theory_import_graph == {}`。
- `contract_cards` 只含 `contract_id`、`name`，不得含 `owner_theory`。
- 不含 `FORBIDDEN_PAYLOAD_KEYS`（proof / proof_body / human_proof /
  original_proof / source_location / trusted_source_path）。

## 4. `parent-premises.sqlite` 与 manifest

统一使用 `regen/preparation/premise_database.py` 的 v2 `SCHEMA`
（`PRAGMA user_version=2`，10 列表格 + FTS5 检索表）。不再使用
`planning_input.py` 中的 22 列旧 `PARENT_SCHEMA`。

```text
parent-premises-manifest.json
{
  "schema_version": 2,
  "artifact_type": "parent-premises-manifest",
  "record_count": 0,
  "database_sha256": "<sha256>",
  "record_ids_sha256": "<sha256 of sorted ids>",
  "context_profile_ids": ["context:Refine:parents"],
  "planning_input_id": "<planning_input_id>"
}
```

不变量：

- `verify_database(path) == 2`。
- parent 中不得出现 `Refine` / `CRefine` target-session fact；
  `owner_session` 必须落在 Refine 父闭包内，`source_class` 合法。
- 与 `checker-context.json` 的 `premise_database_sha256` 互相绑定。

## 5. `checker-context.json`

沿用 `regen/preparation/checker_context.py` 的 `refine-checker-context` 结构：

```json
{
  "schema_version": 1,
  "artifact_type": "refine-checker-context",
  "target_session": "Refine",
  "architecture": "ARM",
  "paths": {
    "l4v": "checker-assets/l4v",
    "semantic_workspace": "checker-assets/semantic-base",
    "heaps": "checker-assets/heaps",
    "isabelle": "checker-assets/isabelle"
  },
  "premise_database": "parent-premises.sqlite",
  "premise_audit_context": "premise-audit-context.json",
  "base_session": "Semantic_Base",
  "base_import": "Semantic_Base.Semantic_Root",
  "base_catalog": "/semantic-base",
  "image": "<image id>",
  "checker_policy": "fixed-prefix-cold-green-v1",
  "budgets": {},
  "checker_context_id": "checker-context:<sha256-20>"
}
```

## 6. `preparation.json` receipt（统一契约）

```json
{
  "schema_version": 1,
  "artifact_type": "refine-dataset-preparation-receipt",
  "status": "prepared",
  "target_session": "Refine",
  "architecture": "ARM",
  "contract_visibility": "owner-blind-v1",
  "target_count": 287,
  "source_environment_gate_id": "<gate_id>",
  "source_dataflow_id": "<dataflow_id>",
  "same_fixed_statements": true,
  "target_proofs_added": 0,
  "parent_facts": 0,
  "quarantined_facts": 0,
  "premise_database_sha256": "<sha256>",
  "planning_input_id": "<planning_input_id>",
  "checker_context_id": "<checker_context_id>",
  "synthetic_owner_is_original_mapping": false,
  "original_owner_groups_and_imports_removed": true,
  "excluded": [
    "proof/refine original theories",
    "ordinary proofs and proof helpers",
    "private command inventory",
    "original proof import graph"
  ]
}
```

必填字段由 `regen/preparation/refine_dataset.py` 的
`PREPARATION_REQUIRED_FIELDS` 定义；`target_count` 不能硬编码为历史 287，
应以本次冻结数据流的 `counts.contracts` 为准。

## 7. 哈希与身份链

```text
environment_gate.json  (gate_id)
  -> environment-index.json (dataflow_id, environment_gate_id)
  -> repository-planning-input.json (planning_input_id, database_sha256)
  -> checker-context.json (checker_context_id, premise_database_sha256)
  -> preparation.json (receipt binds all of the above)
```

任何一层改动都必须导致下游身份变化；`planning_input_id`、
`checker_context_id`、`premise_database_sha256` 与 receipt 必须一致。

## 8. 与现有代码的关系 / 开放决定

1. `owner_groups` 已统一为空 `{}`，`session_graph` / `theory_import_graph`
   也为空；`intervaltree_dataset.py` / `calg_dataset.py` /
   `calg_shuffled_dataset.py` 已对齐。
2. canonical contract 已完全删除 `owner_theory` / `owner_file_slot`。遗留的
   `owner_blind_dataset.vero_benchmark` 仍假设合成 owner 存在，后续单独收敛。
3. legacy `repository_planner.make_input()` 已在 owner-blind 模式下剥离
   `owner_theory / owner_file_slot / normalized_statement_sha256`，并移除
   `original_owner_groups`。
4. `planning_input.py` 的 22 列 `PARENT_SCHEMA` 与 `premise_database.SCHEMA`
   v2 并存；Refine 独立数据集只使用 v2。
