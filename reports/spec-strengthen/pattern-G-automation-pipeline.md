# Pattern G Automation Pipeline — 全流程技术参考

> 目标：让读者**仅凭这一份文档**复现 Pattern G 从扫描到落档的完整自动化流程。每一节给出概念定义、代码位置、关键算法、可执行命令。结尾配 [§9 复现配方](#9-复现配方) 和 [§10 故障排查](#10-故障排查)。
>
> 适用对象：seL4 abstract spec 强化工作者；想理解"为什么 G 能 100% 自动化"的工程师；维护这套工具的下一任。

---

## 0. 文档约定

- **路径**：所有相对路径以 `/home/lijun/seL4-docker-main/`（repo root）为基准。
- **代码引用**：`<file>:<line>` 形式（如 `spec-strengthen/run.sh:866`）。
- **命令**：在 repo root 下执行（除非另注）。
- **实验编号**：`[[0035]]` 形式指 `spec-strengthen/experiments/0035-*/`。
- **commit 引用**：`b0fc947` 形式指 spec-strengthen 分支上的 commit。

---

## 1. 系统架构

Pattern G 自动化流程是一个 6 层 pipeline。从用户视角看是 `survey → execute → audit`，从内部实现看分层如下：

```
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 6: Audit + Replay                                             │
│  spec-strengthen/experiments/<id>/                                  │
│    decision.md / measurement.json / patch.diff                      │
│    range-patch.patch.txt / command.sh                               │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 5: Impact + Apply (gate before irreversible write)            │
│  spec_impact.py --measurement-out  (gate)                           │
│  check-theory.sh --apply        (持久化到 .thy 文件)                 │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 4: Verification (ground truth for candidate truth)            │
│  check-theory.sh --patch  (trial, 不写盘)                            │
│  返回 `OK (<ms>)` 或 `FAILED (<ms>) *** <err>`                       │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 3: Executor (auto-template)                                   │
│  execute_G:                                                         │
│   - 找 anchor lemma + block-end (done | by)                          │
│   - 调 spec_op_args.py 提取 op formal args                           │
│   - 调 op_transitively_calls 检测 get_object                         │
│   - 拼 range-replace patch + 默认 tactic                             │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 2: State machine (append-only ledger)                         │
│  spec-strengthen/candidates/candidate-ledger.jsonl                  │
│  event ∈ {discovered, preflight_failed, trial_failed,               │
│           impact_failed, applied, audited, aborted}                 │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 1: Detector (static analysis)                                 │
│  spec_frame_gap.py:                                                 │
│   - 枚举 (op, field) 缺格                                            │
│   - 4 道 gate: direct / crunch / dmo / dxo                          │
│   - 输出 JSONL: clean | preflight_failed:<reason>                   │
└─────────────────────────────────────────────────────────────────────┘
```

**入口脚本**：`spec-strengthen/run.sh`。提供 5 个 subcommand：

```bash
spec-strengthen/run.sh survey  <file> [--pattern G|C|A|all] [--out <path>]
spec-strengthen/run.sh execute --candidate <key> --expid <id> [-y] [--retry]
spec-strengthen/run.sh status  [<key>]
spec-strengthen/run.sh ledger
spec-strengthen/run.sh mark-audited <key> [--expid <id>]
```

Pattern G 用 `survey` + `execute --candidate G:...`。

---

## 2. Pattern G 的概念契约

### 2.1 是什么

**Pattern G = 在 lemma database 里加一条新 frame lemma**：

```isabelle
lemma <op>_<field>[wp]:
  "\<lbrace>\<lambda>s. P (<field> s)\<rbrace> <op> <args> \<lbrace>\<lambda>_ s. P (<field> s)\<rbrace>"
  by <tactic>
```

读作："对任意谓词 P 在 `<field>` 上的取值，`<op>` 调用前后保持不变"。

### 2.2 严格强化的来源

加这条 lemma 之前，spec 没有公开承诺 `<op>` 保 `<field>`；加上之后，spec 承诺集**纯增**。新承诺集 ⊋ 旧承诺集，是 strict-strengthening 的最干净形式：

- 不改任何 existing lemma
- 不动任何 consumer
- 无 cascade 风险
- 不需要 `_old` witness

### 2.3 为什么 G 唯一可全自动

`(op, field)` 是否"保持"是一个**纯静态、纯 local** 的性质：

> "op 的 def 体（以及它传递调用到的 helper）有没有写 `<field>`？"

这个问题的答案完全在 `verification/l4v/spec/abstract/*.thy` 里。Detector 只需 parse spec/abstract 即可定。**不需要查 proof tree，不需要查 wp 数据库，不需要查 consumer 图**。

对比 C / A：

| Pattern | Truth 住址 | Detector 能否自答 |
|---|---|:-:|
| **G** | op 的 def 体 (local) | ✓ |
| C | proof body + wp 数据库 (tree-global) | ✗ |
| A | 所有 use site (tree-global) | ✗ |

详见 [phase-1-summary.md §3](phase-1-summary.md#3-为什么-g-已经实现自动化扫描而-a--c-不能)。

---

## 3. Detector 层 — `spec_frame_gap.py`

文件：`spec-strengthen/scripts/spec_frame_gap.py`

### 3.1 输入 / 输出

```bash
python3 spec-strengthen/scripts/spec_frame_gap.py <theory.thy> [--op <op>]
```

**输入**：一个 `.thy` 文件路径（通常是 `verification/l4v/proof/invariant-abstract/<X>_AI.thy`）。

**输出**：JSONL（每行一个 candidate）：

```json
{
  "key": "G:Untyped_AI:set_cdt:arch_state",
  "pattern": "G",
  "theory": "verification/l4v/proof/invariant-abstract/Untyped_AI.thy",
  "op": "set_cdt",
  "field": "arch_state",
  "anchor": "set_cdt_state_hyp_refs_of",
  "anchor_line": 2871,
  "status": "clean",
  "reason": "",
  "evidence": "mechanical",
  "tier": 1
}
```

`status` 取值：

- `clean`：通过全部 gate，可直接 execute
- `preflight_failed:direct`：lemma 名已存在于 proof tree
- `preflight_failed:crunch`：crunch 已自动派生
- `preflight_failed:dmo_path`：op 传递调 do_machine_op AND field 是 machine_state
- `preflight_failed:dxo_path`：op 传递调 do_extended_op AND field ∈ EXT_STATE_PROJECTED

### 3.2 字段枚举（STATE_FIELDS）

文件 `spec_frame_gap.py:52-63`：

```python
STATE_FIELDS = [
    "machine_state",
    "cdt", "cur_thread", "idle_thread",
    "scheduler_action", "ready_queues", "cur_domain",
    "domain_index", "domain_time",
    "arch_state", "interrupt_irq_node", "interrupt_states",
    "kheap",  # ← 总是 skip（设计上 set_<op> 必写 kheap）
    "is_original_cap",
]
```

`kheap` 在 `main()` 里硬 skip（`spec_frame_gap.py:239-243`），因为所有 `set_<op>` 都写 kheap，对它做 frame lemma 没意义。

### 3.3 Op 识别 — `find_set_op_wp_lemmas` + 3-token allowlist

文件 `spec_frame_gap.py:94-128`。

从 `.thy` 文件抓所有 `^lemma set_<name>[wp]:` 形式的 lemma，按 op 分组。

**核心算法**：默认按 `<lemma>` 的前 2 个 underscore-token 当 op（例如 `set_object_machine_state` → op=`set_object`）。但有些 op 是 3-token 的（如 `set_thread_state`），如果没认出来会被错误分到 `set_thread`。

**3-token allowlist** (`spec_frame_gap.py:83-100`)：

```python
_KNOWN_3TOKEN_OPS = frozenset({
    "set_simple_ko", "set_thread_state", "set_bound_notification",
    "set_extra_badge", "set_message_info", "set_irq_state",
    "set_object_no",
    "set_asid_pool", "set_vm_root", "set_scheduler_action",  # 后期补
})
```

如何决定补哪个：跨 AInvs grep `^lemma set_<X>_<Y>_*[wp]` 的命中数 ≥3 且 `<X>_<Y>` 在 `spec/abstract/` 里能找到对应 `definition` → 真 3-token op。详见 `e0f560a` commit。

**过滤**：`find_set_op_wp_lemmas` 末尾 `spec_frame_gap.py:127-128` 要求每个 op 在文件里有 ≥2 个 [wp] companion，否则视作"一次性 lemma"，不当 family 候选。

### 3.4 Mechanical preflight — 4 道 gate

`preflight(op, field, repo_root)` 在 `spec_frame_gap.py:171-227`。按顺序执行 4 道 gate，命中即返回，无命中则 `clean`：

#### Gate 1: Direct grep

```python
direct_hits = grep_repo(rf"\b{op}_{field}\b", [verification/l4v/])
if direct_hits:
    return ("preflight_failed:direct", f"{lemma_name} already exists ...")
```

如果 lemma 名已经在 proof tree 里出现过，加 [wp] 会撞名。

#### Gate 2: Crunch grep

```python
pattern_parts = [rf"crunch\s+{field}\b.*{w}" for w in COMMON_WRAPPERS + [op]]
crunch_hits = grep_repo("|".join(pattern_parts), [proof/invariant-abstract/])
if crunch_hits:
    return ("preflight_failed:crunch", ...)
```

`crunch <field>[wp]: <op>` 是 seL4 的元语法，会自动派生 frame lemma。如果已经被 crunch 派生了，再手加会冲突。

`COMMON_WRAPPERS` 列表 (`spec_frame_gap.py:67-75`) 涵盖 17 个常调 set_object 的 wrapper：`set_simple_ko`, `set_cap`, `set_endpoint`, `set_thread_state`, `thread_set` 等。

#### Gate 3: dmo gate（语义）

```python
if field == "machine_state":
    hit, chain = op_transitively_calls(op, "do_machine_op", repo_root)
    if hit:
        return ("preflight_failed:dmo_path", f"do_machine_op reachable via {chain}")
```

`do_machine_op X` 在 spec 层语义是 "运行 machine_state monad action X 并把结果写回 kernel state 的 `machine_state` 字段"。无论 X 干什么，**machine_state 都被替换**。所以 `\<lbrace>P (machine_state s)\<rbrace> <op> <args> \<lbrace>... P (machine_state s) ...\<rbrace>` 一定不成立 — 语义 FP。

经验来源：[[0028 set_mrs:machine_state]] 的 trial_failed。该 op 经 `store_word_offs → do_machine_op (storeWord ...)` 实际写 `machine_state.memory`。

#### Gate 4: dxo gate（语义）

```python
EXT_STATE_PROJECTED = {
    "domain_index", "domain_time", "cur_domain",
    "scheduler_action", "ready_queues",
}
if field in EXT_STATE_PROJECTED:
    hit, chain = op_transitively_calls(op, "do_extended_op", repo_root)
    if hit:
        return ("preflight_failed:dxo_path", ...)
```

`do_extended_op` 在 spec 层语义是 "把整个 `exst` 字段替换为 ext-monad 跑完的结果"。所以任何 `exst` 子字段都可能改变。

`Invariants_AI.thy:3405-3437` 给了 9 条元等式（do_extended_op 保 kheap / cdt / cur_thread / idle_thread / machine_state / interrupt_irq_node / interrupt_states / arch_state / is_original_cap），但 **EXT_STATE_PROJECTED 这 5 个字段没有**。需要 per-op lift infrastructure。本阶段未提供，故走 dxo gate 直接 skip。

经验来源：[[0030]] 抛弃 set_thread_state:domain_index / set_thread_state:domain_time 候选。

### 3.5 静态调用图 — `op_transitively_calls`

`spec_frame_gap.py:118-145`（位置见 commit `5cd9c65`）。

```python
def op_transitively_calls(op, target, repo_root, depth=2) -> (bool, str):
    """BFS 从 op 开始，最多 depth 层，看能否走到 target."""
    visited = set()
    queue = [(op, op)]  # (name, chain)
    while queue:
        name, chain = queue.pop(0)
        if name in visited or chain.count("→") > depth:
            continue
        visited.add(name)
        body = _op_body(name, repo_root)
        if body is None:
            continue
        if target in body:
            return True, chain
        if chain.count("→") < depth:
            for sub in _extract_callees(body):
                queue.append((sub, f"{chain}→{sub}"))
    return False, ""
```

辅助函数：

- `_find_op_def_block(op, repo_root)`：定位 `spec/abstract/*.thy` 中 op 的 definition block。从 `<op> ::` 行往前找 `definition` 关键词，往后取 ≤80 行或下一个 `definition / lemma / abbreviation / fun / primrec` 关键词。memoized in `_OP_BODY_CACHE`。
- `_extract_callees(body, max_n=80)`：用正则 `\b([a-z][a-z0-9_]{3,})\b` 抓 lowercase-underscore token，去掉 Isabelle 关键词 (`do`, `od`, `let`, `gets`, `modify`, ...)，最多取 80 个。

经验：早期 `max_n=20` 太紧，set_mrs body 里 `store_word_offs` 在 token 顺序第 25+ 位被截断，gate 漏抓 → 命中率 3/5 而不是 4/5。bump 到 80 后 4/4 抓到（实际后期 5/5）。

### 3.6 Retroactive validation

`5cd9c65` commit 验证了：

- **EXPECTED-FAIL**（本阶段 5 个误标 Tier 1 的失败案例）：5/5 抓到（3 dmo_path + 2 dxo_path）
- **CONTROL**（8 个已成功 applied 的 lemma）：8/8 不误报

Gate 是 **field-specific** 触发：

- `set_thread_state` 有 do_extended_op path → 但只对 EXT_STATE_PROJECTED 字段触发 dxo_path → machine_state / arch_state 通过 ✓
- `set_extra_badge` 有 do_machine_op path → 但只对 machine_state 触发 dmo_path → domain_index 等通过 ✓
- `set_mrs` 同上

**但要注意**：上面这段如果只写结论，不给样本集和命令，就只能当历史说明，不能当可复现验证。要把 detector 修改完整复现出来，必须把 regression harness 也一并跑一遍。

#### 可重跑的 regression harness

主入口脚本：`spec-strengthen/scripts/pattern_g_regression.sh`。它把样本集、detector 调用和断言都固化好了。

```bash
bash spec-strengthen/scripts/pattern_g_regression.sh
```

最小要求是把样本分成两组：

- **EXPECTED-FAIL**：加入新 gate 后，`survey --pattern G` 应该产出 `preflight_failed:*`，而不是 `clean`
- **CONTROL**：加入新 gate 后，原本已经成功 apply 的 Pattern G 候选仍应保持 `clean`

推荐样本集：

**EXPECTED-FAIL**

- `G:Ipc_AI:set_mrs:machine_state`  → 期望 `preflight_failed:dmo_path`
- `G:TcbAcc_AI:set_mrs:machine_state` → 期望 `preflight_failed:dmo_path`
- `G:Ipc_AI:set_extra_badge:machine_state` → 期望 `preflight_failed:dmo_path`
- `G:TcbAcc_AI:set_thread_state:domain_index` → 期望 `preflight_failed:dxo_path`
- `G:TcbAcc_AI:set_thread_state:domain_time` → 期望 `preflight_failed:dxo_path`

**CONTROL**

- `G:KHeap_AI:set_ep:machine_state`
- `G:KHeap_AI:set_ep:domain_index`
- `G:KHeap_AI:set_ep:domain_time`
- `G:KHeap_AI:set_ep:arch_state`
- `G:KHeap_AI:set_aobject:machine_state`
- `G:KHeap_AI:set_aobject:domain_index`
- `G:KHeap_AI:set_aobject:domain_time`
- `G:KHeap_AI:set_aobject:arch_state`

**脚本展开版（便于人工调试）**：下面是 `pattern_g_regression.sh` 内部实际做的 detector 调用；当你要加样本、改断言时再手动展开它。

```bash
cd /home/lijun/seL4-docker-main

python3 spec-strengthen/scripts/spec_frame_gap.py   verification/l4v/proof/invariant-abstract/Ipc_AI.thy   | tee /tmp/g-ipc.jsonl

python3 spec-strengthen/scripts/spec_frame_gap.py   verification/l4v/proof/invariant-abstract/TcbAcc_AI.thy   | tee /tmp/g-tcbacc.jsonl

python3 spec-strengthen/scripts/spec_frame_gap.py   verification/l4v/proof/invariant-abstract/KHeap_AI.thy   | tee /tmp/g-kheap.jsonl
```

**通过判据**：

```bash
# EXPECTED-FAIL: 必须命中对应 gate
rg 'set_mrs:machine_state.*preflight_failed:dmo_path' /tmp/g-ipc.jsonl /tmp/g-tcbacc.jsonl
rg 'set_extra_badge:machine_state.*preflight_failed:dmo_path' /tmp/g-ipc.jsonl
rg 'set_thread_state:domain_(index|time).*preflight_failed:dxo_path' /tmp/g-tcbacc.jsonl

# CONTROL: 必须仍为 clean
rg 'set_ep:(machine_state|domain_index|domain_time|arch_state).*"status": "clean"' /tmp/g-kheap.jsonl
rg 'set_aobject:(machine_state|domain_index|domain_time|arch_state).*"status": "clean"' /tmp/g-kheap.jsonl
```

如果这些断言成立，才算“detector 修改被验证”。这一步和 [§9.2](#92-单-candidate-端到端命令序列) 的 execute/apply 流程是**互补关系**：

- regression harness 验证 detector 没误报/漏报
- execute 流水线验证某个 `clean` candidate 真能自动 patch + trial + impact + apply

---

## 4. Executor 层 — `execute_G`

文件：`spec-strengthen/run.sh:866-960`

### 4.1 流程总览

```
execute_G "$key" "$expid" "$skip"
  ├─ parse key 'G:<theory_base>:<op>:<field>'
  ├─ locate theory file
  ├─ find anchor lemma (last set_<op>_*[wp] in file)
  ├─ find block-end (^\s*done$ | ^\s*by\s)
  ├─ extract op args (via spec_op_args.py)
  ├─ detect get_object → decide extra wp rule
  ├─ generate range-replace patch
  ├─ prompt user (skip if -y)
  └─ standard_pipeline (下章节)
```

### 4.2 Anchor 定位 — block-end finder

```bash
anchor_line=$(grep -nE "^lemma ${op}_[a-zA-Z_]+\[wp\]\s*:" "$theory_abs" \
              | tail -1 | cut -d: -f1)

block_end_line=$(awk -v start="$anchor_line" '
  NR>=start {
    if (/^[[:space:]]*done[[:space:]]*$/) { print NR; exit }
    if (/^[[:space:]]*by[[:space:]]/) { print NR; exit }
  }
' "$theory_abs")
```

锚定逻辑：

1. 找文件里 `^lemma <op>_*[wp]:` 形式的**最后一条** lemma（即同 op family 的最近一条）
2. 从那条开始往下找**第一行**是 `^\s*done\s*$` 或 `^\s*by\s+...` 的行 — 这是 anchor lemma 的 proof body 结尾

**重要修复（commit `767d57f`）**：早期实现只找 `^\s*by\s+` 行，遇到 `apply ... done` 形 proof 时会跳到下一条 lemma 的 `by`-shortcut，patch 插错地方。现在 `done` 和 `by` 都识别，取先出现的。

### 4.3 Op formal args — `spec_op_args.py`

文件：`spec-strengthen/scripts/spec_op_args.py`

```bash
op_args=$(python3 spec-strengthen/scripts/spec_op_args.py "$op" 2>/dev/null || echo "p ko")
```

**输入**：op 名（如 `set_cap`）。

**输出**：space-separated 形参名（如 `cap a`）。

**算法**（`spec_op_args.py:extract_op_args`）：

1. 调用 `_find_op_def_block(op, repo_root)` 取 op 的 definition block
2. **named args from equation**：用正则 `"<op>\s+(<args>)\s*\<equiv>` 取等号左边的绑定名
3. **arity from type signature**：用正则 `<op>\s*::\s*"<type>"` 取类型，调 `_type_arity` 数顶层 `\<Rightarrow>` 数量
4. **pad if needed**：如果绑定名数 < arity（pattern-match lambda 情况，如 set_cap），用 `_pad_args` 加单字母补齐

**关键 case：set_cap**

```isabelle
set_cap :: "cap \<Rightarrow> cslot_ptr \<Rightarrow> (unit,'z::state_ext) s_monad"
"set_cap cap \<equiv> \<lambda>(oref, cref). do ..."
```

- 等号绑定名：`["cap"]`（只有 1 个，第 2 个 arg 在 lambda 里被 pattern-match）
- 类型 arity：2（两个 `\<Rightarrow>`）
- pad：用 `a` 补一个 → 结果 `cap a`

**避开变量冲突**：pad 时跳过 `P`（Hoare 谓词变量）和 `s`（state 变量）。

**验证表**（12 个 op）：

```
set_object                 → ptr obj
set_cdt                    → t
set_cap                    → cap a         ← pattern-match lambda padded
set_simple_ko              → f ptr ep
set_thread_state           → ref ts
set_bound_notification     → ref ntfn
set_message_info           → thread info
set_extra_badge            → buffer badge n
set_mrs                    → thread buf msgs
set_scheduler_action       → action
set_asid_pool              → ptr pool
set_vm_root                → tcb
```

### 4.4 动态 tactic — get_object 检测

```bash
extra_wp=""
if python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/$SPEC_TOOLS')
from spec_frame_gap import op_transitively_calls
from pathlib import Path
hit, _ = op_transitively_calls('$op', 'get_object', Path('$REPO_ROOT'))
sys.exit(0 if hit else 1)
" 2>/dev/null; then
    extra_wp=" wp: get_object_wp"
fi
tactic="by (wpsimp${extra_wp} simp: ${op}_def | clarsimp)+"
```

**为什么需要**：set_cap / set_simple_ko 等 op 的 def 内部调 `get_object` 后做 case-on-result dispatch。wpsimp 默认 ruleset 处理 get_object 时留 schematic 前提 `?R x`，无法与 case-distributed 后置条件统一 → trial fail。

**修复路径**：显式 `wp: get_object_wp` 给 wp 一个非 schematic 的 get_object 处理方式。Empirically：4/4 之前 trial_failed 的候选（[[0050-0053]]）retry 都通过。

**为什么不静态写死 set_cap | set_simple_ko**：未来可能有别的 op 也走 get_object 链。用 `op_transitively_calls` 自动检测更可扩展。

**clarsimp 迭代**：`| clarsimp)+` 处理 `H ⟹ H` 形剩余 obligation（intro + assumption discharge）。对简单 op 不会被触发（wpsimp 已闭合）。

### 4.5 Patch 生成

```bash
cat > "$patch" <<EOF
${block_end_line} ${block_end_line}
${block_end_text}

lemma ${op}_${field}[wp]:
  "\\<lbrace>\\<lambda>s. P (${field} s)\\<rbrace> ${op} ${op_args} \\<lbrace>\\<lambda>_ s. P (${field} s)\\<rbrace>"
  ${tactic}
EOF
```

**Range-replace 格式**（check-theory.sh patch 协议）：

```
<start_line> <end_line>
<replacement text — possibly multiline>
---  (optional 分隔多个 block)
<start_line> <end_line>
<replacement>
```

我们的 patch 只有一个 block：**替换 anchor lemma 的 block-end 行**为"该行原内容 + 空行 + 新 lemma"。这样新 lemma 插在 anchor lemma 之后。

**实例**（[[0035]] set_cdt:arch_state 的 patch）：

```
2878 2878
  done

lemma set_cdt_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def | clarsimp)+
```

L2878 是 `set_cdt_state_hyp_refs_of` 的 `done` 行。替换后该 done 仍在，新 lemma 接在后面。

---

## 5. Verifier + Apply — `standard_pipeline`

文件：`spec-strengthen/run.sh:540-648`

### 5.1 六步流程

```bash
standard_pipeline "$key" "$pattern" "$theory_abs" "$session" "$patch" "$expid"
  ├─ [1/6] snapshot → /tmp/spec_strengthen_<expid>_pre.<pid>
  ├─ [2/6] baseline wall
  ├─ [3/6] trial wall (with patch)
  ├─ [4/6] spec_impact verdict
  ├─ [5/6] apply
  └─ [6/6] capture patch.diff + audit dir
```

### 5.2 各步细节

**[1] Snapshot**：`cp $theory_abs $snap`。用于 [6] 算 unified diff。

**[2] Baseline**：

```bash
baseline_raw=$(bash "$ISA_SCRIPTS/check-theory.sh" "$theory_abs" "$session" 2>&1 || true)
baseline_out=$(echo "$baseline_raw" | tail -1)
baseline_ms=$(echo "$baseline_out" | grep -oE '\([0-9]+ms\)' | tr -d '()ms' || true)
```

`|| true` 是 set-e 防御：check-theory.sh 失败（如 heap lock 冲突）时 pipeline 还能进 ledger 记录 fail 而不是 silent abort。

**[3] Trial**：

```bash
trial_raw=$(bash "$ISA_SCRIPTS/check-theory.sh" "$theory_abs" "$session" --patch "$patch" 2>&1 || true)
trial_out=$(echo "$trial_raw" | tail -1)
if ! echo "$trial_out" | grep -q '^OK'; then
    ledger_append '{"key":"'$key'","event":"trial_failed","expid":"'$expid'","reason":"check-theory.sh --patch did not return OK"}'
    exit 7
fi
trial_ms=$(...)
```

**[4] Spec impact**：

```bash
python3 "$SPEC_TOOLS/spec_impact.py" "$patch" "$theory_abs" \
  --baseline-wall "$baseline_ms" --trial-wall "$trial_ms" \
  --tree "$REPO_ROOT/verification/l4v/proof" \
  --measurement-out "$audit_dir/measurement.json"

gate=$(python3 -c "import json; d=json.load(open('$audit_dir/measurement.json')); print('PASS' if d.get('gate_pass') else 'FAIL')")
verdict=$(python3 -c "import json; d=json.load(open('$audit_dir/measurement.json')); print(d.get('impact_verdict','?'))")

if [ "$gate" != "PASS" ]; then
    ledger_append '{"event":"impact_failed",...}'
    exit 8
fi
```

`spec_impact.py` 输出 `measurement.json`，含字段：

```json
{
  "session": "AInvs",
  "baseline_wall_ms": 68616,
  "trial_wall_ms": 66787,
  "delta_pct": -2.7,
  "wall_gate_pass": true,
  "consumers_lines": 0,
  "consumers_files": 0,
  "impact_verdict": "additive",
  "strength_score": 0.0,
  "witness_present": false,
  "witness_advisory_pass": true,
  "gate_pass": true,
  "has_weakening": false
}
```

`gate_pass` = (wall_gate_pass AND witness_advisory_pass AND impact_verdict ∈ {additive, null} AND NOT has_weakening)。

**[5] Apply**：

```bash
apply_out=$(bash "$ISA_SCRIPTS/check-theory.sh" "$theory_abs" "$session" --apply "$patch" 2>&1 | tail -3)
if ! echo "$apply_out" | grep -q 'Patch applied'; then
    ledger_append '{"event":"trial_failed","reason":"apply failed"}'
    exit 7
fi
apply_ms=$(...)
```

`--apply` 内部先 verify 再 write。Verify pass 后 patch 永久写入 .thy 文件。

**[6] Audit dir**：

```bash
diff -u "$snap" "$theory_abs" > "$diff_tmp"
{
    echo "diff --git a/${theory_short} b/${theory_short}"
    sed "1s|^--- .*|--- a/${theory_short}|; 2s|^+++ .*|+++ b/${theory_short}|" "$diff_tmp"
} > "$audit_dir/patch.diff"
cp "$patch" "$audit_dir/range-patch.patch.txt"
write_command_sh   # 生成 re-runnable command.sh
write_decision_md  # 生成 decision.md skeleton

ledger_append '{"event":"applied","walls":{...},"verdict":"...","expid":"..."}'
```

### 5.3 工程层 4 道 gate

| Gate | 实现 | 失败处理 |
|---|---|---|
| **G1 trial** | `check-theory.sh --patch` returns `OK` | event=trial_failed, exit 7 |
| **G2 impact** | `spec_impact.py` `gate_pass=true` (additive verdict + 无 weakening + witness OK + wall gate) | event=impact_failed, exit 8 |
| **G3 wall** | trial wall ≤ baseline × 1.30 | 内含在 G2 |
| **G4 hard rules** | decision.md skeleton 留 TODO，apply 后人工填 | 不阻塞 apply，但 mark-audited 前作者必填 |

四道全过 = `event=applied` 入账，进 audit dir。

---

## 6. Ledger 状态机

文件：`spec-strengthen/candidates/candidate-ledger.jsonl`

### 6.1 状态与转移

```
[空状态]
    │
    │ survey
    ▼
discovered
    │
    ├──[preflight_failed]──→ [terminal — 不可 retry]
    │
    │ execute
    ▼
{probe_failed | trial_failed | impact_failed}
    │
    │ execute --retry
    ▼
applied
    │
    │ author commits audit dir
    ▼
audited  [terminal]

可在任意状态用 mark-aborted 触发 → aborted [terminal]
```

### 6.2 JSONL schema

每行一个 JSON 对象，必填字段 `ts`, `key`, `event`。可选字段按 event 类型：

```jsonc
// survey 发起的 discovered
{"ts":"2026-06-09T...", "key":"G:Untyped_AI:set_cdt:arch_state",
 "event":"discovered", "pattern":"G",
 "theory":"verification/l4v/proof/invariant-abstract/Untyped_AI.thy",
 "evidence":"mechanical",
 "metadata":{"op":"set_cdt","field":"arch_state",
             "anchor":"set_cdt_state_hyp_refs_of","anchor_line":2871}}

// survey 发起的 preflight_failed
{"ts":"...", "key":"G:Ipc_AI:set_mrs:machine_state",
 "event":"preflight_failed", "pattern":"G", "evidence":"mechanical",
 "reason":"do_machine_op reachable via set_mrs→store_word_offs — writes machine_state.memory, ..."}

// execute 成功
{"ts":"...", "key":"G:Untyped_AI:set_cdt:arch_state",
 "event":"applied", "expid":"0035-set-cdt-arch-state-frame-lemma",
 "walls":{"baseline":68616, "trial":66787, "apply":67591},
 "verdict":"additive"}

// execute 失败
{"ts":"...", "key":"G:Ipc_AI:set_mrs:machine_state",
 "event":"trial_failed", "expid":"0028-...",
 "reason":"check-theory.sh --patch did not return OK"}

// 人工 mark
{"ts":"...", "key":"G:Untyped_AI:set_cdt:arch_state",
 "event":"audited", "expid":"0035-..."}
```

### 6.3 SIGPIPE-safe 读取

`ledger_state(key)` 在 `spec-strengthen/run.sh:78-115`：

```bash
ledger_state() {
  local key="$1"
  python3 -c "
import json, os, sys
key = '$key'
path = '$LEDGER'
if not os.path.exists(path):
    sys.exit(0)
with open(path) as f:
    lines = f.readlines()
for line in reversed(lines):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    if d.get('key') == key:
        print(json.dumps(d, ensure_ascii=False))
        sys.exit(0)
"
}
```

**为什么直接 python 读不走 pipe**：早期版本是 `tac $LEDGER | python3 -c "..."`。python 找到 match 后 `sys.exit(0)` 触发 SIGPIPE 给 tac → tac exit 141 → `set -euo pipefail` 把 141 当 script-level error → set -e 终止整个 script。

修复路径在 `3796668` commit。详见 `spec-strengthen/run.sh:80-92` 注释。

---

## 7. Audit dir — 5 件套

每个成功 applied 的 experiment 在 `spec-strengthen/experiments/<expid>/` 留 5 个文件：

```
0035-set-cdt-arch-state-frame-lemma/
├── decision.md            ← strict-strengthening 声明 + 4 道 gate trace + 上下游影响
├── measurement.json       ← spec_impact.py 出的 metric
├── patch.diff             ← unified diff (canonical replay artifact)
├── range-patch.patch.txt  ← check-theory.sh 用的 range-replace 格式
└── command.sh             ← re-runnable measurement script
```

### 7.1 `decision.md`

由 `write_decision_md` (`spec-strengthen/run.sh:692-803`) 生成 skeleton。包含：

- Field table（key / verdict / walls / Δ wall）
- What changed（patch 内容）
- Pattern-specific section（G 是 "Reference companion(s)" + "Strengthening claim"）
- Acceptance gate trace（4 道 gate 结果）
- Impact on seL4
- Notes / follow-ups

作者在 mark-audited 前需填完 `TODO` 占位（"Strengthening claim" 段必填）。

### 7.2 `measurement.json`

`spec_impact.py --measurement-out` 直接写。schema 见 §5.2 [4]。

### 7.3 `patch.diff`

```diff
diff --git a/proof/invariant-abstract/Untyped_AI.thy b/proof/invariant-abstract/Untyped_AI.thy
--- a/proof/invariant-abstract/Untyped_AI.thy
+++ b/proof/invariant-abstract/Untyped_AI.thy
@@ -2876,6 +2876,11 @@
   apply (clarsimp ...)
   done

+lemma set_cdt_arch_state[wp]:
+  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
+  by (wpsimp simp: set_cdt_def | clarsimp)+
+
+
 lemma <next existing lemma>:
```

由 `diff -u snap theory_abs` 生成，加上 git-style header。

### 7.4 `range-patch.patch.txt`

Check-theory.sh 的 range-replace 格式，原样保留：

```
2878 2878
  done

lemma set_cdt_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def | clarsimp)+
```

未来重放可 `check-theory.sh --apply range-patch.patch.txt` 直接再跑一次（前提：anchor line 仍未变）。

### 7.5 `command.sh`

由 `write_command_sh` (`spec-strengthen/run.sh:651-689`) 生成。可独立重放整个 measurement 流程：

```bash
#!/usr/bin/env bash
set -euo pipefail
THEORY="verification/l4v/proof/invariant-abstract/Untyped_AI.thy"
SESSION="AInvs"
ISA_SCRIPTS="${ISA_SCRIPTS:-.claude/skills/isabelle_prover/scripts}"
SPEC_TOOLS="${SPEC_TOOLS:-spec-strengthen/scripts}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

TMP_RANGE_PATCH=$(mktemp /tmp/0035-...-XXXXXX.patch)
cp "$(dirname "$0")/range-patch.patch.txt" "$TMP_RANGE_PATCH"

# [1/3] baseline / [2/3] trial / [3/3] impact
BASELINE_OUT=$(bash "$REPO_ROOT/$ISA_SCRIPTS/check-theory.sh" "$REPO_ROOT/$THEORY" "$SESSION" 2>&1 | tail -1)
BASELINE_MS=$(echo "$BASELINE_OUT" | grep -oE '\([0-9]+ms\)' | tr -d '()ms')
echo "baseline_wall_ms=$BASELINE_MS"
...
```

---

## 8. Container 基础设施

整个 verifier 链运行在 docker compose service `l4v` 里。host 通过 `docker compose exec` 调入。

### 8.1 路径映射

```
HOST:                                CONTAINER:
/home/lijun/seL4-docker-main/    →   /workspace/
```

所有 host 路径自动翻译，见 `.claude/skills/isabelle_prover/scripts/_dx.sh:25-31`。

### 8.2 Orphan reaper trap

**问题**：host wrapper 死时（timeout SIGTERM / Ctrl-C / OOM），`docker compose exec` 不把 signal 传递给 container 内的 polyml/isabelle 子进程 → 它们成为 host orphan，每个 ~3 GB RAM，累积几次就 OOM 杀新 build。

**解决**：`.claude/skills/isabelle_prover/scripts/_dx.sh:14-38` 加 trap：

```bash
__dx_baseline_file="/tmp/_dx_baseline_$$_$(date +%s%N | head -c 12).txt"
docker compose -f "$COMPOSE_FILE" exec -T l4v bash -c \
  "pgrep -f 'polyml|isabelle' 2>/dev/null | sort -u > '$__dx_baseline_file' || true" \
  >/dev/null 2>&1 || true

__dx_cleanup() {
  docker compose -f "$COMPOSE_FILE" exec -T l4v bash -c "
    if [ -f '$__dx_baseline_file' ]; then
      NEW=\$(comm -23 <(pgrep -f 'polyml|isabelle' 2>/dev/null | sort -u) <(sort -u '$__dx_baseline_file') 2>/dev/null)
      if [ -n \"\$NEW\" ]; then
        kill -9 \$NEW 2>/dev/null || true
      fi
      rm -f '$__dx_baseline_file'
    fi
  " >/dev/null 2>&1 || true
}
trap __dx_cleanup EXIT
trap '__dx_cleanup; exit 143' TERM
trap '__dx_cleanup; exit 130' INT
```

策略：

- 入口时 snapshot 容器内当前 polyml/isabelle PID list
- 退出时 (任何 trap) 杀掉**新出现的** polyml/isabelle，不动 baseline 里已有的（保护 ml_server daemon 等长寿守护进程）

同样的 trap 也放在 `spec-strengthen/scripts/spec_premise_probe.sh:34-58`。

详见 `7045ac0` commit。

### 8.3 Session lock

`.claude/skills/isabelle_prover/scripts-container/check-theory.sh` 在 container 内用 `flock -n` 抢 `/tmp/isabelle-session-<SESSION>.lock`。同 session 多个 isabelle process 并发会破坏 heap，flock 保证串行。

**手动清锁**（debugging 用）：

```bash
docker compose -f docker-compose.yml exec -T l4v bash -c 'rm -f /tmp/isabelle-session-*.lock'
```

---

## 9. 复现配方

### 9.1 环境前置

- docker compose `l4v` service 已起（`docker compose -f docker-compose.yml ps` 看到 Up）
- AInvs heap 已 build（`/root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/AInvs` 存在 in container）
  - 若缺：`docker compose exec -T l4v bash -c 'cd /workspace/verification/isabelle && ./bin/isabelle build -b -d /workspace/verification/l4v AInvs'`
  - 用时约 55 min（一次性，除非 spec 大改才需要重 build）

### 9.2 单 candidate 端到端命令序列

以 `G:Untyped_AI:set_cdt:arch_state` 为例：

```bash
cd /home/lijun/seL4-docker-main

# Step 1: survey 把 candidate 入 ledger
bash spec-strengthen/run.sh survey \
  verification/l4v/proof/invariant-abstract/Untyped_AI.thy \
  --pattern G

# 现在 spec-strengthen/candidates/candidate-ledger.jsonl 里有:
# {"key":"G:Untyped_AI:set_cdt:arch_state","event":"discovered","pattern":"G",...}
# 以及 surveys/survey-Untyped_AI-<YYYYMMDD>.md

# Step 2: execute（自动 patch + trial + apply + audit dir）
bash spec-strengthen/run.sh execute \
  --candidate 'G:Untyped_AI:set_cdt:arch_state' \
  --expid 0035-set-cdt-arch-state-frame-lemma \
  -y

# Step 3: 检查 audit dir
ls spec-strengthen/experiments/0035-set-cdt-arch-state-frame-lemma/
#   command.sh decision.md measurement.json patch.diff range-patch.patch.txt

# Step 4: 作者填 decision.md 的 TODO 段（人工）
$EDITOR spec-strengthen/experiments/0035-.../decision.md

# Step 5: commit audit dir + mark audited
git add spec-strengthen/experiments/0035-.../
git commit -m "spec(0035): ..."

bash spec-strengthen/run.sh mark-audited \
  'G:Untyped_AI:set_cdt:arch_state' \
  --expid 0035-set-cdt-arch-state-frame-lemma
```

### 9.3 Worked example — [[0035]] 走读

完整运行轨迹（节选自实际 commit `246bbdc`）：

```
================================================================
execute G  G:Untyped_AI:set_cdt:arch_state  → 0035-set-cdt-arch-state-frame-lemma
  theory:  verification/l4v/proof/invariant-abstract/Untyped_AI.thy
  op:      set_cdt
  field:   arch_state
  session: AInvs
================================================================
Generated template patch: logs/spec-strengthen-Untyped_AI-set_cdt_arch_state-20260609.patch
----------------------------------------------------------------
2878 2878
  done

lemma set_cdt_arch_state[wp]:
  "\<lbrace>\<lambda>s. P (arch_state s)\<rbrace> set_cdt t \<lbrace>\<lambda>_ s. P (arch_state s)\<rbrace>"
  by (wpsimp simp: set_cdt_def | clarsimp)+
----------------------------------------------------------------
[1/6] snapshot → /tmp/spec_strengthen_0035-..._pre.<pid>
[2/6] baseline wall ...
  ✓ baseline_wall_ms=68616
[3/6] trial wall (with patch) ...
  ✓ trial_wall_ms=66787 (Δ -2.7%)
[4/6] spec_impact verdict ...
  verdict=additive gate=PASS
[5/6] apply ...
  ✓ apply_wall_ms=67591
[6/6] capture patch.diff + audit dir ...
================================================================
SUMMARY
  walls:     baseline=68616 ms  trial=66787 ms (Δ-2.7%)  apply=67591 ms
  verdict:   additive / PASS
  audit:     spec-strengthen/experiments/0035-.../
```

每一步的逐项展开见 [§5.2](#52-各步细节)。

### 9.4 批量处理

驱动一组候选用 bash 循环：

```bash
CANDS=(
  "0036|G:Untyped_AI:set_cdt:domain_index|set-cdt-domain-index-frame-lemma"
  "0037|G:Untyped_AI:set_cdt:domain_time|set-cdt-domain-time-frame-lemma"
  # ...
)
for entry in "${CANDS[@]}"; do
  IFS='|' read -r num key tail <<< "$entry"
  expid="${num}-${tail}"
  docker compose exec -T l4v bash -c 'rm -f /tmp/isabelle-session-*.lock' >/dev/null
  bash spec-strengthen/run.sh execute \
    --candidate "$key" --expid "$expid" -y \
    > /tmp/${num}.log 2>&1
done
```

[[0036-0049]] 那批 14 个用的就是这个模式，详见 commit `13b056d`。

### 9.5 Detector 修改后的完整验证路线

如果你的目标不是“使用现成 detector”，而是“修改 detector 后证明它仍然正确”，推荐按下面的两段式路线执行：

1. **先跑 regression harness**
   优先直接跑 `bash spec-strengthen/scripts/pattern_g_regression.sh`。它会验证 EXPECTED-FAIL / CONTROL 两组样本是否符合预期。这里不需要 Isabelle，也不需要 `check-theory.sh --apply`。

2. **再挑一个 clean candidate 跑端到端流水**
   例如 `G:Untyped_AI:set_cdt:arch_state`。这一步验证的是 executor / verifier / impact / audit 链路没有被 detector 改动意外破坏。

最小可执行命令序列：

```bash
cd /home/lijun/seL4-docker-main

# [A] detector regression
bash spec-strengthen/scripts/pattern_g_regression.sh

# [B] one clean candidate end-to-end
bash spec-strengthen/run.sh execute   --candidate 'G:Untyped_AI:set_cdt:arch_state'   --expid 0035-set-cdt-arch-state-frame-lemma   -y
```

通过标准：

- [A] 的 gate 断言全部成立
- [B] 输出 `verdict=additive gate=PASS` 且 audit dir 五件套齐全

**为什么 control 样本不再复用旧 apply 案例**：随着 branch 持续前进，很多历史上的 `clean` 候选已经被真正 apply，或者后来被 crunch / direct gate 吸收掉了。回归 harness 的 control 集应以“当前 tree 上仍然 clean”为准，而不是机械复用历史实验编号。

这样才算完整复现了“Pattern G detector 的修改 + 验证”路线，而不是只复现使用方式。

---

## 10. 故障排查

### 10.1 Heap stale

**症状**：`isabelle process` 报错 `*** Missing heap image for session "AInvs"` 或 `Exception- Fail "The parent for this saved state does not match or has been changed"`。

**原因**：

- AInvs heap 文件缺失（被 sudo kill cascade 斩没）
- 或 heap 比某个 spec/abstract 或 proof 文件**旧**（heap 里的 source fingerprint 跟现实不一致）

**解决**：

```bash
docker compose exec -T l4v bash -c '
  rm -f /root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/log/AInvs.db
  rm -f /root/.isabelle/heaps/polyml-5.9.1_x86_64_32-linux/log/SCALA_ISABELLE_TEMP_*.db
'
docker compose exec -T l4v bash -c '
  export L4V_ARCH=ARM
  cd /workspace/verification/isabelle
  ./bin/isabelle build -b -d /workspace/verification/l4v AInvs
'
```

**关键 fact**：单 lemma 加进 .thy 文件**不需要** rebuild heap。`check-theory.sh --apply` 只 update 文件，不动 heap。下一次 `--patch` 或 `--apply` 直接复用旧 heap 跑新文件，靠 `isabelle process` 的 TMP-alias 机制隔离。只有 heap 文件本身丢失或损坏才 rebuild。

### 10.2 Container orphan 累积

**症状**：host `ps aux | grep polyml` 看到一堆累积的 polyml 进程，内存被吃满。

**预防**：用 `_dx.sh` / `spec_premise_probe.sh` 路径调，不要绕过去裸调 docker。两个 host wrapper 都装了 trap（§8.2）。

**应急**：

```bash
docker compose exec -T l4v bash -c 'pkill -9 -f "polyml|isabelle" || true'
```

注意会同时杀掉 ml_server daemon（若有）。

### 10.3 Lock 冲突

**症状**：`Error: another Isabelle tool is already running on session 'AInvs'`。

**原因**：上一次 check-theory.sh 没正常退出，flock 没释放。

**解决**：

```bash
docker compose exec -T l4v bash -c 'rm -f /tmp/isabelle-session-*.lock'
```

每次 run.sh execute 前都做这一步是安全的。

### 10.4 Template tactic 闭不上

**症状**：trial fail，错误信息提示有未闭合的 schematic precondition (`?R x`) 或 case-split 分支留 H⟹H residual。

**诊断**：

```bash
# 看完整 trial 错误
docker compose exec -T l4v bash -c 'rm -f /tmp/isabelle-session-*.lock'
timeout 240 bash .claude/skills/isabelle_prover/scripts/check-theory.sh \
  <theory.thy> AInvs \
  --patch logs/spec-strengthen-<file>-<op>_<field>-<date>.patch 2>&1 | tail -30
```

**常见 cause**：

- **schematic precondition `?R x`**：op 调 `get_object` 但 wp 默认 ruleset 处理不彻底 → 需要 `wp: get_object_wp`。execute_G 的动态检测应已经加上；若没加（如 op 不在静态调用图里、或调用层级 >2），手动加 `--proof-tactic 'by (wpsimp wp: get_object_wp simp: <op>_def | clarsimp)+'` 重试（未来 execute_additive 实施时该 flag 上线）。
- **H ⟹ H 残余**：默认 `| clarsimp)+` 应能闭合；若不行，手动写 patch 用 `fastforce`（注意超时风险）或更精确的 `intro impI conjI` 链。

### 10.5 Patch anchor 错位

**症状**：trial fail 信息提示语法错误，新 lemma 被插到了 module 中段或其他奇怪位置。

**诊断**：手动看 patch 的第一行（`<start> <end>`），核对 `<start>` 是不是 anchor lemma 的 block-end。

**常见 cause**：

- 文件被另一个 apply 改了行号但 detector 没重 survey → 用 stale anchor_line。
- 解决：重跑 `spec-strengthen/run.sh survey <file>` 拿新 anchor。

### 10.6 `preflight_failed:*` 怎么诊断

这是 Pattern G detector 维护里最重要的一类“非执行期”故障。`survey` 结果不是 `clean`，并不一定说明工具坏了；很多时候恰恰说明新 gate 生效了。诊断时先分类型，不要直接去改 tactic。

**`preflight_failed:direct`**

- 含义：同名 lemma 已经在 proof tree 里存在。
- 先查：

```bash
rg -n '\b<lemma_name>\b' verification/l4v
```

- 处理：如果确实已有同名 lemma，这是正确跳过；如果只是误匹配到注释/历史文本，收紧 direct grep 的正则。

**`preflight_failed:crunch`**

- 含义：crunch 已经自动派生过这个 frame family。
- 先查：

```bash
rg -n 'crunch\s+<field>.*<op>' verification/l4v/proof/invariant-abstract
```

- 处理：如果命中真实 crunch 声明，应保留 skip；如果命中的是 wrapper 名噪声，收紧 `COMMON_WRAPPERS` 或 crunch grep pattern。

**`preflight_failed:dmo_path`**

- 含义：`op_transitively_calls(op, "do_machine_op")` 命中，且当前 field = `machine_state`。
- 先查：

```bash
python3 spec-strengthen/scripts/spec_frame_gap.py   verification/l4v/proof/invariant-abstract/<File>.thy --op <op>
```

再去看对应 `spec/abstract/*.thy` 里的 op definition，确认 chain 上是否真会进入 `do_machine_op`。

- 处理：
  - 如果 chain 真实存在，这是语义正确的 skip。
  - 如果 chain 是 `_extract_callees` 误抓到的伪 callee，优先修 callee 过滤，而不是关掉 dmo gate。

**`preflight_failed:dxo_path`**

- 含义：`op_transitively_calls(op, "do_extended_op")` 命中，且 field 属于 `EXT_STATE_PROJECTED`。
- 先查：definition block 里是否真实走到 `do_extended_op`，以及 field 是否确实在 `EXT_STATE_PROJECTED` 集合里。
- 处理：
  - 真实命中 → 应跳过。
  - 误报 → 多半是调用图或 field 分类过宽，分别修 `_extract_callees` / `EXT_STATE_PROJECTED`，不要直接删 gate。

**一个总原则**：

- `trial_failed` 往往该看 tactic / patch / anchor
- `preflight_failed:*` 往往该看 detector 的语义 gate / grep gate

先分清自己处在哪一层，再决定修哪里。

---

## 11. 文件清单（reproducibility 黄金记录）

### 11.1 工具源文件

| 文件 | 行数 | 职责 |
|---|---:|---|
| `spec-strengthen/run.sh` | ~1000 | 入口 + 5 个 subcommand + standard_pipeline |
| `spec-strengthen/scripts/spec_frame_gap.py` | ~370 | G detector + 4 道 gate |
| `spec-strengthen/scripts/pattern_g_regression.sh` | ~70 | G detector retroactive regression harness |
| `spec-strengthen/scripts/spec_op_args.py` | ~135 | op 签名解析 + arity padding |
| `spec-strengthen/scripts/spec_impact.py` | — | impact verdict + measurement.json |
| `spec-strengthen/scripts/spec_strengthen_scan.py` | — | Hoare triple parser |
| `.claude/skills/isabelle_prover/scripts/_dx.sh` | ~80 | host wrapper + orphan trap |
| `.claude/skills/isabelle_prover/scripts/check-theory.sh` | — | thin wrapper of `_dx.sh` |
| `.claude/skills/isabelle_prover/scripts-container/check-theory.sh` | ~350 | container 内 isabelle process 驱动 |

### 11.2 关键 commit 时间轴（按演化顺序）

| Commit | 引入了什么 |
|---|---|
| `5cd9c65` | dmo / dxo 两道 semantic gate + op_transitively_calls |
| `e0f560a` | 3-token op allowlist 补 (set_asid_pool / set_vm_root / set_scheduler_action) |
| `767d57f` | execute_G: 自动 args + block-end + 默认 tactic |
| `246bbdc` | [[0035]] 首个端到端全自动 G — 验证以上 |
| `13b056d` | [[0036-0049]] 14 candidate batch — 10 apply / 4 trial fail（fail 揭示 get_object 问题） |
| `3796668` | execute_G 加 dynamic `wp: get_object_wp` 检测 + ledger_state SIGPIPE fix |
| `7045ac0` | docker orphan reaper trap |
| `f3579be` / `a23a4d5` / `7959533` | 把 tools/spec_strengthen → spec-strengthen/，layout 迁移 |
| `ae3f2f7` | post-migration cleanup |

### 11.3 失败案例（保留为反例）

| Experiment | 失败模式 | 解决路径 |
|---|---|---|
| [[0028]] G:set_mrs:machine_state | dmo path → 实际写 machine_state.memory | dmo gate (`5cd9c65`) |
| [[0030 dropped]] G:set_thread_state:domain_index/time | dxo path → exst 替换 | dxo gate (`5cd9c65`) |
| [[0039/0040/0042/0043]] G:set_cap/set_simple_ko:* | get_object schematic precondition 不闭合 | 动态 `wp: get_object_wp` (`3796668`) |

每个失败都对应一个工具改进。这是 phase-1 工程节奏的真实记录。

---

## 12. 边界 — 这套自动化不覆盖什么

Pattern G automation **专攻"加新 frame lemma"**。下列工作**不自动**：

- **Pattern C / A**：truth condition 在 proof tree 的 global 状态里，需 trial 验证，detector 只能给怀疑信号（详见 [phase-1-summary.md §3](phase-1-summary.md#3-为什么-g-已经实现自动化扫描而-a--c-不能)）。
- **跨文件 consumer 影响分析**：spec_impact.py 只算 candidate lemma 名在 proof tree 出现行数，不分析 consumer proof 是否 break。
- **新 op family 的发现**：detector 假设"目标 op 在文件里至少有 2 个 [wp] companion"，全新 op 的 frame family 不在扫描范围。
- **proof body 重写**：default tactic 不闭合时，目前需人工写 patch 走 `execute_custom`。未来 execute_additive + LLM agent 接口（phase-2）有望接管。

---

**报告生成时间**：2026-06-10
**对应 spec-strengthen branch HEAD**：`25befb5` (`reports(spec-strengthen): phase-1 summary v2 — tighten taxonomy + Gate 2`)
**Pattern G 累计 applied 实验数**：~35 个（[[0014-0017]], [[0019-0027]], [[0030-0034]], [[0035-0049]], [[0050-0053]]，含 batch 实验）
