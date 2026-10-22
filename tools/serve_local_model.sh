#!/usr/bin/env bash
# Serve a model locally for HarnessGrad, and print the configuration to point the
# platform at it.
#
# Why this exists as a script rather than a line in the README: getting a local
# server up for this platform has three failure modes that each cost a debugging
# round, and none of them look like what they are.
#
#   1. **The wrong vLLM.** Qwen3 / Qwen3.5 architectures are recent; a vLLM from
#      before their support fails at *model load* with an architecture error, which
#      reads like a corrupt checkpoint. Measured: vLLM 0.6.6 (which is what the
#      system environment had) cannot load `Qwen3MoeForCausalLM` at all, and
#      `Qwen3_5ForConditionalGeneration` needs `transformers>=5`.
#   2. **An old xgrammar.** vLLM 0.6.6 with a newer xgrammar crashes the *whole
#      server* on the first request carrying `response_format={"type":"json_object"}`,
#      with `TokenizerInfo has no attribute from_huggingface`. The function that needs
#      guided decoding is the one that asks for JSON, so this appears only after the
#      server looks healthy. Kept pinned by using an environment where vLLM and its
#      dependencies were resolved together.
#   3. **pkill matching itself.** `pgrep -f vllm` matches the shell running the
#      command, because the pattern is in its own argv. Stop servers with
#      `--stop`, which builds the pattern at runtime.
#
# Usage:
#   ./tools/serve_local_model.sh --model /path/to/model --port 8002 [--tp 2]
#   ./tools/serve_local_model.sh --stop
#   ./tools/serve_local_model.sh --list         # candidate local models
#
# The environment is deliberately separate from the system one. Resolving vLLM's
# dependencies inside a shared conda environment upgrades torch and breaks whatever
# else lived there; that already happened once, which is why this points at its own.
set -uo pipefail

VENV="${HARNESSGRAD_VLLM_VENV:-/mnt/20t/xubuqiang/venvs/harnessgrad}"
VLLM="$VENV/bin/vllm"
LOG="${HARNESSGRAD_VLLM_LOG:-/tmp/harnessgrad_vllm.log}"

# The environment's own bin/ must come first on PATH, and this is not tidiness.
#
# vLLM shells out to `ninja` while compiling kernels during warm-up, through a
# subprocess that inherits PATH. `ninja` is installed *inside* the venv as a Python
# package (it ships a binary), but Python finds packages without putting the venv's
# bin/ on PATH -- `python -m venv` only prepends it for an interactive `activate`. So
# vLLM loads the model, allocates the KV cache, captures 51 CUDA graphs, and then dies
# with:
#
#     FileNotFoundError: [Errno 2] No such file or directory: 'ninja'
#
# which reads like a missing build tool rather than a PATH this script forgot to set.
# Measured: 379,443 KV tokens allocated and every graph captured before this killed
# the engine core, so the failure appears at the very end of a long successful start.
export PATH="$VENV/bin:$PATH"

# ...and the environment's own CUDA toolkit, for the same reason one level deeper.
#
# flashinfer JIT-compiles kernels with `nvcc` during warm-up. The system toolkit here
# is CUDA 11.8, and its nvcc rejects an option flashinfer passes:
#
#     nvcc fatal : Unknown option '--compress-mode=size'
#     RuntimeError: Ninja build failed.
#
# `--compress-mode` arrived in CUDA 12.8. The fix is not to install a 3 GB toolkit:
# `vllm==0.29.0` already depends on `nvidia-cuda-nvcc`, which ships a complete CUDA 13
# toolchain (nvcc 13.4, include/, lib/) inside site-packages. Pointing CUDA_HOME and
# PATH at it lets the compile succeed, and it is the version matched to the torch that
# vLLM itself installed -- so this is more correct than whatever is in /usr/local.
#
# Detected rather than hardcoded because the path contains the CUDA major version and
# moves when vLLM moves.
_vendored_cuda="$(dirname "$(dirname "$(find "$VENV/lib" -name nvcc -type f 2>/dev/null | head -1)")")"
if [ -n "$_vendored_cuda" ] && [ -d "$_vendored_cuda/include" ]; then
    export CUDA_HOME="$_vendored_cuda"
    # CUDA_PATH as well as CUDA_HOME: flashinfer's `get_cuda_path()` reads either, but
    # other components in vLLM's stack read only CUDA_PATH, and a half-set pair gives
    # a compiler from one toolkit with headers from another -- which is exactly the
    # failure this is here to prevent.
    export CUDA_PATH="$_vendored_cuda"
    export PATH="$_vendored_cuda/bin:$PATH"
