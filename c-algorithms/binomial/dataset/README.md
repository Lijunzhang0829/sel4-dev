> 这是固定 HOL 迁移前的来源及历史验收版本。当前生成入口为 [dataset-hol-001](../dataset-hol-001/README.md)；本目录的冻结输入与历史成绩保留原绑定。

# Binomial heap 轻量数据集

活动输入只有 [input/](input/)。该目录的C、Isabelle定义、contract陈述、环境、编号和manifest均保持冻结，binding为 `8148dcb80eeeaa888e80256d45ff269410d39e98ea861ca4a668acee028eb52e`。

91条生成目标＝78条主目标＋13条固定辅助目标。另有9条背景目录声明，既不安排生成，也不是已证事实。0054注释停用，不计入91条，通用共享DAG回滚正确性不在本次覆盖范围。

已验收的91条分别有83条正向证明和8条环境内证伪，没有未判定项。这是历史原生验收，不是新模型成绩，也不保证模型在指定预算内完成全部目标。环境内证伪为 `E ⇒ ∃x. A(x) ∧ ¬Q(x)`，没有证明固定全局地址环境E本身成立。

- [全部声明](ALL-SPECS.md)
- [全部定义](DEFINITIONS.md)
- [冻结生成目标](input/targets.json)
- [C预处理及绑定配方](input/translation/recipe.json)
- [精确工具链要求](input/environment.json)
- [主目标／固定辅助目标分组](../comparison-groups.json)

仅spec、建模、C及绑定输入约684 KiB。为使“91条已有判定”可以复核，另保留约2.24 MiB的 [压缩验收证据](acceptance/manifest.json)：最终证明源、原生日志、原始校验器和必要前驱快照。该包不供生成模型检索，不作为生成父heap或合法前提库。

复核冻结输入及历史证书，仅使用Python标准库，不运行模型或Isabelle：

```sh
python3 c-algorithms/binomial/tools/compact_dataset.py verify
python3 c-algorithms/binomial/tools/test_compact_dataset.py
```

在目录外重新展开定义和待证陈述：

```sh
python3 c-algorithms/binomial/tools/compact_dataset.py materialize --output /tmp/binomial-build
```

生成父会话、定义、尚未证明的contract和91条JSONL目标，不会展开已知证明。指定外部固定版本的 `--isabelle /path/to/Isabelle2026 --afp /path/to/afp/thys` 可同时输出build命令；`--with-acceptance` 仅供可信准备，额外展开私有证书重放源。不要把该选项用于模型生成输入。

当前不保留Isabelle/AFP安装、大型heap、prepared事实库或实验日志。数据内容已验收，但本机并非可直接启动生成的ready环境：先恢复 `input/environment.json` 固定的环境，重新build父会话和prepare，再规划并建立新run。源文件展开和历史证书复核不等于新的原生预检。

旧实验、pilot、下载缓存及重复CLI不再保留，旧run不能续跑。原始验收材料中的历史绝对路径只说明当时执行环境；复核工具在隔离临时目录验证保留包，不依赖已清理的旧路径。
