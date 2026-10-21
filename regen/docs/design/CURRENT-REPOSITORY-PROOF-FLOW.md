# 当前仓库级证明生产完整流程

本文描述默认 `document` / theory 源码工作区路径。仓库级 theory DAG 是规划、调度和
认证成果发布结构；theory 内不要求 File Planner 维护 proof/helper DAG，`.thy` 源码顺序是
探索状态的权威表示。旧 graph/GRA 接口仅用于兼容与历史回放。

```mermaid
flowchart TD
  subgraph M0[冻结输入与可信边界]
    C[固定 contracts\nstatement + owned context]
    S[Semantic Base\n合法定义与 premises]
    X[checker context / policy\n模型与资源配置]
    H[输入与实现摘要绑定]
    C --> H
    S --> H
    X --> H
  end

  subgraph M1[仓库规划：产生 theory DAG]
    RP[Repository Planner Agent\n读取去归属 contracts 与合法语义资料]
    PLAN[计划草稿\ncontract→theory exact ownership\ntheory imports / order / reasons]
    SOFT[软协作信息\ncollaboration groups / reuse suggestions]
    PV{结构验收}
    PF[失败反馈\n缺失/重复 contract、非法名、悬空引用、环]
    PRE{原生 oops 预检\n声明、context、imports 可加载?}
    REV[同一 Planner 修订]
    FREEZE[冻结 accepted plan\nplan id + hashes + evidence]
    DAG[(Theory DAG\n硬 import/provider→consumer 边)]
    H --> RP --> PLAN
    RP --> SOFT
    PLAN --> PV
    PV -- 否 --> PF --> REV --> PLAN
    PV -- 是 --> PRE
    PRE -- 否 --> REV
    PRE -- 是 --> FREEZE --> DAG
  end

  subgraph M2[编译与初始化]
    COMPILE[编译 file-generation-list\n生成 theory 源码骨架]
    DRAFT[每 theory 私有 draft.thy\n固定 imports/contracts + oops]
    STATE[状态初始化\nwaiting / ready]
    DAG --> COMPILE --> DRAFT
    DAG --> STATE
  end

  subgraph M3[仓库级并发调度]
    READY{全部硬前驱 sealed?}
    WAIT[waiting\n不占 File Planner 槽]
    SLOT{有 theory agent 槽?}
    START[启动/恢复一个持续 File Planner\n单 theory 单 writer]
    STATE --> READY
    READY -- 否 --> WAIT
    READY -- 是 --> SLOT
    SLOT -- 否 --> STATE
    SLOT -- 是 --> START
  end

  subgraph M4[theory 内持续源码工作区：无显式局部 proof DAG]
    OBS[读取当前 draft、proof state、反馈、\n合法 facts、sealed 上游 proofs]
    EDIT[同一 File Planner 直接编辑 .thy\n主证明 + 多个 helper + 多个 contracts]
    LOCAL[源码/请求验证\n固定 statement/imports、禁止 sorry、\n版本/hash/stale 检查]
    REJECT[请求拒绝并返回独立错误]
    CHECK[check_theory]
    START --> OBS --> EDIT --> LOCAL
    LOCAL -- 非法 --> REJECT --> OBS
    LOCAL -- 合法 --> CHECK
  end

  subgraph M5[当前 Isabelle 检查与接受语义]
    EXTRACT[按源码顺序提取已填写 proofs\noops 不发布定理]
    BASE[恢复/重放 accepted prefix\n持久 worker；必要时 cold fallback]
    POOL{取得 native Isabelle slot}
    RUN[执行 candidate transitions\n当前：首个异常停止]
    RESULT{整批全部通过?}
    RED[多项状态反馈\ncompleted_uncommitted / failed / not_reached\n保留 draft 与旧 accepted revision]
    AUDIT[依赖、skip-proof、oracle、\n新公理与证据绑定审计]
    ACCEPT[原子接受完整 candidate revision\n写 journal/checker evidence]
    CHECK --> EXTRACT --> BASE --> POOL --> RUN --> RESULT
    RESULT -- 否 --> RED --> OBS
    RESULT -- 是 --> AUDIT
    AUDIT -- 否 --> RED
    AUDIT -- 是 --> ACCEPT
  end

  subgraph M6[theory 完成、封存与知识发布]
    COMPLETE{仍有 oops/未完成 contract?}
    CONT[继续在同一工作区证明]
    SEALREQ[请求 seal]
    FULL[完整 theory check + audit]
    BUILD[独立 session/theory build]
    SEALED[sealed theory\n冻结源码、事实索引、证明与证据]
    PUBLISH[仅向 DAG 后继发布认证成果]
    ACCEPT --> COMPLETE
    COMPLETE -- 是 --> CONT --> OBS
    COMPLETE -- 否 --> SEALREQ --> FULL --> BUILD --> SEALED --> PUBLISH
  end

  subgraph M7[DAG 推进与全仓集成]
    UPDATE[更新后继未满足前驱数]
    MORE{还有未 sealed theory?}
    REPO[检查 exact coverage、plan/source/evidence 绑定]
    IBUILD[按 theory DAG 完整 repository build]
    FINALAUDIT[最终依赖与禁止机制审计]
    VERIFIED[repository verified]
    PUBLISH --> UPDATE --> MORE
    MORE -- 是 --> STATE
    MORE -- 否 --> REPO --> IBUILD --> FINALAUDIT --> VERIFIED
  end

  subgraph R[贯穿全流程的恢复与观测]
    JOURNAL[(不可变 journal / transcripts / metrics)]
    RECOVER[重启时校验冻结绑定\n重放 accepted state 与 scheduler state]
    COST[分别记录模型、checker、排队、\n规划、seal 与 integration 成本]
  end

  FREEZE -.-> JOURNAL
  ACCEPT -.-> JOURNAL
  SEALED -.-> JOURNAL
  VERIFIED -.-> JOURNAL
  JOURNAL --> RECOVER --> STATE
  START -.-> COST
  RUN -.-> COST
  BUILD -.-> COST
```

## 图结构边界

- **保留且必要**：仓库级 theory DAG。硬边来自 imports/provider-consumer 依赖，决定
  readiness、并发、sealed 成果发布、恢复和最终构建顺序。
- **不作为默认 Agent 状态**：theory 内 proof/helper DAG。File Planner 直接维护 `.thy`，
  新增 helper 和失败驱动的策略修订都由同一持续 Agent 完成。
- **允许内部派生**：checker 可从源码顺序、实际 theorem dependencies 和审计证据生成声明
  依赖信息，用于失效、`blocked` 归因和复用统计；它不是要求模型同步维护的第二事实源。
- **软关系不变成硬边**：reuse suggestion 或 collaboration group 不应自动阻止 theory 启动；
  只有真实 imports/逻辑前提形成 DAG 硬边。

## 当前实现与计划改进的边界

当前检查按源码顺序运行并在首个 Isabelle 异常处停止，整批通过后才原子接受；因此失败反馈
可区分已执行未提交、首个失败和未到达项，但不会继续验证失败点之后的独立 proof。计划中的
多结果事务检查将在同一冻结上下文和常驻 worker 中继续验证独立改动，逐项返回 GREEN、RED、
blocked 或 not_checked；该计划不改变 theory DAG、seal 或最终仓库验收边界，也不得用 `sorry`
代替已认证证明。
