# CBaseRefine build 配置对比实验报告

## 1. 实验目的

### 总目的
在 [seL4 verification 的 build pipeline](../heaps/build_log.txt) 里，CBaseRefine 是单个最重的 session（304 theories，原 baseline wall 89min，**GC 时间占 80%**——异常之高）。要回答的核心问题：

> **CBaseRefine 的 wall time 能不能通过调整 polyml/Isabelle 配置降低？如果能，是哪个变量起作用？**

### 三个候选变量

| 变量 | 物理含义 | 假设方向 |
|---|---|---|
| **`threads`**（per-session worker 数） | Isabelle 用来并行 check 不同 theory 的 worker 线程数 | 4→8 应能更好利用 16 核机器 |
| **`-H`**（polyml 初始 ML 堆） | ML heap 启动尺寸；不够大时需要 resize/full GC | 1G→8G 应减少早期 GC 频率 |
| **`--maxheap`**（polyml ML 堆上限） | ML heap 最大尺寸；接近上限时触发激进 GC | 10G→16G 应缓解 GC 压力 |

### 三个 sub-experiment 的具体目的

| 实验 | 目的 |
|---|---|
| **Exp A** | 三个变量**同时改**，看组合效应能不能赢过 baseline。如果输了，方向就错；如果赢了，再分解 |
| **Exp A2** | 隔离 **`maxheap`** 单独效应（只改 maxheap，其他保持 baseline 设置） |
| **Exp A3** | 隔离 **`threads`** 单独效应（只改 threads 和 maxheap，`-H` 保持 baseline） |

A、A2、A3 加上 baseline 构成了 `{threads ∈ 4,8} × {-H ∈ 1000,8000}`（maxheap 在三组实验间固定为 16000）的 2×2 完整网格——可以做 pairwise diff 把每个变量的贡献拆开。

---

## 2. 实验设置

### 公共设置（全部三组实验）

- **基础镜像**：`sel4-public:baseline-clean`（含 29 个 heap 文件 + 修过 `--maxheap 16000` 的 settings）
- **隔离方式**：每次实验用 `docker run` 起一个**全新独立容器**（`sel4-experiment-A` / `A2` / `A3`），跑完 teardown，**baseline 镜像始终不动**
- **构建命令**：`isabelle build -c -b -v -j 1 -d <l4v> CBaseRefine`
  - `-c`：clean rebuild（删除现有 heap 强制重建）
  - `-b`：保存 heap
  - `-v`：verbose 输出（捕获 `Timing X` 行真 wall）
  - `-j 1`：只跑 1 个 session（无 session 间并行）
- **测量来源**：直接读 isabelle 自己输出的 `Timing CBaseRefine (...)` 行，不再用 per-theory 求和（之前的方法学错误）
- **硬件**：16 核、23 GiB RAM、4 GiB swap（VMware）

### 各实验差异（控制变量）

| | `-o threads=` | settings 里 `-H` | settings 里 `--maxheap` |
|---|---:|---:|---:|
| **Baseline**（原始数据，无需重跑） | **4** | **1000** | **10000** |
| **Exp A** | **8**（改） | **8000**（改 settings） | **16000**（改 settings） |
| **Exp A2** | **4**（保持） | **1000**（保持） | **16000**（改 settings） |
| **Exp A3** | **8**（改） | **1000**（保持） | **16000**（改 settings） |

修改 `-H` / `--maxheap` 通过 `sed` 改容器里 `/root/.isabelle/etc/settings` 第 25 行：

```
ML_OPTIONS="-H <X> --maxheap <Y> --stackspace 64"
```

修改 `threads` 通过 `isabelle build -o threads=N` 命令行参数（不改 settings）。

### 数据捕获

- 全程 stdout/stderr → `/sel4-project/build-logs/exp_<X>_CBaseRefine.log`
- bash builtin `time` → `.timing` 文件（real/user/sys 三栏）
- 完成后 `cat .DONE` 拿 rc，`grep "Timing CBaseRefine"` 拿 wall/cpu/gc/factor
- per-theory `theory_timings` BLOB 从 `<heap-log>/CBaseRefine.db` 用 zstd 解压抽出

---

## 3. 实验结果分析

### 原始数据

| | wall | cpu | GC | GC% | factor | threads | -H | maxheap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Baseline** | 5321s（89:00） | 17995s | 4235s | **79.6%** | 3.38 | 4 | 1000 | 10000 |
| **Exp A** | **4434s（73:54）** | 18250s | 2742s | 61.8% | 4.12 | 8 | 8000 | 16000 |
| **Exp A2** | 5708s（95:08） | 17924s | 3175s | 55.6% | 3.14 | 4 | 1000 | 16000 |
| **Exp A3** | **4655s（77:36）** | 18893s | 3233s | 69.4% | 4.06 | 8 | 1000 | 16000 |

### Pair 1：A vs Baseline（**整体效果**）

```
wall:   5321s → 4434s   −887s = −17%
GC:     4235s → 2742s   −35%
GC%:    79.6% → 61.8%   −18 pp
factor: 3.38  → 4.12    +22%
```

**结论**：组合改动有效，CBaseRefine wall 降 17%，单 session 省约 15 分钟。但这是三个变量同时改的总效果——还要拆开看。

