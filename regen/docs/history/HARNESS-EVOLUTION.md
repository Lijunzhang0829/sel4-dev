# Repo-Level Proof Harness：问题、修复与经验

本文档不再保存逐轮开发流水账。它只记录真正改变 harness 设计的问题，并按问题
类型分类。每个案例回答四件事：

1. 在什么阶段或 generation 遇到；
2. 观察到什么失败；
3. 根因是什么；
4. harness 如何修复，以及形成了什么长期规则。

更细的旧命令、逐 attempt residual 和 provider stream 仍可从对应 generation artifacts
与 Git 历史恢复，不在这里重复。

## 1. 实验边界与信息隔离

### 案例 1：早期 repo regeneration 实际退化为 lemma infill

**阶段**：早期 `regen` 分支，whole-theory rewrite 与增量 lemma lock-in 实验。

**问题**：系统保留了大量人类 helper statements、声明顺序和局部 proof decomposition，
然后只让模型填 proof body。单 lemma 通过率可以提升，但无法支持
repo-level proof architecture regeneration 的结论。

**根因**：生成单位虽然名义上是 theory，模型实际面对的仍是人类已经拆好的 lemma
队列。规划器没有重新决定 theorem graph。

**修复**：把 fixed contract、generated obligation 和 generated helper 分开。Proof
theory 只公开 fixed contracts 与合法 semantic context，File Planner 自行生成 theorem
DAG，Lemma Prover 只负责 certified node 的 proof body。

**形成的规则**：评价 repo-level 能力时，不能把人类 helper 名称、statement、role、
order 或 consumer graph 当作输入。保留这些信息的实验只能称为 proof repair 或
lemma completion。

### 案例 2：Proof/Support 分类把人类 proof power 当成合法环境

**阶段**：Setting B 初版，对 Refine theories 按是否包含 selected contract 分类。

**问题**：零 selected-contract 的 Support theory 被整体冻结，其中仍可能包含大量
人类 theorem、automation registration 和 refinement-shaped facts。模型虽未读取
Proof theory source，却可以经 Isabelle context 使用 Support 中的人类证明能力。

**根因**：`不拥有目标 contract` 被错误等同为 `纯语义基础`。文件级分类无法描述
command 执行后的 proof power。

**修复**：删除 Proof/Support trust boundary，改为 command-level semantic substrate。
普通 theorem/helper commands 删除；contract elaboration 所需 semantic commands
保留；所有 retained exports 与 automation memberships 单独披露。

**形成的规则**：信任边界必须落在 command/effect 上，不能只落在文件类别上。

### 案例 3：必要 semantic command 仍可能导出 refinement theorem

**阶段**：第一次 Semantic Base clean-build 后的 Context Effect Audit。

**问题**：源码中没有普通 `lemma/theorem` command，并不表示 Isabelle context 中没有
refinement proof power。审计发现包括
`IpcCancel_R.delete_one.delete_one_corres` 在内的 refinement-shaped exports。

**根因**：locale、definition、interpretation 或其他 framework command 执行后可以
生成 theorem facts 和 automation effects。只扫描源码 command kind 会漏掉运行时
结果。

**修复**：保留 command 与评估 proof power 分成两个问题。command 是否保留只由
contract semantic necessity 决定；保留后产生的 facts、`simp/wp/cong` membership
和 named collections 由 Context Effect Disclosure 记录。Generated theories 封存时
再统计显式、proof-term 和 automation 实际依赖。

**形成的规则**：`Premise Catalog 不公开某个 fact` 不能证明 automation 没有使用它。
最终报告必须给出 retained facts 数量以及实际受其影响的 generated theorems。

## 2. Contract 提取与 declaration 净化

### 案例 4：public contract package 泄漏 proof-context hints

**阶段**：campaign `sel4-refine-setting-b-360fdf9a7f7eb738` 的 extraction audit。

**问题**：公开 package 中残留 `notes`、`using`、`including`、`is` pattern、
`[wp del]` 和 `[cong]`。这些内容不是 proposition，却会直接指导证明搜索。

**根因**：提取器按文本截断 proof body，没有完整覆盖 Isabelle outer syntax 中位于
theorem context 的 proof commands 和同一行尾随命令。Audit 与 extractor 共用同一
不完整关键词集合，因此出现假阴性。

**修复**：把 proposition clauses 与 proof-context commands 分开处理；保留 theorem
name、fixed attributes、`fixes/assumes/defines/shows`；删除 notes、using、including、
unfolding、supply、is pattern 和 proposition 外注释。真实泄漏案例进入回归测试。

**形成的规则**：sanitizer 与 leakage audit 必须使用不同检查路径；public package
需要通过 exact contract comparison 和 forbidden-command negative tests。

### 案例 5：前置 includes 被错误当作 proof-body 截断点

**阶段**：修复上一个 contamination 后的复审。

**问题**：

```isabelle
lemma demo_corres:
  includes no_pre
  shows "corres dc P P f g"
  by simp
```

被净化成只有 `lemma demo_corres:`，后续 proposition 全部丢失。

**根因**：`includes` 与 `including` 被放进同一个通用禁止词集合。提取器遇到前者后
截断整个声明，而不是只删除 context clause。

**修复**：`includes ...` 使用独立 context-block removal；`including` 仍作为
proposition 后的 proof-body boundary。增加 proposition-presence 与 exact hash 测试。

**形成的规则**：相似 Isabelle keyword 不能仅凭字符串相似共享处理策略，必须根据
outer-syntax 位置判断。

### 案例 6：只冻结顶层 Git revision 无法标识真实 l4v 来源

**阶段**：Setting B 第一次 campaign freeze 复审。

**问题**：manifest 记录了顶层 `seL4-docker-main` revision，却没有记录独立
`verification/l4v` repository revision；Proof theory bodies 改变而 contracts 不变时，
旧 evidence 仍可能看似有效。

**根因**：顶层 repository 不追踪 l4v source files，ROOT hash 也不能替代全部 trusted
sources identity。

**修复**：冻结 l4v revision、scope clean state、全部 41 theory source hashes 和
forbidden source inventory；`--verify` 重算这些输入。

**形成的规则**：campaign identity 必须绑定实际提供 contracts 和 Isabelle context
的 repository，而不是只绑定外层容器工程。

## 3. 干净 Isabelle 环境与 Semantic Base

### 案例 7：净化后的文件存在，不等于访问隔离成立

**阶段**：旧 Setting B 的 environment preflight `739cfcb1ec51da4e`，后来统一为 Environment Preparation。

**问题**：workspace content audit 没发现 forbidden hashes 或 paths，但模型进程仍
可能通过原路径、heap、cache 或 alias 读取人类 Proof source。最初报告把这一半结果
写成 `source-access PASS`。

**根因**：materialization audit 只检查复制了什么，没有检查运行进程还能访问什么。

**修复**：模型运行环境只挂载 sanitized runtime 和 Semantic Base；原始 target Refine
theories 从 runtime 中删除；audited Codex 使用只读、无工具 clean runtime；gate
绑定 sanitized-runtime manifest。

**形成的规则**：`workspace clean` 与 `source access denied` 是两个不同断言，报告
必须分别给证据。

### 案例 8：删除 defs-style definitions 使最小 proof 也无法成立

**阶段**：早期 `setCurThread_corres` tower-leaf 实验。

**问题**：看似简单的 state relation proof 长时间无法闭合，最后发现关键
`defs`-style specification definitions 被 keepset 删除。

**根因**：旧 sanitizer 把某些 declaration 误判为可删 proof scaffolding。模型面对的
不是困难 theorem，而是语义不完整环境。

**修复**：Command Inventory 记录完整 declaration owner span；Semantic Base baseline
必须和所有 exact contract shells 一起 build。只有 Isabelle PASS 才说明一组删除对
当前 contracts 是可接受的。

**形成的规则**：在归因模型能力前，先证明 statement 的 semantic environment 健康。

### 案例 9：Word_Lib timeout 被误当成 contract elaboration failure

**阶段**：Environment Preparation 初版，180 秒 preflight。

**问题**：构建一直停在 `Building Word_Lib`，contract shells 尚未开始检查，却被外层
runner 看成 gate 命令成功保存诊断，甚至可能被 CI 当作 PASS。

**根因**：父 session cold build 与 contract checking 共用 timeout；CLI 退出码没有
区分 `诊断已保存` 和 `gate 已通过`。

**修复**：建立可复现 parent heap cache，记录 cache identity、image、effective
command 和 wall time；只有 Semantic Base 与 287 shells 全部 GREEN 才发布 gate；
timeout 返回非 gate-success 状态。

**形成的规则**：基础设施 timeout 不能成为语义判定，也不能消耗 proof attempt 预算。

### 案例 10：contract statement 本身不足以构造完整 Isabelle context

**阶段**：ArchMove_R、Invariants_H 和早期 contract-shell experiments。

**问题**：只把 `corres` proposition 放进临时 theory 时出现 unknown constants、
locale context 缺失、syntax/notation 不可用或 attributes 无法解析。

**根因**：statement 使用的类型、notation、locale、interpretation 和 framework
declarations 不一定出现在 proposition 文本里。Corres statement 描述目标，不描述
让目标可解析的全部环境。

