# 方向快照:seL4 自动化反应式证明维护

> 快照源:**B@a7d7326:reports/proof-maintenance/DIRECTION.md**(canonical,
> 2026-07-12 定稿)。此处为要点摘录;全文以 B 为准。

## 一句话

首个 Isabelle refinement 栈上的**自动化反应式证明维护系统**:artifact
(spec/Haskell/C)变更后自动定位断裂、修复证明链、验证全绿,匹配或优于人类
修复;配**首个可复现的 change-induced 修复 benchmark**(即本战役)。

## 动机链

- 痛点:seL4 ~20 人年证明是一次性成本,持续税是维护(每次变更断链手工修)。
- 外部背书:Brugger et al. survey(arXiv 2510.20514)——维护 41% 列顶级障碍、
  "no mature solutions"、RD2 逐字推荐 proof maintenance 工具形态。
- 自有实证(B@…:reports/repair/,694 commit 窗口):proof-touching 52%,
  co-change 仅 4.5%(修复在**独立 commit**,单元=commit 对),86% 单 session
  (L1 主体),CRefine 主战场。
- 反方向证伪:主动增强经三 flavor 净效用测量全部否定
  (B@…:reports/proof-maintenance/POSTMORTEM-strengthening-dead.md)——
  成熟 l4v 无净正主动强化矿脉,连人类"泛化"2/3 是移植驱动=本质反应式。

## 5 moats(为什么不是 naive skill)

1. sound 影响分析(LLM 静态定位 fileP=0.22 不可靠 → build oracle+DAG 增量);
2. 级联不动点编排(修序敏感,DAG 上迭代到 fixpoint);
3. 防作弊语义门(弱化/sorry 会骗绿 → evolution-vs-weakening gate);
4. 抗污染评估(双轨 GT + memorization 控制)——**本战役的核心产出**;
5. 成本/自治管理(预算化+NEEDS-HUMAN+autonomy rate)。

## 已验证能力(战役前置)

修复 3/3 全 shape GREEN(divergent-yet-valid),完整 pipeline e2e
SESSION-GREEN(autonomy=1.0,$1.48/39min),15 项 harness 失败分类学全归因。
详见 B@…:coevolve/README.md 前半部。
