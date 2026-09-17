# 单 theory 证明、提交恢复与发布

当前 repository projection 路径由 [proof_session_runner.py](../../execution/proof_session_runner.py)
分派到 [repository_execution.py](../../execution/repository_execution.py)。复用现有 File Planner、
单 theory DAG、GRA、Lemma Prover、whole-proof/stepwise backend 和 Isabelle checker；没有新增
Architecture/Observed/Lineage 运行图。旧 schema 1 schedule 保留原执行路径用于历史实验。
政策见 [总体设计](../design/REPO-LEVEL-PROOF-HARNESS.md)，上游见
[仓库规划](SESSION-PLANNING-IMPLEMENTATION.md)。

## 输入与启动

```bash
python3 -m regen.execution.proof_session_runner init \
  --run-dir reports/regen/proof-run-001 --run-id proof-run-001 \
  --environment-run reports/regen/live-environment-002 \
  --schedule reports/regen/live-environment-002/Session-Planning/file-generation-list.json
python3 -m regen.execution.proof_session_runner plan-file \
  --run-dir reports/regen/proof-run-001 --theory Generated_Arch_Access
python3 -m regen.execution.proof_session_runner run-session \
  --run-dir reports/regen/proof-run-001
```

`init` 验证完整 planning bundle/transcript、dataflow、gate 和冻结 policy，再生成 run-local
packages 与 execution-input。外层 generated theory 决定执行文件，contract 内的原 owner、
statement、attributes、context 和 provenance 原样保留。辅助专用文件可有空 contracts；
其辅助 intents 关联的 support contracts 只提供目标上下文，不作为已证 premise。
默认消费 v3 plan/projection 和 v4 file list，同时保留历史 v2 投影适配。Runner 按列表中的
theory 拓扑顺序处理文件；直接前驱的描述只作规划提示，未证明结果不能作为 premise。
仓库投影不携带局部 contract/helper DAG，由 File Planner 首次构造。
Repository suggestions 不自动成为局部 dependencies；File Planner 接受的精确图仍校验本地
引用、无环及 contract 完整性。停止的辅助专用 theory 从后续候选 provider 建议中排除，
原始不可变仓库方案不改写。没有 required cuts 或跨文件 helper 承诺。

已完成的 preparation manifest 不改写。新 proof run 单独记录执行模块字节哈希，绑定旧环境
和规划的身份；执行代码改变后应创建新 proof run。checker 使用记录的镜像 ID 和 Isabelle
安装，拒绝安装漂移。init 可重入但不能更换已有 run 身份或重置证明状态。

## 单图与辅助节点

在 repository 模式下，helper/theorem 都是普通辅助节点，允许非叶和无 consumer；consumers
与执行顺序从 dependencies 重建。dependencies 是调度前置条件，不要求实际证明引用。
SPLIT 保留原 parent 的命题及上下文，并增加 children 前置条件；children GREEN 不能替代
parent GREEN。REPLACE_STATEMENT 是辅助命题修订，不声明它蕴含旧命题。

每轮失败反馈 GRA；终止或耗尽预算后，辅助节点通过显式撤销候选清理计划边，重新做 graph
和 Isabelle statement 检查，再提交。固定 contracts 和 GREEN 内容不得撤销或削弱；旧命题、
原因、尝试结果、累计 rounds/revisions 留在提交历史。contract 继续尝试；contract 本身耗尽
预算则文件 failed。辅助专用文件所有节点撤销后记为 `retired`，不记 proved/sealed、不发布事实。
全部固定 contracts 所在文件 sealed，且其他活动文件 sealed/retired 时，运行汇总 complete。

## 提交和恢复

[proof_commits.py](../../execution/proof_commits.py) 实现：

```text
保存候选与 checker 输出 -> 校验转换 -> fsync 不可变 commit -> 更新可重建 HEAD/progress
```

每 theory 的 `acceptance/commits/000001.json` 起构成有序哈希链，绑定 run、projection package、
环境、规划、执行代码和 imports。覆盖初始 graph、GRA revision、撤销、节点 GREEN，以及
尝试预算、失败和最终 seal。commit 保存状态快照和校验证据，来源从前后状态与 decision
重建；没有独立 lineage graph。

