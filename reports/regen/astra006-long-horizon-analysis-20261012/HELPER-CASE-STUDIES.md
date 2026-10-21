# Astra 006 实验中的 helper 成本与目标推进案例

实验：`refine-astra-medium-20261005-006`。

在线分享版：[Astra 006 helper 成本与证明案例](https://astra006-helper-case-studies.blush-duck-9562.chatgpt.site)。任何持链接者可访问；包含下方统计、对话摘录、连续 helper 证明回放和 CSV 下载，不公开完整 JSONL 日志。本地原始文件链接继续保留用于复核。部署回执见 [publication.json](publication.json)。

本文汇总四个历史案例及对应模型对话，用于分析 helper 是否推动 contract 证明，以及复杂 helper 的成本来源。案例集中在 `resetUntypedCap_corres` 和 `schedule_corres`；其中两个案例是同一目标下的具体失败路径，不能当作四个独立 contract 样本。

## Helper 创建到验证和使用的连续对话回放

以下链接展示的是选定案例的连续公开交互，不再仅是几句结果摘录。建议先读 TCB 和 machine-operation 两例，再对照 device 分支和未完成的循环算术例。

| 公开回放 | 展示的完整链路 | 公开事件 | 编辑事件 | helper 完整版本条目 |
| --- | --- | ---: | ---: | ---: |
| [TCB slot 创建与父 helper 使用](https://astra006-helper-case-studies.blush-duck-9562.chatgpt.site/tcb-slots.html) | 原始直接展开 → 31 个残留目标 → 抽出 slot helper → 修复参数和语法 → 使用 helper → 批次 GREEN | 68 | 10 | 19 |
| [Device reset 分支设计与反馈修改](https://astra006-helper-case-studies.blush-duck-9562.chatgpt.site/device-reset.html) | 检索两侧定义 → 专门化 free-index 更新 → 设计两个分支等式 → lift 归一化 → witness 修正 → 三个 helper GREEN | 40 | 3 | 7 |
| [Machine operation 的证明生成与 witness 修复](https://astra006-helper-case-studies.blush-duck-9562.chatgpt.site/machine-operation.html) | 初始通用陈述和 proof → 恢复后继续 → 自动搜索超时 → 显式 witness → 作用域及同批次语法修正 → GREEN 与实际引用 | 71 | 6 | 9 |
| [Reset 循环算术设计与检查断点](https://astra006-helper-case-studies.blush-duck-9562.chatgpt.site/reset-arithmetic.html) | 循环对应缺口 → 检索枚举定义 → 三个完整算术 proof → power_add 修改 → INVALID 检查请求被拒绝 | 14 | 2 | 4 |

四个回放共 193 条公开事件、21 次编辑事件、39 个相关 helper 版本条目；版本条目是编辑后保留的完整版本，不是 39 个不同 helper。每个页面均包含：

1. 原 contract 的目标陈述，以及明确标为分析的导读。
2. 模型当时的公开说明、检索与检查请求，按日志顺序排列并保留行号、北京时间。
3. 相关 helper 的完整陈述、完整 proof body 和相对上一版的逐行差异。补丁式编辑另外保留原始 old/new 请求。
4. 工具实际返回给模型的内容；另外提供逐批次完整 proof 和原生诊断作为补充证据，二者不混为同一种输入。
5. 正式接受和实际使用的边界：命令通过不等于批次接受，helper 引用不等于 contract 闭合。

点击每条事件旁的 `#` 可以分享该步骤；等待消息、诊断与版本差异可以展开，也可使用“展开全部”。公开回放 JSON 可下载。它们不含模型内部推理或系统提示，不重复无关 theory 前缀；删除本地绝对路径、环境绑定及整份原生审计/日志副本。相关 helper 的 proof body 和所选批次的诊断不截断。

回放来源及文件哈希见 [helper-replays.json](helper-replays.json)，导出实现见 [export_helper_replays.py](export_helper_replays.py)。循环算术案例只到检查请求被拒绝，不能补写为已验证或已使用。

## 证据口径

- 请求编号指各 theory 的 batch request，不是实验全局 step。
- 接受与失败以检查结果和正式接受记录为准；模型对话用于解释当时的证明意图。
- `completed_uncommitted` 不等于正式接受，`not_reached` 不等于检查失败。
- “helper 已接受但父目标未完成”不等于“helper 无用”。本文区分局部成果未兑现、复杂证明反复失败和最终成功的支撑引理。
- 这些案例不能证明用途记录会提高或降低生成效果，也不能直接解释后续 A/B 测试的因果关系。

原始日志根目录为 `reports/regen/refine-astra-medium-20261005-006/proof/`。下方链接保留原始文件与物理行号；Markdown 阅读器不支持行号跳转时，可按标注行号在 IDE 中打开。已有汇总数据见 [evidence.json](evidence.json)。

## Helper 数量与成本补充核算

核算脚本：[helper_costs.py](helper_costs.py)；机器可读结果：[helper-costs.json](helper-costs.json)；两个 theory 的全部 130 个 helper ID 明细：[helper-costs.csv](helper-costs.csv)。这些是分析产物，不修改原始实验。

### 数量和未使用的口径

“生成”按初始图、最终图、保存的 proposal 和模型编辑请求中出现的唯一 helper ID 计数，包含未接受草稿；同一 ID 的多次修改不重复计数。阶段归属按模型明确转向目标后的首次提出时间判断，不等于已验证的父子依赖关系。

| 范围 | 累计提出 helper | 006 继承的已接受 helper | 006 新提出 | 006 新接受 | 最终已接受 | 最终未接受 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Scheduler_Dispatch | 66 | 24 | 42 | 37 | 61 | 5 |
| Untyped_Object_Lifecycle | 64 | 35 | 29 | 26 | 61 | 3 |

| 目标阶段 | 新提出 | 已接受 | 未接受 | 已接受但未被任何最终接受证明显式引用 | 已接受但不在任何最终 GREEN contract 的显式依赖闭包内 |
| --- | ---: | ---: | ---: | ---: | ---: |
| schedule_corres | 29 | 24 | 5 | 10 | 24 |
| resetUntypedCap_corres | 10 | 7 | 3 | 5 | 7 |

这里的“未引用”是最终接受 proof body 的名称匹配结果，不是 Isabelle 原生审计确认的“无用”。自动化隐式使用、失败草稿中的使用、证明策略价值，均不能由此排除。两个 theory 尚未完成完整 seal 审计。最终仓库快照累计有 885 个已接受 helper，其中 40 个没有在最终接受证明中检出显式引用；已有原生依赖记录也没有为这 40 个提供正向引用证据，但覆盖不完整，不能据此宣布全部无用。

schedule 阶段接受的 24 个 helper 中，14 个被其他已接受 helper 显式引用，10 个没有此类引用；24 个都尚未接入任何最终 GREEN contract 的显式依赖闭包。reset 阶段接受的 7 个中，`ul_machine_op_corres` 被 `ul_clear_memory_corres` 引用，`ul_untyped_reads` 被 `ul_reset_zero` 引用；其余 5 个没有来自最终接受证明的显式引用，7 个都未接入最终 GREEN contract 的显式依赖闭包。

**对原先七个 helper 描述的补充：** reset 阶段后来还提出了 `ul_filter_upt_less`、`ul_reset_indices` 和 `ul_step_enum_nat` 三个算术 helper。retry-001 第 1878 行包含新增草稿，第 1894 行的检查请求使用 `source_sha256="INVALID"`，请求 77 被拒绝，没有这些 helper 的原生检查结果。因此本阶段是“提出 10 个、接受 7 个”，不是只提出 7 个。

### 阶段时间和 token

阶段起点分别为 Scheduler 主会话第 4135 行、Untyped 主会话第 5242 行的目标切换消息。终点为现存该 theory 会话日志结束，包含 retry-001。墙钟按每个会话日志的区间分别求和，扣除主进程退出到重试启动之间的空档，仍包含初始化、检查、等待和故障时间；**不是有效生成时间，也不是所有时间都独占用于该 contract**。

Token 对每个 thread 的 `thread/tokenUsage/updated` 累计值求相邻差值，再按事件落入阶段求和；不重复加总本地回执。缓存输入是输入的子集：总 token = 缓存输入 + 非缓存输入 + 输出。边界按用量事件时间划分，不能精确切开跨边界的一次模型调用；未上报用量也无法补算。

| 目标阶段 | 观测墙钟 分钟 | 缓存输入 token | 非缓存输入 token | 输出 token | 总 token |
| --- | ---: | ---: | ---: | ---: | ---: |
| schedule_corres | 215.48 | 45,817,344 | 333,915 | 46,447 | 46,197,706 |
| resetUntypedCap_corres | 117.64 | 18,069,632 | 208,703 | 17,445 | 18,295,780 |

下表是可核查批次的成本，不等于上述整阶段成本。子案例包含在父案例中，不能再相加。action 总耗时包含候选检查、前缀准备及其他应用层开销。

| 案例 | 已记录批次 | action 秒 | 候选检查秒 | 前缀准备秒 | 其余 action 秒 |
| --- | --- | ---: | ---: | ---: | ---: |
| schedule 支撑链 | 65—96 | 5994.580 | 1233.638 | 3037.378 | 1723.564 |
| reset 支撑链 | 68—76 | 3221.864 | 397.879 | 1007.471 | 1816.514 |
| TCB validity 子案例 | 77—85 | 1376.972 | 416.487 | 566.970 | 393.515 |
| machine operation 子案例 | 68—73 | 2441.838 | 265.380 | 1007.302 | 1169.156 |

TCB 子案例覆盖请求 77—85，直到正式接受；不能在请求 82 首次命令通过时停止计费。reset 的请求 77 属于被拒绝请求，不在原生检查批次求和中；Scheduler 的后续恢复尾部也不应被当作请求 65—96 的原生检查成本。阶段墙钟与批次之差混合了模型交互、检索、其他工具、等待和故障等，不能全部标成模型推理时间。

### 每个案例 helper 的检查成本

下表涵盖两个目标阶段提出的 39 个 helper。完整 130 个 ID、消费者、请求列表及状态计数见 CSV/JSON。

- “完整段秒”只累计该节点 `REGEN_TIMING_START/END` 成对标记的区间，包含节点上下文建立，不是纯 tactic 时间。失败缺少 END 的部分不在其中，所以通常只是已知部分。
- “缺 END 次数”表示已开始但无法由成对标记核算完整耗时的次数；0 秒不能解释为免费。未执行或未提交检查的节点以“未到达”或“未检查”标注。
- “关联批次秒”是包含该节点结果的批次 action 总和，包括批次中其他节点、前缀和准备成本，**不能视为该 helper 独占耗时，也不能跨 helper 求和**。
- 单个 helper 的独占 LLM token 均无法从现有记录可靠拆分，CSV 的 `llm_tokens` 留空，而非记为 0。不能把同一轮同时修改多个 helper 的用量平均分摊并称为实测值；可报告的用量是上面的阶段合计。

| Helper | 最终接受 | 最终显式消费者数 | 完整段秒 | 缺 END 次数 | 直接失败次数 | 关联批次秒 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `dispatch_cte_wp_load` | 是 | 1 | 18.024 | 0 | 0 | 1111.213 |
| `dispatch_enqueue_invs` | 否 | 0 | 未完整计时 | 1 | 1 | 9.305 |
| `dispatch_getObject_tcb_result` | 否 | 0 | 未到达 | 0 | 0 | 1004.918 |
| `dispatch_lookup_member` | 是 | 1 | 17.358 | 1 | 1 | 96.955 |
| `dispatch_lookup_replace` | 是 | 1 | 28.985 | 3 | 3 | 1151.233 |
| `dispatch_resume` | 是 | 0 | 10.157 | 1 | 1 | 939.753 |
| `dispatch_runnable_bind` | 是 | 0 | 16.061 | 0 | 0 | 939.753 |
| `dispatch_sched_cap_invariants` | 是 | 0 | 20.621 | 2 | 2 | 1178.727 |
| `dispatch_sched_cte_wp` | 是 | 2 | 8.900 | 1 | 1 | 96.955 |
| `dispatch_sched_ctes` | 是 | 1 | 26.094 | 1 | 1 | 1376.972 |
| `dispatch_sched_liveness` | 是 | 0 | 20.451 | 0 | 0 | 1178.727 |
| `dispatch_sched_load_cte` | 是 | 1 | 17.537 | 1 | 1 | 1376.972 |
| `dispatch_sched_refs` | 是 | 0 | 20.520 | 0 | 0 | 1178.727 |
| `dispatch_sched_tcb_slots` | 是 | 2 | 97.681 | 0 | 0 | 1111.213 |
| `dispatch_sched_tcb_valid` | 是 | 0 | 34.646 | 5 | 5 | 1376.972 |
| `dispatch_sched_thread_invariants` | 是 | 0 | 11.823 | 1 | 1 | 530.637 |
| `dispatch_state_gets_commute` | 是 | 0 | 26.873 | 1 | 1 | 968.694 |
| `dispatch_tcb_arch_frame` | 否 | 0 | 20.218 | 0 | 0 | 1004.918 |
| `dispatch_tcb_at_update` | 是 | 3 | 52.350 | 0 | 0 | 1359.009 |
| `dispatch_tcb_cap_update` | 是 | 1 | 22.098 | 3 | 3 | 1359.009 |
| `dispatch_tcb_clear_update` | 是 | 6 | 59.557 | 1 | 1 | 1359.009 |
| `dispatch_tcb_ko_wp_update` | 是 | 4 | 31.907 | 0 | 0 | 219.363 |
| `dispatch_tcb_layout_update` | 是 | 0 | 78.480 | 0 | 0 | 1376.972 |
| `dispatch_tcb_memory_frame` | 否 | 0 | 未完整计时 | 2 | 2 | 1004.918 |
| `dispatch_tcb_obj_at_update` | 是 | 1 | 20.288 | 0 | 0 | 530.637 |
| `dispatch_tcb_obj_frame` | 是 | 1 | 26.061 | 0 | 0 | 402.007 |
| `dispatch_tcb_objs_update` | 是 | 0 | 12.596 | 1 | 1 | 402.007 |
| `dispatch_tcb_type_update` | 是 | 2 | 53.853 | 1 | 1 | 1359.009 |
| `dispatch_threadSet_wp` | 否 | 0 | 未到达 | 0 | 0 | 1004.918 |
| `ul_clear_memory_corres` | 是 | 0 | 9.452 | 1 | 1 | 2441.838 |
| `ul_filter_upt_less` | 否 | 0 | 未检查 | 0 | 0 | — |
| `ul_machine_op_corres` | 是 | 1 | 21.067 | 4 | 4 | 2441.838 |
| `ul_reset_device_A` | 是 | 0 | 27.709 | 2 | 2 | 780.027 |
| `ul_reset_device_C` | 是 | 0 | 17.809 | 0 | 0 | 780.027 |
| `ul_reset_indices` | 否 | 0 | 未检查 | 0 | 0 | — |
| `ul_reset_zero` | 是 | 0 | 28.661 | 0 | 0 | 2441.838 |
| `ul_set_freeindex_corres` | 是 | 0 | 24.758 | 0 | 0 | 780.027 |
| `ul_step_enum_nat` | 否 | 0 | 未检查 | 0 | 0 | — |
| `ul_untyped_reads` | 是 | 1 | 9.742 | 0 | 0 | 2441.838 |

以上逐 helper 明细仅统计 006 本轮中高于初始 batch_requests 的请求：Scheduler 从 49 起，Untyped 从 45 起；不把继承证明的历史生成成本计入本轮。缺失失败区间和独占 token 保留未知，不以估计冒充精确值。


## 案例一 resetUntypedCap_corres 的七个 helper 未兑现为目标闭合

### 检查轨迹

| 请求 | 新接受的 helper |
| --- | --- |
| 73 | `ul_machine_op_corres`、`ul_clear_memory_corres`、`ul_untyped_reads`、`ul_reset_zero` |
| 76 | `ul_set_freeindex_corres`、`ul_reset_device_A`、`ul_reset_device_C` |

这些引理覆盖机器操作、清零、读取、零索引和 device 分支。非 device、非零索引路径仍需要 chunk-loop invariant、循环对应和抢占处理。在检索到的检查记录中，没有 `resetUntypedCap_corres` 本体的直接检查结果；不能描述为该 contract 反复检查失败。

该 theory 最终有 64 个接受条目，其中 3 个 contract、61 个 helper；仍有 3 个 contract 未完成。这是累计状态，不能将全部 helper 都归于这一次 reset 证明。

### 当时的对话

四个 helper 接受后，模型表示准备使用它们完成完整 reset 证明：

> All four helpers are now accepted. I’ll use them to continue the full reset correspondence proof, including the nonzero-index branches.

[原始对话第 1049 行][u1049]

随后模型汇报七个新增 helper 已验证，但目标仍未完成：

> Saved and validated seven additional helpers; 64 entries are now accepted.
>
> The theory remains unsealed. Three contracts are unfinished: `decodeUntypedInvocation_corres`, `inv_untyped_corres'`, and `resetUntypedCap_corres`.

[原始对话第 1722 行][u1722]

继续生成时，它指出核心缺口仍是循环不变量：

> The invocation proof also needs object-creation correspondence, while reset needs a chunk-loop invariant. I’ll finish the reset support first so those two proof paths remain manageable.

[原始对话第 1847 行][u1847]

### 判断

这是“局部 helper 成功，但对最终目标的贡献尚未兑现”的明确案例。值得进一步验证的假设是：是否优先完成容易拆出的分支，却迟迟没有验证核心循环证明方案。

它不是七个 helper 全部无用的证据。贡献判断需要检查它们能否消除父目标的实际子目标，以及使用后核心缺口是否缩小。

检查入口：[请求 73](../refine-astra-medium-20261005-006/proof/theories/Untyped_Object_Lifecycle/batch-requests/000073/check.json)、[请求 76](../refine-astra-medium-20261005-006/proof/theories/Untyped_Object_Lifecycle/batch-requests/000076/check.json)。

## 案例二 schedule_corres 的支撑证明链持续扩展

### 检查轨迹

- 请求 66：直接检查 `schedule_corres`，失败，留下九个子目标。
- 请求 69：尝试 `dispatch_enqueue_invs`，仍未完成 enqueue 后的不变量保持。
- 后续扩展到 TCB 布局、capability、引用、liveness、architecture 和 memory 保持性。
- 请求 95、96：`dispatch_tcb_memory_frame` 仍失败；后面的 `dispatch_getObject_tcb_result` 和 `dispatch_threadSet_wp` 未执行到。

请求 96 剩余义务涉及更新 TCB 后，`valid_pde_mappings'` 和 `pspace_domain_valid` 的值保持不变。

### 当时的对话

模型最初指出队列操作的不变量保持是主要难点：

> The next substantial obligation is preserving the scheduler invariants across enqueue and append. The imported queue proofs provide concrete execution results and preserve some queue properties; I’ll use those to determine what additional preservation lemmas are needed.

[原始对话第 4457 行][s4457]

之后仍在继续补充更底层的保持性证明：

> Five more helpers are accepted, covering capability, reference, liveness, and thread invariants. I’ve submitted the architecture and memory frames, together with a rule connecting successful `threadSet` executions to the record updates used in these proofs.

[原始对话第 7988 行][s7988]

### 判断与耗时边界

这是“为父目标引入深层支撑链，但支撑链自身尚未闭合”的案例。该 theory 最终完成 10/11 个 contract，不能将全部 helper 归为最后一个失败目标的浪费。

请求 96 的总耗时约 915 秒，其中前缀准备约 872 秒，候选检查约 17 秒；其余是其他阶段开销。不能将这次记录解释为 memory helper 本身搜索了十五分钟。这里同时存在证明路径未闭合和基础设施准备成本，两者需要分开分析。

检查入口：[请求 66](../refine-astra-medium-20261005-006/proof/theories/Scheduler_Dispatch/batch-requests/000066/check.json)、[请求 69](../refine-astra-medium-20261005-006/proof/theories/Scheduler_Dispatch/batch-requests/000069/check.json)、[请求 96](../refine-astra-medium-20261005-006/proof/theories/Scheduler_Dispatch/batch-requests/000096/check.json)。

## 案例三 dispatch_sched_tcb_valid 的宽泛展开导致反复失败

这是案例二中的一个具体 helper，不是另一个独立 contract。

### 检查轨迹

- 请求 77—81：`dispatch_sched_tcb_valid` 连续五次直接失败。
- 请求 77：产生 31 个剩余子目标，大量涉及 TCB capability slot 的分类展开。
- 后来提取独立的 slot-preservation 引理，并处理 record selector 化简，才逐步解决。

### 当时的对话

> The layout-update lemma passed. The TCB-validity proof expanded capability-slot cases too broadly, so I’ve factored out a separate slot-preservation lemma and reused it in the capability-map proof.

[原始对话第 6021 行][s6021]

之后模型明确报告原 helper 的证明通过：

> The TCB-validity proof now passes. The capability-map proof needs record selectors simplified before applying the imported preservation theorem; I’ve added that step. The shared search did not reveal a directly applicable enqueue-invariant helper.

[原始对话第 6428 行][s6428]

### 判断

这是“复杂 helper 难证”的明确案例，但该 helper 最终成功，不能归为无用。失败轨迹支持的解释是：最初展开粒度过大，没有先隔离局部保持性。

它也提醒我们不能简单限制 helper 数量：增加一个针对性的支撑引理，反而可能降低原 helper 的证明成本。

检查入口：[请求 77](../refine-astra-medium-20261005-006/proof/theories/Scheduler_Dispatch/batch-requests/000077/check.json)、[请求 81](../refine-astra-medium-20261005-006/proof/theories/Scheduler_Dispatch/batch-requests/000081/check.json)。

## 案例四 ul_machine_op_corres 的自动 witness 搜索超时

这是案例一中的通用机器操作 helper，其目标为：

```isabelle
assumes nf: "no_fail ⊤ m"
shows "corres (=) ⊤ ⊤ (do_machine_op m) (doMachineOp m)"
```

### 检查轨迹

- 请求 68—71：四次直接失败。
- 请求 70：自动寻找匹配结果的 witness 超时，候选检查约 121 秒。
- 请求 71：改为显式 witness，但出现 Isabelle 变量作用域问题。
- 请求 72：该 helper 的命令通过，批次随后因另一个 memory-clearing helper 的语法错误停止，尚不能将整个批次视为接受。
- 请求 73：该 helper 与另外三个 helper 一起正式接受。

### 当时的对话

> The automated witness search timed out. I’ve replaced it with a direct choice of the result pair, followed by the existing machine-state update lemma.

[原始对话第 730 行][u730]

下一次模型说明剩余问题是作用域修正：

> The explicit witness needed a small Isabelle scoping correction. That correction is saved and checking; the proof strategy is unchanged.

[原始对话第 829 行][u829]

### 判断

这是证明搜索和脚本构造问题，不是网络超时。显式构造 witness 缩小了搜索空间，之后修正作用域和同批次其他 helper 的语法问题，才完成接受。

该案例说明复杂度不只由 helper 陈述长度决定，也取决于泛化程度和自动化策略；它最终成功，不能据此认定为无用 helper。

检查入口：[请求 70](../refine-astra-medium-20261005-006/proof/theories/Untyped_Object_Lifecycle/batch-requests/000070/check.json)、[请求 71](../refine-astra-medium-20261005-006/proof/theories/Untyped_Object_Lifecycle/batch-requests/000071/check.json)、[请求 73](../refine-astra-medium-20261005-006/proof/theories/Untyped_Object_Lifecycle/batch-requests/000073/check.json)。

## 对用途记录和后续对比的启示

这些日志中，模型已经用自然语言解释过 helper 的用途，但仍可能不断扩展支撑证明。因此，单纯增加“服务于哪个 contract”的描述未必足够。更有区分度的检查项是：

1. helper 对应父目标的哪个实际子目标。
2. helper 接受后是否真正被接回父目标。
3. 接回后消除了哪些证明义务，核心缺口是否缩小。
4. 新增 helper 是必要分解，还是尚未检验可组合性的旁支。

优先复盘案例一和案例二，考察局部成果为何没有转化为 contract GREEN。案例三、四适合作为对照，避免将最终有用但困难的 helper 误判为浪费。用途记录应保留“待证明、已接受、已在目标中使用”的区别，不能把模型声称准备使用当作已经使用。

## 原始对话入口

- [Scheduler_Dispatch 主会话](../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Scheduler_Dispatch/session-transcript.jsonl)
- [Untyped_Object_Lifecycle retry-001 会话](../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl)

本文保留的是公开模型消息和检查结果入口，不是模型内部推理记录。检查产物若采用增量引用格式，应通过项目 `regen.infrastructure.incremental_log.read_artifact` 解码，不能把引用外壳误认为完整检查结果。

[u1049]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl:1049
[u1722]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl:1722
[u1847]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl:1847
[s4457]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Scheduler_Dispatch/session-transcript.jsonl:4457
[s7988]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Scheduler_Dispatch/session-transcript.jsonl:7988
[s6021]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Scheduler_Dispatch/session-transcript.jsonl:6021
[s6428]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Scheduler_Dispatch/session-transcript.jsonl:6428
[u730]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl:730
[u829]: ../refine-astra-medium-20261005-006/proof/parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a/Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl:829
