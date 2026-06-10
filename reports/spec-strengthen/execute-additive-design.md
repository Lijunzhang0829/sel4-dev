# execute_additive 设计方案 — 槽位 × 下游机制 二维约束

> **核心立场**：抛弃 pattern 字母（G/C/A）的概念框架，重新定义 spec 强化的最小动作：**给定一条 existing lemma L，识别其 spec 上的"空槽"，加新 lemma L' 填空，原 L 不动**。
>
> **关键约束**：每个候选 L' 必须回答"它通过什么机制作用于下文"。答案只能是三种之一：`[wp]/[simp]` 自动生效、被具名 consumer 引用、或作为下层组合 lemma 的 building block。**答不上来 = 不收**。
>
> 这份文档是 phase-2 工程化的设计基础。读完应能直接进入实施。

---

## 0. 为什么需要 execute_additive

Phase-1 暴露的根本问题：

**modify-based pattern (C / A / D)** 把 spec 强化定义为"改老 L"。这一动作触发 L 周围 search space 的全面重 search：

- L 的 proof body 自己（C 的失败模式）
- L 的 in-file consumer（[[0029 lsfco]] 失败模式）
- L 的 cross-file consumer（A 跨 24 file 89 行的 cascade）

cascade 是 modification 的副作用，不是内容的副作用。**任何 modify-base 的自动化都会撞到这堵墙**。Phase-1 的 [[0054]] PoC 验证：把同一个 0029 候选改成 additive 形式（加新 `lsfco_real_cte_at`，原 `lsfco_cte_at` 不动），cascade 消失，trial 通过。

execute_additive 把这个 PoC 升级为**统一的 spec 强化工程动作**：

> **任何 spec 强化 ≡ 加新 lemma L'，原 L 不动**

但这一抽象需要两个支撑约束才能落地：

1. **L' 的内容如何对应到一种"可识别的空槽"** — 否则 detector 无从下手
2. **L' 加进 lemma db 后如何作用到下文** — 否则就只是"可证明"，不是"有价值"

§1 解决第一个约束。§2 解决第二个。§3 把两个约束组合成 execute_additive 的二维输入。

---

## 1. 三种空槽 — spec 强化的全部内容空间

### 1.1 设定

设 existing lemma：

```
L:  \<lbrace>P\<rbrace> op args \<lbrace>Q\<rbrace>
```

我们想加 L'。L' 的 spec 必须**严格强于 L 在某个维度上的承诺**（strict-strengthening 硬约束），且**原 L 不动**（additive）。

L 的 spec 表面有三个可"加紧"的维度：**pre / post / frame**。每个维度对应一种**空槽**：

### 1.2 P-slot：弱化前提槽

```
L:    \<lbrace>P_a \<and> P_b \<and> P_c\<rbrace> op args \<lbrace>Q\<rbrace>
L_new: \<lbrace>P_a \<and> P_b\<rbrace>           op args \<lbrace>Q\<rbrace>
```

**空槽位置**：L 的 pre 里某个 conjunct `P_c` 实际上对 proof 不构成依赖。

**严格强化关系**：`(P_a ∧ P_b) ⟸ (P_a ∧ P_b ∧ P_c)` — L_new 的 pre 严格更弱，spec 更强。

**Truth condition**：proof body 不消耗 `P_c`（直接显式 + wp 链隐式两方面）。Detector 能给的是 heuristic 怀疑值；ground truth 必须走 trial。

**典型例子**：[[0023]] `unbind_maybe_notification_not_bound` 的 `valid_objs` 是 P-slot — drop 后 proof 仍 verify。Phase-1 在 modify 模式下做的；additive 模式下应该写成：

```isabelle
lemma unbind_maybe_notification_not_bound_weak:    (* 新 lemma *)
  "\<lbrace>ntfn_at ntfnptr and sym_refs (state_refs_of)\<rbrace>      (* P_c=valid_objs 缺席 *)
     unbind_maybe_notification ntfnptr
   \<lbrace>...\<rbrace>"
  by <same proof as L>      (* 不依赖 valid_objs *)
```