checker source/log 内容在提交前嵌入，避免后续同名检查文件覆盖破坏已接受证据。GREEN
绑定 statement/proof、可见事实清单和实际 checker 结果。只恢复完整且通过转换校验的 commit；
候选文件、最大 graph-v 编号、progress 自报 GREEN 均不具有接受权威。commit 已写、HEAD
落后时只重建派生视图，不重复调用模型或 prover。缺失链中提交、证据或字节漂移会失败。

`active-graph.json`、`progress.json`、session-progress-ledger、生成源码及 generated-premises
是可重建视图。发布事实仅从 seal commits 重建，旧目录中的任意记录不能自动继承。
运行修改锁仍由 Runner 持有；正常使用应从 Runner CLI/API 进入，不并发调用内部 adapter。
恢复现有有效提交不等于重建丢失的外部 Docker 镜像或任意 heap。

## 事实可见性与完整检查

[fact_context.py](../../execution/fact_context.py) 为 prompt 与 checker 提供同一份 ordered facts。
同 theory 所有已接受节点形成固定声明前缀；重查一个 GREEN 节点只使用当时的前缀。
新增节点的拓扑排序不会在旧前缀中插入声明。每条事实保留自身 context 和 proof hash；
required/optional 仅区分调度关系，Isabelle 仍决定名称和 locale 上下文是否可用。
whole-proof 和 stepwise 的源码及 prompt 均使用这个前缀。跨文件仍只开放已 seal 的文件，
当前 theory 首次执行时固定 imports，后续跨 theory 回改不在本批范围。

所有节点 GREEN 后仍执行 complete check、retained impact/dependency audit 和 accumulated
Generated_Refine build；失败保存诊断，不自动修复组装，也不发布事实。通过后提交 seal，
再发布。引用报告使用 `evidence-confirmed` / `not-confirmed`，仅匹配 Isabelle 导出的完整
事实身份，不分析 proof 字符串推断采用，不承诺恢复 simp/wp 的完整引用集合。

## 预算与验证范围

冻结 campaign 中的 attempts/model timeout/generation revision 上限仍生效。未在 campaign
定义的每节点 rounds、timeouts 等使用 CLI 设置，并在文件首次执行时固定为 runtime-policy；
同一运行不能通过恢复更换预算。尝试开始先提交计数，中断的尝试也保留计费，不因撤销或改名
抹去历史成本。没有新增 4h/24h 的统一时钟政策。

测试见 [test_proof_commits.py](../../tests/current/test_proof_commits.py) 和
[test_repository_execution.py](../../tests/current/test_repository_execution.py)：覆盖未提交候选、
commit/pointer 间中断、缺失证据、GREEN/prefix 不变、撤销成本、辅助文件 retired、自由分组、
发布恢复和 optional fact 上下文。原模块测试继续运行，旧路径不声称获得新提交协议。
真实 Isabelle smoke 使用冻结 Semantic_Base，检查无调度边的 helper 引用、完整 theory
和 dependency audit；这些小例子不等于完成 287 个 seL4 contracts 的证明实验。

## 固定前缀检查与直接修复（2026-09-09）

新 repository projection 执行绑定使用 fixed-prefix-cold-green-v1：同一目标重试
复用 GREEN 前缀，前缀变化重建 worker；不同目标尚未实现自动增量追加。实现位于
`regen/execution/persistent_checker.py` 与 `.ML`，由 `isabelle_node_checker.py` 调用。
worker 的 GREEN 只作初筛，必须由原冷 checker 复核；audit/完整 theory/build 不变。
故障退回冷检查。旧运行没有此政策字段时保持冷检查，新 ML 实现纳入执行哈希。

普通失败默认直接把诊断交给下一次 Prover，不再独立调用 salvage；
`--salvage-provider deterministic|codex|codex-relay|claude` 可显式启用。
这不改变 GRA、contract 完整性或累计证明预算。

模型输出无效 JSON、缺少 proof_body、null、非字符串或空白时，Prover 保存本次输出校验失败并提前交给 GRA，明确标记 Isabelle 未调用；已有 checker 残余和模型自报原因分别保留。模型自报原因不作为事实。该分支计入尝试历史，不应因 strip/类型异常使 Runner 退出。

## 跨轮失败证据与检索一致性

