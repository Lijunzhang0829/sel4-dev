# 战役进展账本(2026-07-12 开工 → 2026-07-14 现状)

> 每行 commit 均在 **B 机 coevolve 分支**;核验:
> `$SSH_B 'cd /data/zljj/sel4-dev && git show <commit> --stat'`。

## Commit 时间线(数据战役段,0bb9866..5ec8717)

| commit | 日期 | 里程碑 |
|---|---|---|
| 0bb9866 | 07-13 | **R1 全历史挖掘**:496 pair+380 co-change 候选(vs 原 15 seed);post-2026 金子集 20;naive-skill baseline 臂设计 |
| ce5ea45 | 07-13 | stage-1 零 build 筛查:post-2025 16/45 干净可逆 |
| 76083be | 07-13 | **发现 B 树在 2024-07**→B-frame 重筛 25/54 干净;26 bundle;session-build oracle 批处理(check-theory selfqual 对 Arch* 无 patch 也 false-RED 的新 soundness 事实) |
| 2c17d8f | 07-13 | **2024 帧 AInvs 波:3 FORCED/3 CHOICE**(共享 baseline 952s) |
| 3a09950 | 07-13 | era-advance:历史 bundle+l4v-2026 worktree+生成文件三墙(design specs/document tex/Kernel_Config stopgap) |
| bcd353f | 07-13 | era-advance 环境定稿:**Isabelle2025**(非 2025-1);Kernel_Config 重生成 |
| 3121736 | 07-13 | **2026 帧(去污染)AInvs 波:3 FORCED/4 CHOICE**;adjudicator 修复 added-file/ROOT-swap 两类 |
| 355c525 | 07-13 | **2026 帧 Refine 波:4 FORCED/1 CHOICE**(含 41 文件 canonicalAddressAssert);修复 deleted-file 复活类 |
| 451ae47 | 07-13 | **BENCHMARK-SPEC v1**(规模/形式/接口) |
| 21b1b20 | 07-13 | CRefine 双波准备:2024 帧 cspec 产物;**manifest 精确背书本帧**(l4v 1ad54b0bf+seL4 f6978b12+Isabelle2025);配对内核 A→B;C-parser 语法产物墙 |
| ccc8fb9 | 07-14 | **2024 帧 CRefine 波:4 FORCED/1 CHOICE**(heap 链 6868s 建成) |
| 75b462d | 07-14 | **2026 帧 CRefine 波:2/2 FORCED**;infra 定论:两版 isabelle 均忽略 ISABELLE_HOME_USER(heap 污染事故根因),备份按真实架构重组 |
| facdc58 | 07-14 | **2024 帧 Refine 波:5 FORCED+1 隔离**;方法学新增:重建有效性检查+sorry-discharge shape |
| fec8514 | 07-14 | CHOICE 轨 X-相关性切分:3 related+1 borderline+5 unrelated |
| 5ec8717 | 07-14 | **SPEC v1.1**(颗粒度双层+呈现方式环境为本) |

## 漏斗(当前)

```
全历史 6170 commits(2014..2026-07,A 机 ~/l4v-full-bare)
 └─ pair 挖掘: 496 候选(+380 co-change)          [B@0bb9866:coevolve/mining/pairs.json]
     └─ 干净可逆筛查: 2024帧 25/54 · 2026帧 16/45  [revert-screen*.json]
         └─ bundle: 45 个(26+19)                  [coevolve/cases-pair{,-2026}/]
             └─ build 判决: 31 个
                 ├─ FORCED 21(去污染 9)★ 考题
                 ├─ CHOICE 9 → related 3 ▸ 对齐副轨 / unrelated 5+borderline 1 ▸ 审计
                 └─ QUARANTINED 1 ▸ 审计(隔离率 3% = 重建配方质检)
```

## 目标对照(SPEC §1)

| 维度 | 目标 | 现状 |
|---|---|---|
| L1 FORCED | 40-60 | 21(约 1/3) |
| 去污染 FORCED | ≥30 | 9 |
| 每 session 层 ≥15 | AInvs/Refine/CRefine | 6/9(4+5)/6 — 三层全有,量待扩 |
| 合成轨 | 300-1000 | 未启动 |

## 下一步(定序)

1. **bench CLI**(接口规格已定稿,80% 代码可自 batch_adjudicate_pair/repair_driver_tree 重构);
2. **选择性回撤战役**(先 9 去污染 FORCED,文件级,~10 build 时;校准过度修复率);
3. Track S 合成扰动生成器(自 utility/e1-pilot-archive/gen_cases.py 推广);
4. naive-skill baseline 在 21 FORCED 首跑(设计:B@0bb9866:reports/proof-maintenance/BASELINE-NAIVE-SKILL-ARM.md);
5. 量的扩张:放宽 gap 重筛 / ARM 波(**先恢复 arm-image 到主 home**)/ x64。

## 关键运维事实(踩坑账,详见 B@…:coevolve/README.md 各节)

- era-advance 五连墙全清:design specs(纯 Python make)/document tex/
  **Isabelle 版本**(master 用 2025 非 2025-1;tarball 在 website-<REL>/dist/)/
  **Kernel_Config**(配对内核+gen-config-thy.py 重生成)/C-parser 语法产物(mlton);
- **两版 isabelle 均无条件忽略 ISABELLE_HOME_USER**(getenv 实证)——arch 隔离
  从未存在;ARM 波前必须先恢复 heaps-backup/arm-image 到主 home;
- 失败的实例 build 会**删除该 session 的 heap**(Isabelle 行为)——每波后立即
  按架构分目录备份;
- oracle:check-theory selfqual 对 Arch* theory 不 sound(无 patch 也 false-RED)
  → 一律 session-build oracle(换文件→增量 build→还原+树清洁断言)。
