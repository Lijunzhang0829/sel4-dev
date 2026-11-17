# Binomial heap

当前生成入口为 [固定 HOL 数据集](dataset-hol-001/README.md)，活动规格为 [targets.json](dataset-hol-001/input/targets.json)，[current-dataset.json](current-dataset.json) 绑定该输入。

模型和91条目标已冻结；生成不读取 C，也不执行 C 翻译。原生准备验收为83条正向、8条环境内证伪，13条固定辅助目标仍属于待生成目标。0054停用。模型只读取 `dataset-hol-001/input`；验收证明位于独立的 `acceptance/`，不进入生成事实库。

运行需要数据集外的 Isabelle2026 和固定 AFP 语义库。机器 A 已建立新的 heap/prepared，并完成无模型端到端预检，路径与重建步骤见数据集说明。新实验需重新规划；旧结果不追改。

[dataset/](dataset/README.md) 保留迁移前的 C 绑定、定义、规格和历史验收存档。`tools/compact_dataset.py` 专门复核或展开这个历史版本。`c/`、`theories/`、`targets-session/`及锁定文件用于原翻译的来源追溯，不作为新生成任务入口。
