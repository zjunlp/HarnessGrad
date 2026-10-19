"""Transport-independent adaptation of the two pinned official LLM judges.

Prompts, sampling and arithmetic follow MemoryAgentBench 538026089d1a8a8eff05121d0db89b388f360eba.
Failed calls and inadmissible responses raise; callers must withhold the score.
No provider credentials, network client, retries or actor-facing API live here.
"""
import hashlib
import json
import re
from pathlib import Path


SUMMARY = "infbench_sum_eng_shots2"
LONGMEM = "longmemeval_s*"
PROMPTS_SHA256 = "3e0b5f48be435cce70613e51bcdf0cbf9720f24f9a9ebceb39f452ab400119af"
_RESOURCE = Path(__file__).resolve().parents[1] / "specs" / "judge-prompts.json"


class JudgeError(ValueError):
    """Invalid judge input or response; never an ordinary zero score."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def _prompts():
    raw = _RESOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROMPTS_SHA256:
        raise JudgeError("judge prompt resource differs from the fixed source")
    return json.loads(raw)


def profile(source, model=None):
    """Return the exact default protocol or an explicitly identified model override."""
    if source not in (SUMMARY, LONGMEM):
        raise JudgeError("unsupported judge source")
    default = "gpt-4o-2024-05-13" if source == SUMMARY else "gpt-4o"
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise JudgeError("judge model must be a nonempty string")
    prompts = _prompts()
    result = {
        "schema": "hg-mab-judge-profile-v1", "source": source,
        "upstream_revision": prompts["upstream_revision"],
        "upstream_source_sha256": prompts["source_sha256"],
        "prompts_sha256": PROMPTS_SHA256,
        "model": default if model is None else model,
        "official_default_model": default,
        "model_override": model is not None and model != default,
        "metric": "gpt-4-f1" if source == SUMMARY else "Accuracy",
        "max_output_tokens": 4096 if source == SUMMARY else 10,
        "parameters": ({"temperature": 0.1, "top_p": 0.9, "seed": 42}
                       if source == SUMMARY else {"temperature": 0}),
        "omitted_api_defaults": {"n": 1, "stop": None},
        "calls_per_question": 3 if source == SUMMARY else 1,
        "admission": ("last_json_object;binary_fluency;bounded_integer_counts;errors_unmeasured"
                      if source == SUMMARY else
                      "official_yes_substring;require_yes_or_no_word;errors_unmeasured"),
    }
    result["identity"] = _digest(result)
    return result


def _parse_json(text):
    # Preserve official last-object selection and fenced-JSON fallback exactly.
    matches = re.findall(r"\{.*?\}", text, re.DOTALL)
    if matches:
        try:
            return json.loads(matches[-1])
        except (ValueError, TypeError):
            matches = re.findall(r"(?:```json)(.+)(?:```)", text, re.DOTALL)
            try:
                return json.loads(matches[-1])
            except (ValueError, TypeError, IndexError):
                pass
    return None


def _count(value, field, maximum=None):
    if (type(value) is not int or value < 0
            or (maximum is not None and value > maximum)):
        raise JudgeError("invalid judge count: " + field)
    return value


def _answer_input(answers):
    if (not isinstance(answers, list) or not answers
            or not all(isinstance(answer, str) for answer in answers)):
        raise JudgeError("judge answers must be a nonempty list of strings")


def _check_profile(source, value):
    if not isinstance(value, dict) or value != profile(source, value.get("model")):
        raise JudgeError("judge profile does not match the fixed protocol")


def score_question(source, prediction, answers, metadata, complete, profile):
    """Judge one submitted answer using ``complete(messages) -> content``.

The caller binds transport to this profile's model, parameters and output limit.
Returns the official per-question metric plus private judge provenance. Model or
admission overrides cannot silently share an identity with the default protocol.
"""
    _check_profile(source, profile)
    _answer_input(answers)
    if not isinstance(prediction, str) or not isinstance(metadata, dict):
        raise JudgeError("judge requires a submitted string and private metadata")
    prompts = _prompts()
    calls = []

    def request(kind, prompt):
        messages = [{"role": "user", "content": prompt}]
        content = complete(messages)
        if not isinstance(content, str) or not content.strip():
            raise JudgeError("empty or nontext judge response: " + kind)
        calls.append({"kind": kind, "messages_sha256": _digest(messages), "response": content})
        return content

    if source == SUMMARY:
        keypoints = metadata.get("keypoints")
        if (not isinstance(keypoints, list)
                or not all(isinstance(point, str) for point in keypoints)):
            raise JudgeError("summary keypoints must be a list of strings")
        text = prediction.strip()
        book = prompts["summary"]
        inputs = [
            ("fluency", book["fluency_prompt_book"].format(text=text)),
            ("recall", book["recall_prompt_book"].format(
                keypoints="\n".join(f"{i+1}. {point}" for i, point in enumerate(keypoints)), summary=text)),
            ("precision", book["precision_prompt_book"].format(expert_summary=answers[0], summary=text)),
        ]
        parsed = [_parse_json(request(kind, prompt)) for kind, prompt in inputs]
        if not all(isinstance(value, dict) for value in parsed):
            raise JudgeError("judge response has no admissible JSON object")
        f, r, p = parsed
        fluency = _count(f.get("fluency"), "fluency", 1)
        recall_found = _count(r.get("recall"), "recall", len(keypoints))
        precision_total = _count(p.get("sentence_count"), "sentence_count")
        precision_found = _count(p.get("precision"), "precision", precision_total)
        recall = recall_found / len(keypoints) if keypoints else 0
        precision = precision_found / precision_total if precision_total else 0
        score = fluency * 2 * (recall * precision) / (recall + precision) if recall + precision > 0 else 0
        details = {"fluency": fluency, "recall_total": len(keypoints), "recall_found": recall_found,
                   "precision_total": precision_total, "precision_found": precision_found,
                   "recall": recall, "precision": precision, "f1": score}
    else:
        question, qid, qtype = (metadata.get(key) for key in ("question", "question_id", "question_type"))
        if not isinstance(question, str) or not isinstance(qid, str) or not qid:
            raise JudgeError("LongMemEval requires its original question and question_id")
        abstention = "_abs" in qid
        if abstention:
            kind = "abstention"
        elif qtype in ("single-session-user", "single-session-assistant", "multi-session"):
            kind = "standard"
        elif qtype in ("temporal-reasoning", "knowledge-update", "single-session-preference"):
            kind = qtype
        else:
            raise JudgeError("unsupported LongMemEval question_type")
        # Upstream formats entry['answers'][i] directly: retain its list repr.
        response = request("accuracy", prompts["longmem"][kind].format(question, answers, prediction)).strip()
        if re.search(r"\b(?:yes|no)\b", response, re.IGNORECASE) is None:
            raise JudgeError("judge response contains no yes/no decision")
        label = "yes" in response.lower()
        score = float(label)
        details = {"question_type": qtype, "question_id": qid, "abstention": abstention, "label": label}
    return {"status": "measured", "metric": profile["metric"], "score": score,
            "judge": {"profile": profile, "calls": calls, "details": details}}
