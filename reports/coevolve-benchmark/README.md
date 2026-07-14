# coevolve-benchmark — 本地档案(A 机):change-induced 修复 benchmark 战役

> 本目录是 **索引+快照**,不是数据本体。数据与文档的**单一真相源在 B 机
> coevolve 分支**(`$SSH_B`,repo `/data/zljj/sel4-dev`,见仓库 `.env`)。
> 本地快照仅为离线可读;任何冲突以 B 分支对应 commit 为准。

## 回溯规则(可控可回溯的约定)

每条实验数据/结论用四元组定位:**(机器, 分支, commit, 路径)**。
本目录内一律写作 `B@<commit>:<路径>`,例如
`B@3121736:coevolve/cases-pair-2026/f784412d4/adjudication-pair.json`。
在 B 机核验:`$SSH_B 'cd /data/zljj/sel4-dev && git show <commit>:<路径>'`。

## 文档地图

| 文件 | 内容 | 真相源 |
|---|---|---|
| [00-DIRECTION.md](00-DIRECTION.md) | 研究方向快照(反应式证明维护) | B@a7d7326:reports/proof-maintenance/DIRECTION.md |
| [01-SPEC.md](01-SPEC.md) | benchmark 规格 v1.1 快照(规模/形式/接口/颗粒度/呈现) | B@5ec8717:reports/proof-maintenance/BENCHMARK-SPEC.md |
| [02-PROGRESS.md](02-PROGRESS.md) | 战役进展账本(15 commit 时间线+各波记分板) | B 分支 git log(0bb9866..5ec8717) |
| [03-DATA-INDEX.md](03-DATA-INDEX.md) | **数据索引**:31 实例判决表/挖掘产物/工具链/heap 资产/build 日志 | 逐条四元组 |

## 与本地其他目录的关系

- `reports/proof-maintenance/sel4_proof_maintenance_progress_2026-07-11.md`:
  本战役立项前的方向探索记录(前史),被 00-DIRECTION 快照取代。
- `reports/repair/`:694-commit 分布实证(方向的经验地基),本战役的挖掘
  (pair_mine)是它的全历史扩展。
- 审计格式约定见 `reports/audit-framework/README.md`;B 机实例 bundle
  (delta_artifact/delta_proof/meta/gt_lemmas/adjudication)即该约定在
  benchmark 语境的实例化。

## 当前状态一句话(2026-07-14)

挖掘 496 候选 → 45 bundle → **31 判决 = 21 FORCED(9 去污染)/ 3 related-choice
/ 其余审计记录**;双帧(2024/2026)× 三层(AInvs/Refine/CRefine)全覆盖;
判决管线全自动;下一步 bench CLI → 选择性回撤 → Track S → naive baseline。
