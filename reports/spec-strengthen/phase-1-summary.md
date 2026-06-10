# spec-strengthen Phase 1 — Pattern 模式、自动化边界、阶段成果

> 一阶段（2026-04 → 2026-06）spec 增强探索的总结。回答三个问题：
>   1. spec 增强的目标是什么？
>   2. A / C / D / G 四个 pattern 各自的"修改模式"是什么？
>   3. 为什么 G 已经实现自动化扫描，而 A / C / D 不能？
>
> 最后报告本阶段的工具沉淀与已 apply 的 spec 强化清单。

---

## 1. spec 增强的目标

seL4 抽象规范的**公开承诺集**（即所有可从 spec 派生的 Hoare 三元组定理）决定了下游 proof（Refine / CRefine / Access / InfoFlow）能用什么事实。承诺越多、承诺越紧，下游 proof 在 `wp` 阶段就有更多 shortcut，wall 更低、`apply` 链更短、模块接口更清晰。

**严格强化（strict strengthening）** 的硬约束：

```
new_contract ⟹ old_contract     (新承诺蕴含旧承诺)
old_contract ⇏ new_contract     (旧承诺不能蕴含新承诺)
```

任何**等价改写**（refactor 性质）和任何**弱化**（撤回承诺）都被排除在外。spec 强化必须在 spec 的契约空间里**严格向上**移动。

工程上的衡量：

| 验证关口 | 通过判据 |
|---|---|
| **Gate 1** check-theory.sh --patch | trial 返回 `OK` |
| **Gate 2** spec_impact.py | verdict 为 `additive` 或 `null` |
| **Gate 3** wall delta | trial wall ≤ baseline × 1.30 |
| **Gate 4** SKILL hard rules | 不引入 `sorry`，不破坏向后兼容 |

四关全过 = 一次合法的 spec 强化。

---

## 2. A / C / D / G 四个 Pattern 的修改模式

`A/C/D/G` 是**修改形状**的字母编号，不是内容方向的标签。每个字母对应**对 lemma database 做什么操作**。

### G — Frame lemma addition（加新 frame 引理）

```
+   lemma <op>_<field>[wp]:
+     "\<lbrace>\<lambda>s. P (<field> s)\<rbrace>
+        <op> args
+      \<lbrace>\<lambda>_ s. P (<field> s)\<rbrace>"
+     by (wpsimp simp: <op>_def)
```

**修改形状**：纯 ADDITION。只增节点，不改任何 existing 节点。

**严格强化的来源**：原 spec 没有公开"`<op>` 保 `<field>`"这条承诺；新 lemma 把它加进 spec。新增承诺集 ⊋ 旧承诺集。

**典型例子**：[[0027]] `set_thread_state_machine_state[wp]` — 之前没有这条 frame，加上后 wall −4.4%。

### C — Premise drop（弱化 pre / 丢前提）

```
-   lemma L:
-     "\<lbrace>P_a \<and> P_b \<and> P_c\<rbrace>
-        op
-      \<lbrace>Q\<rbrace>"
-     by <proof>
+   lemma L:
+     "\<lbrace>P_a \<and> P_b\<rbrace>           (* drop P_c *)
+        op
+      \<lbrace>Q\<rbrace>"
+     by <same proof>
```

**修改形状**：MODIFY existing lemma 的 pre（一个 conjunct 被丢掉）。Proof body 不改。

**严格强化的来源**：调用方现在需要满足更少前提（pre 弱化），spec 对调用方更宽容；但同 op 还是给出同 `Q`，承诺更广。

**典型例子**：[[0023]] `unbind_maybe_notification_not_bound` drop `valid_objs` — 该 lemma 的 proof body 是 `wp get_simple_ko_wp sbn_obj_at_impossible`，不消耗 `valid_objs`，drop 后 proof 仍 verify。

### A — Postcondition strengthening（强化 post）

```
-   lemma L:
-     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv. Q_weak rv\<rbrace>"
-     by (rule hoare_strengthen_post, rule L_strong, simp add: Q_strong_imp_Q_weak)
+   lemma L:
+     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv. Q_strong rv\<rbrace>"
+     by (rule L_strong)
+   lemma L_old:                                   (* witness 保旧形式 *)
+     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv. Q_weak rv\<rbrace>"
+     by (rule hoare_strengthen_post[OF L]) (simp add: Q_strong_imp_Q_weak)
```