**修复**：public contract package允许经过净化的最小 enclosing context opener；
Semantic Base 保留 contract-sufficient framework commands；每个 exact shell 在同一
base heap 中 elaboration。

**形成的规则**：Contract 是证明目标接口，不是完整 framework declaration inventory。

## 4. Premise、自动化与上下文可见性

### 案例 11：全库检索既召回错误架构 facts，也放大 prompt

**阶段**：早期 retrieval over full theorem dumps。

**问题**：检索结果混入其他 architecture、不可见 sibling theories 和未来生成 facts；
模型偶尔生成 Isabelle 中不存在或当前 context 不可访问的 fact names。

**根因**：文本相似度没有编码 owner session、architecture、publication state 和实际
import context。

**修复**：Premise Catalog 记录 canonical identity、owner、source class、attributes、
automation membership 与 publication state；retrieval 先做 legal visibility filter，
再做语义排序。只有 frozen parents、retained semantic exports、sealed generated
theories 和当前 DAG dependency closure 可见。

**形成的规则**：`相关` 和 `合法可调用` 必须先后分开，legal filter 先于 ranking。

### 案例 12：同一 theorem 的 alias 和重复 export 破坏引用身份

**阶段**：ArchAcc_R runtime retrieval 与 generated-premise repair。

**问题**：同一事实以短名、fully-qualified name、locale alias 或多个 inventory record
出现。模型选中一个名字，validator 却认为它不在授权集合；重复 records 还挤占 top-k。

**根因**：数据库把 source name 当作 theorem identity，没有规范化 derivation identity
和 aliases。

**修复**：Premise Catalog 建立 canonical declaration ID 与 alias set；查询去重后再
diversify；proof text 中的 fact name由 parser 映射回 canonical record。

**形成的规则**：authorization 对 canonical fact identity 判断，prompt 可以展示最适合
当前 context 的可解析 alias。

### 案例 13：有正确 premise，不等于知道如何组合

**阶段**：KHeap_R `getEndpoint_corres`、`setEndpoint_corres` 与 ArchAcc_R。

**问题**：retrieval 已找到关键 corres、wp 和 relation lemmas，模型仍因实例化顺序、
monadic composition 或 branch structure 错误而失败。

**根因**：Premise Retrieval 只回答 `WHAT EXISTS`，不回答 theorem dependencies 应按
什么顺序组合。扩大 top-k 只会增加 branching factor。

**修复**：拆分三类信息：
- Premise Retrieval：合法 facts；
- Automation Advisor：局部 `simp/wp` probes；
- Local Proof Outline：composition steps 和 dependency order。

**形成的规则**：premise availability、local automation choice 和 theorem composition
是三种不同失败类别。

### 案例 14：名称扫描看不到 simp/wp 的真实依赖

**阶段**：KHeap_R GREEN proofs 的 automation audit。

**问题**：proof body 只写 `simp`、`wp` 或 `auto`，源代码没有列出被使用 theorem
names。只统计显式 `simp add` 和 `rule` 会低估 premise reuse，也无法判断 retained
semantic facts 是否影响生成结果。

**根因**：automation pool 存在于 Isabelle context，实际 tactic selection 由
simplifier/wp engine 完成。

**修复**：checker snapshot active automation context，并用 Isabelle proof dependency
information提取 accepted theorem 的实际 dependencies。Theory seal 按来源分类：
frozen parent、retained semantic export、local generated 和 sealed generated。

**形成的规则**：模型不需要模拟 simplifier，但 harness 必须让 Isabelle报告实际
context 和 proof dependencies。

## 5. Repo / Session 级规划

### 案例 15：空接口 theory 被误解为无用文件

**阶段**：LevityCatch、Move_R、InvariantUpdates_H、Invocations_R 的初期生成。

**问题**：前六个 theory 没有 selected corres，生成文件看似只是空接口。系统一度
把 `无 contract` 理解为 `无需考虑`。

**根因**：theory 可能提供 syntax、locale、definitions、interpretations 或下游需要的
framework declarations；contract count 不能直接代表 repository role。

**修复**：早期使用 framework inventory；当前架构进一步取消 Proof/Support trust
分类，把 contract-sufficient commands 统一放入 Semantic Base。Session Planner 只
安排 21 个 contract-owning slots。

**形成的规则**：无目标 contract 的文件不需要生成 proof layer，但其 semantic effects
必须经过环境模块处理。

### 案例 16：有效 transcript 可以产生没有证据的 schedule

**阶段**：第一次 evidence-aware Session Planner audit。

**问题**：模型调用真实、日志完整、JSON schema 合法，但 candidate dependency 为空或
理由只写通用架构知识。旧 audit 仍给 PASS。

**根因**：protocol validity 与 schedule quality 混在一起；validator 只检查格式，不
检查 edge 是否引用两端 current contracts。

**修复**：每条 edge 必须引用 provider/consumer contracts，并提供两端 statement 中
共享的非 generic symbol。`contract-composition` 才能形成 layer；
`operation-call` 只影响同层顺序。Accepted/rejected evidence 全部保存。

**形成的规则**：模型输出是候选证据，不是最终 schedule authority。

### 案例 17：旧 file list 可以被错误接到新 gate

**阶段**：旧两层 schedule 向 successor environment 迁移时。

**问题**：文件名和 contract 数量相同，runner 可能接受来自旧 policy、旧 package
payload 或旧 planner transcript 的 list。

**根因**：验证器只看 list 内部 hash，没有从当前 gate 和 sibling artifacts重建期望
bundle。

**修复**：file list 绑定 campaign、environment gate、contract-package payload、
policy 和 dependency evidence；runner 验证完整 sibling bundle并逐个比对 slots 与
contract IDs。

**形成的规则**：immutable-looking artifact 不等于 replay evidence；verifier 必须从
权威输入重建。

### 案例 18：contract-only planning 得到一层，而不是强 theory DAG

**阶段**：当前 production campaign `d6c26eb4fae33cab`。

**问题**：GPT-5.6 提出 17 条 candidates，14 条合法，但全部是 weak
`operation-call`；没有 strong `contract-composition`，因此 21 theories 处于同一层。

**根因**：fixed contract surfaces 能暴露部分 callee-before-caller 关系，却不足以可靠
推断完整 proof dependency。强行分层会把猜测包装成事实。

**修复**：保留一层与确定性 serial fallback，明确报告 schedule-quality limitation，
不读取 human import graph 补答案。

**形成的规则**：协议 PASS 与规划质量是两个结论；同层表示证据不足，不表示独立。

## 6. 单 Theory theorem DAG 与 Graph Revision

### 案例 19：无限 helper 增长与过严 leaf 限制都失败

**阶段**：regen 分支 Claude helper experiments，以及 ArchAcc_R generation
`20260723T090932Z-92a8df5c`。

**问题**：不限制 helper 时，模型不断把 residual 拆成新 lemmas，图快速膨胀；后来把
新增 helper 强制为永远不可拆的 leaf，又使真正复杂的 helper 6 卡死。

**根因**：helper count 被当作唯一控制变量，没有区分局部 proof failure、statement
错误和需要结构修订的情况。

**修复**：File Planner给出初始 theorem DAG；Lemma Prover 不能改图；Graph Revision
Agent 根据 machine evidence审核 `KEEP/RETRY/ADD/SPLIT/REPLACE/ABANDON`。
Helper leaf 没有 generated children；若需要拆分，必须先通过 role promotion 或 node
replacement transaction。

**形成的规则**：图增长必须有独立审批，但审批依据是 residual 和 consumer need，
不是固定 helper 数量。

### 案例 20：helper statement 丢失 parent guard

**阶段**：ArchAcc_R helper 6 和 parent-guarded preservation trial。

**问题**：拆出的 helper 在更弱前提下声称 preservation，Isabelle长期无法证明；把
human proof tactic 换来换去也没有帮助。

**根因**：planner 从 parent contract 中抽取局部结论时遗漏 ownership、domain 或
unchanged-region guard。Statement shell 只证明语法和类型，不证明命题为真。

**修复**：Graph Revision request 必须携带 consumer assumptions 与 parent guard
provenance；新增 statement 由 certifier 检查 guard continuity；必要时拆为 concrete
domain、address normalization、changed point、same-owner、foreign-owner 和 relation
composition 等独立 obligations。

**形成的规则**：修改 statement 时首先检查逻辑强度与 guard provenance，再增加 proof
search budget。

### 案例 21：GREEN node 不可修改降低跨 contract 复用

**阶段**：第一版完整 KHeap_R 对比 human proof 后。

**问题**：每个 contract 根据自己的 residual 增加专用 nodes。一旦 GREEN 就冻结，
后来的 contract 即使出现相似 helper，也不能回头 generalize，导致跨 contract sharing
较少、proof 较长。

**根因**：在线生成优先保证稳定和可恢复，缺少 theory-green 后的全局 abstraction pass。

**修复**：当前版本保留 GREEN immutability，避免重新打开已完成证明。记录未来
Graph Abstraction Pass：只在完整 theory GREEN 后，基于 generated statements、
actual dependencies 和 consumers提出 merge/generalize/extract，并要求全部重检。

**形成的规则**：active generation 与 post-hoc maintainability optimization 分开；
当前不为追求复用牺牲可恢复性。

## 7. 单 Node 证明、反馈与搜索

