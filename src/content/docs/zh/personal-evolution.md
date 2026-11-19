---
title: 个人进化
description: 在本机启动个人工作台，并查看一次反馈会话的证据。
---

[小绿](https://github.com/zjunlp/HarnessGrad#evolve-personal-harness) 展示了如何把会话纠正变成有明确作用域的改动提案。

## 启动本机工作台

在 HarnessGrad 仓库中安装软件，并导入一条合成会话。将下面的项目路径替换为已有项目，状态目录应单独存放。

~~~bash
python -m pip install .
harnessgrad personal demo --state-root /path/to/state --project /path/to/project
harnessgrad personal serve --state-root /path/to/state
~~~

打开终端输出的本机地址，选择对应服务的连接文件。合成演示无需调用模型，个人服务独立于实验 Console 运行。

反馈的作用域、Codex 兼容性和原生桌面端安装见[个人进化指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/personal-evolution.md)与[桌面端安装指南](https://github.com/zjunlp/HarnessGrad/blob/main/desktop/personal/README.md)。
