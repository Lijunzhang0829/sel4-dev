# 数据索引(逐条四元组;B 机 coevolve 分支,repo /data/zljj/sel4-dev)

> 读法:路径前缀 `coevolve/` 省略。实例 bundle 内含
> `meta.json / delta_artifact.diff / delta_proof.diff(held-out) /
> gt_lemmas.json / adjudication-pair.json(+logs)`。
> 判决入账 commit:AInvs 波=2c17d8f(2024)/3121736(2026);
> Refine 波=355c525(2026)/facdc58(2024);CRefine 波=ccc8fb9(2024)/75b462d(2026);
> X-相关性标签=fec8514。

## 31 实例判决总表

### 2024 帧(`cases-pair/<id>/`,l4v@00d9073f7,容器默认 isabelle)

| id | session | 判决 | 备注 |
|---|---|---|---|
| 7713dffcc | AInvs | **FORCED** | aspec sync with Haskell(2f) |
| 8d04c5b8a | AInvs | **FORCED** | added cache op |
| e48a0419d | AInvs | **FORCED** | restore_virt_timer |
| 20fad5b9f | Refine | **FORCED** | Arch_R 级联 |
| 7154cc9d3 | Refine | **FORCED** | proof failure |
| c4dee689b | Refine | **FORCED** | proof failure |
| 1ea097a7b | Refine | **FORCED** | sorry-discharge shape |
| a88bf412a | Refine | **FORCED** | sorry-discharge shape |
| 192f9f932 | CRefine | **FORCED** | vcpuEnable ccorres |
| 81dfca882 | CRefine | **FORCED** | sorry 复活("Cheating…") |
| c85c8ae24 | CRefine | **FORCED** | fastpathKernelAssertions |
| dcdd95dda | CRefine | **FORCED** | isFpuEnable_ccorres 未定义 |
| 04a4b37a1 | AInvs | CHOICE·**related** | irq_len 魔数消除(用了 X 的新标识符) |
| 3981e9a60 | AInvs | CHOICE·unrelated | session 可用性调整 |
| 9d5c8be3d | AInvs | CHOICE·unrelated | FIXME 清理 |
| 76c5eeb50 | CRefine | CHOICE·unrelated | lemma 搬家 |
| 61bce8351 | Refine | **QUARANTINED-LOADFAIL** | 重建 artifact:C_p 仅新建 Corres.thy,后续 commit import 依赖 |

### 2026 帧(`cases-pair-2026/<id>/`,l4v-2026@1ad54b0bf,Isabelle2025,**post-cutoff 去污染**)

| id | session | 判决 | 备注 |
|---|---|---|---|
| 5206126ce | AInvs | **FORCED** | VTIMER_UPDATE_VOFFSET(9f) |
| 6124f6d90 | AInvs | **FORCED** | SGISignalCap(7f,3 个 C_a 共享) |
| f784412d4 | AInvs | **FORCED** | asid_map(12f,L2 陈述演化形态) |
| 4d1121081 | Refine | **FORCED** | canonicalAddressAssert(41f 跨架构级联) |
| be9e4cd40 | Refine | **FORCED** | explicit-vcpu |
| ff1be5122 | Refine | **FORCED** | vcpu flush set_domain |
| 666ec75f6 | Refine | **FORCED** | SMC caps(Refine 侧) |
| 656b27d96 | CRefine | **FORCED** | SMC caps(IRQControlCap ccorres) |
| eea82a2a6 | CRefine | **FORCED** | Ctac_lemmas_C 级联 |
| b7db8a119 | AInvs | CHOICE·**related** | explicit vcpu 适配 |
| c1689bec5 | AInvs | CHOICE·**related** | arch_prepare_next_domain |
| 06d04b344 | AInvs | CHOICE·borderline | 泛词重叠,session-infra 编辑 |
| a3a4e2152 | AInvs | CHOICE·unrelated | lemma 移动 |
| 617ea042e | Refine | CHOICE·unrelated | Arch 前缀重命名(其 forced 性疑在 ARM 架构) |

## 挖掘与筛查产物

| 产物 | 位置 | 入账 commit |
|---|---|---|
| 全历史 pair 候选(496)+co-change(380) | `mining/pairs.json` / `pairs-summary.md` | 0bb9866 |
| post-2025 筛查(16/45 干净) | `mining/revert-screen.json` | ce5ea45 |
| B-frame 筛查(25/54 干净) | `mining/pairs-bframe.json` / `revert-screen-bframe.json` | 76083be |
| 各批判决汇总 | `cases-pair{,-2026}/batch-adjudication-summary.json`(注意:同目录后批覆盖前批,以各实例 adjudication-pair.json 为准) | 各波 commit |
| build 日志 | B:`logs/batch-adjp-*.log`+`logs/adjp-build-*.out`(**gitignore,不入库**;仅 B 本地) | — |

## 工具链(`coevolve/scripts/`)

| 工具 | 作用 |
|---|---|
| `pair_mine.py` | C_a(artifact-only)→C_p(proof-only,gap≤3)全历史挖掘 |
| `revert_screen.py` | stage-1 零 build:C_p 能否干净 reverse-apply 到帧树 |
| `pair_extract.py` | bundle 提取(实例身份=C_p,多 C_a 合并 triggers) |
| `batch_adjudicate_pair.py` | stage-2 判决:session-build oracle,ADJP_* 环境变量参数化(L4V/L4V_C/CASES/SESSION/ISABELLE/两级 timeout);已含 added/deleted-file、ROOT-swap、TIMEOUT 处理 |

## 帧与 heap 资产(B 机)

| 资产 | 位置 | 说明 |
|---|---|---|
| 2024 帧树 | `verification/l4v`@00d9073f7 + 容器内 seL4@cd6d3b8c2(13.0.0) | 战役原生环境 |
| 2026 帧树 | `verification/l4v-2026`@1ad54b0bf + `verification/seL4-2026`@f6978b12 | manifest 精确配对(含重生成的 config/cspec/语法产物) |
| Isabelle2025 | `Isabelle2025/`(容器 /workspace/Isabelle2025) | 2026 帧专用 |
| AARCH64-2024 heap 链 | `heaps-backup/aarch64-2024/`(07-13 全链) | 恢复目标=容器 /root/.isabelle/heaps/…(**主 home,两版 isabelle 都只认它**) |
| ARM 原件 | `heaps-backup/arm-image/` + docker image 兜底 | ARM 波前必须先恢复 |
| 2026 heap 链 | `heaps-backup-2026/`(Word_Lib..Refine;CRefine 按需重建) | Isabelle2025 home 版本自命名空间 |

## 设计文档(B:reports/proof-maintenance/)

- `DIRECTION.md`(a7d7326)· `BENCHMARK-SPEC.md`(451ae47+5ec8717)·
  `BASELINE-NAIVE-SKILL-ARM.md`(0bb9866)· `POSTMORTEM-strengthening-dead.md`
  (76920cb)· 战役叙事日志=`coevolve/README.md`(逐波追加,含 15+ 项踩坑账)。
