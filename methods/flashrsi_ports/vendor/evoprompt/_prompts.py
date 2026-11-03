"""EvoPrompt's GA, DE and initialization templates.

The bodies below are the pinned upstream's own text, so a reproduction can be
audited line by line -- including the worked example each template carries:
upstream shows the model one solved crossover before asking for its own, and
removing it would be a different prompt.

Upstream: https://github.com/microsoft/EvoPrompt
Commit: 2e235c9410e38d531dabfae7fdd9cbded1577373
"""

_HEADER = (
    "The prompt becomes the complete contents of {path}, which the target agent "
    "loads for every task. Keep that file's native format, write general "
    "guidance rather than answers, and never name a specific task.\n\n"
)

_GA = """Please follow the instruction step-by-step to generate a better prompt.
1. Crossover the following prompts to generate a new prompt:
Prompt 1: Your task is to classify the comment as one of the following categories: terrible, bad, okay, good, great.
Prompt 2: In this task, you are given sentences from movie reviews. The task is to classify a sentence as one of the following categories: terrible, bad, okay, good, great.
2. Mutate the prompt generated in Step 1 and generate a final prompt bracketed with <prompt> and </prompt>.

1. Crossover Prompt: In this task, you are given comments from movie reviews. Your task is to classify each comment as one of the following categories: terrible, bad, okay, good, great.
2. <prompt>Given a sentence from a movie review, classify it into one of the following categories: terrible, bad, okay, good, or great.</prompt>

Please follow the instruction step-by-step to generate a better prompt.
1. Crossover the following prompts and generate a new prompt:
Prompt 1: {prompt1}
Prompt 2: {prompt2}
2. Mutate the prompt generated in Step 1 and generate a final prompt bracketed with <prompt> and </prompt>.

1. """


_DE = """Please follow the instruction step-by-step to generate a better prompt.
1. Identify the different parts between the Prompt 1 and Prompt 2:
Prompt 1: Rewrite the input text into simpler text.
Prompt 2: Rewrite my complex sentence in simpler terms, but keep the meaning.
2. Randomly mutate the different parts
3. Combine the different parts with Prompt 3, selectively replace it with the different parts in step 2 and generate a new prompt.
Prompt 3: Rewrite the given input text into simpler English sentences while preserving the same meaning, so it can be understood by non-native English speakers.
4. Crossover the prompt in the step3 with the following basic prompt and generate a final prompt bracketed with <prompt> and </prompt>:
Basic Prompt: Make the sentence easier for people who do not speak English fluently to comprehend.

1. Identifying the different parts between Prompt 1 and Prompt 2:
Prompt 1: {prompt1}
Prompt 2: {prompt2}
2. Randomly mutate the different parts
3. Combine the different parts with Prompt 3, selectively replace it with the different parts in step 2 and generate a new prompt.
Prompt 3: {prompt3}
4. Crossover the prompt in the step3 with the following basic prompt and generate a final prompt bracketed with <prompt> and </prompt>:
Basic Prompt: {prompt0}
"""


#: Upstream seeds its population from a file of hand-written prompts per task.
#: A FlashRSI run starts from whatever instruction the target harness ships, so
#: the remaining members are paraphrases of it rather than of a fixed list.
_INITIALIZE = """Paraphrase the following task prompt to create a semantically faithful but structurally diverse initial population member. Preserve all constraints. Return only the complete prompt bracketed by <prompt> and </prompt>.

SEED PROMPT:
{seed}
"""


GA = "[EvoPrompt: crossover]\n" + _HEADER + _GA
DE = "[EvoPrompt: mutate]\n" + _HEADER + _DE
INITIALIZE = "[EvoPrompt: paraphrase]\n" + _HEADER + _INITIALIZE
