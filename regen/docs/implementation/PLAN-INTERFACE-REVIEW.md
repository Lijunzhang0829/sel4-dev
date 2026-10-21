# 冻结前的规划消费检查

恢复基线：`085913f` 的 repository_planner、workspace_planner、workspace_harness、workspace_tools。
移除 0759f03/4e6f262 引入的 pilot 实现、配置和测试；历史结果保留。
没有 reset 分支或覆盖无关 Vero 工作区修改。当前 repository planner 保持完整 catalog/精确声明按需读取，
没有新增滑动窗口。正式生成仍允许局部 helper/脚本修改，不调整全局规划。

## 实现与理由

`regen.planning.plan_review` 是独立的只读评审命令，可在规划草案完成、正式生成前调用，
也可审计历史已发布规划。V1 不自动修改规划、不增加提交门槛、不执行证明。
与旧 pilot 的区别是消费端接口评估，不用随机根目标的短证明成功率代替规划质量。

1. 确认精确 contract 覆盖和无环导入图，计算直接/传递提供者、seal 阻塞范围及等待的 contract 数。
2. 全部 theories、精确 contract 陈述和结构事实交给一次受审计模型调用。
   逐 theory 输出消费报告，以及最多八项具体问题，每项需绑定 contract IDs、场景、证据、调整建议、代价和未知项。
3. 校验全量报告覆盖、引用 ID 和非空解释；这仅验证格式及引用，不认证模型判断。
4. 保存问题清单与输入/实现/模型身份，再打开独立历史依赖审计进行回测。
   通过 contract→helper→contract 的原生依赖路径核对接口实际使用，传递导入视为可见。
5. 输出候选修改供复核，不自动删除强依赖，不导入未证明事实，不写 production GREEN。
   采用修改后仍须运行现有 normalize/preflight 流程；本版未实现条件组合证明检查。

输入 prompt 使用 contract 字段白名单，剔除原始文件路径、原始 import 图和历史执行结果。
原规划中的论证视为待检验设计意见。模型没有额外检索工具，因此接口匹配可能遗漏定义内部信息，
报告必须保留不确定性。所有理论由同一全局上下文评审，不是假装独立的多 agent 投票。

## 用法

```bash
python3 -m regen.planning.plan_review \
  --plan reports/regen/refine-planning-pilot-astra-20261017-001/planning/planner-proposal.json \
  --input reports/regen/refine-planning-input-v2-20260930/repository-planning-input.json \
  --output reports/regen/plan-interface-review-NEW \
  --history reports/regen/astra006-repository-reuse-20261013/analysis.json \
  --allow-external-call --model gpt-6-astra --reasoning-effort medium --timeout 600
```

不传 allow-external-call 只准备结构报告与审计输入。输出目录必须全新，失败不覆盖证据。
模型失败记录 failed；不得将未完成模型输出当作评审结论。
历史比较文件同时绑定历史审计摘要和已提交评审摘要，不将历史信息回传给评审模型。

## 解释边界

- 静态阻塞范围是调度事实，不是已发生的延迟，也不代表必须删边。
- 模型意见是待验证假设；只有合法性、引用和图关系由程序检查。
- 历史原生依赖说明某条成功路线使用过某接口，不证明不可替代。
- 旧实验与新规划不同；不覆盖历史路径只说明旧路线按新边界复用需要适配。
- 未找到证据不代表问题不存在；未运行完整新规划就不能声称复现了规划失败。
- 只匹配精确 contract ID；不按名称模糊匹配，不把历史 helper 所在 theory 强行映射到新组。
- 未实施自动修订、质量总分、原生条件 sketch 或生成期重规划。

## 参考依据（迁移设计，非已验证收益）