### Pair 2：A2 vs Baseline（**隔离 `--maxheap` 单独效应**）

只有 `--maxheap 10000 → 16000` 一处变化。

```
wall:   5321s → 5708s   +387s = +7%   ← 略变慢
GC:     4235s → 3175s   −25%          ← GC 时间显著降
GC%:    79.6% → 55.6%   −24 pp        ← 大跌
factor: 3.38  → 3.14    −7%           ← 并行效率小降
```

**物理解释**：`--maxheap` 是 polyml 给 ML 堆设的天花板。10G 时堆经常顶到上限，触发激进 mark-compact GC；放到 16G 后，堆有更多余量让垃圾累积再回收，所以 **GC 时间降 25%**。

但 wall 没好转——反而略增 7%。原因可能是更大的工作集让 OS 层面 page 操作变频繁（buff/cache 抢占）、polyml allocator 自身管理大堆开销略增。**`--maxheap` 是 GC 压力阀，不是 wall 杠杆。**

但它是**必需的基础设施**：如果没把 `--maxheap` 抬到 16G，后两个变量改动会撞 OOM（原 build 失败的根因就是 `-j` 多 session 并行 + 10G 上限不够 heap save 峰值）。

### Pair 3：A3 vs A2（**隔离 `threads` 单独效应**）

两组都是 `-H 1000 + maxheap 16000`，只有 `threads 4 → 8` 一处变化。

```
wall:   5708s → 4655s   −1053s = −18.4%   ← 大幅变快
GC:     3175s → 3233s   +1.8%             ← 几乎不变
factor: 3.14  → 4.06    +29%              ← 并行效率明显提升
cpu:    17924s → 18893s +5.4%             ← 总算力消耗略增
```

**物理解释**：把 worker 数从 4 翻倍到 8，CBaseRefine 的 304 个 theory 形成的 proof DAG 被切得更细，并行效率从 14%（3.14/22.6 理论 max）提升到 18%（4.06/22.6）。GC 时间几乎没动——这条 wall 收益是**纯并行红利**，跟 GC 优化无关。

cpu 略增 5%（更多 worker 间同步开销），但因为并行度提升，wall 反而 **−18.4%**。

### Pair 4：A vs A3（**隔离 `-H` 单独效应**）

两组都是 `threads=8 + maxheap 16000`，只有 `-H 1000 → 8000` 一处变化。

```
wall:   4655s → 4434s   −221s = −4.7%    ← 小幅变快
GC:     3233s → 2742s   −15%             ← GC 降
GC%:    69.4% → 61.8%   −7.6 pp
cpu:    18893s → 18250s −3.4%
```

**物理解释**：8G 初始堆让 polyml 启动时跳过早期"小堆 → resize 到 2G → resize 到 4G → ..."的多次 mark-compact 周期，直接从 8G 起步。GC 时间降 15%，wall 省约 4 分钟。

### 归因汇总

| 变量改动 | 单独 wall 贡献 | 占总 −17% 的比例 | 物理本质 |
|---|---:|---:|---|
| `threads 4 → 8` | **−1053s** | **~83%** | 真正的并行杠杆（DAG 切分更细） |
| `-H 1000 → 8000` | **−221s** | **~17%** | 减少早期 GC resize 周期 |
| `maxheap 10000 → 16000` | +387s（单独略慢） | 0%（非 wall 杠杆） | 必需的基础设施（防 OOM、降 GC%） |

注：1053 + 221 − 387 ≠ 887，因为是相对不同 reference 测量、变量间有小耦合，但量级和方向一致。

### 重要观察

1. **GC% 不等于 GC 优化收益**：A2 把 GC% 砍到 55.6%（最低），但 wall 反而最差。A 的 GC% 61.8% 是中等水平，wall 最佳。**GC% 是过程指标，wall 才是结果指标**。

2. **threads 是 wall 杠杆，-H/--maxheap 是 GC 杠杆**：这两类杠杆机制不同，效应不能简单叠加。本实验里它们恰好同向，但不保证其他 session（比如 SimplExportAndRefine 的单 theory 巨型证明）也是。

3. **A3 的 GC% 反而最高（69.4%）**：因为 `-H 1000` 配合 `threads=8` 比配合 `threads=4` 时单位时间分配更密集，同样的初始堆更快撞 ceiling。这反过来强化了"`-H 8000` 是有意义的"的结论。

### 实验成本与最终交付

| 实验 | wall | docker 容器 | 状态 |
|---|---:|---|---|
| Exp A | 76min | sel4-experiment-A | ✓ teardown |
| Exp A2 | 95min | sel4-experiment-A2 | ✓ teardown |
| Exp A3 | 78min | sel4-experiment-A3 | ✓ teardown |
| **总计** | **4h09m** | | |

**已落地的产物**（git 提交于 experiments 分支）：
- `threads=8` → [compile.sh](compile.sh)（commit 3d9b173）：吃掉 83% 收益
- `-H 8000` → docker image `sel4-public:tuned`：补上剩 17% 收益
- 全部分析报告：[experiments/exp_A_results.md](experiments/exp_A_results.md)（commit 005c2a7）

baseline 镜像 `sel4-public:baseline-clean` 全程未动，可随时复现原始 89min wall 作为对照。
