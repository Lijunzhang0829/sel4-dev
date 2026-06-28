# spec-strengthen — execute_additive 技术架构

把 seL4 **Abstract Spec / invariant-abstract** 的强化做成一条**可复现、全程留痕**
的流水线：一个**控制全流程的 shell**（`strengthen.sh`）+ 一个**扫描并提议的 agent**
（`spec_agent.py`）+ **既有验证脚本**（`check-theory.sh` / `spec_impact.py`），每次运行
的全部 trace 归档到 `spec-strengthen/experiments/`。

这是 `lemma-staticize/` 那套架构在 spec 强化上的对应物：

| lemma-staticize（proof 静态化） | spec-strengthen（spec 强化） | 角色 |
|---|---|---|
| `bench.sh` | **`strengthen.sh`** | 控制全流程的主驱动 |
| `proposer_host.py` / `ab_agent.py` | **`scripts/spec_agent.py`** | LLM agent：扫描 + 提议 |
| `gate.py` + `check-theory.sh` | **`scripts/spec_impact.py` + `check-theory.sh`** | 验证闸（正确性 + 裁决） |
| `runs/bench-<ts>/` | **`experiments/strengthen-<thy>-<ts>/`** | 全 trace 归档 |

## 设计基石：execute_additive

详见 [`reports/spec-strengthen/execute-additive-design.md`](../reports/spec-strengthen/execute-additive-design.md)。
一句话：

> **任何 spec 强化 ≡ 加一条新 lemma `L'`，原 `L` 不动（additive）。**

每个候选是二维空间里的一个点：

- **槽位（动什么）**：`P`（弱化前提）/ `Q`（强化结论）/ `F`（字段保性 frame）。
- **下游机制（怎么生效）**：`wp`/`simp`（自动）/ `named`（具名 consumer）/ `block`（building block）。
  缺这一维 = 死码，**进 build 前就被拒**。

旧的 pattern 字母框架（G/C/A/D）+ `_old` witness 已退役：additive 形态下原 L 不动，
没有"老形式"要反推，因此**不需要 witness**，也天然避免 modify 触发的 consumer cascade
（对照 [[0029]] modify 失败 vs [[0054]] additive 成功）。

## 四个环节

```
strengthen.sh <theory.thy> [--slot P|Q|F|all] [--n N] [--model M] [--apply] [-y]

 [0] BASELINE  check-theory.sh 跑未改文件（一次）               → baseline wall
 [0.5] HINTS   spec_slot_hints.py 机械扫描 Q/P 空槽信号          → hints.json
               Q: redirect 分类学（inline-redirect=强 post 没名字=真空槽 /
                  named-redirect=强 rule 已存在=槽已填 / exactness=post 只承诺
                  rv≤e 而计算可定精确值）
               P: 前提 conjunct 在 proof body 零出现（前缀匹配，simp:
                  valid_objs_def 算消费 valid_objs）+ 差分证据（其它 conjunct
                  可见消费才算 high）+ 排除 opaque 一行流证明
 [1] SCAN      spec_agent.py：agent 读 theory + hints，提议 N 个 additive
               候选（slot+delivery+新 lemma 全文+anchor 行）     → proposals.json
               slot 硬执行：--slot Q 只产 Q 或空，off-slot 提议被丢弃
 [1.5] 预登记 本轮所有 proposal 作为 `discovered` 写入 ledger（让同一轮
               A 依赖 B 的 block 链能满足 gate condition-1）
 [2] 逐候选：
     a. PATCH          按 anchor 行 + new_lemma 造 range-replace 补丁；named-realized
                       的候选还会带 consumer_hunks（多 hunk `---` 复合补丁，
                       同 patch 改/加 consumer）→ 原 lemma 不动
     b. DELIVERY GATE  spec_delivery_gate.py（§2.5 下游契约，grep **打过补丁的预览**
                       验 named-realized）                       → accept/reject（零 build 代价）
     c. TRIAL+REPAIR   check-theory.sh --patch 必须 OK           → trial wall（唯一真值来源）
                       失败时闭环：prover 错误 + 被拒 proposal + 源码窗口回流
                       agent（--repair）→ 修正提议（或 [] = 判定语义不成立）
                       → 重建补丁重试，至多 REPAIR_TRIES 轮，全程留痕
                       （proposal-0.json / repair-N.json / trial-error.txt）
     d. IMPACT         spec_impact.py 裁决 + wall gate           → measurement.json
     e. APPLY          check-theory.sh --apply（仅 --apply 时）；否则 dry-run 只渲染 patch.diff
     → 每候选一个 audit bundle + 一条 ledger 事件
 [3] SUMMARY   summary.md / summary.csv
```

**agent 只提议，trial 是唯一裁判**：提议错了就在 TRIAL 撞墙，记 `trial_failed`，仅此而已。
这正是设计的分工（execute-additive-design.md §6.3：detector/agent 给方向，trial 给真值）。

## 怎么运行

