# Contract GREEN 保留与修改约束（2026-10-15）

适用于机器 A 的 repository-level 方法、机器 B 的 VERO 与通用 Codex 对比。目标是在允许同一 theory 继续生成的同时，按已验证的 contract 单独统计 GREEN；theory 的当前草稿失败、seal 失败与 contract 证据失效必须分开记录。本文件定义下一发布的要求，不改变既有冻结 run 的账本。

## 状态和计数

1. `accepted_green` 是一个 contract 的**已提交、可复核**的 Isabelle 成功证据，绑定固定 statement/context、proof body、实际使用的本地前缀和导入/辅助事实、检查器及原生收据。它不以整个 theory 最新草稿的检查结果为唯一状态。`historical_first_green` 只在首次接受时增加一次；同一 contract 重检或 seal 不重复计数。
2. 分别发布 `accepted_green_contract_count`、`historical_first_green_contract_count`、`draft_check_status`、`sealed_theory_count` 与集成状态。一个后续 lemma 的草稿失败只使 theory 当前草稿和 seal 不可接受；它不能自动撤销已有、上下文未变的 contract GREEN。
3. 已 GREEN contract 的 statement/context 始终不可变。**非 seal 阶段不得改动其已接受 proof body**；修改同一 theory 中其他 OPEN lemma、添加 helper 或调整后续证明顺序可以继续进行，但不能悄悄替换已接受证明。共享工作区的直接文件写入也必须在检查和结果发布前由源文件守卫检出并拒绝，不能只靠 prompt 约束。
4. 当 theory 的全部目标已 GREEN 并由 harness 进入显式 seal 事务时，允许暂存对已 GREEN proof body 的调整，包括 seal 检查与其反馈驱动的修复；单凭模型声明“正在 seal”不能打开例外。旧接受版本保留为独立、不可变证据；新版本及受影响后续节点通过所需原生检查后才原子替换。失败候选不增加 GREEN 次数，不把仍可复用的旧版本抹掉；若旧版本的依赖上下文真实变化，则另按第 5 条失效。seal 成败单独统计。
5. 仅在已接受 contract 自身 proof/statement、其实际证明上下文、导入、前置声明或已用 helper 发生影响其证书的变化时，按受影响依赖集合撤销当前 GREEN，并保留历史首次 GREEN 与成本。必须记录 contract ID、旧/新上下文指纹、触发的源文件与失效原因。对于一般同 theory 编辑，最少需证明该 contract 的已检查前缀及导入上下文未变；无法证明同源时应重验受影响 contract，不能把其他独立 contract 一律清零。
6. 局部 `check THEORY` 的临时渲染快照只用于该请求及其真实导入闭包；不得用其中被置为 `oops` 的闭包外 theory 对账、撤销旧 seal/GREEN。跨 theory 源码对账必须使用原始共享工作区的规范化全量快照，并把“真实编辑”和“局部候选渲染”分开标记。
7. 对于一次含多条 lemma 的检查，新 GREEN 只从实际完成并审计通过的原生证据接受；整体检查 RED 时不能把未独立证明的后续候选当作 GREEN，也不能用 RED 覆盖之前不同提交中仍有效的 GREEN。账本应分别记录 `accepted`、`retained`、`invalidated`、`draft_failed`、`seal_failed`，并可按任意截止时间重建计数。

## 最小验收案例

- 两个 contract 已 GREEN；把其后一个 OPEN contract 改成语法/证明失败的草稿。旧两个 GREEN 数量不变，当前 theory 草稿 RED，seal 阻断；再次修正后无需把旧两个计作新 GREEN。
- 对已 GREEN contract 在普通生成阶段修改 proof body：编辑/检查拒绝，记录违规；从未改变的接受版本恢复。seal 事务中可试改，失败保留旧证据，成功替换证据且历史次数仍为一次。
- 修改已 GREEN contract 依赖的前置 helper/import：只撤销受影响 contract 与下游消费者，保留无关合同的 GREEN；修改其后无关 lemma 不撤销它。
- 在 KHeap 的局部检查中把闭包外 Bits 临时渲染为 `oops`：Bits 的原始 seal 和 GREEN 不变；若原始 Bits 源码真的变化，则撤销并要求重验。
- 对完整与局部检查、暂停截止、恢复、seal 前后分别重建原生收据和计数；新 GREEN、保留 GREEN、失效及 token/时间各有独立事件，不能靠最终 theory RED 推断历史或当前单项数。

## B005 暴露的偏差

