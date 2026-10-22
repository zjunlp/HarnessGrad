"""TEST ONLY, UNWIRED: preflight for Python prompting text before ANY decoder.

Allow only a small literal grammar. In particular *all* BinOp nodes are refused:
even `1 + 2` enters upstream eval, and large literal expressions consume resources.
This helper neither calls a scorer nor turns a refusal into a reward/zero score.
No production containment or official execution authorization is supplied here.
"""
from __future__ import annotations

import ast

MAX_CHARS = 65536
MAX_NODES = 4096
MAX_DEPTH = 64


def refuse(reason):
    return dict(status="unmeasured", measured=False, reason=reason, production_authorized=False)


def preflight(text, language="Python"):
    if language != "Python":
        return refuse("bfclguard_unsupported_language")
    if type(text) is not str or len(text) > MAX_CHARS:
        return refuse("bfclguard_input_bound")
    # Exactly mirror default_decode_ast_prompting + ast_parse's Python cleanup.
    # A future caller must pass this SAME immutable string to the decoder.
    normalized = text.strip("`\n ")
    if not normalized.startswith("["):
        normalized = "[" + normalized
    if not normalized.endswith("]"):
        normalized += "]"
    try:
        tree = ast.parse(normalized.strip("[]'"), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return refuse("bfclguard_parse_refused")
    pending, count = [(tree, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            return refuse("bfclguard_ast_bound")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    body = tree.body
    calls = [body] if isinstance(body, ast.Call) else (body.elts if isinstance(body, (ast.List, ast.Tuple)) else None)
    if calls is None or not all(isinstance(call, ast.Call) for call in calls):
        return refuse("bfclguard_expected_calls")
    for call in calls:
        func = call.func
        while isinstance(func, ast.Attribute):
            func = func.value
        if not isinstance(func, ast.Name):
            return refuse("bfclguard_call_target")
        if call.args or any(keyword.arg is None for keyword in call.keywords):
            return refuse("bfclguard_argument_unpack_or_positional")
        # Inspect every descendant, including values nested in containers, keys,
        # lambda bodies, comprehensions and subscript expressions.
        values = [keyword.value for keyword in call.keywords]
        descendants = [node for value in values for node in ast.walk(value)]
        if any(isinstance(node, ast.BinOp) and any(isinstance(child, ast.Call) for child in ast.walk(node))
               for node in descendants):
            return refuse("bfclguard_binop_contains_call")
        if any(isinstance(node, ast.Call) for node in descendants):
            return refuse("bfclguard_call_in_argument")
        if any(isinstance(node, ast.BinOp) for node in descendants):
            return refuse("bfclguard_binop_eval")
        # A positive allowlist also excludes Lambda (another retained eval site),
        # attributes, comprehensions, subscripts and future expression node types.
        for node in descendants:
            if not isinstance(node, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.UnaryOp,
                                     ast.USub, ast.Load)):
                return refuse("bfclguard_nonliteral_argument")
            if isinstance(node, ast.UnaryOp) and not (
                    isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant)
                    and type(node.operand.value) in (int, float, complex)):
                return refuse("bfclguard_nonliteral_unary")
            if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
                return refuse("bfclguard_dict_unpack")
    return dict(status="pass", production_authorized=False)


def guarded_decode(text, decoder, language="Python"):
    """Test seam: refuse before invoking even a spy decoder; never score."""
    result = preflight(text, language)
    if result["status"] != "pass":
        return result
    return dict(status="decoded", output=decoder(text, language), production_authorized=False)
