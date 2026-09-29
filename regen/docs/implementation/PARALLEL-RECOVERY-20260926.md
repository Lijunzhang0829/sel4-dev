# 双 File Planner 异常恢复修复（2026-09-26）

本记录对应 `repository-theory-document-astra-20260926-005` 中断后的基础设施修复。
不改变固定 contracts、模型、证明预算和 Isabelle 接受标准，不导入人工目标证明。

## 触发证据

2026-09-26 12:27:17（America/Toronto），VS Code extension host 收到 renderer
的 terminate 消息并以 code 0 退出；12:27:25 新 host 启动。原实验由 IDE 的
PTY 启动，launcher/coordinator 消失，但两个 Docker Isabelle worker 仍存活。
该时间线支持宿主生命周期导致中断；没有记录证明具体信号，也没有本次
OOM 或 ENOSPC 的直接证据。旧 `running` 文件不能作为存活依据。

## 修改及恢复边界

- `document` 与 `batch/green/seal` 共用提交取消互斥；不可变 commit 写入后立即
  标记已发布，即使后续 HEAD 投影写失败也不允许取消已接受证明。若发布后
  目录 fsync 失败，仍在互斥内确认可见的精确 commit，禁止将其误判为可取消。
- 历史恢复同时识别当前链和嵌套 continuation 源链中的 `document`。
  对 -005 的只读恢复验证保留 Thread 的 14 条、Translation 的 9 条 accepted；
  proofs 不变，Thread request 75 为 interrupted_action，累计请求仍为 75。
- executor 提交失败释放 theory owner，保留不可重复执行的终态身份；取消兼容
  缺失 future，单项清理失败不会跳过其余任务和 worker。只有确认任务线程
  全部退出后才释放 executor 与 run 锁；join 失败保留锁并允许重试清理。
- native resident 淘汰在全局锁外执行；淘汰期间预留容量，仍遵守 resident 上限。
- Docker CLI 退出不视为容器退出。按精确 owned name 回收；失败保留引用供重试。
- Prefix/Batch 容器的 PID 1 guardian 检查独立 owner lease。owner 失联约 60 秒后
  容器退出；正常每 5 秒续租。此资源租约不写周期 progress/runner events，
  不更改候选、命令或证明超时。guardian 脚本纳入 implementation 哈希。
- heap 复制采用同用户、同文件系统的跨线程/进程互斥准入；预估空间并保留
  2 GiB，复制到临时目录后再发布。初始化、theory 初始化、仓库集成都使用
  同一路径；不持有此锁执行证明。策略和复制观测写入运行记录。
- 独立 user systemd 服务托管原 `proof_session_runner parallel-workspace`，
  不引入另一套 scheduler；禁用自动重启和请求重放。服务身份包含 unit、boot、
  invocation 及 PID 出生信息，终态与证明完成状态分别判断。
- 仅在 theory 已 sealed、provider completed、controller 成功关闭后回收该
  theory 的可重建 heap 缓存；核验 seal commit 与精确目录，先持久化审计，
  不删除源文件、证明、请求、日志或 run 的基础 heaps。

## 已完成验证

1. 提交/恢复聚焦回归通过，包括取消先到、提交先到、并发竞争、HEAD
   写失败、不可变记录发布后的目录 fsync 失败、嵌套源链、未提交 GREEN
   不得升级接受、pending 请求计数保留。
2. executor 故障注入：ENOSPC、dispatch/Thread.start 失败、部分初始化取消、
   清理失败、慢淘汰与并发容量边界通过。
3. Docker 实测：SIGKILL owner 后忙碌容器自动退出 125；杀死 Docker CLI 后
   容器仍存活时，close 能回收容器。
4. 原生 Isabelle HOL 双 worker 并行：Prefix 的 False 为 RED、True 为
   speculative GREEN；Batch 的 True 为 GREEN，审计包含相应事实，align 为
   COMMITTED。测试只使用合成命题，不计入 repository contracts。
   证据：`reports/regen/worker-lifecycle-validation-20260926/native-workers-1v_c9w8d/result.json`。
5. 首轮完整 current 回归：789 项，零失败，3 项真实环境测试默认跳过；
   这些 Docker/Isabelle 测试已在前两项中单独执行。