`vero-sol-medium-smoke-2h-001` 的 c0127/c0128 已有成功检查证据，后续 c0131 的未完成 `apply` 使整个 KHeap 草稿 RED，现有 VERO `proof_commit` 将两者重新写为 OPEN；但截至 c0128 证明结束的原生检查源码前缀及其 support/import 上下文在此后直到两小时截止始终同源。Bits 的 c0031/c0032 则被闭包外临时 `oops` 快照错误撤销 seal；共享工作区原始 Bits 文件未改变。这两个事件都不能解释为四条已 GREEN 证明正文被 agent 改坏。原冻结账本的严格截止当前计数为 0；单列按未变证书纠正过度失效的审计口径为 **4**，不能原位改写旧账本。详情见 `reports/regen/experiment-stability-20261014/B005-TWO-HOUR-SMOKE.md`。

## A / Codex 现状审计

机器 A 的共享证明工作区以不可变接受 journal 为准；实际 `refine-sol-medium-andor-20261011-008/proof-008/theories/KernelStatePrimitives` 中，一次 `rejected_batch` 前后都保留 32 个已接受证明（其中 10 个 contract），之后仍增长至 43/13 和 48/13。A 的 document 生成路径同样以已接受前缀为基线，失败草稿不提交；同一 theory 后续 lemma 的 RED 不会覆盖已接受的 GREEN。机器 B 的 Codex B005 实测中，`Generated_InterruptAcc/getIRQSlot_corres` 在账本 seq 671 接受，随后 `setIRQState_corres` 在 seq 690 检查失败，seq 692 仍保留前者 GREEN；最终历史/当前均为 13。79 次 `edit_theory` 中有 46 次发生于已 GREEN 后，未发现一次改写已接受 proof body。这说明这两次实际运行没有复现 VERO 的 KHeap 式计数回退。

但 A 的普通批量 `submit_batch` 路径禁止替换已接受证明，document/Codex 路径的 `DocumentStore.edit()` 却只保护固定声明，允许普通阶段改写已接受 proof body；`baseline()` 会重验受影响后缀，测试也覆盖这一能力，尚不符合第 3–4 条。通用汇总投影对 `seal_invalidated`、`source_missing`、`source_invalid` 仍可能整 theory 清除当前 GREEN，未按单项证书区分受影响范围；B005 Codex 没有触发此路径。下一发布必须同时补工具守卫和计数投影，不能以本次 agent 恰好没有改写作为约束已生效的证据。

## VERO 实现状态

本工作区已先实现 VERO 侧：对每个 contract 记录已接受证明正文、已检查前缀及导入上下文的收据；普通生成检查前拒绝改动已 GREEN 正文；局部检查使用原始工作区的规范化全量快照对账，不以闭包外临时 `oops` 撤销收据。后续 lemma 检查失败只保留真正同源的先前 GREEN，seal 失效与 contract 失效分别入账。已知归属的文件提取或 imports 错误按受影响 theory 及其消费者撤销，无法确定来源或依赖图时保守撤销。

允许改动已 GREEN 正文的 seal 事务由 harness 显式开启，要求该 theory 的合同均有已接受收据、导入 theory 已 seal，且**当前完整源码**有对应的原生成功检查（含 helper）。seal 候选直到构建成功才取代旧收据；失败时旧版本留存，可恢复原文后中止事务。原生合同结果发布后若共享源码在 seal 期间变化，重新提取并对账，阻止旧源码的 seal 结果发布。证明依赖采用检查前缀作为安全边界：改动位于已 GREEN 证明之前、即使实际上未被引用的 helper，也会要求重验该证明；位于其后的无关 lemma 不会清除它。

后续本地实现补充了“已接受源码投影”：每份新收据仍保存原生检查时的完整原始前缀哈希，另记录当时位于目标之前、正文为空或 `oops` 的证明槽，以及仅将这些槽规范化后的前缀哈希。之后这些 **OPEN 槽的草稿正文**变化时，旧 GREEN 保留为与原始已接受源码绑定的版本；失败草稿不是新证明，也不会成为旧证明的前提。固定声明、helper、导入、已填充的前序证明及目标自身正文变化仍按原有守卫失效或要求重验。前序槽通过原生检查成为新 GREEN，且后续合同也获原生检查确认时，才为后续合同发布绑定新前缀的收据；否则保留的旧收据仅证明原已接受版本，不代表当前完整 theory 已通过。seal 仍要求当前完整 theory 的原生检查、审计与 build。旧 release 的收据没有该投影字段，继续沿用旧的严格前缀规则，不隐式改写冻结实验账本。

这些修改只改变后续运行的代码，不改写已冻结的 B005 账本。A 的 document 路径及 B 的通用 Codex 对齐仍需分别实施、验收。
