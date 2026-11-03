"""Official DeepSearchQA Kaggle starter notebook v1 prompt and metric formulas.

Source: https://www.kaggle.com/code/andrewmingwang/deepsearchqa-starter-code
Notebook source SHA256 recorded below. Prompt constants copied verbatim;
parsing/counts follow cells 13-14, without retaining private grader text.
"""

import textwrap

REVISION = "aa1ace0a1e0023a5cad5e662968bcf18c6b4f5c0666cbad78a46ba5f813559b2"
MODEL = "gemini-2.5-flash"


def prompt(row, prediction):
    return DEEPSEARCH_QA_PROMPT + GRADER_RATING_OUTPUT_EXAMPLE.format(
        prompt=row["problem"].strip(),
        answer=row["answer"].strip(),
        prompt_type=row["answer_type"].strip(),
        response=prediction.strip(),
    )


def counts(text):
    import json

    value = text.strip()
    start = value.find("```json")
    if start != -1:
        value = value[start + len("```json") :].strip()
        end = value.rfind("```")
        if end != -1:
            value = value[:end].strip()
    node = json.loads(value)["Answer Correctness"]
    details = node["Correctness Details"]
    excessive = node.get("Excessive Answers", [])
    if (
        not isinstance(node["Explanation"], str)
        or not isinstance(details, dict)
        or any(
            not isinstance(k, str) or type(v) is not bool for k, v in details.items()
        )
        or not isinstance(excessive, list)
        or any(not isinstance(x, str) for x in excessive)
    ):
        raise ValueError("Invalid DeepSearchQA rating")
    tp = sum(details.values())
    return {
        "true_positives": tp,
        "false_positives": len(excessive),
        "false_negatives": len(details) - tp,
    }


def metrics(counts):
    tp, fp, fn = (
        counts[k] for k in ("true_positives", "false_positives", "false_negatives")
    )
    if any(type(n) is not int or n < 0 for n in (tp, fp, fn)):
        raise ValueError("DeepSearchQA rating counts must be nonnegative integers")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1_score": 2 * (precision * recall) / (precision + recall)
        if precision + recall
        else 0.0,
        "accuracy": float(fn == 0 and fp == 0),
    }


DEEPSEARCH_QA_PROMPT = textwrap.dedent("""\
Your task is to evaluate whether a given "AI Response" for a specific "User Prompt" arrived at the correct answer.

**Answer Correctness Task**

*   **Purpose:** Assess whether the AI response provides the correct answer(s) based on the provided "Correct Answer" and "Prompt Type".
*   **Process:**
    *   Identify the "Prompt Type": "<prompt_type>".
    *   Refer to the "Correct Answer": "<answer>".
    *   Based on the "Prompt Type", determine if the "AI Response" contains the expected answer(s).
        *   **'Single Answer'**: Check if the response provides the answer that addresses the user's question. It does not have to match the exact wording of the provided answer.
        *   **'Set Answer'**: Check if the response includes *each* item from the provided ground truth answers. The order might not matter unless specified otherwise. The response might include more answers than the list. Determine the correctness *only* based on the list first and then check if the response includes answers not in the list.
    *   **Explanation:** Provide a brief explanation justifying your assessment of answer correctness, referencing specific parts of the AI response and the correct answer.
    *   **Correctness Details:** Provide a dictionary, one key for each expected answer part, and value is a boolean indicating whether each expected answer part was found.
        *   For 'Set Answer', this will be a list of attributes, one for each item/part in the "Correct Answer". Each key will be a string indicating the expected answer part, and the value will be a boolean indicating whether that part was found in the response.
    *   **Excessive Answers:** Provide a list of strings, each indicating an excessive answer part. If the response provides answers that are **not** in the "Correct Answer" list, add these answers as excessive answers. Return an empty list when there's no excessive answers in the response.


**Output Format:**

Your evaluation *must* be structured as a nested JSON dictionary with the following top-level keys: `"Answer Correctness"`. Please return NULL if any of "Prompt", "AI Response" or "Correct Answer" is empty.
The value for `"Answer Correctness"` should be a dictionary containing `"Explanation"` (a string), `"Correctness Details"` (a dictionary where each key is the expected correct answer, and the value is a boolean indicating whether the response contains the correct answer), and `"Excessive Answers"` (a list of strings indicating the excessive answers).

Make sure you return a valid JSON string. Pay special attention to quotes, commas and special characters in the JSON string. Make sure to escape all special characters and quotes in the JSON string.


""")

GRADER_RATING_OUTPUT_EXAMPLE = r"""**Example (Partial):**

"```json
{{
  "Answer Correctness": {{
    "Explanation": "The response correctly identified Belgium and France but also includes an excessive answer, Italy.",
    "Correctness Details": {{
      "Belgium": true,
      "France": true,
    }},
    "Excessive Answers": [ "Italy" ]
  }}
}}
```"

**Now, proceed with the evaluation using the provided User Prompt, AI Response, and Correct Answer.**

User Prompt (Wrapped in <prompt> and </prompt>):
<prompt>
{prompt}
</prompt>
--------------------
**  Correct Answer (Wrapped in <answer> and </answer>):
Prompt Type: {prompt_type}
<answer>
{answer}
</answer>
--------------------
AI assistant response (Wrapped in <response> and </response>):
<response>
{response}
</response>

--------------------
Rating:"""
