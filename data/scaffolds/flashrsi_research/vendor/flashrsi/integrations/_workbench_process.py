"""Single-thread native WorkBench state and scorer; no model credentials needed."""

import json
import sys


def main():
    setup = json.loads(sys.stdin.readline())
    sys.path.insert(0, setup["upstream"])
    from src.evals.actions import convert_intermediate_step_to_function_call
    from src.evals.evaluation import has_side_effects, is_correct, is_exact_match
    from src.tools.state import get_state
    from src.tools.tool import render_tool_description
    from src.tools.toolkits import all_tools

    get_state()  # Fail before a model call if the native database cannot load.
    tools = {tool.name: tool for tool in all_tools}
    actions = []
    print(
        json.dumps({"tools": "\n\n".join(map(render_tool_description, all_tools))}),
        flush=True,
    )
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request["operation"] == "call":
                name, args = request["name"], request["arguments"]
                if (
                    name not in tools
                    or not isinstance(args, dict)
                    or any(
                        not isinstance(k, str) or not isinstance(v, str)
                        for k, v in args.items()
                    )
                ):
                    raise ValueError(
                        "Use a listed native tool with string keyword arguments"
                    )
                actions.append(convert_intermediate_step_to_function_call(name, args))
                try:
                    result = {"result": tools[name](**args)}
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            elif request["operation"] == "grade":
                reference = [a.replace("\n", "\\n") for a in request["reference"]]
                correct = is_correct(actions, reference, "")
                result = {
                    "metrics": {
                        "correct": float(correct),
                        "exact_match": float(is_exact_match(actions, reference)),
                        "unwanted_side_effects": float(
                            has_side_effects(actions, correct)
                        ),
                    },
                    "actions": actions,
                }
            else:
                raise ValueError("Unknown WorkBench operation")
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
