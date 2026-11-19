---
title: Customize a harness
description: Prepare domain tasks and turn their feedback into changes you can review.
---

Start from a base harness and identify the sources, tools and rules the domain needs. Prepare realistic tasks and define what a successful outcome must contain. [Ocean Harness](https://github.com/zjunlp/HarnessGrad#customize-harness) demonstrates one way to connect domain sources and cited answers.

## Try it locally

~~~bash
python -m pip install .
harnessgrad customize --help
~~~

Start the Console with an existing run directory as described in the [Workbench guide](https://github.com/zjunlp/HarnessGrad/blob/main/console/WORKBENCH.md). Choose **Customize a harness** to draft or import task bundles. You can also open the Harness editor to work on an existing candidate.

Use the [customization guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/customization.md) to collect selected feedback, generate a candidate, and review its changes before testing it on domain tasks.