### 案例 22：把所有 RED 都归因为 theorem 太难

**阶段**：ArchAcc_R 早期失败复盘。

**问题**：失败中混有 DAG/JSON 格式错误、premise context 错误、runner 生命周期错误、
provider timeout 和真正 Isabelle residual，却统一触发更多 lemma attempts。

**根因**：harness 没有先分类 infrastructure、planning、retrieval、proof search 和
semantic failure。

**修复**：RED result 保存 failure class、failed command、Isabelle diagnostic、
retrieval history、best frontier 和 provider metadata。基础设施错误不消耗 theorem
attempt budget；statement/graph 错误回规划；只有 proof residual 进入修复。

**形成的规则**：先定位 failure authority，再决定增加算力还是修改结构。

### 案例 23：只保存最后 residual 会丢失真实进展

**阶段**：ArchAcc_R 深层 relation proof 与 KHeap_R long proof repair。

**问题**：多次尝试 subgoal 数量相同，但有些已经执行更长 verified prefix 或暴露更
具体的 semantic residual。旧 runner 只保留最后一次失败，后续修复反而倒退。

**根因**：用 subgoal count 或 attempt index 代替 proof progress；没有 monotone
frontier。

**修复**：保留 top verified prefixes/residuals，按 proof depth、residual shape、
subgoal count 和 state novelty排序。Whole-proof repair 必须逐字保留 chosen verified
prefix，只生成关闭 residual 的 suffix。

**形成的规则**：RED node 是机器检查过的 ranked frontier，不是可以被下一次随机
candidate 覆盖的空状态。

### 案例 24：Stepwise 搜索开销大且不保证优于 whole-proof

**阶段**：新版 KHeap_R runner 性能分析。

**问题**：每个 node round 重建临时 session、重放 dependencies、展开多个 IsaREPL
states。简单 theorem 也进入树搜索，wall time 明显高于旧对话辅助 whole-proof。

**根因**：Stepwise 被固定成唯一入口，而不是可替换 prover backend；state expansion、
checkpoint 和 provider latency叠加。

**修复**：把单 node proving 定义为 backend-agnostic logical rounds。当前 baseline
使用 whole-proof + local salvage，每 round 最多五次；Stepwise 保留为可比较 backend，
可以从 verified prefix/residual 启动而不影响 whole-proof 能力。

**形成的规则**：repo-level harness 评价 node GREEN/RED 和证据质量，不把某一种搜索
算法写死成研究结论。

### 案例 25：Salvage 与 Graph Revision Agent 职责混淆

**阶段**：KHeap_R 多 round generation，出现连续 decision 与预算耗尽。

**问题**：Salvage 和 GRA 都返回 `再试一个证明`，但 GRA 的整体建议未进入下一
logical round；同一 node 出现多个 decision，后一 decision 又没有预算执行。

**根因**：局部 proof repair、跨 attempt diagnosis 和 graph transaction 没有清晰控制流。

**修复**：
- Salvage 只看局部 verified prefix、best/latest candidate 和 residual差分，修 proof；
- GRA 看全部 round摘要、consumer、dependency 和 graph role，决定下一 round directive
  或 graph change；
- 一个 logical round 完成后只产生一次 GRA decision；
- `RETRY_PROOF` 必须携带新的 retrieval、outline、frontier、automation policy 或
  repair diagnosis，并在下一 round真正执行。

**形成的规则**：局部 suffix 修复不等价于结构诊断；decision 没有执行证据就不算完成。

### 案例 26：timeout 是独立 residual，不是普通 tactic failure

**阶段**：ArchAcc_R proof search 和 KHeap_R assembly 多次长时间无输出。

**问题**：某个 command 卡到总 timeout，日志只显示 theory timeout；runner 不知道卡在
哪个 node/command，前端看起来像数小时 think。

**根因**：Isabelle build 输出没有 node markers；provider think、网络等待和 checker
运行也未分别记录 heartbeat。

**修复**：node checker与 assembly在 declaration 前写 marker；timeout 提取最后 marker
与 source offset；LLM、Isabelle 和 runner分别发 started/heartbeat/finished events；
provider failure 与 theorem RED分开。

**形成的规则**：任何 timeout 都必须回答 `在哪个模块、哪个 node、哪个完整 command`。

## 8. Theory 组装、封存与发布

### 案例 27：单节点 GREEN，完整 theory 仍可能 RED

**阶段**：KHeap_R 首次所有 contracts GREEN 后的 assembly。

**问题**：单 node checker 中全部证明通过，拼成完整 KHeap_R 后仍出现 fact ambiguity、
simp pool变化、声明顺序影响、duplicate terminator 或 timeout。

**根因**：单 node context 是相关性较高的局部环境；完整 theory 会增加 local facts、
attributes、automation pools 和 namespace effects。两者证明空间并不完全相同。

**修复**：Assembly Stabilizer 从完整 theory root replay，只允许修改 proof body；
statement 和 graph 不变。它使用最后 marker定位失败 node，并在成功后重新做完整
dependency audit。

**形成的规则**：node GREEN 是必要条件，不是 theory seal。Assembly failure 默认先做
body-only stabilization，不立即重开 theorem DAG。

### 案例 28：模型生成 theory header 和 imports 不稳定

**阶段**：早期 whole-theory rewrite。

**问题**：模型会 hallucinate import、漏掉 namespace、复制人类 header 或引入未授权
theory。Proof body 正确也无法 build。

**根因**：把确定性 repository plumbing 交给了生成模型。

**修复**：theory name、slot 和 fixed contracts由 harness拥有；imports 先由 proof 前的
candidate import检查建立 context，再根据 actual cross-file dependencies 在 assembly
保留和最小化；完整 theory header 由 assembler生成。

**形成的规则**：模型提出 proof architecture，确定性代码组装 repository syntax 与
validated imports。

### 案例 29：Isabelle GREEN 与可发布不是同一状态

**阶段**：增量 heap sealing 与 generated-premise support wave。

**问题**：某个 theorem或 theory 在当前 workspace GREEN，就立刻进入后续 premise
pool。后来发现 transcript 不可审计、source drift 或完整 theory未通过，后续结果已受
污染。

**根因**：没有区分 `green`、`sealable` 和 `published`。

**修复**：local GREEN nodes 只在当前 DAG dependency closure 临时可见；只有完整
theory clean-build、audit 和 seal 后才持久发布。下游 record provenance 指向 exact
generation ID 与 theory seal。

**形成的规则**：publication 是独立 gate，不是 Isabelle return code 的副作用。

## 9. 日志、生命周期与运行可靠性

### 案例 30：对话驱动启动无法稳定恢复

**阶段**：Bits_R、ArchAcc_R 初期由聊天逐文件启动。

**问题**：generation ID 的创建时机、脚本参数、LLM输入输出位置和 resume point依赖
对话记忆。服务重启后无法判断任务是否仍运行。

**根因**：conversation 承担了 runner 控制面。

**修复**：每个 theory 使用 source-independent fresh generation ID；脚本负责
plan/run/resume；模块日志按 Session Planner、File Planner、Node Prover、GRA、
Assembly 分目录；runner ledger保存状态和 active graph。

**形成的规则**：对话用于决策和审查，不是生产任务的唯一启动器或状态数据库。

### 案例 31：共享 transcript 文件会覆盖失败证据

**阶段**：per-theory planner logging 和当前 Session Planner model-name failure。

**问题**：重试复用 `planner.final.txt`，可能覆盖前一次 provider error；空 final 又会
让 runner以为已有结果而拒绝继续。

**根因**：一个 logical call 与一个 physical provider attempt 被当成同一个文件。

**修复**：transcript family使用 `name`、`name--call-001` 等不可覆盖 prefixes；
reconcile只选择 prompt/model匹配的最新 sealable attempt，失败 attempts 永久保留。

**形成的规则**：逻辑 decision 可以有多个物理 attempts，但只能有一个被绑定的成功
response。

### 案例 32：provider、quota 与模型 ID 错误被误判为证明失败

**阶段**：Claude 529/weekly quota、网络中断，以及 Session Planner
`codex-5.6` 400 failure。

**问题**：provider没有返回 candidate，runner却消耗 node预算或生成 GRA
`ABANDON`；前端只看到 think 卡住。

**根因**：transport failure、account quota、model availability和 theorem residual没有
独立状态。

**修复**：provider metadata记录 return code、timeout、stderr和 heartbeat；
provider failure不消耗 proof budget；模型 ID 先用小 prompt smoke，再冻结到 campaign。
当前 GPT-5.6 正式 ID 使用 `gpt-5.6-sol`。

**形成的规则**：大 payload生产调用前先验证 transport与模型；所有外部失败可重试且
不改变 theorem judgment。

## 10. 评价、复用与当前保留问题

### 案例 33：KHeap_R 显示模型更偏向长、显式 proof

**阶段**：第一版完整 generated KHeap_R 与 human version 对比。

**问题**：模型 proof LOC 和中间 nodes更多，倾向显式 `have`、relation展开和局部
residual修复；human proof更多利用稳定的 `wp/simp` 注册与已有组合接口。

**根因**：模型能看到 retrieved premises，却缺少 active automation affordance和
theory-level abstraction经验；residual-driven repair又偏向当前 consumer专用闭合。

