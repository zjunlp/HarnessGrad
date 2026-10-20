"""Text-only inputs; reference answers and persona annotations stay verifier-only."""
import ast
import hashlib
import json
import random


def question_id(task_id, raw_query):
    """Opaque stable identity independent of the private MCQ shuffle index.

    Publishing the CSV row index would reveal the correct option under the
    upstream 42+index shuffle. The query text itself contains no such index.
    """
    return task_id + "-q" + hashlib.sha256(raw_query.encode("utf-8")).hexdigest()[:24]


def question(row, index):
    """Match official MemAgent preprocessing's seed 42 + original split row index."""
    raw = row["user_query"]
    try:
        parsed = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        parsed = raw
    text = str(parsed.get("content", "")) if isinstance(parsed, dict) else raw
    if not row["incorrect_answers"]:
        # Keep the released question identity, but never invent distractors.
        return text, None
    try:
        wrong = ast.literal_eval(row["incorrect_answers"])
    except (SyntaxError, ValueError):
        wrong = json.loads(row["incorrect_answers"])
        # Ten released train rows contain three distractors followed by JSON
        # null. Keep the three supplied distractors; null is not an option.
        if isinstance(wrong, list) and len(wrong) == 4 and wrong[-1] is None:
            wrong = wrong[:3]
        else:
            raise ValueError("unrecognized MCQ serialization")
    if not text or not row["correct_answer"] or not isinstance(wrong, list) or len(wrong) != 3:
        raise ValueError("expected one question and four released MCQ options")
    options = [row["correct_answer"], *wrong]
    if any(not isinstance(option, str) or not option for option in options) or len(set(options)) != 4:
        raise ValueError("MCQ options must be four distinct nonempty strings")
    random.Random(42 + index).shuffle(options)
    letter = chr(97 + options.index(row["correct_answer"]))
    options_text = "\n".join(f"({chr(97 + i)}) {option}" for i, option in enumerate(options))
    text += ("\n\nYou are performing a multiple-choice question task. You must choose the best response "
             "from the following options to answer the user query above:\n" + options_text
             + "\n\nProvide your answer in the format: \\boxed{a}, \\boxed{b}, \\boxed{c}, or \\boxed{d}."
             + " Always perform your reasoning inside <think> and </think> tags before your final answer.")
    return text, letter


def history_chunks(history):
    """Deliver native messages in order without adding an artificial token budget.

    Formatting and first-system-message removal match MemAgent preprocessing.
    Each native message is one delivery item; no content is truncated or sampled.
    """
    if isinstance(history, dict):
        history = history.get("chat_history")
    if not isinstance(history, list) or not history:
        raise ValueError("history must be a nonempty message list")
    if isinstance(history[0], dict) and history[0].get("role") == "system":
        history = history[1:]
    chunks = []
    for i, message in enumerate(history):
        # Official preprocessing skips objects without role/content. One released
        # assistant entry has no content key; preserve numbering of later turns.
        if not isinstance(message, dict) or "role" not in message or "content" not in message:
            continue
        if (message["role"] not in ("user", "assistant")
                or (message["content"] is not None and not isinstance(message["content"], str))):
            raise ValueError("invalid text history message")
        # Upstream uses an f-string, retaining empty content and rendering None
        # literally. The release contains both; neither warrants dropping a turn.
        chunks.append(f"Message {i + 1} ({message['role']}): {message['content']}")
    if not chunks:
        raise ValueError("empty history")
    return chunks
