# 前缀准备与并行实验比较（2026-10-29）

用户授权：前缀准备不设置时限，单独计时；两个生成 worker 配两个 native 槽。

## 实现与边界

BatchWorker 的 ready 等待没有固定期限。初始已认证前缀的 ML 命令执行也不再套用 command_timeout；初始前缀完整执行和审计结束后恢复命令时限。候选 execute-and-audit 预算仍为 300 秒，候选 Python watchdog 仍有原有 grace。取消、进程退出检测、owner heartbeat/租约和完整 kernel audit 保留，不跳过证明。

prefix_preparation 的 generation_exclusion 起止事件继续独立记录准备耗时。并行有效预算是活动 worker 时间区间的并集：一个 worker 准备前缀时，另一个 worker 若仍生成，该重叠区间仍计入全局有效时间；不能从全局时钟再次减去整个准备耗时。准备时长还应作为端到端成本披露。

carry_parallel_budget 新增 target_native_slots，只允许增加到冻结 max_parallel_theories/resident_limit 范围内。新 policy hash、授权和变更事件一起保存，继承全部有效时间、worker 时间、规划成本和历史事件，无退款、无预算延长。现有任意 NativePreparationError 的自动重试策略未泛化；本次去掉已诊断的前缀期限，并以完整续跑恢复失败 theory。

## 实验解释

原单槽运行：reports/regen/refine-astra-effective48h-20261029-001。
新双槽续跑：reports/regen/refine-astra-effective48h-dual-native-20261029-001。

新 runtime 由原冻结 application 加 runtime.patch 生成，只包含本次 batch_checker.py、batch_checker.ML、parallel_generation_budget.py 修改。模型仍为 gpt-6-astra/low，两个 theory worker，总有效预算 172800 秒（含规划），所有已接受证明继承。此次长期运行是混合配置的稳定性探索，不能作为从起点固定配置的正式消融结果。

## 文献和比较口径

- [CodeMonkeys](https://arxiv.org/html/2501.14723v1)，特别是 §2.2、Figure 5：扫描并行状态机数量和串行迭代上限，绘制效果与推理成本关系。区分 coverage（候选中至少一个正确，oracle 上限）与最终选择后的 score；不能将候选覆盖率直接与单个最终提交的成功率对比。
- [Scaling Inference-Time Compute with Teams of Coding Agents](https://arxiv.org/html/2610.17471v1)，§2–3：固定 harness，分别改变委派阶段、模型组合、worker 数和调度。明确没有逐配置匹配预算，报告 reward–cost 前沿、相对 solo 的增量收益/增量成本，以及单独的 wall-clock latency。这类结果不能改述成等预算因果证据。

前者是对同一问题采样多个候选；后者是团队分工。Ours 主要是对不同就绪 theory 按依赖并行生成，更接近后者，Isabelle 提供可靠的成功判定，不需要模型从候选中猜测正确答案。

## 建议的 Refine 对照（尚未启动）

1. 规划价值：human / ours / no-plan 使用相同模型、推理档位、输入、proof-removal、工具、检查标准、生成器及硬件。规划之外的接口和修复机制也固定；no-plan 若复用框架，应标为 VERO-like，而不是未经等价验证的原版 VERO。
2. 固定并发研究规划：三组都用 2 workers / 2 native，或都用 1 / 1。human 原始 theory 归属是该组的实验处理，不能额外泄露证明或辅助引理。人为规划已有知识的成本不能伪装成模型免费获得。
3. 并发消融：至少比较 ours 1 worker / 1 native、2 / 1、2 / 2，区分模型并行收益和 native 排队瓶颈。若要证明规划与并发的交互，再做三种规划 × 两种并发的因子设计。
4. 两条预算曲线分别回答问题：相同总模型 token/价格预算下的 GREEN，评估计算效率；相同有效时间及端到端墙钟下的 GREEN，评估吞吐和时延。相同并集有效时间不是相同累计 worker 时间，两个 worker 运行 6 小时可接近 12 worker-hours。
5. 计入所有角色与阶段：规划、初审/终审、修复、生成、重试；报告 input、cached input、output（reasoning 若为 output 子集不能重复相加）。失败重试的服务资源仍报告在原始成本里，另提供按预先冻结规则排除故障后的有效时间。
6. 主指标为唯一原始 contracts GREEN 数/比例，辅以 sealed contracts/theories 和完整仓库验证；helpers 单独报告。展示 GREEN-vs-cost、GREEN-vs-effective-time、GREEN-vs-wall-time，以及到达固定 GREEN 数的耗时。不要仅汇总每轮 pass@k。
7. 基础设施指标：模型等待、native 排队、前缀准备、候选执行、审计/封存、重试；同时给 wall-time 区间并集与 worker/native 累积时间，避免重叠重复计费。记录 native 峰值并发、CPU/RAM/槽数、版本和镜像、provider 限流与故障政策。
8. 多个独立种子/重复运行，配对固定任务与预算，报告离散程度；只有 Refine 单仓库时，不把有依赖的 287 contracts 当作独立样本夸大显著性。正式比较从相同初始状态开始，不使用这次续跑的 GREEN 作某一组独有起点。

并发 no-plan 的定义需要预先固定：可以提供通用任务队列与合法上下文检查，但不能为凑齐两个 worker 暗中引入 ours 的分组、依赖 DAG 或 human theory 归属。如果无法构造不含规划信息的双 worker 基线，规划因果对照优先全部用 1/1，再把 ours 2/2 与原始串行基线作为系统吞吐对照，明确额外并行资源。