**修复**：增加 Automation Advisor、proof-term dependency extraction和 future
Graph Abstraction Pass设计；当前版本不自动重写已 GREEN graphs。

**形成的规则**：评价不能只看 contract GREEN，还要测 proof LOC、helper reuse、
automation dependence、cross-contract fan-out和 rebuild stability。

### 案例 34：Premise Retrieval 的返回数量不等于真实复用

**阶段**：KHeap_R premise reuse重新统计。

**问题**：日志中召回很多 facts，但 proof最终只显式使用少数；另一些由 `simp/wp`
隐式使用。只看 retrieved top-k 会同时高估显式复用、低估 automation复用。

**根因**：retrieval candidate、prompt mention、proof source name和Isabelle certificate
dependency是四个不同集合。

**修复**：分别报告 retrieved、named-in-candidate、explicitly-used、
proof-term-dependent和automation-dependent records，并按 provenance分类。

**形成的规则**：Premise Database 的价值是合法检索和可追踪 provenance，不以
`召回后全部被使用` 为成功标准。

### 案例 35：retained semantic proof power 的影响审计

**阶段**：Semantic Base 已封存、proof generation 尚未产出完整 Refine 时。

**问题**：环境已经记录 retained semantic exports 与 automation memberships，但仅凭
fact 存在或位于 `simp/wp` pool，不能回答 generated proofs 是否实际使用了它。反过来，
只扫描 proof source 又会漏掉 automation 隐式依赖。

**根因**：source-name mention、active automation membership、Isabelle proof
certificate dependency 和 tactic 内部选择不是同一类证据。

**修复**：新增 `retained_semantic_impact.py`，并接入 Theory 组装硬 gate。所有 nodes
GREEN 且完整 theory GREEN 后，runner 在一次 complete-theory Isabelle process 中批量
导出所有 generated theorems 的 certificates，并复用同一 active automation snapshot，
输出：

```text
retained semantic facts available
retained simp/wp/cong memberships available
unique retained names mentioned in generated proof source
retained facts present in Isabelle certificate dependencies
automation-associated retained certificate dependencies
exact fact/rule names and affected theorem IDs
```

只有 certificate dependency 计为实际使用。一个依赖同时属于 active automation pool，
且 proof source 调用了支持的 automation method 时，标记为 `automation-associated`；该
标签不宣称已经重建 tactic 内部因果。缺失审计文件、非 GREEN audit 或 skip-proof
certificate 都阻止 theory build/publish。完整 Refine seal 使用同一模块聚合去重后的
session-level 数量。

**当前结果**：审计代码与测试已经存在，但 generated Refine 尚未完成，因此尚无生产
session 数据。若 refinement-shaped retained facts 存在但实际使用为零，最终报告必须
明确报告零；若被使用，则受影响 proofs 不能描述为完全不依赖 human-certified
semantic proof power。

**形成的规则**：保留 proof power 可以是实验边界的一部分，但必须由 Isabelle 证据
量化其实际影响，不能以“未向模型显式展示”为未使用的替代证据。

## 11. Gate 与状态账本复审

### 案例 36：pending disclosure 被旧 Environment Gate 放行

**阶段**：`d6c26eb4fae33cab` 环境与 Session Planning 复审。

**问题**：`context-effect-disclosure.json` 明确是 `pending`，旧 gate 却设置
`proof_generation_allowed=true`，因此 clean-build 和 Premise Catalog 自洽被误写成
完整环境边界 PASS。

**根因**：Disclosure 被当作 non-blocking future report，gate 没有引用其 artifact
identity、完成状态或 coverage。

**修复**：Semantic Base seal 与 disclosure 分步产生；Premise exporter 必须真实导出
facts、simp/wp、cong/weak-cong、classical intro/elim 和 named collections，并生成
`status=complete` 的 disclosure。Gate 绑定该 artifact，pending、hash 不一致或 coverage
缺失一律拒绝。Generated proof 的实际使用仍在 theory seal 前检查。

**形成的规则**：环境中的可用 proof power 必须在 proof generation 之前完成披露；
“以后统计实际使用”不能成为跳过静态 context surface 的理由。

### 案例 37：声明 automation coverage 不等于真实 Isabelle 导出

**阶段**：expanded Premise exporter 的第一次真实 smoke。

**问题**：Python schema 可以写 `coverage=true`，但最初 ML exporter 只导出
`simp/wp/corres`；扩展后第一次 Isabelle build 又因 simps 的 `(name * thm) list`
类型与 `thm list` 不一致失败。

**根因**：用文档字段替代 prover 端证据，并假设 Isabelle 内部 pool API 的元素类型。

**修复**：在 sealed heap 的隔离副本上真实编译执行 exporter。最终 smoke 导出
84,219 facts、13,497 simp rows、4,470 wp rows、116 weak-cong rows、89 named
collections，以及 classical intro/elim pools；raw TSV hashes进入 disclosure。

**形成的规则**：context-effect coverage 是 Isabelle 运行产物，不是 Python
validator 可以自行宣称的布尔值。

### 案例 38：seal 和 Catalog verifier 过度相信已有 metadata

**阶段**：环境 gate 独立性复审。

**问题**：旧 seal 只相信 manifest 的 `raw_target_refine_theory_count=0`；Catalog
database 有 13 列，verifier 却只比较 7 列。Manifest 或 provenance/publication state
漂移仍可能 PASS。

**根因**：生成器输出与 verifier 共用了缩减后的摘要，而没有从真实 runtime/database
重建全部受信状态。

**修复**：seal 独立扫描 `Runtime/l4v/proof/refine/`，重算 seal/gate ID，并核对 campaign、
base session/import；Catalog verifier 查询并比较全部 13 列。泄漏 theory、任一列漂移
和 runtime identity 变化均有负向回归测试。

**形成的规则**：seal 是独立复查边界，不是 manifest 的格式确认器。

### 案例 39：Planner ledger 与 runner files.json 形成双状态源

**阶段**：Session Planning 与 Proof Session Runner 交接复审。

**问题**：Planner 创建 canonical `session-progress-ledger.json`，runner 却不读取它，
另建 `Proof-Run/files.json`。Generation IDs、失败状态和 theory seal 无法回写到规范
artifact，也没有完整 serial traversal driver。

**根因**：runner 沿用了早期 file-level harness 生命周期，Session Planner 后加的
ledger 仅停留在输出层。

**修复**：runner `init` 将 run ID 绑定到 Planner ledger；`plan-file/run-file` 原子更新
同一 ledger；只有真实 theory seal 后才标记 `sealed` 并发布 premises。新增
`run-session` 按 immutable serial fallback 遍历，单 theory 失败后继续，最终写
`partial/complete`。所有旁路工具同步删除 `files.json/built` 语义。

**形成的规则**：一个 session 只能有一个权威状态账本；proof GREEN、theory sealed
和 premise published 必须按同一状态机推进。

## 12. 当前 harness 的总原则

1. 固定 contracts，生成 theorem graph 与 proof layer。
2. Isabelle给事实，deterministic validator定合法性，LLM只在合法选择中规划或生成。
3. 环境健康、规划质量、proof能力和assembly稳定性分别评价。
4. Premise legality先于relevance，actual dependency先于source-name统计。
5. Residual必须可定位、可恢复，并保留monotone verified frontier。
6. Graph变化只有Graph Revision Agent可以提出，并由deterministic certifier批准。
7. Node GREEN、theory GREEN、sealable和published是不同状态。
8. retained semantic proof power必须披露，并在生成后审计实际影响。
9. 生产任务由脚本和ledger控制，完整模型输入输出不可覆盖。
10. Partial campaign只能报告partial，不能进入Refine → CRefine生产交接。


### 案例 40：File Planner 提前看到 premises，且未认证图就标记 planned

**阶段**：`e7f7...` successor 前的单 Theory 结构规划接口审计。

**问题**：`planning_environment` 从 Premise Catalog 召回名称和 theorem statements，
虽然 prompt 要求不规划 external premise，但职责边界只靠文字约束。同时 `plan-file`
在 Isabelle statement-shell check 前就写入 `graph-v1` 并把 ledger 标记为 `planned`。

**根因**：早期 harness 希望借 premise records 帮助模型理解语义，混淆了 theorem graph
设计和 node-specific premise retrieval；statement certification 又被放在 `run-file` 的
开头，导致 `planned` 实际只表示 JSON/结构合法。

**修复**：File Planner 改为 fixed-contracts-only payload，Premise Catalog 只由 Lemma
Prover 按 node 和 residual 检索。每个候选 graph 先做 deterministic structure check，再在
Semantic Base 加已 sealed theories 的 context 中由 Isabelle 检查全部 statement shells；
只有 GREEN graph 才写入 canonical artifact 并更新 ledger。Graph 同时绑定 upstream
identities、planner transcript 和 payload hash。图节点数不设预先上限，规模作为评价结果。

**形成的规则**：`planned` 必须表示 certified theorem DAG，而不是“模型返回了一个可
解析 JSON”。File Planner 不得以任何形式看到或承诺 external premise；retrieval 只能
发生在 certified executable node 上。

### 案例 41：PTY 不再轮询时，活跃 runner 被误判为卡住

**阶段**：CNodeInv_R production generation 的长 logical rounds。

