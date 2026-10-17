# 机器 B 实验路径索引（机器 A 留存）

本文件只记录配置和结果的位置，不复制机器 B 的实验输出。路径取自此前的启动、迁移及结果归档回执；本次未重新验证 B 的在线状态。机器 A 自己的 shared-proof-workspace 实验、源码和本地原生检查产物不属于此索引。

## 通用配置

- A 侧 VERO 48 小时规则：`regen/config/vero-global-exploration-limits.json`。
- A 侧通用 Codex/VERO 应用入口：`regen/tools/generic_serial_application.py`、`regen/tools/vero_serial_application.py`。
- B 侧统一应用根目录：`/data/zljj/vero-a-unified/`。具体配置以各冻结发布目录中的 launch binding / prelaunch 文件为准，不能用当前源码配置覆盖历史运行。

## 较新运行结果

| 方法及运行 | 机器 B 的结果目录 |
| --- | --- |
| Codex Astra run003 | `/data/zljj/vero-a-unified/serial-application-20261001-astra-003/codex-astra-medium-003/` |
| Codex Sol 007（部分运行） | `/data/zljj/vero-a-unified/codex-active-wall-sol-20261011-007/codex-sol-medium-active-wall-48h-007/` |
| Codex Sol 009（007 审计续跑） | `/data/zljj/vero-a-unified/codex-active-wall-sol-20261012-009/codex-sol-medium-active-wall-48h-009/` |
| VERO Sol 002 | `/data/zljj/vero-a-unified/vero-active-wall-sol-20261009-002/vero-sol-medium-active-wall-48h-002/` |
| VERO Sol 004 | `/data/zljj/vero-a-unified/vero-active-wall-sol-20261010-004/vero-sol-medium-active-wall-48h-004/` |
| 两小时 Codex/VERO 稳定性测试 | `/data/zljj/vero-a-unified/experiment-stability-20261015-005/`（`codex-sol-medium-smoke-2h-001`、`vero-sol-medium-smoke-2h-001`） |

## 旧最终结果档案

机器 B 根目录：`/data/zljj/vero-final-results/20260929/results/`。下列 23 个子目录曾复制到 A 并完成 SHA-256 核验；该归档只保留历史最终结果，不含被清理的过程日志。历史 A/B 根清单 SHA-256 为 `8b7e87d30b168e61cd472a4dcbd5bd2155e86ee3d150d6e60e14f671ae790d1d`。

```text
codex-audit-diagnose-20260923
codex-audit-fixed-20260923
codex-audit-replay-20260923
codex-audit-total300-20260923
codex-freeform-astra-001
codex-repository-sol-001
codex-repository-sol-001-preflight-bridge-failed
codex-resource-sol-001
codex-resource-sol-002
codex-unified-sol-003
codex-unified-sol-004
refine-001
refine-002
refine-attribution-001
refine-continuous-001
refine-continuous-sol-001
refine-continuous-validation-001
refine-contract-retries-001
refine-contract-retries-002
refine-incremental-sol-001
refine-parity-001
refine-phase-sol-001
refine-plan-001
```

这些目录包含正式实验与定向验证，不能相加作为一次运行的成绩。A 上旧档案副本与 B 运行的派生分析材料已按 2026-10-17 的精简请求清理；需查看 proof body、完整运行配置或日志时，应访问 B 的对应冻结目录。
