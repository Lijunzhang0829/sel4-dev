# 增量实验日志：格式、恢复与验证

状态：2026-09-30。新增格式 `regen-merkle-json-v1` 已接入正式读写路径，
通过显式参数用于新运行；缺省仍是 `inline-v1`。旧实验不原地升级。

## 1. 记录什么

冻结输入、配置、最终计划与最终 theory 文件维持原格式。模型 transcript 仍是
一条追加时间线，不复制进每个 step；其已有审计前缀和成本记录不被截断。

- 仓库规划：`queries` 保存开始事件（请求、调用身份、开始状态）和完成事件
  （响应、结束状态）；`snapshots` 保存计划草稿版本。原有模型审计、验证与最终
  plan 继续保留。`control/state.json` 是可由查询记录恢复的状态投影。
- theory 生成：`batch-requests` 保存请求、候选、反馈与检查证据；
  `acceptance/commits` 保存接受事件。逻辑字段、提交 ID、父提交、成本及证明证据
  不变，但文件内容变为很小的对象根引用。原有持久任务和 transcript 机制继续工作。
- 大对象：运行根目录 `log-objects` 保存内容寻址、无损压缩的不可变 JSON 子树；
  相同源码、反馈、证明或继承记录仅存一次。

保留现有目录名是为了兼容接入，而不是再维护一套平行的日志。
没有把所有权威事件塞进单一 `events.jsonl`：采用原子发布的编号文件，避免半行写入
和多写入者追加问题。`experiment_log events` 可导出 JSONL 摘要；导出不是新权威。

## 2. 为什么不是一条很长的补丁链

每步保存完整逻辑状态的 **Merkle 根引用**，只写发生变化的子对象。
因此每步都是可以随机访问的结构化 checkpoint，无须每 N 步再写一份完整巨型 JSON，
也无需串行应用从初始状态以来的所有文本补丁。对象的字典、列表、值和引用有独立标签，
不与证明内容中的普通字典混淆。

这是存储层的增量，不是跳过证明验证：冷恢复仍通过现有 `ProofCommits` 验证器检查
接受链，历史读取、continuation、sealed reuse、integration 和 heap 回收均使用
兼容读取接口。被引用对象进入缓存 fingerprint，不能因提交文件没变就忽略对象损坏。

## 3. 发布和中断

1. 先压缩并持久化对象，验证已存在对象的哈希。
2. 再原子发布编号记录／commit manifest；这是接受记录的发布点。
3. 最后更新 HEAD／状态投影。

对象已写但 manifest 未发布：只是孤立对象，不构成提交。
manifest 已发布但 HEAD 未更新：重启从提交链恢复，不能重新接受同一步。
缺失／损坏的对象：硬失败，不能退回旧状态冒充成功。
不可变对象并发发布幂等；缓存有解压字节预算。

规划查询同样先记录开始：完成记录已落盘但状态投影未更新时可以恢复；只有开始而
没有完成时保留请求序号并标记 `interrupted_queries`，不自动重发未知请求。
这不是网络服务端 exactly-once 保证，也不替代模型调用原有的重试／成本政策。

跨运行复制必须解码并在目的对象库重新引用，不能只复制 manifest 而丢失对象。
对象库目前不提供自动 GC；保留未引用对象比误删恢复证据安全。

## 4. 使用

新执行运行，在原来的 `proof_session_runner init` 参数后加：

```text
--log-storage regen-merkle-json-v1
```

新仓库规划，在 `repository_planner --planning-input ...` 后使用相同参数。
旧环境规划入口不支持此参数。冻结 contracts 和环境的内容保持原样；新运行记录
所选存储格式。续跑分叉继承格式，旧日志仍可读。

读取新旧日志统一使用：

```bash
python3 -m regen.tools.experiment_log show PATH_TO_JSON
python3 -m regen.tools.experiment_log events PATH_TO_COMMITS_OR_QUERIES
python3 -m regen.tools.experiment_log verify-storage PATH_TO_JSON
```

`verify-storage` 只检查存储可读性／对象完整性，不能宣布证明通过。
外部脚本不能继续假设所有文件都能直接 `json.loads(...)["state"]`，须使用
`read_artifact`。内建的运行、批次恢复、共享工作区、集成和保存步骤工具已适配。

## 5. 验证与测量边界

证据目录：`reports/regen/incremental-log-20260930/`。

固定随机种子 20260930，从实验 015 抽取 Scheduler_Queues 提交 31、54、62，
Capability_Access 提交 13。每例读取前驱和当前记录，核验原始 commit 哈希／parent，
用生产接受验证器核验转换，再构造显式哈希绑定的测试 checkpoint。
62 是真实 document 接受事件，不只有计数更新。

每例在三个窗口真实退出子进程：manifest 发布前、发布后、更新 HEAD 前。
12 个中断均恢复到正确状态，随后到达保存的下一状态，未重复接受，成本字段逐字一致。
这是 **checkpoint 相对恢复**，不是从实验最初开始重新验证所有嵌套前史。
没有调用模型，也没有恢复实验 015。

性能使用新 Python 进程、ABBA 顺序，各模式两次；操作系统页缓存未清空。
正式 RSS 使用 `/proc/self/status` 的进程自身 VmHWM。最初试跑前两例的 getrusage
受启动父进程峰值干扰，原记录保留，但内存结论以 `measurements-002.json` 为准。

| 保存提交 | 旧／新逻辑存储 MiB | 旧／新恢复秒 | 旧／新峰值 RSS MiB |
| --- | --- | --- | --- |
| Scheduler_Queues 31 | 85.67 / 0.37 | 2.545 / 2.407 | 161.7 / 46.2 |
| Scheduler_Queues 54 | 115.75 / 0.49 | 3.702 / 3.272 | 209.0 / 52.6 |
| Scheduler_Queues 62 | 115.38 / 0.53 | 3.963 / 3.446 | 208.0 / 60.9 |
| Capability_Access 13 | 14.45 / 0.26 | 0.523 / 0.519 | 48.9 / 33.1 |

空间同时受结构去重和无损压缩影响。表中每例包含前驱 checkpoint 与下一提交及
少量测试配置，不是整个 theory 的压缩率；实际分配块数也记录在测量报告中。
恢复耗时包含重建及现有接受验证，不包含模型、Isabelle 重启动或重新证明。

如实保留代价：试跑写入从旧版 0.81–5.75 秒变为新版 1.74–9.47 秒，约慢 1.6–2.1 倍。
大量历史重复字段的编码、哈希和对象 fsync 有成本。当前主要收益是磁盘和恢复内存，
不是数量级恢复加速；新生成状态还会受已实现的 attempt-memory 去重影响，需要单独
衡量长运行写入开销。不能把本测试推导为全实验运行时间同比下降。

另有 `native-001` 使用新日志还原实际请求、检查保存的三个生成节点，34.756 秒
得到 GREEN，原生证据保存后在新进程读取通过，容器已清理。没有审计／build 和
接受事件，不等于完整 theory seal；实际收据见该目录 `report.json`。

## 6. 尚未承诺的能力

- 任意断电／文件系统故障下的 exactly-once；本次故障注入为进程退出。
- 完整旧实验一次性无成本迁移、旧目录可立即删除、自动对象回收。
- 新格式下双真实 Planner 长跑或全仓库 integration 已完成。
- 全部外部临时脚本自动理解新格式。

默认不切换旧实验。旧证据、计数和 token 成本均未删除或重置。