`proof_feedback.py` 解析 cold/warm 两种 goal 诊断，模型自报错误不计作 Isabelle 语义证据。GRA 的决策历史来自已验证提交链，历史尝试按完整节点 ID 定位；phase 和 proof_round 明确传递。Prover 保存有界的已检查候选历史，区分最近模型输出错误与真实 checker 残余，避免新一轮空输出清除修复基线。

当前 whole-proof Prover/GRA 只在模型主动请求时召回事实，不自动从目标或残余预载。查询结果和诊断证据都保留审计；当前输入保留本轮选中的完整事实工作集，不按统一字符预算装箱。旧检索函数仍可供其他入口使用，不代表当前主动召回的行为。

## Prover 输入与交互工作集

输入包含完整目标statement/context、最新一次完整proof及反馈、本轮选中的完整事实工作集、已返回诊断页、checker上下文和真实修复指导。此前尝试仅保留方法、结果、错误类别、符号与诊断索引；较早轮次保留最近3轮摘要，每轮最多5条尝试摘要。新轮另带一个完整已检查修复基线。没有自动广泛预载，也不按字符额度填充或截断proof/命题。巨大原始诊断使用证据哈希/分页，摘录不代表完整原始goal。

`observation_context.py`统一组织recalled_facts、observed_nodes和recall_results。事实和图节点按ID去重，观察结果引用同一份完整记录。本轮事实选择后冻结，后续诊断/图查询不能追加fact。跨轮不恢复旧fact或旧观察页；旧Runner即使保存这些审计字段，Prover/GRA输入也不继承。原始输入、查询候选及checker审计继续留存，数据库不因输入筛选而删除记录。

`max_attempts`表示proof候选次数；`max_observations`默认每轮8次独立观察。模型输入明确剩余次数和must_submit_proof，查询用完后仍可交proof。继续请求查询会记录interaction/observation_budget_exhausted，不能误报空proof或Isabelle失败。GRA收到真实错误类别与查询次数，不继承Prover的事实包或查询轨迹。原每次调用/检查超时与外层轮次预算继续适用。

premise_query 的 scope 为 upstream/sealed/local/all，每页最多5条完整命题。来源仍限于冻结合法上游（包含 Semantic Base 定义）、带 seal 的已发布记录和当前 GREEN lemma；不返回历史人类 proof，不开放任意文件访问。

共享入口 `regen/execution/premise_recall.py` 接受 mode=auto/exact/definition/search：exact 按完整名称、名称后缀或 record ID 精确查找，区分大小写且不补模糊结果；definition 按常量查找定义左侧与定义/simps 事实名，支持实际导出的 real_def 等形式；auto 优先精确匹配，也支持一组 _def 名称并逐项披露结果。不存在的精确名称不靠低相关事实填满页面。定义查找基于 catalog，不承诺展开所有 Isabelle abbreviation。

search 要求所有去重标识符均匹配，保留完整标识符的大小写和下划线，不将getObject与get_object混为一类，不能只因 get/object/return 等 CamelCase 碎片而命中。名称匹配优先于仅正文引用，遗漏的正文候选数单独披露。独立定义应分别查询或使用 auto 的 _def 名称列表；词法近似不表示常量相同。

结果记录有效查询模式、匹配类型、词项、精确命中状态、批量逐项结果及 next_offset；完整命题不截断。只读 SQL 在读取前过滤合法来源，取消评分前的字母序512条截断。未命中仅表示该查询规则和可见 catalog 下没有结果，不表示没有有用事实。该精确召回补丁的验证见 `reports/regen/premise-recall-precision-001/REPORT.md`。

## GRA 输入与观察

GRA默认看失败节点、直接前驱/consumer、相关contract、最新一次完整proof及反馈、此前尝试与较早轮次摘要、最后决策摘要/最新拒绝提案。GRA每个决策独立选择一批fact，不继承Prover或上次GRA的事实包。任意当前图节点和诊断页仍可按需查询，结果只在当前决策内保留。每个决策最多7次观察，输入明确剩余次数与must_decide，共享原decision timeout。

观察期间不提交图修改。展示范围不成为修改权限；提案仍经完整图、contract/GREEN和事务验收。已查询的非GREEN图节点只是规划信息，不能当作已证明事实。见reports/regen/recall-context-repair-001/REPORT.md。


## 每轮一次事实选择

