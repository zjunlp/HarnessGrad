"""OPRO's meta-prompt, kept close to upstream's GPT optimizer text.

The body below is ``opro.optimization.opt_utils.gen_meta_prompt``'s instruction
variant, so a reproduction can be audited line by line. Upstream renders four
insertion positions; this port keeps only the instruction template, because the
text becomes a native instruction file the target harness loads for itself and
the framework cannot place it inside the question.

Upstream: https://github.com/google-deepmind/opro
Commit: a76bdce2cbf6d4a0d1e570a6fcfe17be9c2abdd7
"""

_INSTRUCTION = """Your task is to generate the instruction <INS>. Below are some previous instructions with their scores. The score ranges from 0 to 100.
{history}

SCORED TASK EXAMPLES:
{evidence}

Generate an instruction that is different from all the instructions <INS> above, and has a higher score than all the instructions <INS> above. The instruction should begin with <INS> and end with </INS>. The instruction should be concise, effective, and generally applicable to all problems above."""


META = (
    "[OPRO: propose]\n"
    "The instruction becomes the complete contents of {path}, which the target "
    "agent loads for every task. Keep that file's native format, write general "
    "guidance rather than answers, and never name a specific task.\n\n" + _INSTRUCTION
)

#: One row of the prompt/value trajectory. Upstream shows objective values as
#: integer buckets over 0--100 while FlashRSI keeps scores in 0--1, so the
#: bucketizing happens where the row is rendered.
ROW = "\ntext:\n{text}\nscore:\n{score}\n"

NO_HISTORY = "\n(none above threshold)\n"
