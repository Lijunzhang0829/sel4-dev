# Binomial heap：原版实现与模型草稿审查

用户已选择保留原实现、允许正式证伪，绑定见 `version-decision.json`。原版源码、完整 AutoCorres2 翻译、模型和八条候选目标已落盘；模型与目标仍是待语义复核的草稿。binary heap 的六条目标及其实验保持原冻结版本。

上游仍是 `23d453792ed89a28ed7d2c8d4311a4d9f7822edd`。这里的两份源文件逐个核对了前期归档散列，见 `source-lock.json`。

## 已复现的行为

1. 一个元素的非空堆，在 `pop` 内部合并所需的分配失败时返回 `NULL`，计数仍是 1；撤去故障后再次 `pop` 返回原元素，最终释放后没有存活分配。因此弹出规格必须区分空堆、成功取出、分配失败；不能照搬 binary heap 的“非空必返回元素”。该检查见 `diagnostic.json` 的 `pop-failure`。
2. 按顺序插入整数 7 和 11，第二次插入使两个 order=0 的树合并。`binomial_tree_merge` 在 `vendor/binomial-heap.c:133` 调用 `memcpy` 时，源指针是 `NULL`，长度是 0。GCC 9.4 的 UBSan 报告 `null pointer passed as argument 2, which is declared to never be null`，退出码为 1。该检查见 `diagnostic.json` 的 `two-inserts`。

第二项是带源码位置的 sanitizer 诊断，**不是 Isabelle 证伪证书**；不能据此宣称所有运行环境都崩溃，也不能把它直接当成六个 binary heap 目标的反例。

## 可供选择的两个版本

| 选择 | 冻结内容 | 后续规格的解释 |
|---|---|---|
| 保留原实现（建议） | 原上游文件及其散列 | 采用明确的外部函数前提；允许形成合法的证伪或建模不足结论。不能为了证明成功，默默把空指针的零长度复制解释成无条件安全。 |
| 验证最小修复版 | 上游提交加 `zero-length-memcpy.patch`，另建版本身份 | 仅在 order 非零时调用 memcpy。实验描述必须说明验证对象已修改，原版本诊断继续保留。 |

补丁只在 `build/patched` 的对照副本中应用，`vendor` 没有改动。对照副本在同样的两个诊断用例中均退出 0，并最终没有存活分配，见 `patch-diagnostic.json`。这两项有限检查不证明补丁的完整正确性。

## 当前草稿的目标与建模

- API 层：计数对应多重集；插入成功增加一次元素、失败保持抽象内容和活块记录；弹出成功移除一个最小键、失败保留抽象内容；释放恰当的库拥有块并保护 payload。这里的失败恢复不承诺树形或所有字节与调用前完全相同。
- 内部层：表示关系刻画稳定 API 状态中的阶次、堆序、根数组和单一拥有引用。当前独立内部目标只有空引用、引用计数增加和叶节点合并非失败性；完整 merge 的进位、多重集保持、临时共享与 undo 不变量仍需后续证明展开，不能算作已经单独列出的目标。
- 环境层：除现有 malloc/free 语义外，需要显式 calloc 的零初始化和 memcpy 的前提、复制与 frame；涉及 `&new_tree` 和局部 `fake_heap`，还必须把临时栈存储与可分配区域的分离纳入表示，不能只复用 binary heap 的分配记录。
- 不把树替换成排序列表，不把短整型引用计数替换成无界自然数，不给失败目标加 `sorry` 后继续充当事实。

`theories/Binomial_Heap_C.thy` 已完成带显式 malloc/free/calloc/memcpy 环境的完整翻译；`targets-session/Binomial_Heap_Model.thy` 和八条 `oops` 目标通过原生声明检查。AutoCorres2 未自动获得 insert/pop 的 `unchanged_typing` 辅助定理，不能将这两个自动化警告当成功能正确性证明，也不能据此认定翻译失败。独立审计检查翻译事实的 oracle/跳过证明情况。