**问题**：runner、LLM 和 Isabelle 均持续输出 heartbeat，但事件只写到统一 exec
会话的 stdout。对话被中断后前端不再轮询 PTY，runner 仍在后台推进，几十分钟的事件
积在缓冲区，下一次 poll 才一次性显示。用户无法区分 provider 卡住、Isabelle 搜索、
后台正常推进和进程已经退出。

**根因**：`progress_events.emit_event()` 只有 `print(flush=True)`；flush 只能保证
写入 PTY，不能保证控制端继续读取。Canonical ledger 又只在业务状态转换时更新，
不适合作为秒级 liveness monitor。

**修复**：统一 event emitter 同时追加 `runner-events.ndjson` 并原子更新
`runner-status.json`；独立 daemon heartbeat 每 30 秒写入当前 active event/stage。
状态快照记录 PID、theory、node、round、最近 transition/heartbeat 和 terminal reason。
这些文件只用于观察和 watchdog，不参与 proof 恢复或 seal 判定。

**形成的规则**：长任务的可观察性不能依赖 conversation/PTY；业务 ledger、proof
checkpoint 和 runtime monitor 必须职责分离。

### 案例 42：不同 runner 可以覆盖同一 canonical ledger

**阶段**：Session Planner ledger 已成为唯一状态源后的并发复审。

**问题**：`plan-file`、独立 `run-file` 和 `run-session` 都会 read-modify-write 同一份
`session-progress-ledger.json`。即使两个进程处理不同 theory，它们仍可能从相同 revision
读出账本，并由后写者覆盖先写者的状态；同一 theory 并发时还会同时修改 graph、node
attempts 和 seal artifacts。

**根因**：原子 rename 只能防止半写文件，不能把一次 read-modify-write 变成跨进程事务；
只加 per-theory lock 也不能保护共享 ledger。

**修复**：新增 run-level non-blocking `flock`，锁文件记录 PID、operation、theory 和
acquisition time。`init`、`plan-file`、`run-file` 分别在完整 mutation 周期持锁；
`run-session` 在整个 serial traversal 期间持锁并调用内部 unlocked implementation，避免
嵌套死锁。锁粒度暂不允许不同 theory 并发，符合当前“同层不声明安全并行”的协议。

**形成的规则**：只要多个 artifact 共享一个 canonical ledger，正确的最小锁域就是整个
run mutation，而不是单个 theory 文件；原子文件替换和事务互斥是两个独立要求。

### 案例 43：RETRY_PROOF 可以重复同一搜索动作

**阶段**：Graph Revision Agent logical-round policy 复审。

**问题**：旧 validator 检查 failure class、residual evidence 和 budget，却允许 GRA 连续
返回相同的 retrieval query 或 repair guidance。这种 decision 形式合法但没有改变搜索，
仍会消耗下一轮最多五个 Isabelle candidates。

**根因**：`RETRY_PROOF` 被当作分类标签，没有表示为一个必须改变后续执行的结构化动作；
runner 也没有记录 guidance 被哪一轮实际消费。

**修复**：从 retrieval query、repair guidance、outline、frontier、automation policy 和
repair diagnosis 六类字段构造
normalized retry directive 与 SHA-256。RETRY 必须至少包含一个 material field，并且 hash
不能等于上一条已消费 directive。接受后的 decision 绑定 active graph/node/dependencies；
runner 在进入唯一的下一 proof round 前写 `gra-feedback-consumed.json`，重复轮次消费被拒绝。

**形成的规则**：retry 必须是可审计的搜索状态变化，不是“再试一次”的自由文本许可；
decision acceptance 和 execution consumption 必须分别留证。

### 案例 44：中断窗口丢失已完成的 RED attempt 和已接受的 GRA guidance

**阶段**：Node proof crash/restart 语义复审。

**问题**：runner 已能从 completed GRA request 恢复整轮 RED feedback，也能重启未完成的
GRA provider call，但在 `attempts.json` 已写而 theory progress 尚未更新、或 GRA RETRY
decision 已写而 retrieval hint 尚未持久化时，下一次运行仍会丢失最新证据或重复调用 GRA。

**根因**：恢复只覆盖 coarse checkpoint，没有把 checker attempt、GRA decision 和下一
logical round 建立精确事务关系。

**修复**：恢复最新 RED attempt 时要求 active graph payload、node statement、dependency
closure、candidate proof 与实际 checker source 全部匹配，并记录 source hash。恢复 GRA
feedback 时重新运行当前 validator，拒绝 graph/statement/dependency 漂移、无 material
directive 或已有 consumption marker 的 decision。同一 round 的旧 progress 与新 artifact
feedback 采用保信息合并，较新的 round 优先。

**形成的规则**：恢复不是扫描到文件就复用；每份恢复证据必须证明自己属于当前 graph
和执行阶段。已产生的 theorem evidence 可以恢复，陈旧或已消费的控制决策不能重放。

### 第一优先级实现的 campaign 继承边界

上述 runner、GRA、测试、架构文档和 campaign policy 都属于 frozen harness inputs。已有
run 的 campaign identity 不能在原地沿用这些改动；执行时必须创建 successor campaign，
重新冻结输入并由 environment gate 验证新 identity。旧 run 只保留为 predecessor evidence，
不得把新 lock、恢复或 RETRY 语义反写成旧 campaign 已具备的能力。

## 仓库规划与 File Planner 职责重叠（theory policy v3）

**发现**：实际 v2 方案有 287 个 contracts、245 条组内建议边，但没有跨 theory 边或辅助节点。
21 个生成文件沿用原 owner 分组。模型原始数组中 State Readers 排第 3，规范化后变成第 16；
Primitives/Operations 和 Heap/Entry 的先后也被名称排序颠倒。

**原因**：schema 让模型规划 contract/helper 节点边，theory 边只能从跨文件节点边推导；
与 File Planner 的局部依赖职责重叠。同时 ready theories 使用名称排序，模型数组没有明确排序语义。
这不证明模型原始顺序在数学上正确，也不能证明零 helper 必然错误。

**修改**：默认 policy v3 直接输出 theory 前驱及理由、contract 分配、分组理由、整体排序理由，
数组次序明确作为 ready theory 的优先级。只保留共享辅助结果意图，不输出局部节点 DAG。
允许有理由的零边/零辅助意图，不用数量配额制造关系。前驱优先于数组顺序；环和未知前驱拒绝。
plan/projection v3、file list v4 接入现有 Runner；v2 prompt/bundle 重放分支保留，旧产物不改写。

**验证状态**：新增 8 项 v3 测试通过，全套 353 项回归通过，覆盖稳定拓扑排序、非法输入、
投影消费、审计重放和 Runner 初始化。本条修改记录先于系统设计文档更新；测试通过后已同步
设计与接口文档。未重新调用模型生成新方案，旧环境冻结 policy 和既有规划产物不改写。

既有 `live-environment-002/Session-Planning` 的 v2 bundle 经 `load_file_projection` 完整审计读取通过
（`Generated_Arch_Access`）；未转换或改写旧产物。`git diff --check` 通过。

## 规划政策升级不应触发 Semantic Base 重建

旧实现只接受环境 manifest 内的规划政策，无法在复用已验收 heap 的同时升级规划器。
新增 `planning_freeze.py`：独立规划快照绑定既有环境 manifest/gate/dataflow、政策、预算及代码。
`--freeze-planning` 显式创建新输出；已有环境和旧规划不修改。新生成检查 live 代码，
下游读取校验快照字节和原环境绑定；代码改变要求新规划输出。恢复默认读取该规划自己的政策。
`--prepare-only` 只保存可审阅输入，不调用模型、不生成已接受方案。

实现测试先行，验证完成后同步接口文档。实际模型调用被自动审批拒绝，不能把准备完成表述为规划完成。

本次独立冻结改造全套 356 项测试通过；测试完成后已同步设计和规划接口文档。

真实输入已在 `reports/regen/repository-planning-v3-001` 准备完成：287 contracts、31 条合法
premises，规划 freeze 与 live 代码复核通过；没有重建环境或调用模型。审阅产物为
`planner-input.json`、`prompt-preview.txt`、`readiness-review.json`。

## 2026-09-09：真实 theory-level 仓库规划完成

用户明确确认模型调用授权后，继续 `repository-planning-v3-001` 的冻结输入。gpt-5.6-sol
单次调用约 538 秒，首轮 JSON 结构通过，审计确认无工具调用。没有重建环境或修改旧 v2 产物。

新方案 `repository-plan:2f71aca32c9b333c7f8b`：287 固定 contracts、18 theories、18 辅助意图、
48 条 theory 建议边。辅助意图关联 96 个不同固定目标；三个生成 theories 合并多个原 owner。
全部固定 statement/context/attributes 保持一致。26 条边仅对排序可达关系冗余，保留其直接
辅助理由，仅在审阅图中作传递约简，不改写模型方案或新增运行图。

完整 bundle 经真实环境审计重放通过，18 份投影全部通过现有 Runner 适配与 File Planner
输入构造。没有执行 File Planner 模型或 Isabelle，未宣称 helper 已成立或证明生成完成。
报告：`reports/regen/repository-planning-v3-001/QUALITY-REVIEW.md`、`quality-review.json`、
`handoff-audit.json`。上一轮 356 项测试通过；本轮未修改实现代码，`git diff --check` 通过。

