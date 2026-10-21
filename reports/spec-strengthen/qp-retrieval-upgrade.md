# Q/P 检索与执行优化 — redirect 分类学、差分信号、闭环修复

> **背景**:execute_additive 框架落地后,F-slot 已有成熟 applied 案例,但 Q/P
> 为零。直接原因暴露在第一批 live run 里:`--slot Q` 跑在 frame-lemma 文件上,
> agent 找不到 Q 就**擅自退回 F 提议**——纯 LLM 扫描既无方向信号、也无纪律约束;
> trial 失败后一次性丢弃,无错误反馈。本轮按 design §6.3 的分工("detector 给
> 方向,LLM 给 statement,trial 给真值")补齐了机械检测层与修复闭环,并以此产出
> Q/P 案例。

---

## 1. Q 检索:redirect 分类学(本轮核心发现)

Q-slot 的机械信号是 redirect-shape:proof 路过更强 post 再主动弱化。全 AInvs
扫描(75 处 `hoare_strengthen_post*`/`hoare_post_imp`)后发现 **redirect 不是
单一形态,四类的价值完全不同**:

| 形态 | 判别 | 含义 | 价值 |
|---|---|---|---|
| **named-redirect** | `strengthen_post, rule <名字>` / `[OF <名字>]` | 强版本**已有名字**,槽已填 | 低 — 只剩 0054 式 family alias(planned/orphan 风险) |
| **leading inline-redirect** | redirect 是第 1-2 个 tactic,无名字 | 描述本 lemma 自己的 post | 须防**形式适配**伪强化(见下) |
| **interior redirect** | redirect 在证明中段 | 是 **wp 顺序组合的标注 idiom**(给程序前缀标 post),不是本 lemma 的强化空槽 | 不是 Q 候选(但指向内层 op 缺 helper) |
| **exactness** | post 只承诺 `rv ≤ e` | 计算若只能产出特定值,精确 post 严格更强 | **真矿** |

**形式适配伪强化**(静态判不掉,必须 agent 语义判断):
- `cte_wp_at ((=) NullCap)` vs `cte_wp_at (λc. c = NullCap)` — 逻辑同一,
  redirect 只为让 wp 规则 unify;
- `∃ct. ct = cur_thread s ∧ P ct` vs `P (cur_thread s)` — one-point rule
  等价,redirect 只为 `hoare_vcg_ex_lift` 过 binder。

Syscall_AI 仅有的两个带显式 `rule_tac Q=` 文本的 leading redirect 均属此类。

**结论**:AInvs 顶层"proof 路过严格更强 post 又丢弃"的教科书 Q 槽**稀少**;
0054(lsfco)本质也是 named-redirect 的 alias。可执行的 Q 真矿是 **exactness**
(`compute_free_index_wp` 的 `rv ≤ idx`,计算实际只返回 `0` 或 `idx`)。

## 2. P 检索:三条精度规则(从首轮误报学来)

P 信号 = 前提 conjunct 在 proof body 零出现。首轮扫描误报严重,修出三条规则:

1. **前缀匹配而非词边界**:`simp: valid_objs_def` 消费了 `valid_objs`,但
   `\bvalid_objs\b` 匹配不到(`_` 是 word char)→ 用 `\b<head>[\w']*`;
   该规则消除了 `set_object_valid_objs` 这类典型误报。
2. **差分证据**:仅当**其它 conjunct 可见被消费**时才算 high(证明该 proof
   会显式展示依赖,这个前提的缺席才有意义)——0023 正例正是此形态。
3. **排除 opaque 证明**:`by wpsimp` 一行流的消费完全不可见,hint 无意义,
   直接丢弃。

剩余的不可静态判定项(simp 规则前提隐式消费,如 `ntfn_queued_st_tcb_at`
需要 `sym_refs` 作为 hypothesis 从上下文匹配)交给 agent 语义判断 + trial。

## 3. 管线升级(三件)

1. **[0.5] hints 层**:`spec_slot_hints.py` 按上述分类学产 `hints.json`,
   注入 agent prompt(每条带 kind 专属行动指导 + 伪强化警告)。
2. **slot 硬执行**:`--slot Q` 只产 Q 或空;off-slot 提议被驱动器丢弃并记录
   ——"Q run 的结果"重新可以作为关于 Q 的证据来读。
3. **trial 闭环修复**:失败时 prover 错误 + 被拒 proposal + anchor ±40 行
   源码窗口回流 agent(`--repair`),agent 产修正提议(锚点错/证明不闭),
   或 `[]`(判定强化语义不成立,如前提确实 load-bearing)。至多
   `REPAIR_TRIES` 轮,全程留痕(`proposal-0.json`/`repair-N.json`/
   `trial-error.txt`)。trial 仍是唯一真值。

## 4. 案例结果

### Q 成熟案例 ✅ — `compute_free_index_wp_exact`(Untyped_AI)

**全链路**:exactness hint(机械)→ agent 起草 → named-planned gate →
trial OK(66740ms,Δ0.2%)→ impact `additive`/PASS → **APPLIED**。

```isabelle
(* 原 lemma 不动: compute_free_index_wp 的 post 是 rv ≤ idx *)
lemma compute_free_index_wp_exact:          (* L170, 新增 *)
  "\<lbrace>\<top>\<rbrace> const_on_failure idx
   (doE y \<leftarrow> ensure_no_children slot; returnOk (0::nat) odE)
   \<lbrace>\<lambda>rv s. rv = 0 \<or> rv = idx\<rbrace>"
```

强化论证:`rv = 0 ∨ rv = idx ⟹ rv ≤ idx`(nat),且 `≤` 允许中间值而精确
post 钉死取值集——严格更强。**这是框架第一个非 alias 的 Q applied 案例**,
检索路径完全按 §6.3 分工走通(detector 方向 → agent statement → trial 真值)。

agent 同轮自发提的第二个 Q(`get_cap_gets_strong`,存在式 post 钉死到
`cte_wp_at ((=) rv) ptr`)触发了 repair 闭环的首次真实运转(见 §5)。

### repair 闭环首次实战

候选 2 trial 失败(`Outer syntax error ... proposition expected`)→ 驱动器把
prover 错误 + 被拒 proposal + 源码窗口回流 agent → agent 把锚点 L239 修到
L250 → 重建补丁重试。**根因其实是管线 bug**(见下),repair 闭环semantically
补偿了它——这同时证明闭环有效、也暴露闭环不能替代根因修复。

### 发现的管线 bug:apply 后 stale anchor

`--apply` 模式下,候选 1 落地使文件插入 ~10 行,候选 2 的 `anchor_line`
仍按**原文件**计算 → 补丁插进语句中间 → malformed。修复:scan 时(文件尚未
变)为每个候选记录 anchor 行**文本**,build 补丁前若当前行文本不匹配,按
文本就近重定位锚点。

### 运维发现:heap 易变性 + 共存实验的相互作用

P 案例连续三次"卡死在 baseline",诊断出一条三因素链:

1. **容器里有一个孤儿 `ab_agent.py`**(lemma-staticize 的 A→B 搜索 agent,
   18:41 起,父进程 0)带着 IsaREPL JVM;
2. IsaREPL 的 scala-isabelle 在加载 session 前会跑 `isabelle build`——而
   我们 **apply 过 lemma 后源码变了**,heap 相对源码过期,于是它反复触发
   **全量 AInvs heap 重建(~47-90min)**;
3. 我方 run 的外层 timeout SIGKILL 又会打断重建中的 heap 写入,形成
   "杀 → heap 损坏 → 下次重建 → 再被杀"的循环。

要点:`check-theory.sh` 自身用 `isabelle process -l`(只读加载 heap,
**不因源码过期而重建**),所以 spec 管线单独跑没有这个问题;问题出在
**与 scala-isabelle 系工具共存**时 apply 改源码的副作用。处置:不杀别人
的实验进程,等其 build 自然完成后接力;驱动侧已加 `TRIAL_TIMEOUT`(单
trial 兜底),教训是外层预算必须 ≫ 最坏单 build,且 kill 不可打断 heap 写。

### agent 校准问题:把便宜的 trial 当贵的省

IpcCancel / Finalise 两轮 P run agent 全数返回 `[]`(裸空数组,无逐条理由)。
prompt 里"错误提议代价是一次完整 trial build"的措辞让它绝对保守——但 P 的
真值本来就归 trial(~22-66s),**框架的设计就是用便宜的 trial 测 50/50 假设**。
校准:P 专属指导改为"能点名消费规则才跳过,否则就提——retur回 [] 是浪费
便宜实验";并补充 assert-失败路径让三元组空虚成立的常识(`thread_get` 类)。

### P 成熟案例 ✅ — `gts_wf'`(TcbAcc_AI)

校准后首轮即中。**全链路**:unused-premise hint(`gts_wf` drop `tcb_at t`,
差分+frame-premise 双过滤后存活)→ agent 语义判断(get_tcb 失败路径空虚 +
invs⟹valid_objs⟹valid_tcb_state)→ named-planned gate → trial OK
(57218ms,Δ-3.8%)→ additive/PASS → **APPLIED**。

```isabelle
(* 原 gts_wf[wp] 不动: pre = tcb_at t and invs *)
lemma gts_wf': "\<lbrace>invs\<rbrace> get_thread_state t \<lbrace>valid_tcb_state\<rbrace>"
  <原证明逐字复制>      (* L732, 新增 *)
```

严格弱化:`tcb_at t ∧ invs ⟹ invs`,反向不成立。正确遵守了 P+wp 默拒纪律
(新 lemma 不带 `[wp]`,named-planned)。一个记录瑕疵:agent 的 claim 字段
蕴含方向写反(decision.md 已修正并注明)。

**该瑕疵已机械封堵**:`spec_slot_hints.py --check-p-claim` 现在对每个 P
候选静态验证"新 pre 是旧 pre conjunct 的严格子集 + post 逐字相同",通过则
**自动生成正确方向的 claim 覆写 agent 的自由文本**(对真实 gts_wf' 数据验证:
正确导出 `(tcb_at t and invs) ==> (invs)` + 点名 dropped conjunct;反例——
偷改 post / 加 conjunct——均被拒)。strengthen.sh 在 patch 之前跑这一步,
不通过只警告(provability 仍归 trial),但 decision.md 不再记录未经验证的
蕴含声明。

## 5. 总结

| 维度 | 之前 | 之后 |
|---|---|---|
| Q 检索 | 纯 LLM,slot Q 退化为 F | redirect 分类学 + exactness 检测器引导;slot 硬执行 |
| P 检索 | 无信号 | 前缀匹配 + 差分证据 + frame-premise 三重精度规则 |
| 执行 | trial 失败即丢弃(开环) | prover 错误回流 agent 修正重试(闭环,留痕) |
| Q 案例 | 0(唯一 PoC 是 alias) | **`compute_free_index_wp_exact` APPLIED**(非 alias,exactness) |
| P 案例 | 0(0023 是 modify 模式) | **`gts_wf'` APPLIED**(additive,named-planned) |

Q/P 检索的本质结论:**Q 的可执行真矿在 exactness 与"强 post 无名字"的
inline-redirect,经典 redirect 多为形式适配或已填槽;P 的真阳性稀少且
集中在 assert-保护的访问器(`tcb_at` 类前提)与差分形态(0023 类),
机械三重过滤 + agent 语义判断 + 便宜 trial 的三层分工是正确成本结构。**

---

**生成时间**:2026-06-10
**配套**:[execute-additive-design.md](execute-additive-design.md)(框架),
`spec-strengthen/scripts/spec_slot_hints.py`(检测器),
`spec-strengthen/strengthen.sh`(驱动)