**修改形状**：MODIFY existing lemma 的 post，并 add 一个 `_old` witness 保旧形式（backward compat）。

**严格强化的来源**：返回承诺 `Q_strong ⟹ Q_weak`，新 post 是旧 post 的精确化。同 op 同 pre，但返回事实更丰富。

**典型例子（modify 形）**：[[0029]] `lsfco_cte_at` 把 post 从 `cte_at rv` 改为 `real_cte_at rv` — `real_cte_at ⟹ cte_at` via `real_cte_at_cte` simp。Modify 形式 **trial 失败** — 见 §3。

**Additive 变形（[[0054]] PoC）**：

```
+   lemma L_strong:
+     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv. Q_strong rv\<rbrace>"
+     by (rule L_strong_source)
    (* L 完全不改 *)
```

Additive A 把 modify 形的 `_old` witness 化为"原 L 自身就承担 _old 的职责"，零 cascade。详见 [[0054]] decision.md。

### D — Bound tightening（紧化界 / `≤` → `=`）

```
-   lemma L:
-     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv s. f s \<le> x\<rbrace>"
-     by <proof>
+   lemma L:
+     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv s. f s = x\<rbrace>"
+     by <tighter proof>
+   lemma L_old:
+     "\<lbrace>P\<rbrace> op \<lbrace>\<lambda>rv s. f s \<le> x\<rbrace>"
+     by (rule hoare_strengthen_post[OF L]) simp
```

**修改形状**：A 的特例 — post 里某个非严格不等式被替换为严格相等。

**严格强化的来源**：`(f s = x) ⟹ (f s \<le> x)` 平凡成立，反向不成立。

**为什么独立编号**：D 候选的 detector 与 A 不同（要识别 post 里的 `≤` 子表达式而不是整个谓词替换），detector 可以更精准但是更稀有。

### 横向对比

| Pattern | 操作 | 改老 L | 改 in-file 消费者 | 改 cross-file 消费者 | 加 _old witness |
|---|---|:-:|:-:|:-:|:-:|
| **G** (frame) | + new lemma | ✗ | ✗ | ✗ | ✗ |
| **C** (drop) | ~ L's pre | ✓ | ✗ | ✗ | optional |
| **A** (strong) | ~ L's post + proof | ✓ | 可能 | 可能（多文件） | ✓ 必须 |
| **D** (tight) | ~ L's bound + proof | ✓ | 可能 | 可能 | ✓ 必须 |

**G 是唯一的 pure-additive**。C 改单点。A/D 改单点 + cascade 风险跨文件。

---

## 3. 为什么 G 已经实现自动化扫描，而 A / C / D 不能

回答这个问题需要先定义"**自动化扫描可行**"的判据：

> **可扫描** = candidate 的 truth-condition 能从 **detector 可见的静态信息** 推断出来，无需运行验证器或人工判读。

四个 pattern 的 truth-condition **信息住址**完全不同：

### G：truth-condition 住在 **op 的 def 体内**（local，static analysis）

> "`set_thread_state` 保 `machine_state` 吗？"
> ↓
> "查看 `spec/abstract/KHeap_A.thy` 里 `set_thread_state_def` 写了哪些字段。"

Detector 工具：

1. **direct grep**：lemma 名字是否已存在 → preflight_failed:direct
2. **crunch grep**：crunch 是否自动派生过 → preflight_failed:crunch
3. **dmo gate**：op 的 def 是否（传递地）调 `do_machine_op` → preflight_failed:dmo_path
4. **dxo gate**：op 的 def 是否（传递地）调 `do_extended_op` AND field 是 exst-投影 → preflight_failed:dxo_path

四道 gate 都过 = candidate 真值为"L' 可证"，**100% trial OK**（[[5cd9c65]] retroactive 验证：5/5 catch + 8/8 control）。

**关键**：op 的 def 是 spec 自己定义的内容，**信息完全 local**。Detector 只需 parse spec/abstract 即可。

### C：truth-condition 住在 **proof body 的 wp 链** + **wp 规则数据库**（tree-global，dynamic）