### 1.3 Q-slot：强化结论槽

```
L:    \<lbrace>P\<rbrace> op args \<lbrace>\<lambda>rv. Q_weak rv\<rbrace>
L_new: \<lbrace>P\<rbrace> op args \<lbrace>\<lambda>rv. Q_strong rv\<rbrace>      (* Q_strong ⟹ Q_weak *)
```

**空槽位置**：L 的 post 实际上比"可证"的 post 弱（"under-committed"）。

**严格强化关系**：`Q_strong ⟹ Q_weak`，L_new 的 post 严格更强。

**Truth condition**：存在一条更强的可证 post；通常 L 的 proof body 内部已经路过 Q_strong（redirect-shape 暗示），但在最后通过 `hoare_strengthen_post` 弱化到 Q_weak。

**典型例子**：[[0029]] / [[0054]] `lsfco_cte_at` → `lsfco_real_cte_at`。原 L 的 proof 是：

```isabelle
by (rule hoare_strengthen_postE_R, rule lookup_cnode_slot_real_cte, simp add: real_cte_at_cte)
```

— 它实际拿到的是 `real_cte_at`，但被 `real_cte_at_cte` 简化成 `cte_at` 才交出。Additive 形式：

```isabelle
lemma lsfco_real_cte_at:    (* 新 lemma *)
  "\<lbrace>valid_objs and valid_cap cn\<rbrace>
   lookup_slot_for_cnode_op f cn idx depth
   \<lbrace>\<lambda>rv. real_cte_at rv\<rbrace>,-"
  by (rule lookup_cnode_slot_real_cte)    (* 直接，不再弱化 *)
```

**Q-slot 的子类（不构成独立空槽）**：

- **Q-exactness**：Q_weak 包含 `≤` 子表达式，Q_strong 把 `≤` 换为 `=`。形式上：`{P} op {f s ≤ x}` → `{P} op {f s = x}`。严格强化来源 `(f s = x) ⟹ (f s ≤ x)`。这就是 phase-1 早期 taxonomy 里的 D。在本框架下它是 Q-slot 的一个 detector 实现细节，不是独立空槽。

### 1.4 F-slot：字段保性槽

```
(L 可以不存在)

L_new: \<lbrace>\<lambda>s. P (<field> s)\<rbrace> op args \<lbrace>\<lambda>_ s. P (<field> s)\<rbrace>
```

**空槽位置**：op 在 spec 里**没有公开承诺保 `<field>`**。这是个独立的"新承诺"维度，不依赖任何 existing L。

**严格强化关系**：spec 承诺集纯增。