fi
unset _vendored_cuda

# flashinfer JIT-compiles a sampling kernel whose bundled CCCL does a strict
# compiler-versus-headers version check. The vendored toolkit here is nvcc 13.4 with
# 13.0 headers, which the check rejects:
#
#     error: "CUDA compiler and CUDA toolkit headers are incompatible,
#             please check your include paths"
#     RuntimeError: Ninja build failed.
#
# vLLM does not need flashinfer's sampler -- its own is used instead -- and disabling
# it skips the compile entirely. Measured: with this set, zero compile attempts and a
# clean startup; without it, the engine core dies at warm-up.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

# Candidate models, with the architecture recorded so the reader can tell which
# ones this vLLM can load. Kept as data rather than prose because the answer changes
# when vLLM changes.
CANDIDATES="
/mnt/20t/lhz/models/Qwen3-30B-A3B-Instruct-2507|Qwen3MoeForCausalLM|30B MoE (3B active), fast
/mnt/20t/qzs/PLMs/Qwen3.5-9B|Qwen3_5ForConditionalGeneration|9B, needs transformers>=5
/mnt/20t/xuhaoming/models/Qwen2.5-7B-Instruct|Qwen2ForCausalLM|7B dense, oldest and safest
/mnt/20t/xuweihong/workspace/models/Qwen3-4B-Instruct-2507|Qwen3ForCausalLM|4B dense
"

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

stop_servers() {
    me=$$
    me_user="$(id -un)"
    # Two defects were measured here on 2026-10-04, both in this function:
    #
    #  1. **It left the engine behind.** `pgrep -f vllm` is case-sensitive, and vLLM's
    #     worker process is named `VLLM::EngineCore`. So `--stop` killed the API server,
    #     freed port 8001, and left the engine holding **36 GB of the GPU** -- the next
    #     `serve` then had nowhere to put the weights. It reported "ports and GPU memory
    #     should be free" while the GPU was still full.
    #  2. **It would kill another user's server.** The pattern is machine-wide, and a
    #     second vLLM on this host belongs to a colleague. That is not a hypothetical: a
    #     hand-run cleanup tried exactly this and was stopped only by file permissions.
    #
    # So: match case-insensitively, and **only touch processes owned by the invoking
    # user**. Anything else is reported, not killed -- an operator who asked to stop their
    # own server must not be the reason someone else's run dies.
    name="vl""lm"
    for p in $(pgrep -if "$name" 2>/dev/null); do
        [ "$p" = "$me" ] && continue
        owner=$(ps -o user= -p "$p" 2>/dev/null | tr -d ' ')
        [ -z "$owner" ] && continue
        if [ "$owner" != "$me_user" ]; then
            echo "  left alone: pid $p belongs to $owner"
            continue
        fi
        cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null) || continue
        case "$cmd" in
            *serve*|*EngineCore*|*vllm.entrypoints*|*api_server*)
                echo "  stopping pid $p (${cmd:0:60})"
                kill -9 "$p" 2>/dev/null ;;
        esac
    done
    # The port is unambiguous: whoever is listening on it is the server we started.
    for port in 8000 8001 8002; do
        pid=$(ss -ltnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | head -1)
        [ -z "$pid" ] && continue
        owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
        if [ "$owner" != "$me_user" ]; then
            echo "  port $port is held by pid $pid ($owner); left alone"
            continue
        fi
        echo "  stopping pid $pid on port $port"
        kill -9 "$pid" 2>/dev/null
    done
    sleep 2
    echo "stopped; ports 8000-8002 and GPU memory should be free"
}

#: Which GPU memory is ours, and is it actually free? `--stop` used to say this without
#: looking, and the engine it missed kept 36 GB. This is the check that would have caught
#: it, and it names the owner of anything still holding memory so the next reader knows
#: whether it is their problem.
show_gpu_state() {
    echo "GPU memory still held:"
    nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | while read -r line; do
        [ -z "$line" ] && continue
        pid=$(echo "$line" | cut -d, -f1 | tr -d ' ')
        owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
        echo "  $line  (user: ${owner:-gone})"
    done
    nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader
}