结论：接受为可修订的初始仓库规划，不为增加 helper/theory 数量而重跑。精确辅助命题和
局部 DAG 的有效性留给下一模块真实检查，通用组合规则的进一步提取与并行优化按实际反馈决定。

## 2026-09-09：启动首个真实 File Planner

按 v3 仓库顺序，在独立 `projection-run-v3-001` 上处理 `Kernel_Object_Access`。复用已验收
heap；7 个固定 contracts，仓库层没有预设辅助意图。首轮生成 2 个辅助节点，Isabelle 因
`updateObject_default` 参数类型不匹配拒绝；诊断自动反馈，失败源码和日志完整保留。
第二轮提出 2 个 helper，命题检查通过并以 initial commit 接受 9 节点图，状态为 planned。
未生成任何 proof，其他 17 个 theories 尚未规划，未启动完整 session 证明。

质量观察：第二轮两个 helper 主要重组原目标的假设，并未明显拆开核心语义证明；当前结构和
命题检查不能认证分解价值，不将 2 个 helper 的数量当作规划质量结论。后续应结合实际证明
反馈评估这类组合包装。此轮仅执行既有实现，没有修改代码。

产物：`reports/regen/projection-run-v3-001/theories/Kernel_Object_Access/graph-v1.json`、
`file-planner-result.json`、`initial-check-001.json`、`initial-check-002.json` 和 `acceptance/commits/`。

## 2026-09-09：Kernel_Object_Access 证明执行与预算衔接阻点

在 `projection-run-v3-001` 上运行真实 whole-proof backend：接受 `getCurThread_corres`、
`getIdleThread_corres` 和原有两个 helper，共 4 个 GREEN；整个 theory 尚未证明或 seal。
`getEndpoint_corres` 累计 5 轮、25 个候选（24 次证明失败、1 次超时），局部修复均未成功。
GRA 前四次 RETRY_PROOF，第五次 ADD `regen_lookupAround2_exact_hit`，新图 v2 命题检查通过。

发现当前调度与预算的不匹配：图修订保留固定节点的累计 rounds，但耗尽 5 轮后才新增依赖，
即使该 helper 成功，固定目标也无法再进入 prove_node。为避免继续消耗无法完成固定目标的
本次运行，在新 helper 第三个候选检查期间 SIGINT 停止；前两个候选未成功。已有 4 个 GREEN
和全部失败成本保留，提交链重放通过，无遗留本次 checker 容器。最后提交状态仍为 generating，
实际进程退出 130，单独记录执行停止原因，不声称自动 terminal RED。

本轮未改代码、冻结政策或证明预算。详见
`reports/regen/projection-run-v3-001/theories/Kernel_Object_Access/PROOF-RUN-REPORT.md`。
后续应明确晚期图修订如何获得合法剩余预算，累计历史不能清零；新增 helper 也应更早响应
已暴露的语义缺口。此处记录实测问题，不将未修正行为写成已实现能力。


## 2026-09-09：重复诊断、前缀重放与改图后预算断点

**现场**：`projection-run-v3-001` 中 `getEndpoint_corres` 的五轮跨度
97.96 分钟，其中候选生成 17.77 分钟、salvage 23.93 分钟、Isabelle
45.05 分钟、GRA 3.97 分钟，其余时间尚未细分归因。GRA 到第五轮才新增
精确查找 helper，原 contract 却已经耗尽证明轮数。

**原因**：每次 RED 都请求最多五份候选的修复提纲，轮末紧接着再由 GRA
诊断；每个候选都重放所有可见 GREEN 声明；改图保留成本却没有为新事实
到达后的原 contract 留下有限的尝试机会。

**本批修复**：`regen/execution/retry_policy.py` 保守识别重复残余，提前移交
GRA；`lemma_prover.py` 跳过轮末和简单语法/未定义事实错误的 LLM salvage；
`proof_outline_salvage.py` 缩减到最近两个候选；`repository_execution.py`
冻结 `residual-context-v1` 策略，只有新增 required dependency 真正 GREEN
后才允许最多一次补充轮，并在提交重放时核验上下文和累计计数。
`graph_revision_agent.py` 接收真实轮数上限与补充预算信息。

`isabelle_node_checker.py` 对同一冻结环境、相同源码和命令、仍完整匹配的
source/log 字节提供一次性 GREEN 验收证据复用，并输出每个声明块的计时。
图验证、audit、完整 theory 检查和 build 继续执行真实验证。

**实测**：仅导入冻结 Semantic Base 为 12.525 秒；完整四节点前缀为
87.274 秒；加入计时后为 84.910 秒，立即重复验收成功复用证据。主要成本
在声明块重放，尚未将 context setup 与 tactic 分开测量。未实现跨候选
的持久 Isabelle 状态或 prefix heap，也不声称 distinct RED 检查已经加速。

**验证与边界**：276 项当前测试通过；最后计时变更后的 157 项相关测试通过。
另有真实 Isabelle 实验验证计时、证据复用，以及已有 helper 候选加入
`Let_def` 后通过。该修复是开发者辅助实验，不算原 campaign 的自主 GREEN；
旧运行配置、提交与 proof graph 未改写。完整实验记录见
`reports/regen/checker-cost-probe-001/REPORT.md`。

### 显式续跑迁移

`projection-run-v3-002` 通过 `repository_execution.fork_continuation` 从 v3-001
继续 `Kernel_Object_Access`。新运行冻结修改后的实现；其首个提交嵌入原运行
完整提交链，逐项核验哈希、父提交、旧绑定、图及 GREEN 证据；证明环境必须
一致。保留累计 rounds/revisions，按旧 attempt 事务重建已尝试依赖上下文。
旧目录保持不变，隔离实验的人工 helper 修复不导入。新运行可使用已授权的
`residual-context-v1` 有限补充预算。迁移与调度相关 17 项测试通过，包含历史
篡改、预算归零及环境变更的拒绝检查。运行结论以续跑目录的提交和报告为准。

**续跑实测结论**：v3-002 的模型/check 活动跨度 28 分 35 秒，10 个候选中
新增 1 个 GREEN helper；其验收复用了完全相同的检查证据。原 contract 成功
进入累计第六轮，但五个候选仍未解决 unless/when 的局部状态保持义务，GRA
以 `RESOURCE_BUDGET_EXHAUSTED` 自动终止。新旧提交链重放通过，未 seal theory。
本次保守重复残余检测没有提前移交；文字形态或检索集变化仍会掩盖语义重复。
ML 起止标记仅用于观察：Isabelle 可延迟检查证明，END 标记不等于证明通过，
也不能把该区间直接解读为单独 tactic 的 CPU 时间。

## 2026-09-09：显式模型配置与 GPT-5.5 / GPT-6 Astra 对照

新增 `regen/config/proof-models.json`，使用本地已有 Codex 0.153.0，冻结绝对路径和 SHA256。
旧 relay CLI 0.144.6 调用 GPT-6 会被服务端拒绝；未升级安装软件或修改全局配置。
`audited_llm.py` 校验默认二进制哈希并记录实际 CLI/effort；`llm_transcript.py` 传递显式 effort。
新 projection run 冻结角色模型及配置哈希，Runner 默认从运行绑定读取 Lemma Prover。
新默认为 `gpt-6-astra medium`；File Planner/GRA 仍为 gpt-5.6-sol；旧运行绑定未改写。

新增 `regen/tools/compare_proof_models.py`，在独立输出中使用相同题目输入、medium、单候选和
真实 Isabelle 配对比较。三题六次检查：GPT-6 通过独立 unless/when 状态保持小题；两者的
lookup helper 候选失败；两者的 getEndpoint_corres continuation 都遇到 helper `OF` 的
multiple unifiers。完整目标尚未通过，不能将小样本当作普遍成功率或 campaign GREEN。
本轮没有增加证明轮数或执行完整 theory 发布。

产物：`reports/regen/model-comparison-001/REPORT.md`、`manifest.json`、`results.json`、
各候选 transcript/source/log。配对 prompt、实际 medium、CLI SHA 和 source/log 哈希已核验。
当前完整测试 281 项通过，包括新配置审计、二进制漂移拒绝和运行角色绑定测试。

## 2026-09-09：修复跨节点指令和目标定义召回

输入复核发现：新增查找 helper 继承了 getEndpoint_corres 的旧 GRA RETRY_PROOF，
32 条检索记录未包含合法库已有的两个查找定义；无关长声明占据主要输入。

repository_execution 将指导绑定到 node_id、statement/context，只传递 retrieval_query
和 repair_guidance；旧无绑定指令不再作为当前节点提示。premise_retrieval 增加目标
标识符对应定义及一跳定义展开检索（最多 12 条），优先于一般检索；声明总量
上限 20,000 字符、单条上限 5,000，不截断 Isabelle 命题。该检索仍为词法近似，
并不保证完整语义依赖闭包。lemma_prover 输出目标 context 和 checker 基础上下文。

284 项当前测试通过，覆盖跨节点/命题/上下文隔离、旧指令拒绝、定义召回、长
声明限制和 prompt context。真实 GPT-6 medium 诊断位于
reports/regen/input-repair-gpt6-001；helper 从四个既有 GREEN 前缀重新生成，成功后
再向固定 contract 提供新生成的 helper；每目标最多三个候选，未重置原 campaign
预算或写入其验收链。最终结果见该目录报告。

