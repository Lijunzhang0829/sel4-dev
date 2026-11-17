# c-algorithms 证明数据集

当前用于仓库级证明生成的数据集是 [binomial/dataset](binomial/dataset/README.md)：91条活动目标，C代码、Isabelle建模和精确陈述均冻结，主目标与固定辅助目标单独计数。

本目录保留源级材料、小型适配工具及压缩验收证据。Isabelle/AFP、heap、prepared、历史实验、重复CLI和下载包均不属于数据集；运行依赖和生成产物放在目录外。

```sh
python3 c-algorithms/binomial/tools/compact_dataset.py verify
```

该命令复核历史原生证书及源文件绑定，不执行模型或证明器。当前环境需要先恢复固定版本的外部工具链和父heap才能重新运行；完整说明见数据集README。

根目录的C、theories、targets-session、源锁定文件保留早期binary heap的源级建模，它们不属于上述91条目标。binomial下相应基础C模型用于从源码重建父环境，不是新的模型成绩。
