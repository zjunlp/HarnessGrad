#!/usr/bin/env bash
# Keep the two local HarnessGrad Qwen endpoints alive without duplicating healthy servers.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVE="$ROOT/tools/serve_local_model.sh"
STATE="${HARNESSGRAD_MODEL_STATE:-/tmp/harnessgrad-local-models}"
INTERVAL="${HARNESSGRAD_MODEL_CHECK_INTERVAL:-30}"
mkdir -p "$STATE"

is_healthy() {
  local port="$1"
  curl -fsS --max-time 3 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1
}

start_one() {
  local gpu="$1" model="$2" port="$3" name="$4"
  local log="$STATE/${port}.log"
  local marker="$STATE/${port}.launcher.pid"
  if is_healthy "$port"; then
    rm -f "$marker"
    return 0
  fi
  if [ -s "$marker" ] && kill -0 "$(cat "$marker")" 2>/dev/null; then
    return 0
  fi
  rm -f "$marker"
  # The serve script performs its own readiness wait and uses the pinned vLLM environment.
  # Run it detached so a slow model load does not block supervision of the other GPU.
  CUDA_VISIBLE_DEVICES="$gpu" HARNESSGRAD_VLLM_LOG="$log" \
    nohup "$SERVE" --model "$model" --port "$port" --tp 1 \
      --max-len 16384 --name "$name" >>"$log" 2>&1 < /dev/null &
  echo "$!" > "$marker"
}

# **Which cards this supervises is a decision, not a constant.** The supervisor used to hard-code
# two servers (`8001` on GPU 0 and `8002` on GPU 1), which made "give a card back" impossible
# without editing it: killing a server only meant the supervisor started it again 30 s later. On
# 2026-10-23 the operator needed a card for another project, and `8002` is the endpoint the
# platform actually documents (`eval/unix_model_relay.py` hard-codes it), so the plan is now a
# setting: `GPU:PORT` pairs, comma separated. The default is the historical pair, so a machine
# that sets nothing behaves exactly as before.
PLAN="${HARNESSGRAD_LOCAL_MODELS:-0:8001,1:8002}"
IFS=',' read -r -a ENTRIES <<< "$PLAN"
while :; do
  for entry in "${ENTRIES[@]}"; do
    gpu="${entry%%:*}"
    port="${entry##*:}"
    start_one "$gpu" /mnt/20t/qzs/PLMs/Qwen3.5-9B "$port" qwen3.5-9b-local
  done
  sleep "$INTERVAL"
done
