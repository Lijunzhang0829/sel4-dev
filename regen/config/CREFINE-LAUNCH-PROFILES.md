# CRefine 实验启动配置

本目录的日期化 shell 脚本记录机器 B 上 CRefine 实验的冻结启动与恢复参数。它们引用特定部署目录、数据集、模型客户端及外部认证文件；运行前需要对应部署已经准备完毕。脚本只保存认证文件路径，不保存凭证内容。

所有配置使用 309 个固定 contracts、`gpt-5.6-sol` / `medium` 和 172800 秒有效生成预算。恢复配置保留已验证成果、累计生成时间及 token 用量，不重新获得完整预算。

| 脚本 | 用途 |
|---|---|
| `vero-crefine-slots2-20261019-launch.sh` | 原始 VERO 新运行，两个本地 native 槽 |
| `vero-crefine-pool2-resume-20261020.sh` | 经审计恢复原 VERO 运行，将本地容量改为共享双槽池中的一个槽 |
| `codex-crefine-pool2-continue-20261020.sh` | 从指定暂停运行建立 Codex continuation，审计共享池迁移 |
| `vero-crefine-independent-auth-pool2-20261021.sh` | 使用独立 VERO 认证链恢复；只在已有 journal 为 `agent_error` 时启用受审计重开 |
| `codex-crefine-pool2-fresh-20261021.sh` | 在共享双槽池内启动新的 Codex 实验 |
| `vero-crefine-pool2-fresh-20261021.sh` | 使用独立认证链，在共享双槽池内启动新的 VERO 实验 |
| `codex-crefine-pool2-resume-20261021.sh` | 从指定 fresh Codex 运行创建恢复输出，保留规划成本与已记录状态 |

源码更新不会自动更新这些脚本所引用的部署目录。日期化配置与恢复迁移用于对应历史运行；新的配置需要新的运行身份及冻结记录。提交这些脚本不表示运行已启动或完成。

本地 `codex-runtime/` 副本、仓库根目录生成的预检 JSON 及 `.agent-staging/` 操作脚本由 `.gitignore` 排除，仍保存在本地。实验验收口径见 [实验设计](../docs/design/REPO-LEVEL-EVALUATION.md)，有效证据统计见 [GREEN contract 统计](../docs/implementation/GREEN-CONTRACT-ACCOUNTING-20261015.md)。
