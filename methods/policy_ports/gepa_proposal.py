"""GEPA port's instruction proposal role, without selection or scoring."""


def format_prompt(instruction, evidence):
    """Render the experiment port's reflection prompt for caller-selected evidence."""
    return (
        '[GEPA: reflect-and-propose] Improve this instruction from the selected parent and its nonperfect task evidence. Return JSON {"instruction":"complete instruction"}.\n'
        + instruction
        + "\n"
        + evidence
    )


def instruction_from_reply(reply):
    """Retain the policy port's nonempty instruction validation."""
    text = reply.get('instruction')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('optimizer requires a nonempty instruction')
    return text
