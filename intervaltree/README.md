# intervaltree：模型扩展与具体实现层

这是用于 Isabelle 仓库级证明生成的项目。已实现两部分：将 VERO 的固定区间模型和 56 个原始目标移植到 Isabelle 并扩展；加入上游 Python centered AVL 的人工翻译及精化目标。它是部分验证的第一版，尚未证明整个 Python 库。

当前输入版本为 **`expanded-specs-v2`**。91 个目标声明直接展示 spec 定义中的量词、前提和结论；原命名布尔定义及算法均保留。独立输入构建逐条用 Isabelle 的定义转换检查新声明与原 spec 等价，检查不会证明目标本身。此修订解决规划和审查仅看到命题名称的问题，相关操作定义仍通过查询获取。

**已知反证：`tree_remove_preserves_wf` 不成立，未通过改写算法或削弱前提消除。** 同一个 12 节点输入在固定 Python `Node` 实现中也出现删除后的平衡因子 −2；输入通过 Python 自身的 `verify()`。新跨语言回放确认该例的结构、缓存、内容和查询结果一致，但尚未证明它能由公开 `IntervalTree` API 构造。见 [model-feedback.json](model-feedback.json) 和 [本轮修订报告](../reports/regen/intervaltree-model-repair-20261101-001/REPORT.md)。该反馈及反例 fixture 不进入 `sanitized/`，也不作为生成器的答案。

旧输入已归档，本轮没有重写历史实验。新声明会改变 benchmark/contract 身份，后续实验须重新准备数据集和规划，不能直接续用旧运行的冻结绑定。数学模型的定义文件保持原哈希，因此旧反证仍有意义。

**建模阶段只完善模型、证明目标及其与源码的对应关系，不继续手工编写目标证明。** 验收关注定义、类型、必要终止性、目标可解析性和模型行为检查；目标是否被证明不作为建模阶段完成条件。现有 91 个目标均已设置并通过独立输入构建。

用户随后授权在机器 B 使用 VERO、`gpt-6-astra / low` 运行生成。独立运行编号为 `intervaltree-vero-astra-low-20261027-001`，远端目录为 `/data/zljj/vero-runs/intervaltree-vero-astra-low-20261027-001`。只迁移 `sanitized/` 和未修改的 VERO Isabelle backend，使用原生 `vero.isabelle plan-run`；历史准备证明不作为模型输入。部署、配置和最近观察见 [实验记录](experiments/intervaltree-vero-astra-low-20261027-001/latest-status.json)。该记录是观察时刻的快照，不是实时进度或完成声明。

历史准备证明的检查结果以 [validation.json](validation.json) 和逐目标的 [targets.json](targets.json) 为准；以下证明数量不是当前建模任务的完成率：

| 部分 | 固定目标 | 已检查的准备证明 | 无准备证明 |
| --- | ---: | ---: | ---: |
| VERO 基线 | 56 | 13 | 43 |
| 模型扩展 | 9 | 3 | 6 |
| 具体实现与精化 | 26 | 14 | 12 |
| 合计 | 91 | 30 | 61 |

这些准备证明由本次 Codex 协作编写，用于检查模型与目标的可用性，**不能计入后续证明生成实验的成绩**。目标由封闭的布尔命题定义固定，当前输入展开其正文；没有将未证明目标作为假设或已证明事实导入。`pending` 只表示没有准备证明，并不表示性质一定成立；已证伪状态另见 `model-feedback.json`。完整 session 的 [Interval_Audit.thy](theories/Interval_Audit.thy) 检查每个已标记目标的精确命题、未解除假设和 oracle 依赖；工程中没有使用 `sorry` 或新增公理。

## 第一部分：扩展现有模型

[Interval_Model.thy](theories/Interval_Model.thy) 保留 VERO 的半开整数区间、`overlaps`、`merge_overlaps`、`chop` 算法，并增加重复切割 `cut_all`。合并采用 `strict=False`：相接区间也合并。`chop b b` 保留源模型的行为：覆盖集合不变，但可能将一个区间切成两个相接片段。

[Interval_Baseline.thy](theories/Interval_Baseline.thy) 移植全部 56 个原目标，包括它们的前提、重数与边界条件。它对应原 Lean 规范在固定实现上的 `spec canonical` 实例，未保留 code-and-proof 模式的可替换实现接口。唯一目标改名是 `spec_merge_getElem?_lo_split` → `spec_merge_getElem_lo_split`。人工逐项比对未发现语义偏差，但没有 Lean→Isabelle 的机器检查等价证明。