| 目标 | 要求 |
|---|---|
| `bn_count_exact` | 合法根森林的元素多重集大小等于返回计数，状态不变。 |
| `bn_insert_correct` | 成功增加一次 payload；失败保持抽象内容及活块记录；保护调用者数据和其他活块。 |
| `bn_pop_correct` | 成功移除一个最小键元素；NULL 允许表示空堆或分配失败，内容和活块记录保留。 |
| `bn_free_owned_storage` | 精确删除堆、根数组、树节点及子树数组的所有权记录，保护外部内容。 |
| `bn_new_empty_or_failure` | 成功新增一个空堆对象；失败恢复分配状态；保留原活块和调用者数据。 |
| `bn_ref_null` | 对空指针增加引用不改变状态。 |
| `bn_ref_increment` | 在指针满足 `c_guard`、计数小于 65535 时增加一次引用，保持其他结构字段、分配记录和对象之外字节；不包含活块登记或独占所有权前提。 |
| `bn_merge_leaves_nonfailure` | 两个合法 order=0 叶节点的合并不发生语义失败且存在正常结果；这是允许证伪的候选目标，并非已经成立的引理。 |

模型通过 C 字节内存读取节点、根数组和子树数组。`bn_shape` 是表示关系的结构见证，不是替换后的算法。API 边界要求节点拥有引用为 1；合并过程中的临时共享仍在原 C 语义中，需要证明时引入更一般的不变量。根数组的分配字节数从分配记录读取，只要求足以容纳 `roots_length`，允许最后一次 pop 后长度为零但仍拥有根数组。

插入/弹出有显式 `bn_stack_ready`：整个 C 栈区域属于分配器保留区，并且为实际临时堆对象、后续临时指针预留合法栈分配。调用者 payload 区域另用 `P` 表示，与栈区分离；不能把临时栈字节也声明为调用者不变的 payload。下文的准备检查已经给出具体环境下的非空实例；该条件是否足以支持所有实际执行路径，仍由后续目标证明检验。

`memcpy` 环境采用 C11 范围内的非空指针要求，即使长度为零也保留该前提；依据是 [N1570 §7.24.1p2 与 §7.1.4](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)。后续 [N3261](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3261.pdf) 专门提出允许部分零长度操作接收空指针。因此这里不能宣称模型覆盖所有标准版本或实现扩展，也不把 GCC 的 UBSan 诊断当作 Isabelle 证书。

成功插入/弹出还要求 `bn_storage_transition`：原有且不再属于结果堆的块被释放，结果堆之外不能新增活块，新拥有的块不能窃取原先的其他活块。这是待证明的分配记账目标，不是已获得的无泄漏结论。

## 独立回译与对照结果

已按用户授权调用 `gpt-6-astra / low`，覆盖八条目标，耗时约 135 秒。只发送四份形式化源文件，去掉注释和意图说明；审计确认没有工具调用。原始回译保存在 `evidence/readback-001.json`，逐项对照和来源散列保存在 `evidence/readback-comparison.json`。原始响应中的 `author_comparison_pending=true` 是调用结束时的快照；当前比较状态以单独的比较记录为准。

主要语义与草稿一致，但有一处回译需要纠正：插入、弹出中的 `\<S>` 是生成 C 环境的固定常量 `Binomial_Heap_C.\<S>`，不是任意量化的新参数。原生审计已核对这一绑定。回译未包含生成函数体和导入库的全部定义；后续本地核对确认 `runs_to` 排除语义失败，而 `reaches` 的存在项要求结果集合非空。这不提供现实运行时间界限。

本轮对照明确以下边界：

- `bn_count_exact` 的前提已经规定字段计数等于多重集大小；该目标检查读取 API 与表示的连接，本身不是计数不变量保持证明。
- 插入、弹出允许正常的分配失败返回，不保证资源充足时一定成功。成功分支已经包含 `bn_storage_transition`，不能将其误读为未约束最终泄漏；失败分支则只恢复抽象内容及活块映射。
- `bn_ref_increment` 是原始内存模型中的局部字段更新目标。`c_guard` 只检查对齐及对象地址范围避开空地址，不证明对象生命周期或所有权。
- `bn_merge_leaves_nonfailure` 的后置条件是 `True`，因此只检查正常结果存在与非失败性，不包含合并后的堆序、元素或 frame。原版零长度空源 `memcpy` 仍是待正式证伪的诊断线索。
- 回译时尚缺完整表示与栈条件的非空状态见证；这一缺项现已补充，见下节。弹出共用插入的两阶段栈条件，范围仍可能比实际所需更保守。
- 外部函数仍是明确的环境假设，尚未验证 libc；`memcpy_ok` 也没有完整表达 C 指针来源和读写权限。

本次只更新审查证据和说明，模型、八条目标及原版 C 的散列保持不变。回译完成不等于目标成立，也不等于人工已确认新规格。

## 可满足性检查结果

