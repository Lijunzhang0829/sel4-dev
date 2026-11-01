# Ours 与 VERO 差距审计

主要发现：本次 Ours 的 26/91 与 VERO 的 80/91 差距，直接表现为覆盖调度失败；不能用总数推断模型在同一批已尝试目标上弱三倍。两者都使用 gpt-6-astra / low，生成实际时间约 18,834 秒。Ours 端到端结果确实更差，该控制器缺陷属于方法实现的问题，不能从结果中剔除。

逐目标交集为 24 条；Ours 独有成功为 tree_remove_content、tree_rotation_content 两条。VERO 独有成功为 56 条，全部属于 Ours 尚未启动的 61 个目标。Ours 已启动的 30 个目标中，26 条 GREEN，余下 4 条在 VERO 中也未成功；此分母只作覆盖分析，不作为公平成功率比较。VERO 91 条目标均有 charged attempt，最大每目标 5 次；Ours 单理论工作进程一直占用 Tree_Execution，未向其他 ready theory 让出执行。

## 反例与控制器循环

原 Tree_Execution 会话于北京时间 15:50:48 首次报告删除保持良构性的反例。复核 request 26 的原生日志，Isabelle 在一个 12 节点具体输入上依次建立 tree_wf、被删除条目存在、tree_remove 9 成功返回以及输出不满足 balanced_tree；输出包含 balance=-2 的子树。局部 Isar 推导成功建立 `have counterexample: ¬ tree_remove_preserves_wf`，之后在原正向目标的最终 `by fail` 处退出。因此整个 request 为 RED，这不是已发布的证伪证书，也不改变 26 个正向 GREEN 的计数。证据针对冻结形式化模型，不足以直接宣布上游 Python 仓库存在同一缺陷；仍需核查翻译保真及状态可达性。

模型随后反复说明固定目标无法全部完成。冻结程序的 parallel_file_planner.py 在状态仍为 generating、未 sealed/retired、也未 framework_blocked 时，继续向同一个模型会话发送“Continue the same assigned theory … until proof_action finish seals the theory”。本次并未把反例报告、无进展或目标级停止转化为可释放工作槽位的状态。配置的单次请求超时和全局预算不等于单目标尝试上限；不能将 VERO 的 max_contract_retries=5 与 Ours 的局部 round 参数视为实际同等止损。

Tree_Execution 原会话及 retry-001 至 retry-005，各自末次工具调用后的尾段，合计约 9,149.92 秒（2 小时 32 分 30 秒），其中发生 1,192 次完成轮次而没有新的工具调用。末段 retry-005 单独有 641 个完成轮次、12 次工具调用，末次工具调用后又完成 635 轮。此统计包含末次工具调用本身的返回等待，不能当作精确可回收模型计算时间；但重复结论和相同继续提示清楚表明大量无进展交互。最后一个固定目标 GREEN 在 15:27:37，直到 18:41:26 截止未再增加固定目标。

Tree_Execution 消耗约 16,173.58 秒有效时间（占总有效时间 87.4%）。VERO 将 metadata、mutations、fuel、queries 等拆分为较小模块，并逐目标执行最多 5 次的重试策略；Ours 把 8 个树执行目标置于一个理论中，第二个调度到该理论后一直未释放唯一工作槽位。即使很多其他理论并不依赖 Tree_Execution，也未获得执行机会。理论封存屏障与调度独占是不同问题：后者直接导致其他独立目标饥饿。

## 次要因素与结论边界

本次完整封存适配有效，Interval_Normalization 的 22 条已成功封存；旧实验缺 campaign_id 的问题不是本次主要原因。首次认证失败发生在生成预算启动前。树阶段记录的 5 次 HTTP 429 均对应成功恢复，但重启后仍回到相同阻塞理论；不能将数小时无进展简单归于网络限流。两套工具、模型上下文和封存检查并不完全相同，进一步分析吞吐率需做阶段剖析；现有证据不能推算修复后必能达到 80/91。

建议先修复失败退出及公平调度：为可疑反例提供独立检查与证伪证书出口；将模型报告 blocked、无新的原生检查/接受事件、重复最终回复转化为受审计的局部停止；将未封存理论暂存，让其他 ready theory 获得预算；将成功条件性质与 fuel/wf 等高风险目标合理拆分。保持原 spec 不变，不向生成器导入 VERO 证明，不为提高成功率削弱目标。修复后用相同模型、冻结输入和预算重跑。此次审计没有修改生成程序、目标或预算，也未重启实验。

证据：[逐目标对照](comparison.json)、[VERO 尝试摘要](vero-attempt-summary.json)、[Isabelle request 26 原生诊断](counterexample-check-26.json)。完整会话仍保留在机器 B 的本次实验 proof/parallel-sessions/2ac912cc0c8c4a0babf1d3844652c81b/Tree_Execution/。
