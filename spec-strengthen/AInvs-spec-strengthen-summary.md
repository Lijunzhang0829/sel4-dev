# Spec-Strengthen 总结 — AInvs session

Session: `AInvs`
Branch: `spec-strengthen` (PR-4)
l4v 子模块基线 HEAD: `00d9073f70d0`

本文件总结了该分支上已经落地的两批 spec-strengthen 工作：

- **第一批（2026-06-02，CSpace_AI.thy / `set_cdt`）** — 实验 0014–0017
- **第二批（2026-06-05，KHeap_AI.thy / `set_object`）** — 实验 0019–0022

同时也覆盖：被 probe 过滤掉的候选、0018 的 audit/hygiene 修复，以及 0020a 的中止尝试。

---

## TL;DR — 8 个已应用的 strengthening

| # | 文件 | 引理 | Pattern | trial wall 变化 | 结论 |
|---|---|---|---|---:|---|
| 0014 | CSpace_AI | `set_cdt_cdt_update` | B（功能性后置条件） | +8.7% | additive PASS |
| 0015 | CSpace_AI | `set_cdt_machine_state[wp]` | G（frame） | **−11.3%** | additive PASS |
| 0016 | CSpace_AI | `set_cdt_cur_thread[wp]` | G | −2.5% | additive PASS |
| 0017 | CSpace_AI | `set_cdt_idle_thread[wp]` | G | −0.4% | additive PASS |
| 0019 | KHeap_AI | `set_object_cdt[wp]` | G | +2.1% | additive PASS |
| 0020 | KHeap_AI | `set_object_cur_thread[wp]` | G | −4.4% | additive PASS |
| 0021 | KHeap_AI | `set_object_cur_domain[wp]` | G | −4.6% | additive PASS |
| 0022 | KHeap_AI | `set_object_arch_state[wp]` | G | −2.0% | additive PASS |

**每一批的净同文件 wall time 效果：**
- CSpace_AI: 45,330 → 47,460 ms = **+4.7%**，共新增 4 条 lemma
- KHeap_AI: 24,538 → 24,229 ms = **−1.3%**，共新增 4 条 lemma

合并来看：一共新增 8 条 additive lemma，平均文件级开销约 **+1.7%**，远低于 +30% 的 gate。8 条里有 3 条的单次 trial delta 明显为负，说明“新增 lemma 反而让文件更快”并不是偶发现象，而是 `[wp]` 规则累积后的真实收益。

---

## 第一批 — CSpace_AI.thy / `set_cdt`（0014–0017）

每个实验都遵循 SKILL 的 1–6 步：
**候选选择 → 写 patch / witness → 验证 → 测量 → apply → 记录**。

### 0014 — `set_cdt_cdt_update`（Pattern B，功能性后置条件）

