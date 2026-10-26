# CRefine 实验事故复现集 v1

本测试集将 2026-10-24 至 2026-10-27 已记录的 VERO / Codex 运行问题转换为带来源、预期结果和覆盖边界的离线案例，供实现调整前后回归使用。默认运行不访问网络、机器 B 或现有实验目录，不调用模型、Docker 或 Isabelle。临时文件及模拟账本写入临时目录；实际实验的证明、日志、预算和状态不变。

`inventory.json` 登记 44 类已记录问题，`coverage.json` 逐项说明哪些有可执行回归，哪些只有历史统计或文档证据。这不表示 44 类问题都能完整重跑，也不表示全部已修复。

## 运行与比较

从仓库根目录执行，使用已安装项目 Python 依赖的环境：

```bash
# 检查固定文件集合与 SHA-256，再列出可执行案例
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents verify
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents list

# 全套离线回放，保存代码身份、逐例结果和测量值
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents run \
  --output /tmp/crefine-incidents-before.json

# 单独检查真实 Codex batch 628 的结果分类边界
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents run \
  --case codex-batch628-proof-not-timeout \
  --output /tmp/crefine-batch628.json

# 修改实现后使用同一份 fixtures 再运行并比较
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents run \
  --output /tmp/crefine-incidents-after.json
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents compare \
  /tmp/crefine-incidents-before.json /tmp/crefine-incidents-after.json
```

`run` 返回非零状态表示至少一项回放失败。报告保存 fixture manifest 摘要和实现摘要，比较时应先确认 fixture 一致。性能案例输出操作计数或测量值；没有用机器速度相关的固定秒数作为通过阈值。观察型案例通过并不把未解决问题自动变成“已修复”。

`compare` 在同一清单下发现原先通过的回放失败或缺失时返回 `1`，清单不同时返回 `2`，其余返回 `0`。测量值的变化另列在 JSON 中，性能退步需结合这些值判断。请勿使用 `python -O` 或 `PYTHONOPTIMIZE`，运行器会拒绝关闭断言的执行模式。

可导出单例供其他 checkout 使用。输出目录必须尚不存在：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents export \
  vero-transport-frame-16m --output /tmp/crefine-frame-repro
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents \
  --fixtures /tmp/crefine-frame-repro verify
PYTHONDONTWRITEBYTECODE=1 python3 -m regen.tools.replay_incidents \
  --fixtures /tmp/crefine-frame-repro run
