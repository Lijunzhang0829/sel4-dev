# 候选集可信度说明(provenance & caveats)

本目录的 `credible_candidates.json` / `actionable_candidates.json` 是"值得 static-化优化的
lemma 候选集"。这份文档说明**每个数字的来源、可信度、以及不可信的部分(已排除)**。

## 数据源(只用可信的 coherent 数据)

| 数据 | 文件 | 级别 | 可信度 | 用途 |
|---|---|---|---|---|
| session build 时间 | `reports/golden-baseline/walls.json` | session | **高**(golden build `golden-20260529-30gb-j2`,单次 coherent build,j=2 maxheap12000 ARM) | 给 session 加权重(`session_cpu_s`) |
| per-lemma 耗时 | `ranking.json` | lemma | **中高**(golden 期 per-command DB → per-lemma elapsed)**但仅在 coverage≥0.9 时用** | `elapsed_ms` |
| search ratio | `db_candidates_classified_v2.json` | lemma | **measured 仅 1/53**;其余用透明 heuristic proxy(flagged `sf_basis`) | `search_frac` / `search_weight_proxy` |

## 可信度规则(为什么能信)

1. **coverage 门控 ≥0.9**:ranking.json 的 `coverage = max_timed_line / file_lines`。只保留
   完整 timed 的 theory(19/35)。**最大的文件恰恰覆盖率最低、已排除**:
   TcbAcc_R(0.34)、CNode_AC(0.26)、CNodeInv_R(0.02)、Finalise_R(0.23)、KHeap_R(0.47)、
   IpcCancel_AI(0.30)、CSpace_AI(0.43)。→ 这些大文件的耗时**无法可信评估**,需重新 timing。
2. **elapsed ≥ 100ms**:低于此接近噪声地板,提速结论不可信。
3. **provenance 字段**:每行带 `provenance` + `coverage` + `sf_basis`,无任何数字靠猜。

## ★ 必须知道的不可信项

- **当前 heap build DB 不能用**:`/root/.isabelle/heaps/.../log/*.db` 的 mtime 散落在 06-05~06-09
  (非同一次 build),且 `AInvs.db` 是外部 `/sel4-project` build 写的(不同源码树、行号对不上)。
  所以**没有用 DB 直接聚合 per-theory build 时间**——那会引入混合/陈旧数据。
- **search_frac 52/53 是 proxy(heuristic)**,不是 measured。proxy **高估 simp-bound lemma**
  (如 `dmo_bind_ev'` proxy=1.0 但实际是 simp-work,工具闭不了)。→ 所以**不要按
  `recoverable` 排序选 simp-bound 的**;要按 `pattern` 过滤。
- **"proven" pattern ≠ 保证可闭合**:它表示"匹配我们已闭合过某实例的形状":
  - `def-unfold(proven)`:已闭合 **sep_heap_domD'**、**rel_terminate_weaken(789→22ms ACCEPT)** —— **最强**
  - `rule-based(proven)`:已闭合 **ball_subsetE**;但 `states_equiv_for_*` 系列虽属此类、
    之前**未能闭合**(elim 链太深)→ 标 proven 是乐观,需实测。
  - `simp-bound(hard)`:工具**从未闭合**(dmo_bind / set_object 等),不建议投。

## 可信的高耗时 theory(coverage≥0.9,按 hot-classical-lemma 总耗时)

注:是"热点 classical lemma 总耗时"的可信下界,**非** theory 完整 build 时间。

| hot-lemma 总耗时 | n | session(cpu_s) | theory |
|---|---|---|---|
| 28.5s | 68 | InfoFlow(3712) | InfoFlow_IF.thy |
| 19.7s | 13 | InfoFlow(3712) | Retype_IF.thy |
| 2.8s | 6 | InfoFlow(3712) | ADT_IF.thy |
| 2.7s | 65 | AInvs(5695) | Invariants_AI.thy |
| 2.3s | 18 | InfoFlow(3712) | Noninterference_Base.thy |

## 怎么用

- **最稳投资** = `actionable_candidates.json` 里 `def-unfold(proven)` 且 elapsed 大的:
  `rel_terminate_weaken`(已 ACCEPT)、`affects_equiv_scheduler_action`(777ms)、
  `uwr_equiv_Cons_leftI`(523ms)、`empty_fail_updateObject_cte`(512ms,Refine)、`refs_of_rev`(720ms)。
- **高潜力待验证** = `states_equiv_for_*` 系列(1.1–2.5s,rule-based,InfoFlow_IF.thy)——
  若 sonnet+反馈能攻破一个,因高度同构可批量收割,但需实测。
- 复现:`python3 lemma-staticize/scripts/build_credible_candidates.py`

## 若要真正的 per-theory build 时间(当前缺)

需要一次**干净的 coherent build** + `scan_db_timings.py` 重新聚合(walls.json 那种口径)。
当前 DB 被污染,且容器 init flaky + 外部任务争抢,未做。这是已知缺口,非本候选集的依据。
