# 固定 HOL 证明生成数据集

本版本已完成原生准备验收和生成入口预检，详见 [acceptance/REPORT.md](acceptance/REPORT.md) 与 [status.json](status.json)。本轮没有调用生成模型。原 C 数据集保留用于追溯，后续生成使用本目录的 `input/`。

`input/` 保留原来的 91 条待生成目标（78 条主目标、13 条固定辅助目标），9 条背景声明仍不是可用事实，0054 仍停用。目标编号、陈述、前提及 owned-context 判定范围均未更改。

`input/parent/theories/Binomial_Heap_C.thy` 是固定 HOL 定义，不含 `install_C_file`、`autocorres` 或 C 文件加载。它保留递归结构、字节内存、分配和失败语义。函数名称中的 `C` 表示来源。分配器及 libc 接口仍是原来的人工作用模型；未增加对真实 libc 的验证声明。

运行需要 Isabelle2026 和冻结的 AFP 语义库。原 C 文件及翻译步骤只属于准备阶段。heap 和 prepared 是从固定输入重建的运行产物，应放在数据集之外。`acceptance/` 仅供可信准备检查，不应暴露给证明生成器。

原生重放覆盖全部 91 条目标：83 条正向、8 条环境内证伪；主目标为70正向、8证伪，固定辅助目标13条均为正向。证伪范围为原 owned context 内的结论，未宣称实际部署环境全局成立。0054 的共享 DAG 完整回滚覆盖仍被排除。以上是数据集准备验收，不能计作新的模型成绩，也不保证任意模型在给定预算内完成。

91 条验收命题已与实际 prepared 的原生目标逐条比较，仅允许多态类型参数重命名。目标源、前提、环境及判定策略与原版本不变。生成输入不含这些验收证明；13 条固定辅助目标仍需模型生成，动态 helper 另行统计。

输入为45个文件、580,939字节（约0.55 MiB）。可用的机器 A 运行路径见 [runtime.json](runtime.json)；这些路径下的工具链、编译缓存和事实索引是可重建的外部运行产物。重新准备时使用新目录，不能沿用旧绑定的规划或 run。

准备入口（路径按本机环境调整）：

```sh
PYTHONPATH=. python3 c-algorithms/binomial/hol-export/bootstrap_parent.py \
  --source c-algorithms/binomial/dataset-hol-001/input \
  --output /tmp/binomial-hol-parent \
  --isabelle /path/to/Isabelle2026 --afp /path/to/afp/thys \
  --upstream-heaps /path/to/upstream-heaps

python3 -m regen.preparation.calg_shuffled_dataset \
  --source c-algorithms/binomial/dataset-hol-001/input \
  --parent /tmp/binomial-hol-parent --output /tmp/binomial-hol-prepared \
  --comparison-groups c-algorithms/binomial/dataset-hol-001/comparison-groups.json
```

第一步仅建立父环境；第二步才导出实际事实库、检查目标及环境，并建立可供现有 harness 使用的 prepared。两步都不调用生成模型。

现有基线入口可以用 `official_codex_deploy.py prepare --input <本目录/input> --prepared-dataset <新 prepared> ...` 接入。`--validation-only` 加 `preflight` 只验接线，不调用模型、复制登录凭据或分配生成额度。Ours 同样读取新的 prepared，必须重新规划。旧实验与旧验收成绩保留原绑定。