```bash
cd /home/lijun/seL4-docker-main

# dry-run（默认）：只 trial + impact，源文件不动
spec-strengthen/strengthen.sh proof/invariant-abstract/Ipc_AI.thy --slot Q --n 3

# 把通过的候选写进源文件（逐条确认；-y 跳过确认）
spec-strengthen/strengthen.sh proof/invariant-abstract/KHeap_AI.thy --slot F --apply -y
```

theory 参数可给绝对路径 / repo-相对 / `verification/l4v`-相对 / 裸 basename（自动定位）。
session 从路径推断（`spec/abstract`→ASpec、`proof/invariant-abstract`→AInvs、
`proof/refine`→Refine），也可 `--session` 显式给。

环境旋钮：`SLOT N MODEL APPLY OUTROOT WALL_GATE`（见脚本头注释）。
agent 凭据走 host 上 Claude Max OAuth（`CLAUDE_BIN` 覆盖 CLI 路径，
`SPEC_AGENT_MODEL` 覆盖模型，`SPEC_AGENT_MAX_LINES` 控制喂给 agent 的源码行数上限）。

## 归档结构

```
spec-strengthen/experiments/strengthen-<theory>-<时间戳>/
├── command.sh            # 复现整次运行
├── baseline.log          # 未改文件的 baseline build 尾
├── proposals.json        # agent 提议了什么
├── agent.log / agent-raw.txt   # agent 调用 trace + 原始回复
├── summary.md / summary.csv     # 逐候选裁决表
└── NN-<slot>-<lemma>/     # 每个候选一个子目录（rule-5 完整 audit bundle）
    ├── record.md              # ★一页式回放（先看这个）：原 lemma 源码 + 新 lemma
    │                          #   源码 + 为什么改（hint/论证/机械验证的强化关系）
    │                          #   + 试错链（attempt→错误→repair）+ 验证 + 追踪
    ├── proposal.json          # agent 对该候选的结构化输出
    ├── delivery_gate.json     # §2.5 下游契约裁决
    ├── range-patch.patch.txt  # check-theory.sh 的 range-replace 输入
    ├── trial.log              # trial build 尾
    ├── measurement.json       # spec_impact 裁决 + wall
    ├── patch.diff             # additive 统一 diff（原 lemma 零改动）
    ├── decision.md            # 人读小结（slot/delivery/claim/gate trace）
    └── command.sh             # 复现该候选的测量
    （失败候选同样生成 record.md，含 proposal-0/repair-N/trial-error 试错链）
```

被 `applied` 的候选，其子目录即 seL4-source PR 的 rule-5 记录：从 `spec-strengthen`
分支开 PR，引用 lemma 名 + 文件、`reports/golden-baseline/walls.json` 条目、trial wall。

## 验收门（全过才 accept / apply）

1. **delivery gate** 过（§2.5 下游契约）。
2. `check-theory.sh --patch` 返回 `OK`。
3. `spec_impact.py` 无 `weakening`，且至少一个 `premise-weaken / monotone-strengthen
   / postcond-strengthen / additive`。
4. trial wall ≤ baseline × 1.30（强化换 build 时间，略慢但更强是预期方向）。
5. 继承 parent SKILL 五条硬规则（无 `sorry/oops/axiomatization`；check-theory.sh 唯一闸；
   PR 主线；heap 易变性；每候选一条 JSONL ledger 事件）。

## 文件清单

| 文件 | 角色 |
|---|---|
| `strengthen.sh` | **主驱动**（本架构核心新增） |
| `scripts/spec_slot_hints.py` | Q/P 机械检测器：redirect 分类学 + exactness + unused-premise 差分信号 → hints.json；`--check-p-claim` 静态验证 P 候选（严格子集+post 相同）并机械生成正确方向的 claim |
| `scripts/spec_agent.py` | agent：扫描 theory+hints + 提议 additive P/Q/F 候选；`--repair` 接收 prover 错误产修正提议 |
| `scripts/spec_delivery_gate.py` | §2.5 下游契约闸（**带证据**：named-realized 验 consumer 引用 / block 下层在 ledger **且 evidence 真的 cite L'**（reference_snippet/downstream_patch/ab_trial）/ P·Q+wp escalation；build 前廉价拒收） |
| `scripts/spec_wp_escalation.sh` | 跑 §2.5.1 三轮多文件回归 → 产出 escalation record，唯一能放行 P/Q+wp 候选 |
| `scripts/spec_delivery_lifecycle.py` | ledger 周期 sweep：planned→realized（consumer 落地）/ →orphan（grace 超期） |
| `scripts/spec_range_apply.py` | 非破坏性 range-apply（dry-run 渲染 patch.diff 用） |
| `scripts/spec_impact.py` | 裁决 + wall gate（既有，复用） |
| `scripts/spec_op_args.py` `spec_witness_gen.py` `spec_frame_gap.py` `spec_candidates.py` | 既有辅助/检测器（参考保留） |
| `candidates/candidate-ledger.jsonl` | append-only 状态账本（新增 slot/delivery/delivery_substate 字段） |
| `run.sh` | **legacy** 的 per-pattern（G/C/A/D）手驱路径，保留备查（见下） |

