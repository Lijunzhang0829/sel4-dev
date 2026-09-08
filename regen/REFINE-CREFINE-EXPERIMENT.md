# Refine Semantic Base — 历史实验记录

状态：从 semantic-base 基础快照继承的运行记录，本次未重新读取或验证 campaign artifacts。
其中 PASS、SEALED 和模型结果仅描述各自历史运行，不代表当前分支已验证可运行。
当前研究边界见 [Architect Experiment](UNIFIED-PROOF-ARCHITECT-EXPERIMENT.md)。

## 历史实现设置

实验冻结代码与规格端点、父 sessions、目标 contracts 和 contract 所属 theory slots。
普通的人类 Refine contract/helper proof layer 被删除，模型需要跨文件重新设计并生成
selected contracts 及其生成辅助证明。原记录设想 Refine→CRefine 联合运行，但下面的
实际记录仅涉及 Refine；独立／联合 CRefine 不作为当前已确定的要求。

环境使用固定 semantic-core baseline，不声称删除每一个 human certificate，也不声称
保留 commands 已逐项证明必要。Baseline 支持目标 elaboration；facts 和 automation effects
另行披露，实际使用需在生成 proofs 完成后统计。

以下是历史运行所采用的输入政策，不是拟议仓库规划扩展的永久限制：

- fixed contracts 与 contract-owned attributes 不可修改；
- 模型不可读取原始目标 Refine 证明层；
- Session Planner 只看 public contracts，不看 human imports 或 premise records；
- File Planner 只读取 fixed contracts，生成 theorem DAG 和 statements；premise records 仅在 Lemma Prover 阶段召回；
- Lemma Prover 只修改 proof body；
- Graph Revision Agent 审核 graph 变化；
- Isabelle 是 statement、proof 和 complete theory 的最终检查器；
- 只有 sealed theory 才能跨文件发布 facts。

## 前驱记录：d6c

历史 campaign `sel4-refine-semantic-base-d6c26eb4fae33cab` 记录了 41 个 theories、287 个
contracts、500 个 retained core commands，以及 84,219 条 premise records。其 disclosure
为 pending，在后续 gate 收紧后不能作为合法的新运行输入。

该 campaign 的 Session Planning 记录为 17 个候选、14 个 accepted、3 个 rejected，全部
accepted edges 为 weak operation-call evidence，形成一层。它只是该次规划的结果，不能
据此断言 contract-only 输入永远无法产生 strong scheduling edges。

## 后继记录：e7f

```text
campaign             sel4-refine-semantic-base-e7f7eaec83a2ff5f
Semantic Base seal   semantic-base:84fa9c868ef6da8a329c
Premise snapshot     premise-snapshot:aa2eb59957c028d63be8
Environment gate     environment:7b39a93ab33bc5363601
Theory run           refine-e7f7eaec83a2ff5f-run-001
```

原记录的环境结果：

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

## e7f Session Planning 记录

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

## e7f Theory Planning vertical slice 记录

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

该记录结束时尚未开始 ArchAcc_R 的单 Node proof generation；本次不推断之后的运行进展。

## e7f 记录时的状态

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
CRefine                        NOT RUN (historical joint-run plan)
```

## 记录位置与证据限制

历史 campaign 数据的仓库相对位置为
`reports/regen/refine-semantic-base/<campaign-id>/`。它们不是本分支迁移的代码文件，不能假定
该目录在每个 checkout 中存在；因此不提供指向另一 worktree 或本机报告目录的“active”链接。
运行数字、hash 和 PASS 状态保留为历史线索，正式复用须验证实际 artifacts 与当前 frozen inputs。

## 历史评价计划

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
- 原记录提出与 human proof corpus 比较；这不是本轮生成输入或已获准执行的步骤。
  本次文档维护不访问被删除的人类证明，后续如需独立评估另定协议。

就上述历史 scope 而言，完整成功需要 21 个目标 theory slots 和 287 contracts 全部完成并
通过整体构建。其他运行的目标数从自身冻结 inventory 决定。Ledger traversal complete 不替代
最终验收；如以后执行联合 CRefine 任务，才要求它绑定相应完整 generated Refine。