- [ARID, Clements 2000](https://www.sei.cmu.edu/library/active-reviews-for-intermediate-designs/)：从消费端使用设计的场景发现接口缺口。
- [ATAM](https://www.sei.cmu.edu/library/architecture-tradeoff-analysis-method-collection/)：明确风险场景与取舍，不把结构合法性当成质量保证。
- [CodePlan](https://arxiv.org/abs/2309.12499)：依赖/影响分析作为具体证据；本实现不迁移其生成期自适应重规划。
- [Paper2Code](https://arxiv.org/abs/2504.17192)：规划与局部分析先于生成；不据其端到端结果声称本评审已提高成功率。

测试：`python3 -m unittest regen.tests.current.test_plan_review regen.tests.current.test_workspace_planning regen.tests.current.test_workspace_protocol regen.tests.current.test_repository_planner`。

## 本轮真实测试

结果见 [实验报告](../../../reports/regen/plan-interface-review-20261017-001/REPORT.md)。
27/27 theory 检查完成，提出三项风险；一项与历史寄存器遍历失败位置一致，另两项未获得执行验证。
历史审计中 161 条跨新 theory 的 contract 可达关系均已有导入覆盖。不能据此声称提升了下游成功率。

## 自动完成初稿—评审—修订—冻结（2026-10-18）

`regen.tools.reviewed_planning` 串联现有受审计规划入口与消费检查，均使用 gpt-6-astra / medium。
每次必须使用新输出目录；初稿与修订分别保留原生 preflight 和真实提交证据。
修订模型只拿到同轮公开初稿及评审，不提供历史生成信息；风险不是强制修改指令。
模型必须逐项保存 revise/local/retain、理由、取舍和未解决风险，并重新查询必要的合法定义/事实。
只有最终发布成功、决策覆盖全部问题后，根目录才写入 `frozen-plan.json`，引用最终 plan 和 file-generation-list。
初稿的底层发布只是本流程的阶段产物；下游应以根目录冻结收据为准。
冻结不表示证明成功或质量收益已认证，也不会启动正式生成。

```bash
python3 -m regen.tools.reviewed_planning \
  --input reports/regen/refine-planning-input-v2-20260930/repository-planning-input.json \
  --output reports/regen/refine-reviewed-planning-astra-NEW \
  --allow-external-call
```

根目录 `status.json` 持续显示 draft/review/revision/frozen；各阶段日志分别保存。
异常记录 failed 和阶段，不覆盖证据、不把初稿冒充最终冻结结果。
初稿及修订各使用原政策单次模型上限 1800 秒，消费检查上限 600 秒，原生预检查上限 900 秒；
没有引入 proof pilot、滑动窗口或生成期全局重规划。

## 两轮历史核对与完整流程验收

历史检查覆盖 Sol 009（14 theories、24/287 accepted）和 Astra 006（30 theories、199/287 accepted），
均以各自冻结规划和精确 contract ID 核对，包含前序续跑累计状态，不是同预算 A/B。
两轮独立消费检查均完成；评审先冻结预测，再读取历史结果。
Astra 的 setThreadState 前提风险已由局部 helper 解决；部分接口消费者从未执行，不能算命中缺陷。
Sol 的 IRQ 分组发布风险在 Astra 单独 sealed 的 Interrupt_State 中有可行分层实例，但不构成因果收益证明。
详见 `reports/regen/plan-risk-two-runs-20261017-001/REPORT.md`。

完整新规划流程在 `reports/regen/refine-reviewed-planning-astra-20261018-003` 完成：
26 theories、287 contracts，三项风险逐项决策（两项 local、一项 revise）。
将 gcd_corres、curDomain_corres 从 Runqueue_State 移到 Object_State，删除 Untyped_Allocation 对 Runqueue_State 的硬导入；
强制依赖从 42 降到 41，allocation/VM 的传递前置 contract 数分别从 80/139 降到 58/117。
两次原生声明/import preflight 通过，冻结收据绑定的 351 个工件摘要核验一致。
成功轮约 17 分 47 秒，不包括前两轮超时成本；没有正式证明生成或实测提速结论。

结论：已验证检查能产生可追溯、可处理的接口风险，并驱动有限规划修订；
尚未验证其提高整体证明成功率或降低生成成本。风险数量与模型评审赞同率均不作为收益指标。