`regen/execution/round_recall.py` 为 whole-proof Prover 的每个逻辑 round、GRA 的每个决策提供相同协议。模型依据完整目标与最新反馈，首次可请求 `premise_queries`，包含1–4个明确的 query/mode/scope；兼容单条 `premise_query`。每个查询仍由严格召回器返回最多5个候选，按各查询内部排名交错选取、按ID去重，最多12条完整fact。精确查询不补模糊结果；数量上限是结构参数，不是字符截断，也不是语义相关性的保证。

选择一次即冻结，即使返回空集；Prover若先交proof，也锁定本轮空集。再次请求fact得到 `rejected_frozen`，不调用召回器、不替换工作集。下一round/下一GRA决策可重新选择。诊断分页与GRA图查询仍在原观察预算内，不能借此将未证明节点当作fact。已选完整statement/context不截断；若其本身很长，prompt仍可能超过一万字符。

`fact-selection.json` 保存整批请求、原候选、所选ID与政策。输入只包含所选记录一次和查询元数据；旧事实与未选候选只留审计。sealed来源使用该theory冻结的 `sealed-premises.json`，与checker imports一致。

## GRA statement 拒绝与修正

沿用 Isabelle `check_graph` 检查声明在完整候选上下文中的语法、名称和类型，不新增第二套声明解析器；检查通过不等于helper已证明。GRA修订候选先经既有图/contract/GREEN约束，再做声明检查。RED候选不成为活动图，Runner提交拒绝反馈与已消耗预算，随后将最新候选完整statement/context及checker诊断送回GRA，要求基于原活动图修正。大诊断可以按哈希分页查询。

每次纠正计入既有每节点/全局GRA revision预算；耗尽明确停止。只有通过检查的候选才能提交为活动图。进程在拒绝之后退出，恢复先继续纠正，不额外启动一轮proof；预算计数不会回退。GRA显式放弃候选并选择RETRY/撤销时，清除该候选的待纠正状态，仍遵守固定contract和GREEN约束。初始File Planner已有独立的声明检查/最多3次修正路径，保持不变。


## 交互错误、提案拒绝与符号解析（runtime-repair-001）

非法premise/diagnostic/graph请求在原Prover或GRA交互内返回request_error，消耗观察预算而不生成空proof；原事实集不改变，未成功完成选择时不冻结。基础设施错误仍独立退出，不伪装查询未命中。

Repository GRA的非法JSON/decision schema、Python图校验及statement接口错误抛出ProposalRejected，Runner保存拒绝提案、诊断与已计成本，继续GRA纠正，绝不改写为PROOF_BODY_ERROR/RETRY_PROOF。Isabelle statement拒绝沿用同一恢复路径。纠正仍受现有GRA预算约束，固定contract停止时保存决策类别、理由与具体预算计数。

定义请求在每轮唯一一次事实选择中可调用symbol_resolution：使用当前checker合法imports与目标context，批量最多4个标识符，通过Isabelle Syntax.read_term得到展开式、完整限定常量及带类型/类约束的签名。输出明确is_fact=false；真正的定义仍从合法catalog取回。不存在的标识符若仅被解析成自由变量，必须报告未解析，不制造fact。源码/log和证据哈希保存于checker的symbol-resolution目录。

表达式展开后的常量最多进一步查询4个定义入口；所选完整facts仍受每批12条上限和轮内冻结政策约束，未选择候选保留审计。符号检查使用最多120秒、同checker相同imports/context/批量可复用内存结果；不引入目标专用别名、不读删除证明、不执行证明搜索。该接口有额外Isabelle成本，应在真实运行中记录，不能把一次SQLite查询耗时当整个召回开销。


### 长任务监护与中断恢复

长时运行使用 `regen/tools/supervised_proof_run.py RUN_DIR --source SOURCE_RUN --theory THEORY`，由独立的systemd用户服务启动，不依赖对话工具会话存活。`supervisor-status.json` 提供外部进程健康状态和退出码，`runner-status.json` 提供证明阶段；已接受证明仍从acceptance事务重建。外部监护不自动重启或重置预算。

`repository_execution.resume_round_feedback` 复用已有checker绑定恢复，向下轮提供最近中断轮的真实失败候选/诊断；中断轮仍已计费，未返回的模型调用不成为证明事实或失败证据。新轮fact重新召回，历史fact不自动累计。
