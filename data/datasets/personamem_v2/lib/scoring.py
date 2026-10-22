"""The official MemAgent MCQ score, with its exact last-boxed parsing semantics.

Reference: bowen-upenn/PersonaMem-v2@d29d91d016add354e459dfeb0d24af08bc402e2a,
MemAgent/verl/utils/reward_score/implicit_persona.py. Differential reference is
kept verbatim in tests/fixtures/personamem_v2/official_scorer.py.
"""


def score_answer(answer, correct_letter):
    if not isinstance(answer, str) or correct_letter not in ("a", "b", "c", "d"):
        raise ValueError("invalid MCQ scoring input")
    if "\\boxed " in answer:
        value = answer.split("\\boxed ")[-1].split("$")[0]
    else:
        index = answer.rfind("\\boxed")
        # The upstream fbox fallback cannot pass remove_boxed and scores zero.
        if index < 0:
            return 0.0
        depth = 0
        end = None
        for i in range(index, len(answer)):
            if answer[i] == "{":
                depth += 1
            if answer[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None or not answer[index:end + 1].startswith("\\boxed{"):
            return 0.0
        value = answer[index + 7:end]
    return float(value.replace(" ", "").replace("\n", "").lower() == correct_letter)
