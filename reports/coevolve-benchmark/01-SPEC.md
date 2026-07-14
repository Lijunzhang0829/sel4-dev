# Benchmark 规格快照(v1 + v1.1)

> 快照源:**B@451ae47(v1)+ B@5ec8717(v1.1)
> :reports/proof-maintenance/BENCHMARK-SPEC.md**。全文以 B 为准。

## 1. 理想规模(统计效力倒推,非越大越好)

| 层 | 目标 FORCED | 依据 |
|---|---|---|
| L1 单 session | 40-60 | rate CI: n=50→±14pp, n=80→±11pp(95%);配对 McNemar 需 30-50 对 |
| L2 陈述演化 | 20-30 | 真实修复 11/15 含陈述演化,gt_lemmas 标签切分 |
| L3 跨 session 级联 | 5-10 | 天然 ~1/月,存在性+深度分析 |
| 每 session 层 | ≥15 | 分层报告 |
| **post-cutoff 去污染** | **≥30** | **主测试集**;pre-cutoff=dev |

可达性:496 候选→帧兼容→干净 ~50%→forced ~55% ⇒ 真实轨 50-80 = 现实上界
且足够(对比 CertiGC=1 案例)。合成轨 300-1000(6 扰动类×网格控难度),
仅作消融+开发回归——**纪律:不拿真实实例调试系统**。

## 2. 数据形式

自包含 bundle:`meta.json`(帧指纹+triggers+verdict+证据链)/
`delta_artifact.diff`(输入)/ `delta_proof.diff`(held-out)/
`gt_lemmas.json` / `adjudication-pair.json`。
**断裂态存配方**(基准 commit + reverse-apply)非物化树;`frames/` 定义帧;
`splits.json` 管 dev/test × real/synthetic × L 级。

## 3. 测试接口(三动词,方法/oracle 严格分离)

`bench init`(物化断裂 worktree,**剥 .git 防翻历史泄漏**,返回树+Δ_artifact
+RED 集)→ `bench check`(session-build oracle,计次计时=成本账)→
`bench submit`(端到端 build+防作弊门+记指标)→ `bench score`。
所有臂(naive/full/第三方)消费同一接口。

## 4. 颗粒度(v1.1):任务 commit 级 / 计分处级

- 任务=commit 级完整断裂(部署现实;error frontier 串行;处边界=影响分析
  的产物不能预切)。
- 计分=处级**选择性回撤**:对 C_p 分解 forced-places/choice-places(参考
  答案=必要子集);对方法 patch 同型回撤→**过度修复率**+最小性(="改进
  是否真有用"的可靠度量;即净效用三臂反事实法在修复语境的复用)。
- 部分学分(error-frontier 推进)仅次要指标,headline=二值 restore-green+成本。

## 5. 呈现方式(v1.1):环境为本

正典=指定 seL4 环境(bench CLI+计量 oracle)。三理由:上下文选择=任务的
一半(moat#1,预打包=替考);修复=迭代闭环(静态包退化为一发式预测);
agentic 检索下无上下文长度问题(asid_map 12 文件塞不进 context=环境形态
论据)。lite 静态快照=仅小实例衍生品,评分回 oracle。

## 6. 判决类别定义(与 03-DATA-INDEX 对应)

- **FORCED**:撤 C_p 后 build 红(证明层错误)= 编译器强制修复 → 考题。
- **CHOICE-ONLY**:撤后仍绿 = 自愿改动;须过 **X-相关性门**(标识符重叠),
  related 进对齐副轨,unrelated 降级审计(时序邻近挖掘噪声)。
- **QUARANTINED**:重建无效(如后续 commit 依赖 C_p 新建文件致「当前树−C_p」
  ≠历史态;指纹=纯 load 失败+秒级+无证明错误)。**非数据类,是无效测量**;
  隔离率=重建配方质检指标(当前 1/31=3%)。