> "lemma L 的 proof 真的不依赖 premise X 吗？"
> ↓
> "我得跟踪 L 的 proof body 每条 tactic 调用了哪些 [wp] 规则，每条 [wp] 规则的 statement 是否 require X。这是**全 proof tree 的 wp database 遍历**。"

更糟的是：

- [wp] 数据库是**动态的、跨文件的**。新加一条 [wp] 规则在 lemma X 里 → 别处用 X 的 proof 也可能被这条规则改变 search 路径。
- 同一条 premise 在不同 lemma 的 proof 里是否 load-bearing **完全独立**。
- "premise X 没在 proof body 字面出现"是**必要而非充分**条件 — 隐式 wp 链可能用了它。

Detector 工具能给出的**仅是 heuristic 怀疑值**（`suspicion_score`），不是真值：

- `consumer_lines × kind_weight` — 反映"如果这个 candidate 是真的，影响有多大"
- **不**反映"这个 candidate 真的吗"

**唯一 ground truth = 跑一次 trial**（drop premise → check-theory.sh --patch）。但 trial 已经是 verification 本身，detector 等于"扫描 = 验证"，detector 的存在意义被掏空。

**实证**：本阶段 27 个 C candidate 经手工 + trial 分类：
- **HARD_LB**（proof body 显式调 valid_objsE / invs_def / 等已知载体）：3
- **LIKELY_LB**（cspace lookup / invs drop 类，历史 ~100% load-bearing）：17
- **DONE**（已 probed）：4 → 0 通过
- **UNCERTAIN**：2 → 0 通过
- **PARSE error**：1

**命中率 0/27**（除孤例 [[0023]]）。Detector 给的 suspicion ranking 与真值**几乎不相关**。

### A：truth-condition 住在 **所有 use site 的 proof body**（tree-global，无法本地观察）

> "把 lemma L 的 post 从 Q_weak 改成 Q_strong 后，所有用 L 的下游 proof 还跑得通吗？"
> ↓
> "我得跑遍 24 个文件、89 处 use site，每处都看 L 后面的 tactic 是不是依赖 Q_weak 形式而不是 Q_strong 形式。"

Detector 能给的 hint：

- **redirect-shape 检测**：L 的 proof body 是 `(rule hoare_strengthen_post, rule L_strong, simp)` 形式 → 暗示存在已有 strong sibling
- **consumer_lines × kind_weight** suspicion

这两个信号都告诉你"这是一个候选"，但**不告诉你"改完不会破"**。

**实证**：[[0029]] 唯一一个 A 案例 modify 模式 — detector 的 suspicion=88（合理高），redirect shape ✓。但 trial 失败 — in-file `lsfco_cte_wp_at_univ` 的 `clarsimp simp: cte_wp_at_def` 在新 antecedent 下不闭合。Cross-file 24 file 仍未触发就已 trial fail。

**这是 detector 与真值 fundamentally 错位**：detector 看 candidate 自身和它的"出场频率"；trial 看的是 candidate 周围所有 consumer 的 search space 是否仍可收敛。两者**没有可见连接**。

### D：A 的 strict 子集，但更稀有

D 的 detector 需要识别 `≤` 子表达式 — 比 A 的 "redirect shape" 还要精准。理论上更可扫，但**人工书写的 spec 里 `≤` 很少出现在 post 顶层**，candidate 池极小。本阶段未发现可执行的 D candidate。

### 信息地图

| Pattern | 真值住址 | 局部性 | Detector 能给的 | Trial 必要性 |
|---|---|:-:|---|:-:|
| **G** | op 的 def 体 | LOCAL | **真值**（4 道 gate 等价 trial） | 仅作 confirmation |
| **C** | proof body + wp 数据库 | TREE-GLOBAL | 怀疑度（与真值几乎无关）| **必要**（ground truth）|
| **A** | 所有 use site | TREE-GLOBAL | candidate hint + suspicion | **必要**，且 cascade fail 率高 |
| **D** | 所有 use site | TREE-GLOBAL | 极少 candidate | **必要** |

**结论**：G 之所以能自动化扫描，根本原因是它的 truth-condition 在 spec 自身的 op def 里 — 一段**纯静态、纯 local** 的信息。A/C/D 的 truth-condition 都在 **proof tree 的 global 状态**里 — 这是 static analyzer 看不见的地方，只能由 verifier（check-theory.sh trial）告诉你。