**Truth condition**：op 的 def 体（及其传递调用）不写 `<field>`。这是**纯静态、纯 local** 的判断，detector 完全可定（4 道 gate：direct grep / crunch grep / dmo / dxo）。详见 [pattern-G-automation-pipeline.md §3](pattern-G-automation-pipeline.md#3-detector-层--spec_frame_gappy)。

**典型例子**：[[0027]] `set_thread_state_machine_state[wp]`、[[0035]] `set_cdt_arch_state[wp]` 等本阶段 35+ 个 frame lemma。

### 1.5 这三种就是全部 — 论证

L 的 spec 表面 = (pre, op, args, post)。L_new 要严格强于 L，只能在以下空间里动：

| L_new 与 L 关系 | 是否可能严格强化 | 落到哪类槽 |
|---|---|---|
| 同 op，同 args | 同 pre，同 post → 等价（不算强化） | — |
| | pre 弱化，同 post | **P-slot** |
| | 同 pre，post 强化 | **Q-slot** |
| | pre 弱化 + post 强化 | **P-slot + Q-slot 复合**（罕见，但合法） |
| 同 op，不同 args（如 instantiate 部分参数） | 通常构成新 lemma，不与 L 严格可比较 | — |
| 跟 op 同 family 但不同 op | 不属于"强化 L"的动作 | — |
| **L 不存在**，加全新承诺（field frame） | spec 承诺集纯增 | **F-slot** |

**P-slot + Q-slot 复合**在 phase-1 §2 的 G_sub 讨论中提过。在本框架下不构成独立槽 — 检测时分两条候选发现，patch 上可以合写一条 lemma，但语义动作仍是两槽组合。

**结论**：三槽（P / Q / F）穷尽所有 spec 强化方向。任何"对 lemma db 加 strictly stronger 新 lemma"的动作都落入其一或其复合。

---

## 2. 下游机制约束 — 每个 L_new 必答的问题

### 2.1 核心立场

**仅"可证明"不够**。lemma db 里存在但无下文路径的 lemma = 死码，污染 search 但不带来 spec 强化的实质效果。

execute_additive 的纪律：**每个候选 L_new 在 patch 起草时必须声明它如何到达下游**。这个声明跟随 candidate 进 ledger、进 decision.md、进 audit dir，作为该 lemma 存在价值的硬记录。

声明的可选答案有且只有三种：

### 2.2 机制 A — `[wp]` / `[simp]` 自动生效

L_new 注册为 `[wp]` 或 `[simp]`。downstream proof 在 `wp` / `simp` tactic search 阶段自动命中它。

**适用场景**：

- F-slot：frame lemma 是"通用规则"，wp 自动调用是它的正常用法。机制 A 是 F-slot 的默认。
- P-slot / Q-slot：当 L_new 是某条 op 的"主版本规则"，比 L 更强但 wp 行为可控时。罕见 — 因为这意味着 wp 数据库新加了一条规则，可能跟 L 的旧规则在 search 阶段竞争，触发下游 search 路径改变。

**风险**：

- wp database 膨胀（同 op 多条 [wp] 规则）
- wp 选错（旧 L 的 [wp] 与 L_new 的 [wp] 在某些场合产生不同的 search 结果）
- search 路径漂移（即便表面 verdict 还是 additive，下游 proof 的 wp 步骤可能跑得更慢或绕远路）

**Phase-1 数据**：本阶段 F-slot 全部默认 A 机制（35+ lemma 全部 `[wp]`），未观察到 wp 数据库污染问题（因为 frame lemma 的 wp 规则形态非常规整，互相不冲突）。但 Q-slot / P-slot 若大量 A 机制，需要做 wp 行为回归测试。

### 2.3 机制 B — 后续具名 consumer 引用

L_new 不打 `[wp]` / `[simp]`，仅作为命名 lemma 存在。某个下游 lemma 的 proof 显式调用：

```isabelle
apply (rule L_new)              (* 直接用 *)
apply (wp L_new ...)            (* 显式注入 wp *)
apply (drule L_new[where ...])  (* 当 destructor *)
```

**适用场景**：

- Q-slot：consumer 想要 Q_strong 时显式引用 L_new；其它 consumer 继续用 L 拿 Q_weak。这是 [[0054]] `lsfco_real_cte_at` 的标准用法。
- P-slot：consumer 在更松的环境（pre 不满足 L 完整 P 但满足 L_new 的 P'）下显式引用 L_new。
- F-slot：罕见 — frame lemma 通常走 A 机制。但有时 wp 行为不便注册，按名调用。

**风险**：

- "后续 consumer"未必真出现。需要在 candidate 声明时**指明预期 consumer**（具名或描述 type），否则等于无人接盘的孤儿。
- consumer 接入是 separate PR / commit — 工作分割可能漏接。

**纪律**：candidate 提交 patch 时必须在 decision.md 写出"预期 consumer = X"（lemma name 或 file:line range）。L_new 入库时若该 consumer 暂未存在，记 `delivery_pending` 状态在 ledger；后续 consumer landing 时 mark 为 `delivery_realized`。

### 2.4 机制 C — 下层组合 lemma 的 building block

L_new 既不打 `[wp]` / `[simp]`，也不被任何叶子 proof 直接引用，但是是**另一条 spec 强化 lemma 的 input**。

**典型例子**（[[0032]] / [[0033]]）：

- `do_machine_op_<field>[wp]` 三条 helper（[[0032]] 加）
- `as_user_<field>[wp]` 三条 helper（[[0032]] 加）
- `thread_set_<field>[wp]` 三条 helper（[[0033]] 加）

这些都是 spec 强化 chain 的中间环节，没有叶子 proof 直接调；但 `set_extra_badge_<field>[wp]` / `set_message_info_<field>[wp]` / `set_mrs_<field>[wp]` 的 proof 依赖它们 — 没有这些 helper，那批 frame lemma 写不出来。

**适用场景**：

- 一组 spec 强化候选共享 proof 路径上的某个 building block。Building block 单独作为 L_new 加入。
- 大型 op family 自动化的预备步骤。

**风险**：

- 计划落空 — building block 加了，但下层 lemma 链没接上。等同孤儿。
- chain 长度过深 — 多层 building block 嵌套可能让最终 proof 路径过于间接，wp search 成本累积。

**纪律**：candidate 提交时必须**枚举预期使用它的下层 lemma**（candidate key list）。这些下层 candidate 也要进 ledger。building block 与下层之间的依赖在 ledger 里显式标记，便于后续 audit。

### 2.5 必答约束的拒收逻辑

candidate 提交时 metadata 中必须包含字段 `delivery`：

```jsonc
{
  "key": "...",
  "slot": "P" | "Q" | "F",
  "delivery": "wp" | "simp" | "named" | "block",
  "delivery_target": "...",   // B 机制: 预期 consumer 名/范围
                              // C 机制: 下层 candidate keys
                              // A 机制: 可空
  "delivery_attestation": "..." // decision.md 中相关段落的引用
}
```

**拒收规则**：

- `delivery` 缺失 → 候选不进 ledger，detector 阶段就 reject。
- `delivery = named` 但 `delivery_target` 为空 / 仅泛泛描述（"some consumer might want this"）→ reject。
- `delivery = block` 但 `delivery_target` 不指向 ledger 中存在的下层 candidate（或明确标记的"future-N"未来候选）→ reject。
- `delivery = wp` 且槽是 P 或 Q → 触发 advisory warning，要求 decision.md 显式论证 wp 行为不会污染下游（pre-flight 必须包含一个 "no wp regression" 检查）。

通过的候选才进入 execute_additive 的 trial 阶段。

---

## 3. execute_additive 流程

### 3.1 输入合约

```bash
spec-strengthen/run.sh execute_additive \
  --candidate <key> \
  --slot P|Q|F \
  --delivery wp|simp|named|block \
  [--delivery-target <consumer-or-block-list>] \
  --expid <id> \
  [--proof-tactic <override>] \
  [-y] [--retry]
```

`<key>` 的命名约定（按槽位 + delivery 决定）：

| Slot | Delivery | key 格式 | 新 lemma 名 | 例 |
|---|---|---|---|---|
| F | wp | `F:<theory>:<op>:<field>` | `<op>_<field>[wp]` | `set_thread_state_machine_state[wp]` |
| F | named | `F-named:<theory>:<op>:<field>` | `<op>_<field>` (无 [wp]) | 罕见 |
| Q | named | `Q:<theory>:<lemma>:<target-post>` | `<lemma>_<target-post>` | `lsfco_real_cte_at` |
| Q | wp | `Q-wp:<theory>:<lemma>:<target-post>` | `<lemma>_<target-post>[wp]` | 高风险，需 advisory pass |
| P | named | `P:<theory>:<lemma>:no:<premise>` | `<lemma>_no_<premise>` | `unbind_maybe_notification_not_bound_no_valid_objs` |
| P | wp | `P-wp:...` | 罕见 | — |
| any | block | `<slot>-block:<theory>:<lemma>:...` | `<lemma>_<descriptive-suffix>` | helper 命名 |

### 3.2 Pipeline 步骤

跟 standard_pipeline 大致相同；slot-aware 改动落在 [1] 和 [2]：

```
execute_additive
  ├─ [0] candidate validation
  │     - check ledger state (允许 retry, 禁 applied/audited)
  │     - check delivery 必答字段
  │     - per-slot 静态前置检查（见 §3.3）
  │
  ├─ [1] patch generation (slot-specific)
  │     - F-slot: 沿用 execute_G 的 spec_op_args + dynamic tactic
  │     - Q-slot: 用 --delivery-target 指定的源 lemma 作为 by-rule input
  │     - P-slot: 拷原 L 的 proof body + 删掉指定 conjunct
  │
  ├─ [2] [wp] registration policy
  │     - F + wp: 加 [wp]
  │     - Q/P + wp: 显示 advisory 警告 + decision.md 必填 "no wp regression" 段
  │     - named/block: 不加 [wp]
  │
  ├─ [3] snapshot + baseline (沿用 standard_pipeline)
  ├─ [4] trial (check-theory.sh --patch)
  ├─ [5] impact verdict (spec_impact.py)
  ├─ [6] apply + audit dir
  └─ [7] delivery 后续记账
        - delivery=named: 在 ledger 留 delivery_pending 状态
        - delivery=block: 把 delivery_target 中每个下层 candidate
                          标记为 has_unmet_dependency 直到 building block landed
```

### 3.3 per-slot 静态前置检查

**F-slot**：完全沿用 [pattern-G-automation-pipeline.md §3](pattern-G-automation-pipeline.md#3-detector-层--spec_frame_gappy)。4 道 gate（direct / crunch / dmo / dxo）通过 → 可执行。

**Q-slot**：

- redirect-shape detector：检查 L 的 proof body 是否包含 `hoare_strengthen_post*` / `rule <stronger-lemma>, simp add: <weakening-bridge>` 形式 — 暗示存在 Q_strong 可被直接调出。命中即 candidate hint。
- 配套 `--delivery-target` 必须指向一个能直接闭合 L_new 的 rule（通常就是 redirect 内部那条 stronger-lemma），否则 patch 起草阶段就 reject。
- 不做语义判定 — Q_strong 是否真严格强于 Q_weak 是作者职责（在 decision.md 写出蕴含路径）。

**P-slot**：

- 静态发现：grep L 的 proof body，检查指定 premise name 是否出现在显式 tactic 中（`rule <X>_E` / `simp add: <X>_def` / `frule <X>D` 等已知载体）→ 若出现 = HARD_LB，直接 reject。
- 未显式出现 = 进入 trial 阶段，由 trial 判定 wp 链是否隐式依赖。
- 通过的候选才执行。

### 3.4 Ledger schema 扩展

```jsonc
{
  "ts": "2026-06-XX...",
  "key": "Q:Ipc_AI:lsfco_cte_at:real_cte_at",
  "event": "discovered" | "applied" | ... ,
  "slot": "Q",
  "delivery": "named",
  "delivery_target": ["lsfco_cte_wp_at_univ-variants", ...],
  "delivery_state": "pending" | "realized" | "orphan",
  // 其它原有字段...
}
```

`delivery_state` 转移：

- `pending`：L_new applied 但 delivery_target 中无 consumer 已 landed
- `realized`：至少一个 delivery_target landed 且 verifier 通过
- `orphan`：N 个月（如 6 个月）无 consumer landing 且无下层 candidate 发起 → 标记为孤儿，纳入 garbage-collection 候选

---

## 4. 历史实验在新框架下的归属

把 phase-1 应用过的实验重新归类到 (slot, delivery) 坐标系：

| Slot | Delivery | 实验 | 数量 |
|---|---|---|---:|
| F | wp | 0014-0017, 0019-0027, 0030-0031, 0034-0053（不含 0028/0029） | ~35 |
| F-helper (=C-block) | wp | 0032 中的 `do_machine_op_*` × 3 + `as_user_*` × 3 + `thread_set_*` × 3 | 9 |
| P | named (隐式) | 0023（实际走 modify 路径，但概念上是 P-slot + named delivery，老 L 自身作为 backward-compat alias） | 1 |
| Q | named | **0054 PoC** — `lsfco_real_cte_at` 不打 [wp] | 1 |
| **failed** F | wp | 0028（dmo gate 之前的 FP）、0030 中被 dropped 的 2 个 dxo 候选 | 2 |
| **failed** Q | named (cascade) | 0029 modify 路径（cascade 失败；additive 路径 = 0054 通过） | 1 |
| **failed** P | iterative (witness 命名冲突) | 0026 | 1 |

**统计**：phase-1 已 applied 的 ~46 个 lemma 全部能放入 (slot, delivery) 二维空间。失败案例也归位准确。这个二维框架是真正覆盖了已有工作的。

---

## 5. 实施路线（3 步）

### Step 1（≤ 50 行代码）— 重命名 + 兼容层

```bash
cmd_execute() {
  ...
  # 新 dispatch
  case "$slot" in
    F) execute_additive_F "$key" "$expid" ... ;;   # 内部就是当前 execute_G
    Q) execute_additive_Q "$key" "$expid" ... ;;   # 后续实现
    P) execute_additive_P "$key" "$expid" ... ;;   # 后续实现
  esac
}

# 兼容：老 `--candidate G:...` 自动 dispatch 到 execute_additive_F
# 老 `--pattern G --patch <p>` 自动 dispatch 到 execute_additive_F 走 custom-patch 子路径
```

**验收**：现有 35+ 个 F-slot 实验全部能通过新入口 retry 通过（用 0035 / 0050 / 0054 三个不同时期的 retry 各验证一遍）。

### Step 2（≈ 150 行）— 实现 Q-slot 路径

- `execute_additive_Q` 接受 `--delivery-target` 指定 source-rule
- patch 生成：copy L 的 statement，post 替换为 `<target-post>`，proof tactic 默认 `by (rule <source-rule>)`，若 target 不直接闭合则降级到 `by (rule <source-rule>, simp)` 等候选
- **验收**：[[0054]] PoC 用新路径重放，输出与现有 audit dir 完全一致

### Step 3（≈ 200 行）— 实现 P-slot 路径

- `execute_additive_P` 接受 `--drop-premise` 指定要丢的 conjunct name
- patch 生成：parse L 的 pre，按 conjunct 名删一项，proof body 原样复制
- 加 P-slot 的静态前置检查（grep proof body 看是否显式用了 premise）
- **验收**：[[0023]] 候选用新路径重放（注意：原 0023 走的是 modify + named delivery，重放时改成 additive + named delivery，新 lemma 名 `unbind_maybe_notification_not_bound_no_valid_objs`，原 L 不动 → 期望 trial 通过且 in-file 消费者不影响）

每步独立可 commit + retro 验证 + decision.md 留底。三步落定后，phase-2 工程基础完工。

---

## 6. 风险与开放问题

### 6.1 wp 数据库污染

F-slot + wp 在 phase-1 验证了 35+ 个无问题，但累积到 100+ 后 wp 行为可能漂移。需要定期跑全 AInvs 的回归 wall 测试。

mitigation：

- F-slot 默认 wp，可接受 — wall 总体下降是已观察事实
- Q-slot / P-slot + wp 走 advisory：每条都需 decision.md 显式论证 + 单独跑下游 file 的 wall 回归测试

### 6.2 孤儿 lemma

named / block delivery 一旦 consumer / 下层 candidate 长期不接，L_new 沦为孤儿（占空间不提供价值）。

mitigation：

- ledger 跟踪 `delivery_state`
- 周期性 review：6 个月内未实现 delivery → 标记 `orphan`，纳入 garbage-collection 候选
- garbage collection 不直接删（lemma 一旦在 db 里就有作者把关过它的存在），而是聚集汇报，给作者机会接 delivery 或主动撤回

### 6.3 detector 覆盖度

F-slot 的 detector 已经成熟（4 道 gate）。Q-slot 的 redirect-shape detector 命中率不可知；P-slot 的"premise 未显式出现"detector 在 phase-1 命中率仅 3.7%。

mitigation：

- 不强求 detector 高命中率
- 接 LLM agent：让 sonnet 阅读 L 的 statement + proof body，提议 Q_strong 或 drop conjunct，由 execute_additive 走 trial 验证
- detector + LLM 互补：detector 给"大概率值得探的方向"，LLM 给具体 candidate statement

### 6.4 building block 链路深度

C 机制的 helper 嵌套若过深（A 是 B 的 helper，B 是 C 的 helper，C 才接叶子 proof），整体工作链复杂度暴增。

mitigation：

- 限制最大 chain length（如 ≤ 3 层）
- chain 内每一层入 ledger 时都要 declare 下一层 candidate keys，ledger 显式跟踪整条链
- chain 头（最叶子的 building block）和尾（最接近 consumer 的 lemma）必须同 PR 提交

### 6.5 P-slot + Q-slot 复合

理论上一条 L_new 可同时弱化 pre + 强化 post。本设计目前把它们当两个独立 candidate 处理。复合形式是否值得单列槽位？

留作开放问题。phase-2 实施 P / Q 后回头看是否有真实复合 candidate 浮出。若有 ≥ 3 个 → 加 PQ 复合 slot；否则继续按双 candidate 处理。

---

## 7. 设计与 phase-1 框架的关系

| Phase-1 概念 | 本设计的对应 |
|---|---|
| Pattern G | F-slot |
| Pattern C (modify) | P-slot + named delivery（老 L 自身作为 alias）|
| Pattern A (modify) | Q-slot + named delivery（老 L 自身作为 alias）— [[0029]] 失败、[[0054]] additive 成功验证 |
| Pattern D (= A-exactness) | Q-slot 的一种特化 detector，不构成独立槽 |
| Pattern A 的 `_old` witness | additive 形式下不需要 — 原 L 不动 |
| C 的 iterative witness 冲突（[[0026]]） | additive 模式天然避免（每次加新 lemma，名字独立）|

设计完成度评估：

- F-slot：在 phase-1 已经 100% 实现（执行 + detector + 失败 fallback 全套）。execute_additive 实施时直接复用。
- Q-slot：execute 部分有 PoC 模板（[[0054]]），detector 端有 redirect-shape heuristic（spec_candidates.py），需做 Step 2 工程化。
- P-slot：execute 部分原 C-modify 路径有 spec_strengthen_c_patchgen.py，移植到 additive 形式较直接；detector 端是 spec_candidates.py 的 unused-premise 启发。需做 Step 3 工程化。

---

## 8. 一句话总结

**execute_additive = "找 P/Q/F 三种空槽 → 必须答好下游机制 (wp / named / block) → trial 验证 → 加新 lemma 原 L 不动"**。

它是 phase-1 全部洞察的工程产物：

- 摆脱 pattern 字母 / 修改形状的概念框架
- 强制每条 spec 强化都有可追溯的下游路径
- 把 cascade 消化在"原 L 不动"的工程纪律里
- 把"可证明"与"值得纳入"分开 — 前者是 verifier 的事，后者是 delivery 机制的事

满足这两条约束的 spec 强化才纳入框架。其余只是 lemma 库膨胀。

---

**报告生成时间**：2026-06-10
**对应 spec-strengthen branch HEAD**：`8d190c4` (`reports(spec-strengthen): Pattern G automation pipeline reference`)
**配套读物**：[phase-1-summary.md](phase-1-summary.md)（why-论证）；[pattern-G-automation-pipeline.md](pattern-G-automation-pipeline.md)（F-slot 的 how-参考实现）
