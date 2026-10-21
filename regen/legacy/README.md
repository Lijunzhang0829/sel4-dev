# 历史实现

这些代码是本分支已有的旧实验入口，保留用于复现。它们的输入政策、campaign
目录与 seal 格式不等同于当前 Semantic Base 链路，不作为当前能力的验收依据。

| 目录 | 原用途 | 主要入口 |
|---|---|---|
| `refine_arm_session/` | Refine ARM 的 framework / milestone 分层生成、检查、审计与封口 | `run_theory_generation.sh`、`theory_preflight.py`、`theory_checkpoint.py`、`session_seal.py` |
| `mode_r/` | Mode R 增量 theory 生成、premise 数据库、结构探针与指标 | `regen_incr.py`、`premise_db.py`、`structural_composition_probe.py`、`repo_metrics.py` |
| `whole_theory/` | 整个 theory 的提取后剥离、生成与错误分诊 | `regen.sh`、`regen_strip.py`、`regen_agent.py`、`regen_triage.py` |

旧实现复用当前 `preparation/regen_extract.py` 和 `infrastructure/` 公共设施。
这里保留原算法与实验专用产物路径，只更新源码位置、导入和启动路径。
运行 Python 入口优先从仓库根目录使用 `python3 -m regen.legacy.<目录>.<模块>`。
历史运行记录见 [docs/history](../docs/history/)，测试见 [tests/legacy](../tests/legacy/)。