后果：

- **G 的 detector = verifier**（preflight 通过 = trial 通过）
- **A/C/D 的 detector ≠ verifier**（怀疑度 ≠ 真值），自动化只能止步于"产候选"，**确认必须走 trial**

---

## 4. 阶段成果

### 4.1 已 applied 的 spec 强化

| Op family | applied 字段 | 来源 experiment |
|---|---|---|
| `set_object` | machine_state, cdt, cur_thread, cur_domain, arch_state, domain_index, domain_time | 0015-0017, 0019-0025 |
| `set_cdt` | machine_state, cur_thread, idle_thread, cur_domain, arch_state, domain_index, domain_time | 0014-0017, 0035-0037 |
| `set_thread_state` | machine_state, arch_state | 0027, 0030 |
| `set_bound_notification` | machine_state, domain_index, domain_time, arch_state | 0030 |
| `set_mrs` (Ipc_AI + TcbAcc_AI) | domain_index, domain_time, arch_state | 0033, 0034 |
| `set_message_info` | machine_state, domain_index, domain_time, arch_state | 0031, 0032 |
| `set_extra_badge` | domain_index, domain_time, arch_state | 0032 |
| `set_cap` | machine_state, arch_state, domain_index, domain_time | 0038, 0041, 0050, 0051 |
| `set_simple_ko` | machine_state, domain_index, domain_time, arch_state | 0045, 0052, 0053, 0044 |
| `set_scheduler_action` | machine_state, arch_state, domain_index, domain_time | 0046-0049 |
| (helpers in Ipc_AI) | `do_machine_op_<field>[wp]` × 3, `as_user_<field>[wp]` × 3, `thread_set_<field>[wp]` × 3 | 0032, 0033 |
| **Pattern C apply** | `unbind_maybe_notification_not_bound` drop `valid_objs` | 0023 |
| **Pattern A (additive PoC)** | `lsfco_real_cte_at` (新 lemma，原 `lsfco_cte_at` 不动) | 0054 |

**累计 53+ 条新 wp lemma 进入 AInvs**。最强单 lemma wall delta：[[0015]] `set_cdt_machine_state[wp]` −11.3%；最强单 batch：[[0032]] 12-lemma Ipc_AI closeout −3.4%。

### 4.2 工具沉淀（spec-strengthen/scripts/）

| 工具 | 职责 |
|---|---|
| `spec_frame_gap.py` | G detector + dmo/dxo 两道 semantic gate + op 静态调用图分析（`op_transitively_calls`） |
| `spec_candidates.py` | A/C 启发式 detector，emit suspicion-ranked candidates |
| `spec_strengthen_scan.py` | Hoare triple parser |
| `spec_op_args.py` | op 签名 → arg 名 list（支持 type-arity padding，处理 pattern-match lambda） |
| `spec_impact.py` | additive verdict + consumer count + wall gate |
| `spec_strengthen_c_patchgen.py` | C patch generator（drop conjunct + emit `_old` witness） |
| `spec_witness_gen.py` | A/C `_old` witness 生成（按 triple shape 选 Hoare 单调性引理）|
| `spec_premise_probe.{sh,py}` | C 用 Isa-REPL probe — 实际未用（启动太慢，退化为 check-theory.sh trial） |

**run.sh** 提供 5 个 subcommand：`survey`、`execute`、`status`、`ledger`、`mark-audited` / `mark-aborted`。

### 4.3 流程沉淀

| 流水阶段 | 实现 |
|---|---|
| (1) Scan + parse | 自动 — `spec_frame_gap.py` + 3-token op allowlist（set_asid_pool / set_vm_root / set_scheduler_action 等）|
| (2) Mechanical preflight | 自动 — direct grep + crunch grep |
| (3) Semantic gates (G) | 自动 — dmo + dxo |
| (4) Ledger + markdown emit | 自动 — `discovered` 事件入账 + `surveys/survey-<thy>-<date>.md` |
| (5) Execute (G) | 自动 — `execute_G` 加 args 提取、anchor 块尾定位、动态 tactic（探测 `get_object` 自动加 `wp: get_object_wp`） |
| (6) Execute (C/A/D) | 半自动 — 现有 `execute_custom` 接受任意 patch，框架走 standard pipeline，但 patch 内容需人工或专路 |
| (7) Trial + impact + apply | 自动 — `standard_pipeline` |
| (8) Audit dir 永久落档 | 自动 — `experiments/<id>/` 5 件套（decision.md / measurement.json / patch.diff / range-patch.patch.txt / command.sh）|