## ledger 事件类型 + delivery 生命周期

新驱动写入：`rejected_delivery`（§2.5 契约拒）/ `trial_failed` / `impact_failed` /
`trial_passed`（dry-run 通过）/ `applied`（已写入源文件）。每条带 §3.4 全套字段：
`slot` / `delivery` / **`delivery_substate`**（gate 解析后的值，非原始 claim）/
**`delivery_state`**（`realized`|`pending`）/ **`grace_period_weeks`** /
**`escalation_record`**。

生命周期状态机由 `spec_delivery_lifecycle.py` 周期推进（不在单次 strengthen 里跑）：

```bash
python3 spec-strengthen/scripts/spec_delivery_lifecycle.py            # 只报告
python3 spec-strengthen/scripts/spec_delivery_lifecycle.py --apply    # 落 delivery_realized / delivery_orphan 事件
```

`pending` 候选：consumer 落地（block 还要求**下层 source 真的引用本 block**，仅 landed 不算）
→ `delivery_realized`（state→realized）；grace 超期仍无 consumer → `delivery_orphan`
（state→orphan，聚集给 GC review，**不自动删**）。

**P/Q + wp escalation 升级路径**（§2.5.1，默认拒、可人工升级）：

```bash
# 1. 跑三轮多文件回归，产出 record
spec-strengthen/scripts/spec_wp_escalation.sh \
  --patch <range-patch> --lemma-file <L.thy> \
  --files "<L.thy> <consumer.thy> <cross1.thy>" --slot Q \
  --reason "consumer 太多，逐个 named 不现实" --out esc.json
# 2. 带着 record 跑 strengthen，gate 才放行该 P/Q+wp 候选
spec-strengthen/strengthen.sh <L.thy> --slot Q --escalation esc.json --apply
```

record 必须过：trial wall ≤ baseline×1.05、可逆（baseline2 与 baseline1 差 ≤3%）、
≥3 文件。不过即拒，候选降级 named/block 或放弃。

legacy run.sh 另有 `discovered` / `preflight_failed` / `probe_failed` / `audited` / `aborted`。

## 与 lemma-staticize 的取舍一致

- **agent 走 host 上的 `claude -p`**：不把凭据带进容器，neutral cwd + 关 MCP（避免加载
  CLAUDE.md/skills 把 headless 调用拖慢 ~25×），`--max-turns 1` 单发。
- **裁决用脚本而非 in-JVM**：`check-theory.sh` 真实 build + `spec_impact.py` 结构化裁决，
  带 wall regression cap。
- **默认 dry-run**：不写源文件，先看 `summary.md`，确认后 `--apply`。

---

## Legacy：run.sh（per-pattern 手驱路径，保留备查）

`run.sh` 是上一代 pattern-字母（G/C/A/D）框架的编排器
（`survey / execute / status / ledger / mark-audited`）。execute_additive 已取代它作为
主路径，但 run.sh 仍可用于复现历史实验、或手驱单条 modify-mode 候选。

```bash
# 扫描一个文件出 Tier 1/2/3 候选（mechanical 检测器）
bash spec-strengthen/run.sh survey \
  verification/l4v/proof/invariant-abstract/<Theory>.thy --pattern G|C|A|all

# 端到端执行一个候选（auto template + trial + apply + audit dir）
bash spec-strengthen/run.sh execute --candidate '<key>' --expid <NNNN-tag> -y

bash spec-strengthen/run.sh status [<key>]
bash spec-strengthen/run.sh ledger
```

legacy pattern ↔ 新槽位映射（execute-additive-design.md §7）：
Pattern G → **F-slot**；Pattern C → **P-slot + named**；Pattern A → **Q-slot + named**；
Pattern D → **Q-slot 的 `≤`→`=` 子类**。

## Out-of-scope here

- `verification/l4v/proof/**/*.thy` 改动落在嵌套的 l4v git repo（独立提交历史）。
- `.claude/skills/isabelle_prover/scripts/check-theory.sh` 是共享 Isabelle host wrapper，
  属 skill 系统，不在本目录。

## History

`spec-strengthen` 分支里程碑（`git log --oneline --follow spec-strengthen/`）：

- 0014-0026：早期 modify-mode 批（KHeap / CSpace / Finalise）；建立 ledger + audit-dir 约定
- 0027-0034：Tier 1 G 批（TcbAcc / Ipc）— 28 条 frame lemma；暴露 5 个检测器误分类
- 0035-0053：Tier 1 G 批（DetSched / CSpaceInv / Untyped）— 14/14 全自动管线通过
- 0054：additive PoC — 首个 cascade-free 应用
- phase-1/2 设计：`reports/spec-strengthen/{phase-1-summary,pattern-G-automation-pipeline,execute-additive-design}.md`
- （本次）：execute_additive 落地 — `strengthen.sh` 主驱动 + `spec_agent.py` agent +
  `spec_delivery_gate.py` 契约闸；SKILL.md 改写为 additive 工作流
