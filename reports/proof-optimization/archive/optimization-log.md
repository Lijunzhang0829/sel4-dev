# seL4 proof-wall 局部优化 log

**配方**:`wasted_classical.py` 选候选(`auto/fastforce/force` + `simp:`,无 `split:`/`dest:`/`elim:`/`intro:`,文件 ≤1600 行)→ 把该行 `fastforce/auto/force` 换成 **`clarsimp`**(simp 集不变;不要换 `simp`)→ `check-theory.sh <file> <session>` 实测 before/after **各 ×2**,**两簇不重叠且 after 更低**才 ACCEPT。**不 commit**。
机理:去掉 fastforce 浪费的深度回溯搜索,保留 clarsimp 的 clarify 安全步 + simp。多数 seL4 慢 auto/fastforce 的 classical 是必要的(monad 等式 / 指针注入 / 多目标)→ clarsimp 变红 → REJECT。

测量纪律:DB-elapsed 不可信,只信 check-theory 实测;before/after 各 ≥2 次、两簇不重叠才算数(防 noise)。基线 < ~15s 的太便宜、delta 测不出 → SKIP。

session:`crefine`→CRefine,`refine`→Refine,`invariant-abstract`→AInvs,`infoflow`→InfoFlow,`access-control`→Access,`drefine`→DRefine。

---

## ✅ ACCEPT(已写回 l4v 工作区,未 commit)

| file:line | lemma | 改动 | before ms (×n) | after ms (×n) | 降幅 | 复测者 |
|---|---|---|---|---|---|---|
| `proof/invariant-abstract/VSpacePre_AI.thy:162` | arch_update_cap_valid_mdb | fastforce→clarsimp | 23398 / 23120 / 23854 | 18288 / 18367 / 18528 | **−21%** | 父(×3) |
| `proof/infoflow/Decode_IF.thy:137` | OR_choice_def2 | fastforce→clarsimp | 29717 / 29697 | 26078 / 26605 | **−11.5%** | 父独立复测(×2) |

## ❌ REJECT / ⚪ INCONCLUSIVE / ⏭ SKIP

| file:line | 原方法 | 结果 | 原因 |
|---|---|---|---|
| `proof/infoflow/UserOp_IF.thy:151` | fastforce simp: | REJECT | clarsimp 红(monad-eq,classical 必要) |
| `proof/infoflow/UserOp_IF.thy:133` | fastforce simp: | REJECT | 同上 |
| `proof/invariant-abstract/ARM/ArchCSpace_AI.thy:518` | auto simp:(intro conjI 后) | REJECT | 多目标,clarsimp 不够 |
| `proof/infoflow/ARM/ArchUserOp_IF.thy:794/811/833/859` | fastforce\|intro …+ | REJECT | clarsimp 红 |
| `proof/infoflow/Retype_IF.thy:87/93` | fastforce simp:(dmo monad-eq) | REJECT | simp 红 + clarsimp 红(case 分解必要) |
| `proof/invariant-abstract/ARM/ArchRetype_IF.thy:194` | fastforce simp: | REJECT | clarsimp 红 |
| `proof/crefine/ARM/TcbQueue_C.thy:391/393` | fastforce simp: | REJECT | 注入推理,clarsimp 红 |

---

## 后续尝试(逐条追加)

### 批次 2(2026-06-06,sweep agent,0 新 ACCEPT —— 池基本扫尽)
扫了 wasted_classical 剩余未测候选;放宽 nlines>2500/elapsed<=100 无新增(池固定)。

| file:line | 原方法 | session | base ms | clarsimp | 判定 | 原因 |
|---|---|---|---|---|---|---|
| `drefine/Interrupt_DR.thy:491` | auto(drule 后,[1]) | DRefine | 37520 | FAILED | REJECT | 多目标/elim 回溯必要 |
| `infoflow/PolicySystemSAC.thy:921` | auto(case_tac[!] 后) | InfoFlow | 51210 | FAILED | REJECT | 多目标 |
| `invariant-abstract/ARM/ArchEmptyFail_AI.thy:88` | force(subgoal) | AInvs | 83191 | FAILED | REJECT | classical 必要 |
| `infoflow/ARM/ArchSyscall_IF.thy:171` | auto(done 前) | InfoFlow | 26128 | FAILED | REJECT | classical 必要 |
| `invariant-abstract/ARM/ArchTcb_AI.thy:120` | fastforce(cte_wp_atE 分支) | AInvs | 81514 | FAILED | REJECT | elim 分支必要 |
| `infoflow/ARM/ArchRetype_IF.thy:194` | fastforce | InfoFlow | FAILED(import/自引用) | — | SKIP | 基线拿不到 |
| `refine/ARM/Refine.thy:875` | fastforce(subgoal) | Refine | FAILED(import) | — | SKIP | 基线拿不到 |
| `lib/Monad_Commute.thy:38` | fastforce | Lib | FAILED(parse) | — | SKIP | session/parse |
| `lib/Word_Lib/More_Word_Operations.thy:887` | fastforce | Word_Lib? | — | — | SKIP | session 不确定 |
| `infoflow/PolicyExample.thy:80` | fastforce …+ | InfoFlow | 2.2s(<15s) | — | SKIP | 太便宜 + `+` 多目标 |
| `access-control/Syscall_AC.thy:697` | wpsimp | — | — | — | SKIP | 非本配方(wpsimp) |
| `invariant-abstract/ARM/ArchUntyped_AI.thy:175` | 已是 clarsimp | — | — | — | SKIP | 无可 ablate |

**结论**:wasted_classical 池对 `fastforce→clarsimp` 配方已基本扫尽。**总计 2 个正例**(VSpacePre_AI:162 −21%、Decode_IF:137 −11.5%),命中率 ~2/25。可测的全 REJECT(seL4 慢 auto/fastforce 的 classical 多为必要:多目标 / elim 分支 / monad 等式 / 指针注入)。再找正例需**不同的 surfacing 启发式**,不是同一个选择器。