```

导出目录包含 `cases/case.json`、该案例全部证据和独立 manifest。回放仍需本仓库代码及 Python 依赖；不再依赖原 `reports/`、机器 B 或在线服务。导出 `history-a-green-gap-observations` 可取得全部 770 条历史公开事件索引。

## 覆盖的含义

| `coverage_kind` | 实际验证范围 |
|---|---|
| `offline_regression` | 调用选定生产函数或现有受控回归，检查边界、失败分类、状态保持等。模型/native 返回可能来自明确标注的 stub 或历史结果。 |
| `documentary_regression` | 对历史摘要、artifact hash、slot 和计数关系执行断言。没有执行完整生产恢复 runner。 |
| `historical_statistics_only` | 对冻结的机器 A 历史公开事件重算计数、时间并集和 worker 用时；没有测试 A 当前实现。 |
| `documentary_only` | 只有事故登记、证据位置或缺失说明。本套案例没有该问题的可执行回放。 |

主要回归包括：VERO 大响应分帧与分页、gzip 完成标记、旧 `PYTHONPATH` 与迁移链、前缀收据和独立重验、预算区间去重、未知崩溃尾部、ENOSPC 原子发布、owner-blind 输入；Codex locale 事实审计绑定、Merkle 复制、step 991 / batch 628 恢复不变式、普通证明 RED 与 timeout 的区别，以及恢复历史放大的操作计数。

实际历史锚点包括 VERO `iter-004/item_1792`、`item_2196`、撤销事件 `12396/12397/12469`、恢复源 `12366`，以及 Codex locale `batch 39`、恢复 acceptance step `991` / `batch 628`。这些标识随案例保留；receipt 检验不能替代重新运行原生证明。

机器 A 关联窗口包含 **770 条 occurrence**：659 次失败工具回调、3 次 provider 无完整反馈、108 次成功但慢的检索观察。这些不是 770 个独立 bug。统计回放把 407 次普通证明失败和后置审计症状分开；249 条 native 排队成分、4 条受阻跨度、4 次自动继续观察单列。排队已经包含在回调用时中，受阻跨度还含其他工作，均不能叠加成收费或退款时间。

## 文件与来源

- `inventory.json`：问题根因或不确定性、状态、机器范围、原始 step、证据摘要、已有测试 ID 和数据可用性。
- `coverage.json`：44 类问题到案例的映射及未覆盖边界。这里列出的已有测试 ID 只是入口；是否执行以具体 case 和本次报告为准。
- `cases/*.json`：schema 1 案例，包含来源、摘录、可执行回放、独立 oracle 和限制。
- `schema.json`：case 的 JSON Schema；运行器另外检查路径、摘要、唯一身份及非空 oracle。
- `evidence/*`：随包携带的公开结构化摘录、必要源码快照、历史回执以及明确标注的合成输入。
- `manifest.json`：固定文件集合、逐文件 SHA-256 及 case ID。增删改动必须显式审阅并更新版本/清单；验证不自动接受新摘要。
- `regen/replay/incident_checks_*.py`：回放实现，按 `transport`、`codex`、`state`、`history` 注册 handler。
- `regen/tools/replay_incidents.py`：验证、运行、导出和前后比较入口。

每份摘录记录自身 SHA-256、原始 `source_path`、原始 `source_sha256` 和字段/行选择规则。原始路径用于追溯，运行时只读包内相对路径。机器 B 的大体积原始日志或完整验收链没有全部复制进仓库；例如 785 MB 日志案例依赖保存的真实分页验证记录，并以离线大帧/日志输入测试传输边界。

数据不包含认证文件、密钥或模型私有推理。历史 A 事件采用字段白名单，只保留 ID、分类、时间、候选身份、公开错误签名和证据路径/行号；不复制原始模型 transcript、prompt 或 reasoning。

随包的历史生成证明仅供离线检查，不应加入新的实验模型输入或替代固定 contracts。

以下限制应随结果保留：

- 旧机器 B 的三个 xhigh 运行目录、两个发布目录及部分更早试运行原始记录已按授权删除，不能从摘要重建完整历史执行。
- 历史 VERO 巨大 `response.json` 已在完整解压 SHA 校验后归档；无损归档本身不是丢失。完整 native 日志的路径依赖不能靠随意删除解决。
- Codex `batch 628` 仍有 5 个子目标是普通证明失败。回放响应分类正确不等于证明成功。
- locale 案例验证保存的原生审计证据及生产 Python 绑定边界；默认不重新执行 ML。前缀案例的受控 checker 回归也不会发布新 GREEN。
- 恢复性能案例没有重建 6.07 GB 逻辑历史，不能据小样本计数宣称真实恢复速度已经改善。
- `state-artifact-reuse` 只校验历史 artifact 摘要不同、变更 slot 和计数；没有验证跨进程缓存，也没有调用生产 runner。
- A 的规划审计、未知静默根因等只登记为 `documentary_only`。机器 A 正在变化的设置和实现不在本次验证范围。
- medium 与 xhigh 历史运行的输入、实现、预算和资源并非完整配对，测试集不能推出推理深度的因果效果。

## 提取与新增案例

先保留原始证据身份，再建立小型可重复输入。历史步骤不明确时写明未知或实验级配置事件，不根据时间猜造 step。原始数据已删除时保留缺失声明；只有模拟复现时明确标注 synthetic。

六个 `state-*` 案例提供确定性提取脚本。在保留原始本地报告的 checkout 中，可重新提取至新目录，或核对当前包是否逐字节一致：

```bash
python3 -m regen.tools.extract_crefine_state_incidents --output /tmp/crefine-state-extraction
python3 -m regen.tools.extract_crefine_state_incidents --check
```

此脚本只负责状态类案例；其余摘录的来源与字段选择保存在各 case 中。正常回放无需运行提取脚本。

schema 1 的最小结构如下，摘要和位置应替换为实际值：

```json
{
  "schema_version": 1,
  "id": "component-specific-incident",
  "title": "具体故障",
  "component": "component",
  "category": "failure_category",
  "status": "open_observation",
  "source_steps": [{"run": "frozen-run-id", "step": 123}],
  "evidence": [{
    "path": "evidence/example.json",
    "sha256": "EXTRACTED_FILE_SHA256",
    "source_path": "original/report/or/native-result.json",
    "source_sha256": "ORIGINAL_FILE_SHA256",
    "selection": {"json_keys": ["public_result", "source_identity"]}
  }],
  "replays": [{
    "id": "specific-boundary",
    "handler": "component.boundary",
    "kind": "evidence_replay",
    "parameters": {"evidence": "evidence/example.json"},
    "oracle": "独立预期结果、必须保留的不变式以及需要拒绝的反例"
  }],
  "limitations": ["准确说明没有复现的环境或行为"]
}
```

handler 采用 `handler(case, fixture_root, scratch) -> dict`，当前 replay 位于 `case['_replay']`。从 `HANDLERS` 注册到 runner；文件路径必须限定在 fixture 包内，临时输出只写 `scratch`。代码入口选择由已审阅 handler 控制，不执行证据中的命令字符串。

oracle 应来自事前保存的 native 收据、不可变账本、历史统计或独立不变式。新增反例应覆盖原故障和相邻不安全行为，例如截断帧、伪造 receipt、时间重复扣除、失效依赖或损坏对象。不能用“实现返回了什么”生成同一轮预期结果。

最后更新 inventory 与 coverage，核对脱敏和来源摘要，显式生成新 manifest，再执行 `verify`、所改案例和必要关联回归。保留实现调整前后的报告。需要真实 native 复验时应另建精确冻结环境的受控任务，并独立报告；本离线入口不会自动连接实验或调用证明器。