list_models() {
    printf '%-52s %-34s %s\n' MODEL ARCH NOTE
    echo "$CANDIDATES" | while IFS='|' read -r path arch note; do
        [ -z "$path" ] && continue
        if [ -d "$path" ]; then
            size=$(du -sh "$path" 2>/dev/null | cut -f1)
            printf '%-52s %-34s %s (%s)\n' "$(basename "$path")" "$arch" "$note" "$size"
        else
            printf '%-52s %-34s %s\n' "$(basename "$path")" "$arch" "MISSING"
        fi
    done
}

MODEL="" PORT=8002 TP=1 MAXLEN=32768 GPUMEM=0.90 SERVED=""

while [ $# -gt 0 ]; do
    case "$1" in
        --stop) stop_servers; show_gpu_state; exit 0 ;;
        --list) list_models; exit 0 ;;
        --model) MODEL="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --tp) TP="$2"; shift 2 ;;
        --max-len) MAXLEN="$2"; shift 2 ;;
        --gpu-mem) GPUMEM="$2"; shift 2 ;;
        --name) SERVED="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$MODEL" ]; then
    echo "no --model given. Candidates on this machine:" >&2
    list_models >&2
    exit 2
fi
[ -d "$MODEL" ] || { echo "no such model directory: $MODEL" >&2; exit 2; }
[ -x "$VLLM" ] || {
    echo "no vLLM at $VLLM" >&2
    echo "This platform expects a dedicated environment. Creating one:" >&2
    echo "  python3 -m venv $VENV" >&2
    echo "  $VENV/bin/pip install -U -i https://mirrors.aliyun.com/pypi/simple/ 'vllm' 'transformers>=5.10.4'" >&2
    exit 2
}

# What the checkpoint says it is, versus what this vLLM can load. Checked before
# starting, because a mismatch surfaces as a long load followed by an architecture
# error that reads like a broken download.
ARCH=$(python3 -c "
import json,sys
try:
    print(json.load(open('$MODEL/config.json')).get('architectures',['?'])[0])
except Exception: print('?')" 2>/dev/null)

# `<<'PY'` (quoted) so the shell does not interpret the body. Unquoted, a backtick in
# a comment -- `` `Qwen3_5` `` -- is a command substitution, and the script printed
# "Qwen3_5: command not found" while still producing the right answer, which is the
# kind of noise that trains a reader to ignore output. `ARCH` arrives via the
# environment instead.
SUPPORTED=$(ARCH="$ARCH" "$VENV/bin/python" - <<'PY' 2>/dev/null
import os
import vllm.model_executor.models as m

files = set(os.listdir(os.path.dirname(m.__file__)))

# Architecture name -> the module vLLM implements it in. A table rather than a
# derivation: the first version of this check lowercased and split on case
# boundaries, which turned `Qwen3_5` into `qwen_3_5` and reported a supported
# architecture as missing. These names do not decompose mechanically -- digits stay
# attached to the word before them -- so they are listed.
KNOWN = {
    "Qwen3_5ForConditionalGeneration": "qwen3_5.py",
    "Qwen3MoeForCausalLM": "qwen3_moe.py",
    "Qwen3ForCausalLM": "qwen3.py",
    "Qwen2ForCausalLM": "qwen2.py",
    "Qwen2MoeForCausalLM": "qwen2_moe.py",
    "MistralForCausalLM": "mistral.py",
    "LlamaForCausalLM": "llama.py",
    "DeepseekV2ForCausalLM": "deepseek_v2.py",
    "DeepseekV3ForCausalLM": "deepseek_v3.py",
}
arch = os.environ.get("ARCH", "")
mod = KNOWN.get(arch)
if mod is None:
    # Not in the table: say unknown, not no. A wrong "no" costs as much as a wrong
    # "yes" -- vLLM fails at load with a message that reads like a corrupt
    # checkpoint, and this warning is what the reader would have trusted.
    print("unknown")
elif mod in files:
    print("yes")
else:
    print("no")
PY
)

echo "model      $MODEL"
echo "arch       $ARCH  (vLLM support: ${SUPPORTED:-unknown})"
case "$SUPPORTED" in
    no)      echo "           WARNING: this vLLM has no ${ARCH} implementation; loading will fail" ;;
    unknown) echo "           note: architecture not in this script's table; vLLM may still support it" ;;
esac

[ -z "$SERVED" ] && SERVED="$(basename "$MODEL" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9.-' '-')"
echo "served as  $SERVED"
echo "port       $PORT   tensor-parallel $TP"
echo "log        $LOG"
echo