### 4.4 容器与构建基础设施

- **容器 orphan reaper**：`_dx.sh` + `spec_premise_probe.sh` 加 baseline-diff trap。host wrapper 死亡时回收容器内新产生的 polyml/isabelle 进程；ml_server daemon 不会被误杀（被 baseline 保护）。
- **ledger_state SIGPIPE fix**：旧 `tac | python3` 方案在 pipefail 下竞争 SIGPIPE → 早退 set -e 终止 script。改为 python 直读文件，反向迭代。

### 4.5 失败案例（也是成果 — 作为后续设计输入）

| Experiment | Pattern | 失败原因 | 价值 |
|---|---|---|---|
| [[0026]] | C iterative | drop sym_refs 在 0023 之后的 lemma 上产生 witness 命名冲突 | 暴露 iterative-C 在同 lemma 上的限制 |
| [[0028]] | G:set_mrs:machine_state | `do_machine_op storeWord` 实际写 `machine_state.memory` | 触发 dmo gate 的设计 |
| [[0029]] | A:lsfco_cte_at modify | in-file consumer `lsfco_cte_wp_at_univ` 的 clarsimp 配方在新 antecedent 下不闭合 | 触发 [[0054]] additive PoC 的设计 |
| [[0030]] dropped 2 fields | G:set_thread_state:domain_index/time | `do_extended_op` 改 exst 整块，无 per-field lift | 触发 dxo gate 的设计 |

四个失败案例**全部转化为 detector / 工具设计**，没有"白浪费的失败"。

### 4.6 概念产出

- **Pattern 字母描述修改形状，不描述内容方向** — 该洞察解释了为什么 G/C/A/D 的自动化能力有结构性差异。
- **Cascade 是 modification 的副作用，不是内容的副作用** — 该洞察直接推出"additive-unification"提案（[[0054]] PoC 验证）。
- **LLM 的真正切入点是新 proof search，不是 spec content search** — 因为 spec content 受 strict-strengthening 约束极强，搜索空间很小；而某条 statement 的 proof 路径却是开放搜索问题。该洞察来自比较 lemma-staticize 的成功路径与本阶段 C/A 的失败路径。

---

## 5. 阶段边界 — 下一阶段的入口

一阶段的核心命题**已被验证或被证伪**：

| 命题 | 验证状态 |
|---|---|
| G 能完全自动化 | ✓ 100% 应用率 + 4 道 gate + dynamic tactic |
| C 能通过 detector 自动发现 + trial 自动验证 | ✓ 流程自动 — 但 hit rate 3.7%，spec 结构性限制 |
| A 能 single-shot modify 模式自动化 | ✗ cascade fail 是结构性问题 |
| A 能 additive 模式绕开 cascade | ✓ [[0054]] PoC 在 0029 失败案例上验证 |
| D 能形成有效 candidate 池 | ✗ 本阶段未发现可执行候选（候选稀有）|

二阶段的工程入口是 **execute_additive 实施**：把 A/C/D 重写为 additive shape 的统一 execute 路径，让所有 spec 强化都走"加新 lemma + 原 L 不动"模式。配套 design：

- Ledger schema 加 `kind` 字段（`frame_g` / `drop_c` / `strong_a` / `sub_g` / `tight_d`）
- 命名约定 + `[wp]` 注册策略 per kind
- 与 LLM agent 接口：trial_failed candidate 由 LLM 提议新 proof 后重试

这是把一阶段验证的"additive 消 cascade"洞察落实为日常工作流的工程化路径。

---

**报告生成时间**：2026-06-10
**对应 spec-strengthen branch HEAD**：`ae3f2f7` (`spec-strengthen: post-migration cleanup of stale path refs`)
**累计 commit 数**：13（spec-strengthen 相关）
