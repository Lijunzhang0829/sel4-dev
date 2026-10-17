# VERO / 通用 Codex 实验稳定性任务（2026-10-14）

后续 B005 实战测试揭示了 VERO 的跨 theory 误作废与同 theory 后置草稿失败清零问题；下一发布的逐 contract GREEN 保留、seal 修复例外及 A/Codex 对齐要求见 [GREEN-CONTRACT-ACCOUNTING-20261015.md](GREEN-CONTRACT-ACCOUNTING-20261015.md)。冻结测试结果仍以原账本和独立审计校正口径分别呈现。

目标是在不干预证明规划与生成决策的前提下，让两种方法的原生检查、异常恢复、资源占用及 token/时间结果可审计。新代码只进入新发布；旧 run 的冻结绑定和历史账本不原位改写。正式实验需在新发布通过机器 B 预检后单独启动。

| 序号 | 任务 | 验收标准 | 当前状态 |
| --- | --- | --- | --- |
| S0 | 核对编辑和检查粒度 | 说明 VERO/Codex 一次编辑可覆盖的证明、原生检查范围和 seal 语义 | 完成 |
| S1 | 统一原生检查收据 | 请求、上下文、源码及日志哈希，检查范围、超时和耗时可追溯；收据不替代 Isabelle 审计 | 完成；A/B 原生验收通过 |
| S2 | 修复 VERO 依赖失效 | 上游 theory 改动会撤销下游当前 seal/GREEN，按依赖顺序重验；历史首次 GREEN 和成本仍保留 | 旧验收通过；B005 新发现局部快照误撤销 Bits seal 与同 theory 后置失败清零，待按逐 contract 证据修正 |
| S3 | 分类并恢复 provider 故障 | auth 立即暂停；短暂故障有界重试；未知暂停；已接受工具调用不重放；刷新凭证可续跑 | 完成无模型验收；真实 runner/Service + 脚本 CLI 的 503/401 在 A/B 通过 |
| S4 | 统一 native 资源边界 | 新启动可选择共同宿主槽、冻结配额，崩溃释放锁；检查容器清理与驻留内存 | 完成活动检查仲裁；B 双方法共享槽原生验收完整退出。空闲驻留容器内存不受共享槽限制 |
| S5 | 可重建实验统计 | 首次 GREEN、当前有效 GREEN、seal、集成、共享/未归属 token、暂停时间分列 | 旧统计回归通过；B005 显示 theory 级失效会低估逐 contract 当前 GREEN，需修正投影并新增实战回归 |
| S6 | 双方法发布前验收 | 完整回归、模型免费原生正反例、seal/build、provider 故障注入、断点恢复和清理通过 | 完成无模型验收；A 全量 2014 项，B 最终发布 93 项聚焦及原生四组通过；正式 48 小时运行未启动 |
| S7 | 验证 VERO 增量路径 | 只复用审计完备且源码、导入、上下文完全相同的 GREEN 收据；变更时重新原生检查 | 同源复用完成并经 B 原生验收；一般变更后缀增量未启用，仍需影子等价测试 |

## 编辑与检查范围

VERO 的共享工作区允许一次改多个 lemma 和多个 `.thy`。`check MODULE` 快照所有规划的 theory，抽取并渲染原始 VERO slot，然后对请求 theory 及其导入闭包中需要验证的模块逐个调用完整 theory native check。`VERO_RESULT` 标记是 contract 接受依据；每个完成 theory 还须独立 seal/build。一次 VERO 请求可以触发多个完整检查。

通用 Codex 的 `edit_theory` 可以提交整个 theory 文本或多个精确替换，`check_theory` 可以选择多个 `entry_ids`；harness 没有强制逐 lemma。`theory_document.baseline` 找到与已接受源码相同的最长 proof 前缀，驻留 `BatchWorker` 验证受影响后缀。实际是否一次只更新一个 lemma，是 agent 的行为，应从日志观察，不作为额外约束。比较 token/时间时记录 `check_scope`、冷检查数、前缀准备及候选执行时间；不得把 VERO 全 theory 冷检和 Codex 后缀检查视为相同工作量。

VERO 源码另有 `IncrementalChecks`，但机器 B 的共同应用层入口目前没有使用它；其完整源码键缓存与截断 prefix 检查也不等同于 Codex 已接受前缀上的变更后缀执行。当前 S7 只实现进程内、严格同源的 GREEN 收据复用：必需的 `VERO_RESULT` 齐全且已发布、导入已认证，缓存命中前复核原始源码、日志和 manifest；证明体或导入改变就冷检，seal 仍独立执行。真正的 VERO 后缀增量需要专用 worker、失败候选隔离、完整审计和与冷检的影子等价测试，不能直接接 Codex worker。