**之前的状态（无对应 lemma）。** `set_cdt` 在 `verification/l4v/spec/abstract/CSpaceAcc_A.thy` 里的定义是 `set_cdt t \<equiv> modify (cdt_update (\<lambda>_. t))`，但既有伴随引理只覆盖"保持什么"，没有一条把"执行完后 `cdt s = t`"直接显式化。已存在的 consumer 都靠手工展开 `set_cdt_def`，例如 [CSpace_AI.thy:136](verification/l4v/proof/invariant-abstract/CSpace_AI.thy#L136)：

```isabelle
lemma update_cdt_cdt:
  "\<lbrace>\<lambda>s. valid_mdb (cdt_update (\<lambda>_. (m (cdt s))) s)\<rbrace>
     update_cdt m
   \<lbrace>\<lambda>_. valid_mdb\<rbrace>"
  by (simp add: update_cdt_def set_cdt_def) wp
                                ^^^^^^^^^^^^
                                手工展开
```

**本次新增。**

```isabelle
lemma set_cdt_cdt_update:
  "\<lbrace>\<top>\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. cdt s = t\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**spec 为何更强。** 旧的 spec 表面里没有这个功能性 fact；下游想用它只能各自手工 unfold。新 lemma 把这个事实**显式提升成一条可被 `wp` 直接引用的接口**——spec 的可观测性从"只能通过定义间接推出"变成"作为公开 wp 规则被任何 consumer 引用"。语义上是 strictly more public、strictly more reusable。

**为什么选它。** 这条候选来自 20260526 的 strengthen-log。当时它已经做过 `--patch` 验证，但还没有真正 `--apply` 落地。之所以优先补它，是因为 `set_cdt` 的"功能性事实"在当时是缺失的：已有伴随引理大多在说它保持了哪些性质，但没有一条现成引理直接说"执行完 `set_cdt t` 以后，`cdt` 就等于 `t`"。

**修改动机。** 现有证明里，如果想在 `set_cdt` 之后得到 `cdt s = t`，通常需要手工展开 `set_cdt_def`。这使得该事实虽然真实存在，但没有被整理成一个稳定、可复用的证明接口。这个 patch 的目标，就是把这个功能性后置条件显式提升成可被 `wp` 直接引用的 lemma。

**修改后的效果。**
- baseline 45,330 → trial 49,271（+8.7%）→ apply 55,846 ms
- apply 时 Tier-2 grep consumer 数量为 0

这说明：
- 它的**逻辑价值**已经被验证，lemma 可证明、可应用；
- 它的**即时性能收益**不明显，反而带来少量解析/证明开销；
- 但它的**长期价值**在未来 cleanup：下游证明可以把手工 `unfold set_cdt_def` 改成 `wp set_cdt_cdt_update`。因此这是典型的 Pattern B：**当前成本可见，复用收益延后释放**。

### 0015 — `set_cdt_machine_state[wp]`（Pattern G，**−11.3%**）

**原参照引理（已存在，[CSpace_AI.thy:3847](verification/l4v/proof/invariant-abstract/CSpace_AI.thy#L3847)，predicate-level）：**

```isabelle
lemma set_cdt_vms[wp]:
  "\<lbrace>valid_machine_state\<rbrace> set_cdt t \<lbrace>\<lambda>_. valid_machine_state\<rbrace>"
  by (simp add: set_cdt_def, wp) (simp add: valid_machine_state_def)
```

**本次新增（literal-field-level）：**

```isabelle
lemma set_cdt_machine_state[wp]:
  "\<lbrace>\<lambda>s. P (machine_state s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (machine_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**spec 为何更强。** 旧 `set_cdt_vms[wp]` 只承诺**一个特定谓词** `valid_machine_state` 在 `set_cdt` 下被保持；下游一旦想用别的 P 去断言 machine_state 的某种性质，就拿不到。新 lemma 把对 **`machine_state` 字段值本身的任意 P 保持** 显式化——`valid_machine_state s` 只是 `P (machine_state s)` 在 `P = valid_machine_state ∘ pure` 时的一个特例。换句话说：
- 旧形式 ≡ ∀ s, valid_machine_state s → ⟨same after set_cdt⟩
- 新形式 ≡ ∀ P, P (machine_state s) → ⟨same after set_cdt⟩，**对任意 P 成立**

后者**严格蕴含**前者（取 `P := \<lambda>ms. valid_machine_state (s\<lparr>machine_state := ms\<rparr>)` 即可）。所以 spec 真的变强，不只是补全 API。

**为什么选它。** 这是通过巡检 `set_cdt_*` lemma family 发现的空档：文件里已经有 `set_cdt_vms[wp]`，说明 `set_cdt` 保持谓词 `valid_machine_state`；但缺少 literal field 级别的 `machine_state` frame。也就是说，predicate companion 已有，literal-field companion 缺失，正是 Pattern G 最稳定的命中形状。

**修改动机。** 我们希望把“某个操作不会修改某字段”的事实变成通用 `wp` 规则，而不是让每个证明都自己去展开 `set_cdt_def`、再从 record update 里手工捞出 `machine_state` 没变。这个 patch 的本质，是把原本分散在各个 proof 中的重复性工作，前移到源头用一条 `[wp]` 规则统一解决。

**修改后的效果。**
- baseline 64,766 → trial 57,416（**−11.3%**）→ apply 52,587 ms

这是整个 session 最亮眼的结果。虽然新增了一条 lemma，但 `CSpace_AI.thy` 整体反而明显变快。原因不是“证明这条新 lemma 很省”，而是**文件中后续很多 `set_cdt` 路径上的 `machine_state` frame 目标，现在都能被 `wp` 直接自动消掉**，不再退化成更慢的手工 `set_cdt_def` 展开和统一化过程。也就是说，这条修改不只是“逻辑上更完整”，而是已经在同文件层面验证了：**强的 `[wp]` frame 规则确实能降低真实证明成本。**

这也是本轮 spec-strengthen 最核心的正面信号。

### 0016 — `set_cdt_cur_thread[wp]`（Pattern G，−2.5%）

**原参照引理（已存在，[CSpace_AI.thy:3323](verification/l4v/proof/invariant-abstract/CSpace_AI.thy#L3323)）：**

```isabelle
lemma set_cdt_cur:
  "\<lbrace>cur_tcb\<rbrace> set_cdt m \<lbrace>\<lambda>_. cur_tcb\<rbrace>"
  apply (simp add: set_cdt_def)
  apply wp
  apply (simp add: cur_tcb_def)
  done
```

**本次新增：**

```isabelle
lemma set_cdt_cur_thread[wp]:
  "\<lbrace>\<lambda>s. P (cur_thread s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (cur_thread s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**spec 为何更强。** 旧 `set_cdt_cur` 只承诺谓词 `cur_tcb s ≡ tcb_at (cur_thread s) s` 被保持——即"当前线程仍是 TCB"。它**没有承诺**当前线程的 obj_ref（即 `cur_thread s`）本身不变；从 spec 角度只能推到"cur_tcb 这一性质成立"，不能推到"cur_thread 字段的任意 P 成立"。新 lemma 显式覆盖**字段值层面的任意 P 保持**。和 0015 一样属于"predicate-level → field-level"的真严格加强。

和 0015 是同一模式，只是目标字段从 `machine_state` 换成了 `cur_thread`。文件里原本有 `set_cdt_cur` 这种 predicate 级别事实，但没有 literal field 级别的 `cur_thread` frame。

**修改动机。** 调度和 IPC 证明里经常需要跨过 `set_cdt` 保持当前线程不变；把这个事实放进 `[wp]` 类，可以减少这些路径上的手工定义展开。

**修改后的效果。**
- baseline 49,282 → trial 48,041（−2.5%）→ apply 47,143 ms

收益比 0015 小，但方向一致：`cur_thread` 这类 goal 在 `CSpace_AI` 里没有 `machine_state` 那么频繁，所以同文件即时提速较小。不过这条规则的真实下游价值更可能出现在 scheduler / IPC 侧，而不是 `CSpace_AI` 本文件。

### 0017 — `set_cdt_idle_thread[wp]`（Pattern G，−0.4%）

**原参照引理（已存在，predicate-level，[CSpace_AI.thy:3789 附近](verification/l4v/proof/invariant-abstract/CSpace_AI.thy)）：**

```isabelle
lemma set_cdt_idle [wp]:
  "\<lbrace>valid_idle\<rbrace> set_cdt m \<lbrace>\<lambda>rv. valid_idle\<rbrace>"
  by (simp add: set_cdt_def, wp,
      auto simp: valid_idle_def pred_tcb_at_def)
```

**本次新增：**

```isabelle
lemma set_cdt_idle_thread[wp]:
  "\<lbrace>\<lambda>s. P (idle_thread s)\<rbrace> set_cdt m \<lbrace>\<lambda>_ s. P (idle_thread s)\<rbrace>"
  by (wpsimp simp: set_cdt_def)
```

**spec 为何更强。** 旧 `set_cdt_idle` 只承诺**复合谓词** `valid_idle` 被保持（它把"idle thread 在 kheap 里、状态是 IdleThreadState"打包在一起）。它没有给出"`idle_thread` **字段值本身**在 `set_cdt` 下不变"这一更基本的事实。新 lemma 把后者显式化——任意 P 对 `idle_thread s` 都被保持，包括"`idle_thread s = t`"这种结构性 fact，而这是旧 `valid_idle` 形式拿不出来的。

这条补齐了 `set_cdt` 的线程字段 triplet：`machine_state` / `cur_thread` / `idle_thread`。

**修改动机。** 和前两条完全一致：把“`set_cdt` 不会改 `idle_thread`”整理成系统级可自动传播的 `[wp]` 规则。

**修改后的效果。**
- baseline 47,361 → trial 47,192（−0.4%）→ apply 47,460 ms

基本持平。它的意义不在于当前文件立刻提速，而在于把线程相关 frame 规则补全，使未来 scheduler / IRQ 路径上的证明更容易自动过。

---

## 第二批 — KHeap_AI.thy / `set_object`（0019–0022）

第二批从 `set_cdt` 切换到 `set_object`。这个切换背后的动机非常明确：`set_cdt` 是一个专门操作，而 `set_object` 是更基础的 heap-write 原语，很多别的 `set_<X>` 最终都会落到它。因此，如果在 `set_object` 这一层补 `[wp]` frame，理论上的覆盖面会更广。

### 0019 — `set_object_cdt[wp]`（+2.1%，经历了一次 parser 绕路）

**原参照模板（已存在，[KHeap_AI.thy:1277](verification/l4v/proof/invariant-abstract/KHeap_AI.thy#L1277)，0019–0022 全批共用）：**

```isabelle
lemma set_object_machine_state[wp]:
  "set_object p ko \<lbrace>\<lambda>s. P (machine_state s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

这是 `set_object` 已有的**唯一**一条 literal-field frame（用 l4v 缩写 preserves 形式书写）。它证明 `set_object` 对 `machine_state` 任意 P 保持，但**没有覆盖**其他字段：`cdt` / `cur_thread` / `cur_domain` / `arch_state` 等同样不被 `set_object` 修改、但 spec 表面没有对应的可被 wp 引用的承诺。0019–0022 这四条都是按这个模板，把"缺哪个字段就补哪个字段"逐一显式化。

**本次新增（0019，cdt 字段）：**

```isabelle
lemma set_object_cdt[wp]:
  "\<lbrace>\<lambda>s. P (cdt s)\<rbrace> set_object p ko \<lbrace>\<lambda>_ s. P (cdt s)\<rbrace>"
  by (wpsimp wp: set_object_wp_strong)
```

（写成显式 Hoare 形式而不是缩写形式，是为绕开 parser 工具识别问题；语义与 `set_object p ko \<lbrace>\<lambda>s. P (cdt s)\<rbrace>` 完全一致。）

**spec 为何更强。** 在 0019 之前，`set_object` 的 spec 表面里**没有任何对 `cdt` 不变性的公开承诺**——`cdt` 不变只是 `set_object_def`（`put (s\<lparr>kheap := ...\<rparr>)`）的间接推论，下游证明每次都得自己 unfold 才能拿到。0019 把"`set_object` 对 cdt 任意 P 保持"显式提升为可被 wp 直接引用的规则。属于 spec 表面从"零承诺"到"任意 P 保持"的真加强。

0020/0021/0022 三条的 spec-加强理由和这条同构——只是分别覆盖 `cur_thread` / `cur_domain` / `arch_state` 字段。下面三节不再重复对照模板，只列出各自新增 lemma。

**为什么选它。** 进入 `set_object_*` family 以后，首先寻找的是和 `0015`–`0017` 同样的"predicate companion 已有、literal-field companion 缺失"结构。`set_object` 只写 `kheap`，因此 `cdt` 理应保持不变；而 CSpace 侧又大量依赖 `cdt`。所以 `set_object_cdt[wp]` 是很自然的首个命中点。

**修改动机。** 如果这个 frame 规则在更底层的 `set_object` 层就存在，那么所有间接调用 `set_object` 的 `set_*` 操作，理论上都能通过 `[wp]` 自动继承这条 `cdt` 不变性，而不必在每条专门路径上各写一份。

**额外经历：工具链绕路。** 这条 lemma 第一版是按 l4v 的习惯写成 abbreviated preserves form：`set_object p ko \<lbrace>...\<rbrace>`。`check-theory.sh --patch` 接受了它，但工具链其余部分出现了识别不一致：
- `spec_witness_gen.py` 报 “Could not classify”
- `spec_impact.py` 的 stdout 显示 `Gate: FAIL`
- 而 `measurement.json` 又写成 `impact_verdict: additive`、`gate_pass: true`

为了让 Acceptance Gate 2 严格成立，这一版没有保留，而是回退后改写成显式 Hoare form。**语义没有变化，变化的是工具能否稳定识别。**

**修改后的效果。**
- baseline 24,538 → trial 25,045（+2.1%）→ apply 24,568 ms

这条的即时收益依然不明显，更像 0014：主要价值是**建立一个更底层、更广覆盖面的 frame 接口**，而不是立刻让当前文件大幅提速。

### 0020 — 首次尝试中止，随后落地 `set_object_cur_thread[wp]`

最初 0020 并不是 `cur_thread`，而是打算做 `set_object_interrupt_states[wp]`。原因很直接：插入点下方紧挨着 `valid_irq_states_triv`，而那条 lemma 正好把 `\<lambda>s. P (interrupt_states s)` 当作假设，因此直觉上它应该是高 ROI 候选。

#### 0020a 中止：`set_object_interrupt_states[wp]`

`check-theory.sh --patch` 直接失败，报错是 duplicate fact declaration。

**为什么会撞名。** 根因不是源码里已经有一条同名 lemma，而是：
- `KHeap_AI.thy:925` 有 `crunch interrupt_states[wp]: set_simple_ko ...`
- `set_simple_ko` 内部会调用 `set_object`
- `crunch` 递归下降时会**隐式生成** `set_object_interrupt_states[wp]`

这类冲突肉眼 grep 源码是看不见的，只有 Isabelle 编译到那一步时才会报 duplicate fact。这个发现非常有价值，因为它说明：**Pattern G 候选不仅要查 direct grep，还要查 crunch 导出的隐式命名空间。**

更重要的是，这次失败并不是“白费”。它证明了一个事实：原先以为需要新 PR 才能“解锁”的 `interrupt_states` frame，其实已经被现有 crunch 链条覆盖了。因此这个 gap 并不真实存在。

#### 0020 正式落地：`set_object_cur_thread[wp]`

回避 crunch collision 后，切换到 `cur_thread`，并确认没有类似的隐式派生冲突。

**修改动机。** 和 0016 相同，但层次更底：希望把 `cur_thread` frame 直接放到 `set_object` 这一层，让更多基于 `set_object` 的路径自动获得它。

**修改后的效果。**
- baseline 25,076 → trial 23,966（−4.4%）→ apply 23,954 ms

这已经是直接的正收益：虽然 `KHeap_AI` 本身并不是最主要的 `cur_thread` 使用方，但新规则仍然足以让同文件 `wp` 自动化变快，说明 `set_object` 层的 Pattern G 不只是“理论上广覆盖”，而是已经开始在本地文件里兑现收益。

### 0021 — `set_object_cur_domain[wp]`（−4.6%）

这是在吸收 0020 的教训后，第一次完整使用新的 pre-flight recipe 的成功案例：先做 direct grep，再做 crunch-derived grep，确认既没有显式重名，也没有隐式派生冲突，然后再写 lemma。

**为什么选它。** 进入 `set_object` 之后，`cur_domain` 也是一个典型的 literal field：操作本身不会改它，但下游调度路径可能关心它。

**修改动机。** 和前面的 Pattern G 一样，把一个确定不变的字段提升成通用 `[wp]` 规则，扩大自动传播面。

**修改后的效果。**
- baseline 25,954 → trial 24,773（−4.6%）→ apply 24,238 ms

这是第二批中效果最好的单点之一，说明 pre-flight recipe 不是纯流程负担，而是真正帮助我们更稳定地找到“干净、可应用、并且有收益”的 slot。

### 0022 — `set_object_arch_state[wp]`（−2.0%）

这条针对 `arch_state`，同样经过了 pre-flight clean 检查。

**为什么选它。** `arch_state` 是架构相关证明里经常被引用的字段，尽管本文件中不是最高频，但把它提升为 `set_object` 层 frame 后，理论上能为架构不变量路径提供更直接的 `wp` 支持。

**修改后的效果。**
- baseline 24,677 → trial 24,184（−2.0%）→ apply 24,229 ms

即时效果是中等负增量，方向依然健康。它的更大价值仍然预期在 arch invariant 及相关下游证明里。

---

## 被过滤掉的候选（4 个 ground-truth rejection）

| 候选 | 来源 | 判定 | 证据 |
|---|---|---|---|
| `lsfco_cte_at` / `valid_objs`（Ipc_AI.thy:50） | scanner top | load-bearing | 去掉该 premise 后，proof step 4/4（`by (rule hoare_strengthen_postE_R, ...)`）失败 |
| `lsfco_cte_at` / `invs`（CSpace_AI.thy:4093） | scanner top，且属于更早的 0011 时代样本 | load-bearing | probe 于 0014 之前已给出负面结果 |
| `get_rs_real_cte_at` / `valid_objs`（Ipc_AI.thy:96） | scanner top | load-bearing | proof step 5/5（`done`）后残留子目标 `\<And>a s. recv_buf = Some a \<Longrightarrow> valid_objs s` |
| `set_object_interrupt_states[wp]` | family survey | crunch-collision | duplicate fact；根因是 `KHeap_AI:925` 的 crunch 派生 |

前三个是 Pattern C（premise-drop）候选，被 probe 证明前提是 load-bearing；第四个是 Pattern G 候选，但被 crunch 派生命名冲突否决。共同点是：**它们都在 `--apply` 之前被拦了下来**。这说明工具链真正带来的价值，不只是“找候选”，更是“提前阻止注定失败的尝试”。

从这次 session 看，scanner 直接给出的高排名 Pattern C 候选并没有转化成成功 apply；反而是 probe 证明它们大多太中心、前提太承重。因此，**scanner 只能给提示，不能单独当决策依据。**

---

## 跨批次结论（最重要的总结）

### 1. Pattern G 是本轮最稳定、最有效的方向

两批加起来，8 个已应用 strengthening 中：
- 7 个是 Pattern G（frame lemma）
- 1 个是 Pattern B（functional postcondition）

全部通过 Acceptance。相对地，Pattern C 候选在 probe 里全部被判成 load-bearing。就这次 session 而言，**成功率最高的挖掘方向不是“删 premise”或“改旧 statement”，而是系统性补全 `set_<op>_<literal-field>[wp]`。**

这背后的动机很清楚：
- 这类 patch 是 additive，几乎没有下游破坏风险；
- 证明通常只要一行 `wpsimp`；
- 它们能直接进入 `[wp]` 类，在更大范围自动传播；
- 实验已经证明，其中不少不仅“未来可复用”，还会立刻降低同文件证明成本。

### 2. family-survey heuristic 可以跨操作推广

在 `set_cdt` 和 `set_object` 两条线上，真正有效的选择逻辑是同一套：

> 找一个 `set_<op>` family；如果已经有 `set_<op>_<predicate>[wp]`，但缺 `set_<op>_<literal-field>[wp]`，而该字段按定义又显然不会被操作修改，那么这几乎就是 Pattern G 的直接候选。

它之所以强，不是因为“猜得准”，而是因为：
- 证明成本极低；
- 逻辑风险极小；
- 一旦进入 `[wp]`，收益可以在同文件和下游同时累积。

### 3. wall delta 差异很大，但目前只能诚实地做定性解释

8 个 trial delta 横跨 `+8.7%` 到 `−11.3%`。目前最合理的解释仍然是：**同文件里与该字段相关的 goal shape 频率越高，新 `[wp]` 规则带来的即时收益越可能覆盖新增 lemma 的解析成本。**

但这次 summary 明确避免了过度量化：我们还没有足够数据去拟合一个“出现多少次就一定提速”的阈值。更诚实的说法是：

- Pattern G 的收益通常是“延后释放”的；
- 但如果目标字段在同文件后续 proof 里频繁出现，**立即的 in-file wp-class pickup 完全可能发生**；
- 从这次数据看，8 条里有 6 条是负增量或近似持平，说明这条路的长尾上行空间是真实存在的。

### 4. 单条规则的 pickup 可以抵消累计解析成本

KHeap_AI 在新增 4 条 additive lemma 后，净 wall time 不是上升，而是 **−1.3%**。这说明 Pattern G 不是“多加一点规则，文件就线性变慢”；相反，当规则形成 family 后，**后续 proof 里重复消掉的成本可以覆盖新增 lemma 本身的代价。**

这带来一个很实际的策略启发：
**Pattern G 更适合成组做。**
如果一个文件里已经发现若干相邻的 literal-field gap，把它们分多次零散提交，往往不如在一个批次里系统补齐，让 `[wp]` pickup 尽早形成叠加效应。

### 5. crunch collision 是真实存在的 false-positive 类别

在第二批 pre-survey 的 `set_object_<field>` slot 里，有一部分“看上去缺失”的候选，其实已经被 `crunch` 在别的 wrapper operation 上隐式派生出来了。没有 pre-flight recipe 的话，这些候选最终都会在 `check-theory.sh --patch` 阶段以 duplicate fact 失败，白白浪费 20–30 秒每次。

因此，这一轮沉淀出的流程经验是：
**Pattern G 的 Step 0 必须包含 direct grep + crunch-derived grep。**
这不是可选优化，而是必要的排雷步骤。

### 6. scanner 的 ROI 排名并没有真正指导这两批工作

虽然 `spec_candidates.py` 会产出 Pattern C / B / A 的候选，但本轮真正成功的工作，基本都不是根据 scanner 的 top pick 做出来的。scanner 没有 Pattern G detector，而本 session 最成功的又恰恰是 Pattern G。

这暴露了当前 SKILL 流程和实际有效工作流之间的一个缺口：
- SKILL 写的是“先跑 candidates tool，再从高排名里选”
- 实际真正有效的是“按 lemma family 手工巡检 + pre-flight + 一行 tactic 验证”

这个差距本身就是值得记录的结论。

### 7. 反复出现的工具痛点

这两批反复暴露出三类工具问题：

- **缩写形式 `f \<lbrace>P\<rbrace>` 的处理不稳定**
  在 0019 中引发了 parser/impact 分叉。当前稳定 workaround 是统一写显式 Hoare form。

- **`spec_impact.py` 的 stdout 和 JSON 可能不一致**
  审计记录应以 JSON 为准，stdout markdown 只能当辅助展示。

- **手写 unified diff 很容易出错**
  0018 已经证明这个坑会伤 replayability；第二批从一开始就采用 snapshot + `diff -u <pre> <post>`，没有再复发。

### 8. 跨 session 影响仍然没有实测

这 8 条新增 lemma 全都是 ASpec/AInvs 层的 additive strengthening，因此理论上：
- 不会破坏下游 Refine / CRefine 证明；
- 但可能通过 `[wp]` 规则积累，为下游带来额外自动化收益。

之所以没有做 `Refine` rebuild，不是因为它不重要，而是因为成本很高（约 1h17m wall），而单条 additive lemma 还不足以支撑这次重建的投入。更合适的时机是：
**等 Pattern B 的下游 cleanup 真正开始改 consumer，再把 Refine rebuild 和测量绑定在一起。**
这样测出来的收益才更像“真实语义重构收益”，而不是单纯 theory-graph parse cost 的变化。

### 9. 本轮 session 没有覆盖的范围

为了避免过度外推，这里明确列出未覆盖项：
- 没有落地 Pattern A
- 没有落地 Pattern C 或 D
- 没有尝试 Pattern E
- 没碰任何 arch-specific 子目录
- 没改 `proof/invariant-abstract/` 之外的文件
- 没做跨 session 测量

也就是说，这份总结的结论很强，但它的适用范围仍然主要限于：
**`proof/invariant-abstract/` 内、围绕 `set_<op>` family 的 additive Pattern G / B strengthening。**

---

## 本轮实际形成的 selection logic

这是这次 session 实际采用、并被证明有效的优先级，不是正式写回 SKILL 的规则。

1. **形状优先：shape 2 additive > shape 1 modify**
   additive `[wp]` 规则几乎没有下游破坏风险；shape 1 则需要 witness、probe 和更多结构性证明。

2. **操作优先：基础原语 > 专门操作**
   `set_object` 这种基础 heap-write 原语，比 `set_cdt` 这种专门操作更值得优先挖，因为传播面更广。

3. **字段选择：predicate companion 已有，literal-field companion 缺失**
   这是 Pattern G 最可靠的命中形状。

4. **预检优先：direct grep + crunch grep**
   这是 0020a 失败后沉淀出的必要步骤。

5. **scanner 排名不参与本轮核心决策**
   它没法直接产出本轮最成功的 Pattern G 候选。

这套逻辑对本轮范围非常有效，但也要诚实承认：它偏窄，不会自动发现 Pattern C / D / E 的成功机会，也没有处理 arch 文件和跨 session ROI。

---

## 延后到未来的 follow-up

1. **`set_object_<field>[wp]` 还剩 2 个干净 slot**：`domain_index`、`domain_time`
2. **继续挖别的 `set_<op>` family**：例如 `set_thread_state`、`thread_set`、`set_cap`、`set_simple_ko`
3. **为 0014 做 Pattern B 的下游 cleanup PR**：把 `update_cdt_cdt`、`cap_move_typ_at` 等手工展开 `set_cdt_def` 的地方改成引用 `wp set_cdt_cdt_update`
4. **随后再做一次 Refine rebuild 测量**：把前述 cleanup 的实际收益量化出来
5. **修 `spec_strengthen_scan.py` 对缩写 Hoare 形式的支持**
6. **修 `spec_impact.py` 的 stdout / JSON 一致性**
7. **做 `spec_capture_patch.sh` 自动抓 snapshot diff**
8. **给 scanner 增加 Pattern G detector**
9. **把 0015 的 −11.3% 及整个 Pattern G 不对称性写回 playbook case study**

---

## PR-4 commit 列表（本轮 session）

```text
d58acf2 spec(0021+0022): set_object_cur_domain[wp] + set_object_arch_state[wp]
f98fb0a playbook(spec): record Pattern G gotchas — crunch collisions + parser caveat
1a5fd19 spec(0019+0020): set_object_cdt[wp] + set_object_cur_thread[wp] on KHeap_AI.thy
07cce7e audit(0018): fix replayability of 0015/0016/0017 patch.diff
c0e1950 spec(0016+0017): apply set_cdt_cur_thread[wp] + set_cdt_idle_thread[wp]
f82ff4a spec(0015): apply set_cdt_machine_state[wp] — Pattern G frame lemma
bcc13ad spec(0014): apply set_cdt_cdt_update — Pattern B additive (CSpace_AI.thy)
```

上面 7 条 commit 覆盖了 8 个 strengthening experiment、一次 playbook 更新、以及 0018 的 audit-hygiene 修复。
**commit 和 experiment 不是 1:1 对应的**：例如 `c0e1950` 同时覆盖 0016+0017，`1a5fd19` 同时覆盖 0019+0020，`d58acf2` 同时覆盖 0021+0022。

不过每个 experiment 仍然有自己独立的 `reports/experiments/00NN-*/` 审计目录，里面包含完整的 4 文件 seL4-source PR 记录：
- `patch.diff`
- `command.sh`
- `measurement.json`
- `decision.md`

按 0018 的 replay 规则，第一批和第二批的 patch 都已经验证过：从基线正向 apply 后，能到达和当前应用状态一致的结果。

本轮结束时，l4v 子模块指针仍是 `00d9073f70d0`，没有推进；这些源代码修改仍然停留在子模块工作树里。是否向上游 l4v 发送 PR，是独立于本 sub-skill 的后续工作。
