---
title: 领域定制
description: 准备领域任务，并将反馈整理为可审阅的 Harness 改动。
---

从基座 Harness 开始，明确领域需要的数据来源、工具和规则。准备贴近实际的任务，写清楚成功结果必须满足的条件。[Ocean Harness](https://github.com/zjunlp/HarnessGrad#customize-harness) 展示了领域资料如何用于可溯源的回答。

## 在本机试用

~~~bash
python -m pip install .
harnessgrad customize --help
~~~

按[工作台指南](https://github.com/zjunlp/HarnessGrad/blob/main/console/WORKBENCH.md)选择已有的运行记录目录，启动后打开 **领域定制**，起草或导入任务包，也可在编辑器中调整已有 Harness。

参考[定制指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/customization.md)收集反馈、生成候选并审阅改动，再用领域任务验收。
