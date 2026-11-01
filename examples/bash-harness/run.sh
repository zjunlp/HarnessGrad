#!/usr/bin/env bash
set -euo pipefail
TASK="" WORK=""
while (($#)); do
  case "$1" in
    --task) TASK="$2"; shift 2 ;;
    --workdir) WORK="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$TASK" && -f "$TASK" ]] || { echo "missing --task task.json" >&2; exit 2; }
[[ -n "$WORK" ]] || { echo "missing --workdir" >&2; exit 2; }
mkdir -p "$WORK"
printf '%s\n' "${HG_BASH_EXAMPLE_ANSWER:-BASH_HARNESS_OK}" > "$WORK/answer.txt"
printf '%s\n' '{"harness_identity":{"harness":"bash-example","agent_model":null,"agent_backend":"scripted"}}' > "$WORK/trace.jsonl"
printf '%s\n' '{"usage":{"calls":0,"input_tokens":null,"output_tokens":null}}' >> "$WORK/trace.jsonl"
printf '%s\n' '{"submitted":true,"answer_source":"artifact"}' >> "$WORK/trace.jsonl"
