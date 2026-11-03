"""GEPA's reflective mutation operator, kept close to upstream's text.

The body below is ``gepa.proposer.reflective_mutation``'s instruction-proposal
prompt, so a reproduction can be audited line by line. Upstream reflects over
one named DSPy module at a time; this port keeps that shape, because the text
becomes one native instruction file the target harness loads for itself.

Upstream: https://github.com/gepa-ai/gepa
Commit: 0632cdb5dcc052e690eab439e1b4a7e3e9cfe407
"""

_REFLECT = """I provided an assistant with the following instructions to perform a task for me:

```
{instruction}
```

The following are examples of different task inputs provided to the assistant along with the assistant's response for each of them, and some feedback on how the assistant's response could be better:

```
{evidence}
```

Your task is to write a new instruction for the assistant.

Read the inputs carefully and identify the input format and infer detailed task description about the task I wish to solve with the assistant.

Read all the assistant responses and the corresponding feedback. Identify all niche and domain specific factual information about the task and include it in the instruction, as a lot of it may not be available to the assistant in the future. The assistant may have utilized a generalizable strategy to solve the task, if so, include that in the instruction as well.

Provide the new instructions within ``` blocks."""


REFLECT = (
    "[GEPA: propose]\n"
    "The instruction becomes the complete contents of {path}, which the target "
    "agent loads for every task. Keep that file's native format, write general "
    "guidance rather than answers, and never name a specific task.\n"
    "The examples are training tasks. Their feedback includes reference answers "
    "when available, execution status and errors. The "
    "trajectory contains recorded reasoning, tool calls and tool results. "
    "Compare the response and trajectory with the references to diagnose why "
    "the attempt failed and derive reusable improvements. Missing references "
    "or trace details are unknown; do not invent them. Evidence ending in an "
    "ellipsis is truncated.\n\n" + _REFLECT
)
