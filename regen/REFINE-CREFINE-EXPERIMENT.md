# Refine/CRefine Repo-Level Regeneration Experiment

## 实验设置

实验冻结代码与规格端点、父 sessions、目标 contracts 和 contract 所属 theory slots。
普通的人类 Refine contract/helper proof layer 被删除，模型需要跨文件重新设计并生成
该 proof-theory layer。CRefine 只有在完整 generated Refine 封存后才能开始。

干净环境是 contract-sufficient semantic substrate，不声称删除 Refine 中每一个 human
certificate。对 contract elaboration 必需的 commands 可原样保留；它们产生的 facts
和 automation effects 被披露，并在 generated proofs 完成后统计实际使用。

当前采用的生产原则：

- fixed contracts 与 contract-owned attributes 不可修改；
- 模型不可读取原始 target Refine Proof source；
- Session Planner 只看 public contracts，不看 human imports 或 premise records；
- File Planner 只读取 fixed contracts，生成 theorem DAG 和 statements；premise records 仅在 Lemma Prover 阶段召回；
- Lemma Prover 只修改 proof body；
- Graph Revision Agent 审核 graph 变化；
- Isabelle 是 statement、proof 和 complete theory 的最终检查器；
- 只有 sealed theory 才能跨文件发布 facts。

## 当前 production campaign

```text
campaign             sel4-refine-semantic-base-e7f7eaec83a2ff5f
Semantic Base seal   semantic-base:84fa9c868ef6da8a329c
Premise snapshot     premise-snapshot:aa2eb59957c028d63be8
Environment gate     environment:7b39a93ab33bc5363601
Theory run           refine-e7f7eaec83a2ff5f-run-001
```

环境结果：

- 41 个 Refine theory sources；
- 7,102 个 declaration/structure owner spans；
- 21 个 contract-owning theory slots；
- 287 个 exact fixed contracts；
- 500 个 retained semantic-core commands；
- 59 个 mixed/certificate candidates 被整体删除；
- 287 个 exact contract shells 全部 Isabelle GREEN；
- 84,219 条 legal premise records，其中 frozen parents 83,145 条、retained
  Semantic Base exports 1,074 条；
- Context Effect Disclosure 为 `complete`，Environment Gate 已验证并允许 proof
  generation。

该 campaign 不声明 command-minimal：`fixed_point_complete=false`、
`command_minimality_claimed=false`。Retained semantic effects 会在 generated theory
封存时继续审计实际依赖。

## Session Planning 结果

正式 Session Planner 使用 audited `gpt-5.6-sol`，完整 prompt 和 provider stream 已
保存且无 tool events。规划结果：

```text
files                21
contracts            287
candidate edges      15
accepted edges       14
rejected edges       1
contract-composition 7
operation-call       7
layers               2
audit                PASS
replay               reconstructed-and-unchanged
```

Strong contract-composition evidence 把 5 个 consumers 放入第二层；operation-call
evidence 只决定同层 serial priority，不被解释成 proof dependency。

```text
layer 0: ArchAcc_R, CSpace1_R, Interrupt_R, Syscall_R, TcbAcc_R,
         Schedule_R, Arch_R, Bits_R, CNodeInv_R, Detype_R, Finalise_R,
         InterruptAcc_R, KHeap_R, Refine, Retype_R, Untyped_R
layer 1: CSpace_R, IpcCancel_R, Ipc_R, Tcb_R, VSpace_R
```

同层不声明独立或安全并行；第一轮仍使用 list 中的 serial order。

## Theory Planning vertical slice

`Refine.ArchAcc_R` 已通过新的 File Planner 路径：

- File Planner 只看到 24 个 fixed contracts 和 policy metadata；
- prompt 中没有 premise names、premise statements、snapshot 或 retrieval results；
- `gpt-5.6-sol` 在 `clean_runtime=true` 下产生 initial graph；
- graph 含 24 个 contract roots 和 4 个 generated helper leaves；
- graph 绑定 campaign、Environment Gate、Session list、contract package 和 transcript；
- graph payload hash 验证通过；
- Isabelle whole-graph statement-shell check GREEN；
- canonical ledger 只在 GREEN 后标记 `ArchAcc_R = planned`；
- 重放 `plan-file` 没有再次调用模型。

当前尚未开始 ArchAcc_R 的单 Node proof generation。

## 当前状态

```text
环境准备与冻结                 PASS / SEALED
Context Effect Disclosure      COMPLETE
Premise Catalog                VERIFIED
Environment Gate               PASS
Repo / Session Planning        PASS / REPLAYED
Theory Generation run          READY
ArchAcc_R theorem DAG          PLANNED / ISABELLE-SHELL GREEN
ArchAcc_R node proofs          NOT STARTED
其他 20 theories               PENDING
Refine session seal            NOT STARTED
CRefine                        BLOCKED until full Refine seal
```

## 评价方法

环境层报告：

- retained command count 与 selection policy；
- retained commands 携带的 human-certified facts；
- `simp/wp/cong` 与 named automation effects；
- refinement-shaped exports；
- generated proof source 对 retained fact 的唯一名称引用；
- Isabelle certificate 对 retained fact 的实际 dependency；
- certificate dependency 与 active automation pool 的关联量；仅 pool membership 不计使用。

Theory 与 repo 层报告：

- contract coverage 与 RED nodes；
- theorem/helper 数量、proof size 与 build time；
- premise retrieval 数量、命中和实际使用；
- proof-term 与 automation dependencies；
- helper reuse、cross-contract fan-out 与 graph revisions；
- cross-file imports 和 generated premise publication；
- assembly stabilization 次数与原因；
- 与 human proof corpus 的 proof structure、复用和维护性差异。

完整 Refine 成功要求 21 个 theory slots 全部 sealed、287 contracts exact-once GREEN，
并能从 frozen environment 与 generated predecessors clean-build。部分结果只能报告
为 partial campaign，不能进入生产 CRefine。