# Reasoning models narrate before answering, which breaks the harness outright.
#
# Qwen3.5 answers a question with `Thinking Process:\n\n1. **Analyze the Request:**...`
# and only then reaches a value. The reference harness asks for one bare JSON action
# object and nothing else, so a thinking prefix is not a wrong answer -- it is an
# unparseable one, and the trial scores zero for a reason that has nothing to do with
# the model's ability. Measured, same server, same prompt:
#
#     default                          -> 'Thinking Process:\n\n1.  **Analyze the Req'
#     chat_template_kwargs enable_thinking=False -> '391'
#
# Set server-side so the harness stays model-agnostic: a harness should not have to
# know which model family is behind the endpoint. `--default-chat-template-kwargs` is
# applied to every request that does not override it.
EXTRA_ARGS=()
if [ "${HARNESSGRAD_DISABLE_THINKING:-1}" = 1 ]; then
    EXTRA_ARGS+=(--default-chat-template-kwargs '{"enable_thinking": false}')
fi

# **标准 tool_calls 由服务端解析,不是模型的问题。**
#
# 实测(2026-10-04):把 codex 指向这个 9B 之后,模型确实调用了工具,但它输出的是
# `<tool_call><function=exec_command><parameter=cmd>...` 这样的**文本**;codex 读不出,
# 于是一步都没执行、反复重说同一句话。原因不是模型"不会调工具",是 vLLM 没开解析 ——
# 没有这两个开关,模型的工具调用标记原样返回,调用方的 tool_calls 永远是空。
#
# 而 codex 0.160 只认 `wire_api = "responses"`,即它必须拿到结构化的 tool_calls,
# 所以"用一个本地模型驱动 codex"这件事能不能成,由这两行决定。
#
# **parser 必须对上模型的对话模板,不是"Qwen 就用 hermes"。** 实测(2026-10-04):
# 先按 `hermes` 起,模型确实调用了工具,但输出是
#   <tool_call><function=write_note><parameter=text>nine</parameter></function></tool_call>
# 而 `hermes` 期待的是 `<tool_call>{"name":..., "arguments":{...}}</tool_call>`,于是
# `tool_calls` 仍然是空。vLLM 0.29 把 `qwen3_xml` / `qwen3_coder` 都映射到
# `qwen3_engine_tool_parser`,那才是这个 XML 格式的解析器。
# 不开 `--enable-auto-tool-choice` 时 vLLM 会拒绝带 `tools` 的请求;开了之后只影响
# **带 tools 的请求**,我们的 harness 走的是纯文本动作,所以 agent 路径不受影响。
# 换模型时要跟着换 parser(`vllm serve --help` 里列了 granite / hermes / internlm /
# jamba / llama3_json / mistral / pythonic),所以它可以用环境变量覆盖。
TOOL_PARSER="${HARNESSGRAD_TOOL_PARSER:-qwen3_xml}"
if [ "$TOOL_PARSER" != "none" ]; then
    EXTRA_ARGS+=(--enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER")
fi

nohup "$VLLM" serve "$MODEL" \
    --served-model-name "$SERVED" \
    --tensor-parallel-size "$TP" \
    --host 127.0.0.1 --port "$PORT" \
    --gpu-memory-utilization "$GPUMEM" \
    --max-model-len "$MAXLEN" \
    "${EXTRA_ARGS[@]}" > "$LOG" 2>&1 &
PID=$!
echo "started (pid $PID); waiting for the server to answer..."

for i in $(seq 1 60); do
    if curl -s -m 2 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
        echo "ready after ~$((i*5))s"
        break
    fi
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "server died. Last lines of $LOG:" >&2
        tail -20 "$LOG" >&2
        exit 1
    fi
    sleep 5
done

cat <<EOF

Point the platform at it. Both models may use the same server; they are separate
configuration because they are separate jobs:

    # harness -- solves the task; its score is the measurement
    HG_AGENT_BACKEND=openai
    HG_AGENT_MODEL=$SERVED
    HG_AGENT_BASE_URL=http://127.0.0.1:$PORT/v1
    HG_AGENT_API_KEY=local

    # method -- improves the harness, with its own budget
    HG_METHOD_MODEL=$SERVED
    HG_METHOD_BASE_URL=http://127.0.0.1:$PORT/v1
    HG_METHOD_API_KEY=local

For RRSI's search roles, which read their own variables:

    RISE_SEARCH_MODEL=$SERVED
    RISE_SEARCH_BASE_URL=http://127.0.0.1:$PORT/v1
    RISE_SEARCH_API_KEY=local

Stop it with: $0 --stop
EOF