6. 真实 user systemd 专项：启动客户端退出、PTY 关闭后服务继续运行，
   PID/start ticks/InvocationID 保持一致；退出码 0 和 23 正确保留；忽略
   SIGTERM 的父子进程在 2 秒停止超时后全部回收（实测 2.20 秒）。
   追加 SIGKILL coordinator 实测：子进程自动清除，保留 `signal/9` 失败状态。
   五个临时测试 unit 已清理，不曾重启用户 IDE。
   最新证据：`reports/regen/worker-lifecycle-validation-20260926/user-systemd-r_kc_69_/result.json`。
7. 最终组合回归（显式启用全部真实环境测试）：846 项，零失败、零跳过，
   59.223 秒；测试期间实现无漂移，记录包含逐文件 SHA256。
   证据：`reports/regen/parallel-recovery-validation-20260926/regressions-174759.json`。
   包含 supervisor argv/身份/资源准入、封存回收、SIGTERM/SIGHUP 下 ENOSPC
   故障注入及 join 未完成保留运行锁。

## 可重建缓存回收

停止 -005 的两个 orphan 容器后，逐文件哈希核验并回收六处完全相同的旧 heap
副本，共 4,751,759,244 字节。保留 -005 基础 heaps、所有 source、commit、draft、
日志和证据。审计与复制恢复位置见：
`reports/regen/refine-first-run-001/repository-theory-document-astra-20260926-005/heap-reclamation/verified-duplicate-caches-20260926.json`。

## 验收边界

故障测试和合成原生 smoke 证明基础设施行为，不表示 287 contracts 已完成。
真实 continuation 必须保留两项 sealed theory、全部接受证明和已消耗请求；
只有最终 repository integration 成功才允许报告仓库完成。

## 真实续跑与并行验证

已创建 `repository-theory-document-astra-20260926-006`，冻结 82 个实现文件，
复核无漂移。`recovery-verification.json` 逐项核验源与目标的 proofs、
accepted_order、batch_requests 一致；保留 Correspondence_Composition、IRQ_State
两项 seal。Thread_Object_State 请求数仍为 75、24 条证明、14 条 accepted history，
pending request 75 恢复为 interrupted_action；Translation_Entries 请求数仍为 48、
29 条证明、9 条 accepted history。没有人工目标证明干预或请求计数重置。

首次启动被自动安全审查拒绝且未执行。用户随后对将私有仓库派生的 contracts、
证明草稿、相关检索结果及诊断发送至外部 OpenAI `gpt-6-astra` 明确回复
“授权并继续验证”。服务于 2026-09-26 17:58:54 UTC 成功启动，运行身份：
`regen-proof-2cc706d2aca77076eb3fdce1.service`，PID 2319248，InvocationID
`f3ff253b3ba04928a003b70df3c4a26f`。18:15 UTC 复核仍为同一活跃实例。

两个独立真实 Planner 的首轮 proof task 均完整返回 `completed`：
Thread request 76、Translation request 49。两 native slot 重叠 334.565 秒，
实际候选证明检查重叠 18.696 秒；初始化分别 315.435/338.844 秒，owner lease
持续续租，未误杀忙碌 worker。两轮均为普通 `RED/rejected_batch`，不是基础设施
异常，未错误接受整批中先前通过的局部证明。拒绝反馈及任务回执均持久化，
两 Planner 在原 thread 继续编辑并再次检查。

Thread request 77 复用同一 resident worker、无需再次构建前缀，候选耗时
18.466 秒并正常返回 RED。Translation 中间执行 cold symbol lookup，不能
据此声称它也完成 warm 复用。两份已落盘 transcript 前缀均通过
`require_real=True, require_terminal=False` 审计，完成回执的 canonical JSON
SHA256 与结果一致；非终态审计不表示整个 theory 已完成。

18:15 UTC 累计仍为 41/287 accepted contracts、32 helpers、2 sealed theories
（19 个 sealed contracts），无新增 document 接受，未执行最终 integration。
本次已验证真实并行检查闭环，不将候选 RED 或运行存活冒充证明成功；GREEN
发布/取消/恢复仍以已通过回归为证据，后台继续生成证明。未人工干预目标证明。

完整字段、任务 ID、审计 offset/hash 与观察边界见：
`reports/regen/refine-first-run-001/repository-theory-document-astra-20260926-006/dual-planner-live-validation.json`。

获准后的入口是 `regen.tools.parallel_workspace_service start/status/stop`，
使用 `--run-dir reports/regen/refine-first-run-001/repository-theory-document-astra-20260926-006`。
所有控制动作都核验该 run 的独立服务身份；不通过旧 PTY launcher 启动。