[Interval_Extensions.thy](theories/Interval_Extensions.thy) 新增：

| 目标族 | 含义 | 状态 |
| --- | --- | --- |
| 精确覆盖长度 | 合并结果的长度和等于输入覆盖的整数点数；规范区间列表也满足此等式 | 2 项已证明 |
| 重复切割覆盖 | `coverage (cut_all cuts xs) = coverage xs - coverage cuts` | 已证明 |
| 重复切割长度 | 规范输入经合法切割后的长度和等于剩余覆盖大小 | 待证明 |
| 碎片数量上界 | 一次合法切割至多新增一个片段；m 次切割后至多 n+m 个片段 | 2 项待证明 |
| 图连通分量 | 以输入位置为顶点、接触或重叠为边；分量数、分量包围区间及其双射对应合并结果 | 3 项待证明 |

这里的“长度”建立在整数端点与整数点覆盖上。碎片和重复切割长度目标显式要求规范输入、切割满足 `begin < end`。相同区间的不同输入位置仍是不同图顶点；空区间不作为顶点。

## 第二部分：增加具体实现层

上游固定为 [chaimleib/intervaltree@1bc406e](https://github.com/chaimleib/intervaltree/tree/1bc406e1f441577c4e421fc51aba2ab67fbd97fb)。原始文件保存在 [vendor/intervaltree](vendor/intervaltree)，哈希见 [source-lock.json](source-lock.json)。

[Interval_Tree.thy](theories/Interval_Tree.thy) 表示真实算法所需的状态：

- centered AVL 节点的中心、区间 bucket、左右子树、缓存深度和平衡因子；区间保留不可变自然数 payload，相同端点但不同 payload 不会被合并为同一条目。
- `add`、`remove`、`discard`，以及单／双旋转、旋转时的区间提升、空 bucket 修剪和前驱提取。
- 全体区间集合与边界计数索引；点查询，以及通过边界索引组合点查询的 `overlap` 和 `envelop`。

这不是将上游树替换成列表的抽象实现。bucket 的集合以无重复列表表示；Python 对象更新改为显式状态传递。逐路径对应关系和语义差距见 [tree_source_map.json](tree_source_map.json)。

[Interval_Refinement.thy](theories/Interval_Refinement.thy) 将两层关联起来：

```text
具体节点、bucket、缓存和边界索引
                 │ tree_entries / all_items
                 ▼
          保留 payload 的区间集合
                 │ erase_payload / coverage
                 ▼
         第一部分的区间覆盖语义
```

已证明的主要功能性质是：在 `state_wf` 下，点查询、重叠查询、包含查询均精确返回满足条件的所有条目。还证明了覆盖投影、空状态、缓存深度、重复插入和缺失删除的行为，以及公开状态包装层的部分集合记账性质。

**更新后的不变量保持仍是核心验证内容，其中删除保持 `tree_wf` 已被证伪。** 包装层 `all_items` 的集合等式不代表树节点更新已正确；已有查询定理也不能自动扩展为任意更新序列的正确性。没有准备证明的目标包括：

- `tree_rotation_content`、`tree_add_content`、`tree_remove_content`。
- `tree_add_preserves_wf`、`tree_remove_preserves_wf`。
- `refine_add_state_invariant`、`refine_remove_state_invariant`。
- `tree_add_fuel_adequacy`、`tree_remove_fuel_adequacy`。
- `refine_successful_trace_invariant`、`refine_successful_trace_point_query`。
- `refine_trace_abstract_content`：以独立集合状态机从初始集合执行操作序列，结果等于最终实际树的内容；不以最终缓存作为规范。

复杂互递归使用显式 `fuel`。预算不足或非法内部状态可返回 `None`；公开包装另行区分无效区间、缺失条目和执行失败。充分性目标要求合法输入存在足够预算，避免仅凭“成功时正确”就声称完全正确。

## 构建和检查

当前建模与目标准备只需在外层仓库根目录执行以下检查，不求解目标：

```bash
python3 intervaltree/tools/prepare_benchmark.py
python3 intervaltree/tools/build.py --directory intervaltree/sanitized --timeout 180 --build-dir intervaltree/build/sanitized
```

以下命令仅供复查已有准备证明及其历史状态，不是当前任务的验收要求，也不要求补齐剩余证明：

```bash
python3 intervaltree/tools/build.py --timeout 180 --build-dir intervaltree/build/final
python3 intervaltree/tools/record_validation.py
python3 intervaltree/tools/audit.py --require-build --build-result intervaltree/build/final/build-result.json
```

默认构建使用本机已有的 `sel4-public:tuned` 镜像和 `/home/lijun/seL4-docker-main/verification/isabelle`；其他环境可用 `--image`、`--isabelle` 或 `--backend host` 指定安装。Docker 构建关闭网络，挂载冻结的只读源文件快照，使用容器内临时 heaps，不写共享 Isabelle heaps。`build.log` 与 `build-result.json` 位于对应构建目录。工具会拒绝源码已变的构建证据。

`prepare_benchmark.py` 会重新生成输入并把状态重置为 `pending`；须在匹配的完整构建和独立输入构建通过后，运行 `record_validation.py` 才能恢复准备证明状态。`sanitized/goals/Interval_Goals.thy` 的 `oops` 仅检查目标能否正确解释，不证明任何目标。

回放已知建模反馈（核对实际 Python 与 Isabelle 执行结果，包括失败的不变量）：

```bash
python3 intervaltree/tools/check_model_feedback.py --output-dir intervaltree/build/model-feedback
```

该检查分别报告 `translation_matches` 和 `post_wf`；本例预期前者为 `true`、后者为 `false`。它是有限执行的一致性检查，不是新的目标证明，也不是完整翻译等价证明。

实际 Python→Isabelle 差分检查：

```bash
python3 intervaltree/tools/differential.py --fetch-deps --output-dir intervaltree/build/differential-default
python3 intervaltree/tools/differential.py --random-traces 16 --steps 16 --output-dir intervaltree/build/differential-deep
python3 -m unittest discover -s intervaltree/tests -v
```

`--fetch-deps` 将校验过下载哈希的 SortedContainers 2.4.0 放入隔离的 `build/python-deps`。测试运行固定 Python 源码，并让 Isabelle 的 `Code_Evaluation` 实际执行翻译后的操作序列；对比区间身份、树内容、不变量、边界计数和三类查询。合法操作若返回 `None`，检查失败。固定场景覆盖旋转、提升 bucket、删除、重复插入及同端点不同 payload；另外检查五个合并场景。有限差分测试不能替代源码等价或所有输入上的证明。

本次默认检查通过 67 个操作前缀、2,680 次查询观察；加深检查通过 275 个操作前缀、11,000 次查询观察。两次都额外检查结构、内容、边界计数及 5 个合并场景。默认与加深检查存在重复场景，不应相加作为独立覆盖量。证据摘要见 [differential-validation.json](differential-validation.json)，原始输入与日志保存在上述 `build/` 目录。

## 用于后续证明生成

[sanitized](sanitized) 包含独立构建的定义与契约输入，以及调用现有 `vero.isabelle.benchmark.prepare` 接口生成的 [benchmark.json](sanitized/benchmark.json)。其 base import 是 `Intervaltree_Definitions.Interval_Input`，共 91 个目标。

该输入去除了准备证明、证明辅助引理及其陈述／属性，也去除了仅为差分执行加入的已证明代码生成等式。函数的定义与必要终止证明保留。后续实验应只挂载这份输入与合法基础环境，另建运行目录；不要向生成器暴露原始 `theories/`、本次对话、参考证明或准备构建缓存。正式运行仍使用现有仓库级 harness，本项目工具只负责准备与检查。

建议将 VERO 56 项视为兼容性基线，模型扩展 9 项与具体层 26 项单独报告。先完成更新的内容与表示不变量，再组合操作序列性质；否则容易把简单包装层定理当作核心算法已验证。

当前语义边界：只处理整数端点和不可变自然数 payload；SortedDict 以边界计数契约建模，未验证其内部实现；未翻译批量 `from_intervals` 构造；没有证明 Python 堆别名、异常中间状态、自定义哈希／比较或运行时资源行为。**尚无机器检查的 Python→Isabelle 翻译等价定理。** VERO 来源、版本与许可证见 [provenance.json](provenance.json) 和 [vendor/vero](vendor/vero)。