**实测结果**：GPT-6 medium 首次生成查找 helper 通过（模型 16.82 秒，检查 105.36 秒）。
getEndpoint_corres 三候选仍 RED，第三候选已解决对象提取和 OF 实例化，剩余 unless/when
状态保持及对齐/区间检查义务。该运行只做独立诊断，没有写入原 campaign GREEN，未发布
theory。完整结果、输入、耗时及证据限制见 input-repair-gpt6-001/REPORT.md。

## 2026-09-09：默认直接修复与固定前缀检查

取消默认独立 salvage 模型调用；显式 --salvage-provider 仍可启用。新增 persistent_checker
Python/ML worker，在同一已验证前缀 Toplevel 状态上隔离执行候选；前缀变化重建。
GREEN 仍由原冷 checker 独立复核，最终 audit/build 不变。新执行绑定冻结 ML 文件与
checker_policy；旧运行配置不改写。请求/响应及命令计时独立保存，故障退回冷 checker。

真实三个 contract RED 候选回放：378.622 → 159.898 秒，减少 57.8%，含初始化。
另经 RED/GREEN/失败事实隔离/再次 GREEN 验证；286 项测试通过。未重跑完整模型
campaign，不将 checker 加速当作证明成功率提升。详见
reports/regen/persistent-checker-benchmark-001/REPORT.md。

## 2026-09-09：正式执行设计与单节点失败归因

将直接反馈、固定前缀复用、冷 GREEN 复核及冻结/费用边界写入正式设计和 README。
复核 getEndpoint_corres：输入未覆盖残余所需定义，并重复 best/latest 候选；
本地给原 auto 添加六条合法规则后，七个子目标降为三个，仍缺 next 距离下界。
这属于开发者辅助诊断，不作为自主 GREEN。三候选持续推进，预算是否充分尚无
公平对照；stepwise 需先修复运行依赖绑定并同步输入。详见
reports/regen/endpoint-local-diagnosis-001/ASSESSMENT.md。

## 2026-09-09：GPT-6 输入对照执行

用户明确授权后执行预先准备的两组输入；除 premises 外字段相同，32/42 条前提，
各一个 gpt-6-astra medium 候选。原检索组因 objBits_simps 不存在失败；补充组
构造局部 magnitude 证明，但 Min 成员关系未完成。不能将其称为 theorem 仅剩
一个目标，后续证明段未验收；无 campaign GREEN。证据见
reports/regen/endpoint-input-comparison-001/REPORT.md。


### 2026-09-09 — Kernel_Object_Access incremental experiment

An isolated fixed-contract experiment now records candidate requests, full checker evidence, and immutable acceptance snapshots in `reports/regen/kernel-object-access-lab-001`. The driver is `regen/tools/kernel_object_access_lab.py`; observations and limits are in `regen/docs/experiments/KERNEL-OBJECT-ACCESS-LAB.md`. This is developer/LLM collaborative generation, not an autonomous campaign success claim. Original seven contract statements and contexts remain fixed; new helper nodes enter the accepted graph only together with a successful independent cold proof check.

The persistent worker now wraps candidate commands in an ML timeout (60 seconds by default) and records timing even on failure. Real request 020 returned `RED: Timeout after 60.206s` without redundant cold replay; the next task continued. Infrastructure failures retain cold fallback, and speculative GREEN still requires cold certification. The experiment separates this command limit from its enlarged 900-second prefix/cold-check process budget; it does not rewrite an old campaign's frozen configuration.

Completed: all seven fixed contracts and eight helpers passed independent cold checks. Exact-name `Kernel_Object_Access` whole-theory check passed in 185.941 seconds, and its dependency audit passed in 208.203 seconds; all 15 audited nodes have `has_skip_proof=false`. The exported theory, ROOT, immutable manifest and evidence are in `reports/regen/kernel-object-access-final-001`. This reuses the existing Semantic_Base heap; it does not claim an independent session heap build or an autonomous production Runner success.

The final experiment records 38 candidates (28 RED, 10 GREEN, plus five inherited GREEN nodes), 6094.156 seconds of recorded checker execution, and 291 passing current tests. `proof_lab_journal.py` replays accepted states independently of a missing/stale pointer and rejects forks and proof/interface changes. `finalize_kernel_object_access_lab.py` validates the replayed state, cold-checks the exact exported name, audits dependencies and writes the final artifacts. The checker audit API now accepts an optional explicit theory name; existing callers keep their previous behavior. Detailed costs, semantic decomposition, failed tactics and remaining prefix overhead are in the experiment document.


### 2026-09-09 — 纠正 Kernel_Object_Access 成功口径

用户明确限定外层 Codex 只能修复结构 bug 或优化通用流程。此前实验由外层 Codex 根据错误编写具体 helper/proof 并挑选重试，因而只是交互式诊断，不能作为无干预生成成功。完整流程及介入位置已补入 KERNEL-OBJECT-ACCESS-LAB.md。后续无干预运行从合法仓库投影重新规划，不继承诊断证明、helper 或 GREEN；结果须通过生产 Runner 的完整验收。


### 2026-09-09 — 无干预运行 001 的模型空候选崩溃

生产 Runner 自动规划得到 7 contracts 与 3 辅助节点，两个 helper 冷 GREEN；第三辅助节点两次 RED 后模型返回 proof_body=null，lemma_prover 在 strip 处崩溃，未调用 GRA，固定合同 0/7。旧 run 保留为失败，progress 的 generating 仅是最后提交状态，不代表进程存活。

通用修复：校验 JSON/proof_body 类型及非空，缺失、null、错误类型、空白或无效 JSON 记录为 model_output RED（明确未调用 Isabelle），保留模型解释和已有 checker 反馈，交回 GRA。不得将模型自报缺少前提视为验证事实。没有修改任何具体证明、helper 或图。执行代码改变后另建全新无干预 run。

此修复后完整 292 项当前测试通过，包含五种无效候选输入的无 checker/GRA handoff 回归覆盖。


### 2026-09-09 — 无干预执行的残余、历史与检索接口修复

新增 execution/proof_feedback.py 统一 cold/warm 语义 goal 解析，停滞检测复用此解析。repository Runner 从已验证 acceptance commit 获取 GRA 历史，并提供 phase/proof_round；历史残余按完整 node_id 读取。Prover 兼容 attempts/proof_candidates，保留有界 checked_history 与最佳已检查候选；模型空输出单独记录，不覆盖前轮真实 checker 反馈。

精确定义召回扩展到模型检索请求和实际残余，与目标定义交错分配记录，过滤 Isabelle 符号转义，输出检索选择审计。失败报告向 GRA 提供完整最终事实清单，取消前 16 条截断；明确缺少召回不等于存在一个可直接应用的框架桥接定理。没有加入目标特定 helper、证明或人工修复意见。旧 autonomous-002 状态与预算不修改，新运行重新冻结执行代码并从零规划。


### 2026-09-09 — 有界模型输入与按需观察

新增 execution/proof_input.py：Prover/GRA 共享诊断引用和候选去重；大诊断保留哈希、长度和明确不完整的原文片段，完整证据不变。payload 上限 120000 字符，自适应收缩诊断展示，不截断固定命题/上下文/选中前提，仍超限显式拒绝。Prover 可返回 observation_request，请求当前节点已有诊断的有界分页，或由 Runner 在合法 catalog 检索前提；每次请求消耗原候选次数，不开放文件访问或历史人类证明。GRA 暂不执行 observation_request。

provider 调用前检查服务字符界限；input_too_large 不再被 Prover 原样重试五次。原 1297195 字符 prompt 回放为 108975 字符 payload；三份 GRA 样本也通过预算。301 项测试通过。证据见 reports/regen/input-projection-repair-001。没有人工提供具体 helper 或 proof。


### 2026-09-10 — 拒绝 SPLIT 后撤销的验收类型

autonomous-004 验证了真实按需前提召回且未再发生 input_too_large，两个 helper 冷 GREEN。模型 SPLIT 候选因未知常量被拒绝；后续合法撤销却复用了 SPLIT decision，验收误报 parent 丢失而崩溃。修复后撤销记录自己的 DELETE 操作，并另存 preceding_gra_decision；SPLIT parent 不变检查仅适用于 revision。固定合同与 GREEN 不变约束保持。旧运行保留失败证据，未手工补证明。


### 2026-09-10 — 单次完整证明输入限制为 10,000 字符

取代先前 120000 字符 payload 方案：Prover/GRA 改为按角色构造输入，预算包括完整显式 prompt。移除整包历史结果、checker 元数据和重复 premises；基线正文只保留一份，其他尝试为摘要。事实按需检索、去重并整条选入，诊断页改为 1200 字符。固定命题/context 不截断，超限在模型调用前拒绝。

GRA 只展示失败连通分量，完整 statement/context 与边保留，重复内容用引用表示；Runner 还原 ID/context，合并未修改节点，并验证分量外节点不变。仍使用原单图、完整校验和事务，未引入新图权威。真实历史 Isabelle 失败不能被后来的模型输出错误遮盖。