独立子会话 `Calg_Binomial_Satisfiability` 已通过 Isabelle 原生检查。检查从冻结的 `targets.json` 提取 **8 条目标、25 个局部前提**，不手工删减条件；其中 `bn_ref_null` 没有局部前提，另以生成的全局地址上下文实例检查环境约束。28 条准备定理通过无 oracle、无跳过证明、无隐藏假设审计；八条具体前提实例均无未解除前提，原来的算法目标没有导出为事实。

| 目标 | 使用的见证 |
|---|---|
| `bn_count_exact` | 单元素堆，真实字节内存中的根数组、叶节点和计数一致。 |
| `bn_insert_correct` | 单元素堆，再插入同一 payload；同时满足容量边界、payload 有效性、两阶段栈准备和分离条件。另检查空堆实例。 |
| `bn_pop_correct` | 含一个子树的 rank=1 堆，两次出现的 payload 在多重集中计数为 2，并满足栈与分离条件。 |
| `bn_free_owned_storage` | 同一 rank=1 堆，包含堆对象、根数组、两个树节点及子树数组。 |
| `bn_new_empty_or_failure` | 已有活块和非空 payload 保留区的良构分配器。 |
| `bn_ref_null` | 无局部前提；不把 `True` 的检查算作函数正确性证明。 |
| `bn_ref_increment` | 有效节点地址，引用计数为 1，小于 65535。 |
| `bn_merge_leaves_nonfailure` | 两个地址不同、order=0、refcount=1 的有效叶节点，以及合法的最小堆配置。 |

栈检查使用 `[4096,4160)` 的 64 字节具体区域；不仅给出第一次分配，还证明**每一种合法的第一次堆结构体分配之后，都存在第二次临时指针分配**。另对完整的生成地址上下文选择 19 个非空、互异的函数地址及与栈分离的全局区域，并证明该具体实例。

**解释边界：** 原模型的 `\<S>` 是未指定具体取值的固定常量。准备会话没有重定义它、加入新公理或修改目标；检查的是一个可满足的环境参数实例。插入、弹出的两条桥接定理显式保留 `\<S> = sat_stack` 前提。因此，这证明前提并非内在矛盾，不证明任意固定栈环境都有可用空间；空栈被单独证明不可满足 `bn_stack_ready`。

外部关系还检查了 `calloc` 的成功、失败实例，`memcpy` 的正长度及零长度非空指针实例，以及零长度空源指针的拒绝条件。负例控制还覆盖空栈、错误计数和与具体栈重叠的 payload 区域。这些是环境或前提检查，**不是算法目标的正式证伪**。

实现与证据：

- `satisfiability/Binomial_Satisfiability.thy`：具体字节内存、树结构、栈和外部关系的准备见证。
- `satisfiability/Binomial_Target_Satisfiability.thy`、`satisfiability/coverage.json`：由冻结清单生成的精确前提实例、替换记录及审计。
- `tools/satisfiability.py`：核对冻结身份，生成前提检查，调用现有原生构建入口并保存证据。每次只刷新该子会话的可丢弃构建数据库，复用父会话；历史构建日志保留。
- `evidence/satisfiability.json`：结果和文件散列。另有 6 项临时副本完整性控制，确认源码漂移、清单漂移、遗漏目标、增删前提会被拒绝。

相同输入连续两次完整原生构建通过，审计输出一致；第二次从已有成功缓存状态启动，仍重新检查子会话。两次时间戳日志和输入散列见 `evidence/satisfiability-reproducibility.json`。

准备证明位于独立子会话，冻结的 Foundation/Targets 会话没有导入它。后续生成适配必须排除整个 `satisfiability/` 目录及其编译产物，不能将这些准备证明计入成绩或供生成器检索。原 C、四份形式化源文件、八条目标及语义绑定 `5b621912…` 保持不变，先前独立回译仍对应同一模型。

复现完整可满足性检查：

```sh
python3 c-algorithms/binomial/tools/satisfiability.py
```

尚未完成：八条目标与新增环境的人工语义确认、目标证明/证伪、生成适配。引用计数溢出、任意比较器、最大堆、并发和复杂度也不在首版范围内。`targets.json` 保持 `generation_allowed=false`。

复现：先运行 `python3 c-algorithms/binomial/tools/prepare.py` 检查源码，再运行 `python3 c-algorithms/binomial/tools/build.py --session Calg_Binomial_Targets` 检查声明及审计。原始输入锁、环境锁、目标目录和构建证据各自保留。