## 成本与发布原则

一次响应可探索多个目标，也可修改多个 proof body，所以每 contract/helper 的 token 只能按当时**声明的工作范围**归档。单目标区间仍不等于该证明的因果精确成本；多目标成本列为 shared，没有目标声明则列为 unattributed，缺失 provider 用量列为 unknown。实验总 token 以去重的 provider 累计快照为准，时间同时记录连续运行、显式暂停及 native 操作分解。旧 run 不因新统计代码改变历史。

机器 B 的新发布应由 `regen.tools.application_release` 导出包含未提交新增代码的哈希清单，在新目录验证；旧 release、旧 run 和 `CURRENT.json` 不改。正式并行运行时两启动器必须使用同一宿主 inode 的共享锁目录和独立的授权刷新链。共享槽只仲裁活动 native 操作，不能限制闲置驻留 Isabelle worker 的总内存。退出清理只关闭本池拥有的 worker，不再争用原生检查槽；保留的 `shared_host_cleanup_timeout` 是旧冻结绑定兼容字段，不再限制这种清理。

本机模型免费原生证据：`reports/regen/experiment-stability-20261014/vero-exact-green-native-smoke.json` 验证同源复用及 import 变化冷检；`reports/regen/experiment-stability-20261014/vero-dependency-native-smoke.json` 验证 5 次父子冷检、4 次 seal/build、依赖失效与恢复。完整原始日志和收据分别在对应的 `/tmp/vero-exact-green-native-smoke-20261014-003/` 与 `/tmp/vero-dependency-native-smoke-20261014-003/`，均无模型调用。

新增导入路径的原生证据：`reports/regen/experiment-stability-20261014/vero-dynamic-dependency-native-smoke.json` 完成 6 次 native check、4 次 seal/build、0 次模型调用。它覆盖动态父节点未封存时子节点阻断、完全同源复用、父源码变化后子节点冷检与旧 seal 撤销、重封恢复及父源码缺失撤销。共同 cold receipt 现在包含所有挂载的生成 theory 与候选文件的逐文件哈希；单次收据仍不替代原生审计。

若 VERO 新增导入槽非法或成环，原始 `check` 请求仍由 VERO 拒绝；应用层在返回前写 `source_invalid` 并保守撤销当前 GREEN、seal 和集成投影，历史首次 GREEN 与 token 收据保留。`progress` 也校验有效导入图，遇到环时返回 `imports_error` 并撤销当前状态；直接调用适配器也会拒绝成环导入。有效的下一次 native check 才重新建立当前状态。原 runner 的轮次末和恢复评估绕过工具端点，因此入口还会观察原始工作区的缺失/非法源码；若 evaluator 因 hygiene 或依赖失败跳过 native check，则撤销该 theory 及其消费者的旧 seal。对应回归覆盖原始槽、`context()` 成环、`progress` 成环、终评拒绝/缺文件以及观察者重放；这些拒绝不计为原生证明失败。

VERO 新启动器已将短暂 CLI/provider 故障限定在连续 30 分钟恢复窗口内，并仅从已验证的 runner checkpoint 建立新 invocation；认证失败暂停，未知失败也暂停。凭证刷新状态保存在实验输出目录外的私有 HOME，续跑继续使用该状态；两方法的并行启动需要不同刷新链及同一宿主凭证租约目录。正常规划消耗连续运行预算，仅确认的 provider 恢复等待记为排除时间。`regen/tests/current/test_vero_runner_fault_injection.py` 使用真实 runner、Unix Service 和 CodexAgent 子进程，以脚本 CLI/假 native checker 注入 503、401：503 续跑后已接受的 `scope`/`check` 均只调用一次，401 保留 checkpoint 暂停，token 与有效时间断言通过。该测试不调用真实模型，也不替代 Docker/Isabelle 原生验收。

发布后验收记录：机器 B 的 `/data/zljj/vero-a-unified/experiment-stability-20261014-004/acceptance/VALIDATION.json` 与同目录 `COMMANDS.md`。004 发布校验 544 个文件，93 项聚焦测试通过；003 的 Codex/VERO 共享槽、VERO 同源复用、冻结依赖和动态导入四组真实 Isabelle 检查通过，模型调用均为 0。003 与 004 的 271 个运行文件哈希完全相同，故原生结果适用于 004；差异仅为本文档和新增故障注入测试。此段为 004 导出后的文档状态更新，004 内的文件仍以冻结 manifest 为准。旧 run 和 `CURRENT.json` 均未修改；未运行正式模型、凭证或 48 小时长跑。