增加投影前 raw 输入及 selection 审计（长度、哈希、可见事实、省略统计）；重复写入分配新文件名。whole-proof/GRA/可选 stepwise/salvage 发送边界统一拒绝超过 10000 字符的证明请求。后两类旧入口尚未重写专用投影，超限时拒绝发送。

41 个 Prover 与 7 个 GRA 历史请求回放全部达标，最大完整 prompt 9900 字符；旧 1297195 字符样本现为 9883 字符。309 项测试通过。没有新的真实证明运行，不能据此声称成功率提高。详情：`reports/regen/input-projection-10k-001/REPORT.md`。本轮没有人工编写目标证明或 helper。


### 2026-09-10 — Prover 改为目标、近期尝试与主动事实召回

根据进一步澄清，撤销上一版 Prover 的10000字符硬限制及按剩余字符填充输入。默认仅传完整目标/context与最近三次尝试和反馈；完整 proof 不再因2200字符阈值省略。移除自动 premise 预加载、策略/指导/提纲、整包 GREEN 事实。大日志保留可分页原始证据。

新增 execution/premise_recall.py；模型主动请求 premise_query 和 scope/offset，Runner 从冻结上游 catalog、已发布带 seal 绑定的记录、同 theory 已提交 GREEN prefix 召回，按5条完整记录分页。不返 proof body，不接触已删除证明，不将图内未证明节点当事实。请求仍计入原尝试预算。完整输入和查询审计保留，长度只作为统计。

41 份历史请求投影最小1141、中位5989、最大10784字符，不再趋近统一预算；312项测试通过。报告：reports/regen/minimal-prover-input-001/REPORT.md。本轮没有启动真实证明实验；GRA/stepwise/salvage 原有预算暂不改变。


### 2026-09-10 — GRA 最小输入与新一轮授权运行

GRA 默认仅观察失败邻域及相关 contracts、最近三次完整尝试、上次决策/拒绝反馈、权限和停止规则。取消自动 guard-premise 预载及字符装箱。加入合法 premise、任意当前图节点和诊断页查询，最多7次观察并共享原决策超时；每次查询和调用审计独立保存。只有最终合法决策进入完整图/事务验收。

移除输入连通分量之外禁止修改的新增限制；原 statement-certification 范围规则和 contract/GREEN 不变约束保留。314项测试通过。报告：reports/regen/minimal-gra-input-001/REPORT.md。用户明确授权用新Prover/GRA启动独立运行；沿用冻结Semantic Base、上游heap与既有仓库规划，新建autonomous-006，不继承旧证明图、helper或proof。


### 2026-09-10 — 查询预算与完整召回工作集修复

autonomous-006因25次查询、0个proof候选耗尽5轮而失败。修复查询与proof候选共用次数、最后查询误报空proof、独立模型调用只保留最后一页、旧Runner跨轮压缩遗漏观察字段等问题。

Prover默认每轮8次独立查询，输入明确剩余查询/证明次数，查询耗尽后仍可提交proof；继续查询时记录独立交互错误，GRA不再当作空proof。当前轮完整proof/反馈、已请求的完整事实和观察页持续可见；跨轮保留修复基线与工作集，较早轮次用摘要。本地与sealed缓存恢复时重验合法性。GRA自身查询同样不再丢弃旧页。

319项测试通过。报告及合成集成输入：reports/regen/recall-context-repair-001/。本轮没有新真实证明运行或人工目标proof。


### 2026-09-10 — Repository Runner 向 GRA 传递冻结 premise catalog

autonomous-007 在两个固定 contracts 和一个辅助节点已提交 GREEN 后，因 GRA 首次 premise_query 抛出 `GRA premise catalog unavailable` 而退出。根因是 repository_execution.revise_or_revoke 漏传 premise_database，GRA 使用默认 None；不是冻结 catalog 丢失，也不是 Isabelle 判定该 contract 不可证。

修复调用边界，向 GRA 传递与 Prover 相同的 run 冻结 premise_database。增强现有 Runner 撤销/恢复集成测试，在 GRA 边界验证收到正确目录并实际执行上游召回；修复前测试复现 None 传参故障，修复后 repository_execution 的 7 项测试通过。没有改变模型生成的证明、命题或图，也没有改写 autonomous-007 的失败记录。本次验证不包含新的真实 Isabelle 实验。


### 2026-09-10 — GRA 历史残余自匹配导致提前停止

autonomous-008 的 getEndpoint_corres 在第 3/5 轮被禁止重试。审计发现当前指纹集合包含 checked_history，继承的第 2 轮目标与自身历史匹配；第 3 轮实际候选与用于停止的旧目标没有交集。修复为仅以当前 attempts 构造当前集合，历史仍作为修复证据保留。真实跨轮重复与轮次上限约束不变。

新增合成回归验证新目标和空轮次不误判，真实重复及预算耗尽仍限制重试。158 项相关测试通过；对真实 decision-007 离线重放恢复 retry_proof_allowed=true。未人工提供目标证明，也未启动新的真实生成。详细分析：reports/regen/kernel-object-access-autonomous-008/GETENDPOINT-ANALYSIS.md，包含召回噪声、输入增长、十次候选失败及内部命令超时。


### 2026-09-10 — 主动召回区分精确名称、定义与词法搜索

getEndpoint_corres 审计发现整段标识符被拆为通用词，模糊结果填满精确查询页；所有结果持续进入工作集，最终 prompt 超过五万字符。共享 premise_recall 新增 auto/exact/definition/search，精确页不补模糊事实，定义核对左侧常量，批量 _def 名称逐项查询；词法搜索要求完整标识符覆盖并优先名称匹配。取消评分前字母序512条截断，使用只读 SQL，Prover/GRA 共用新模式。未加入目标专用证明提示或字符截断。

324 项测试通过；15 个真实旧查询回放的去重事实从56条降到6条，另有合法定义正向探针验证。中位召回耗时264ms。空结果不证明事实不存在，回放结果不等于证明成功率提升；已有运行工作集的噪声尚未清除。报告及复现脚本：reports/regen/premise-recall-precision-001/。


### 2026-09-10 — Statement拒绝进入GRA修正循环；每轮冻结一次事实选择

autonomous-009的SPLIT生成非法Isabelle转义，原有statement checker已拒绝，但Runner没有继续修正。现在候选RED后提交拒绝反馈与已耗成本，保留原活动图，自动将最新完整候选及诊断返回GRA；通过后才提交revision。纠正计入既有revision预算，耗尽明确停止；中断后先恢复纠正，不另耗proof轮次。没有人工修改目标helper命题。

Prover/GRA分别每轮选择一批fact（至多4查询、12条完整记录），选定后冻结，下一轮重新选择；GRA不继承Prover或旧GRA事实。数据库和完整审计保留，输入仅含本轮所选事实及最新完整proof，旧尝试/轮次保留方法和问题摘要。GRA sealed召回同时改为当前theory冻结的snapshot。

332项测试通过。旧第五轮输入离线投影：Prover 101363→18611字符、96→9条fact；GRA首次输入112370→15871字符。仍未满足所有输入小于一万字符，没有截断最新proof/context；未进行新的真实证明生成，不能据此声称成功率提升。报告：reports/regen/statement-repair-round-recall-001/REPORT.md。此政策取代此前跨轮恢复完整召回工作集的行为。


### 2026-09-10 — 非法请求原轮纠正、图拒绝归GRA、合法符号消歧

历史审计后修正两类剩余控制分支：非法观察请求不再直接终止proof round；Python图/声明接口拒绝不再伪装成proof失败，和Isabelle声明拒绝一样由GRA有界纠正，保留活动图、GREEN和累计成本。固定contract停止记录具体决策和预算。

召回搜索保留大小写/下划线，并增加按需合法Isabelle符号解析，返回完整限定名、展开式及类型，明确不是新定理。真实probe区分getObject/get_object，并展开ep_at'，未知标识符未被当作常量；一轮一批的事实政策不变。339项current测试通过；真实检查及实现记录在reports/regen/runtime-repair-001。准备从autonomous-009保留2个contract GREEN、1个helper GREEN及全部累计预算开始autonomous-010；无人工目标proof或helper输入。


## 运行保障补充：工具会话中断与反馈续接（2026-09-10）

autonomous-010 在新对话开始附近突然失去 runner 进程，未生成退出记录；会话回收是最可能原因，具体信号仍未证实。内部 finally/心跳不足以覆盖外部强制终止，且遗留了 prefix 容器。

新增 `regen/tools/supervised_proof_run.py`，由独立 systemd 用户服务托管：外部监护记录 PID/启动标识、退出码/信号与心跳失效，修正陈旧运行状态并清理本run挂载的checker容器。该元数据不代替证明接受事务。SIGKILL监护本身仍须通过systemd退出状态诊断，不承诺进程内代码能处理SIGKILL。

Repository Runner 同时接入已有 `recover_latest_attempt_feedback` 与 `select_resume_feedback`，恢复checker源码绑定的中断轮反馈；不接受未计费未来轮目录，也不退款。autonomous-011 继承010的2个contract和2个helper，恢复中断第1轮的3次尝试，继续第2轮。343项测试通过，独立用户服务smoke退出0。实际续跑记录见 `reports/regen/kernel-object-access-autonomous-011/RESUME.md`。
