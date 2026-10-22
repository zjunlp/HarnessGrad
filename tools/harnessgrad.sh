#!/usr/bin/env bash
# HarnessGrad 的启停入口:一条命令把平台拉起来,一条命令看它在不在。
#
#     ./tools/harnessgrad.sh start      # 启动本地模型服务和控制台
#     ./tools/harnessgrad.sh status     # 它们现在什么状态
#     ./tools/harnessgrad.sh stop       # 停掉
#     ./tools/harnessgrad.sh restart
#
# 为什么需要它
# ------------
# 平台由两个进程组成,而且是有顺序的:控制台要显示本地模型,模型得先在;实验要跑,
# 两个都得在。之前这些是散在文档里的两条 `setsid nohup ... &`,结果是重启机器后
# 谁都不记得该起什么、起了没有、起的对不对。一个需要记住命令行的测量平台,用起来
# 就会出错,而这里出错的表现是"分数看起来正常但来自一个假模型"。
#
# 所以这个脚本做三件具体的事:
#   * **幂等。** 已经起来的不会重起一个;端口被别的进程占着会说清楚是谁。
#   * **等就绪,不是等端口。** 模型服务要 2 分钟才真正能答话,端口开着不代表能推理,
#     所以启动后是轮询 `/v1/models` 直到它回答,而不是 sleep 一个猜测的秒数。
#   * **状态可查。** `status` 说清每个服务在不在、模型是不是真的能应答;退出码
#     可直接用在脚本里。
#
# 环境变量:
#   HG_MODEL_DIR    要服务的模型目录(默认 Qwen3.5-9B)
#   HG_MODEL_PORT   模型服务端口(默认 8001)
#   HG_UI_PORT      控制台端口(默认 8771)
#   HG_NO_MODEL=1   只起控制台(模型已在别处跑)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.harnessgrad-state"          # PID 文件放这里,gitignored
MODEL_DIR="${HG_MODEL_DIR:-/mnt/20t/qzs/PLMs/Qwen3.5-9B}"
MODEL_PORT="${HG_MODEL_PORT:-8001}"
UI_PORT="${HG_UI_PORT:-8771}"
MODEL_NAME="${HG_MODEL_NAME:-qwen3.5-9b-local}"

mkdir -p "$STATE"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
info() { printf '  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# 一个端口上是谁在听。比 `kill $(lsof ...)` 可靠:没有 lsof 依赖,也返回 PID。
port_pid() {
    ss -ltnp 2>/dev/null | grep ":$1 " | grep -oP 'pid=\K[0-9]+' | head -1
}

http_ok() {  # $1 = url
    curl -s -m 4 "$1" >/dev/null 2>&1
}

pidfile() { echo "$STATE/$1.pid"; }

is_running() {  # $1 = name, $2 = expected-port
    local f; f=$(pidfile "$1")
    [ -f "$f" ] || return 1
    local pid; pid=$(cat "$f" 2>/dev/null)
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    return 0
}

# ---------------------------------------------------------------- start ---

start_model() {
    step "本地模型服务"
    if [ "${HG_NO_MODEL:-0}" = 1 ]; then
        info "HG_NO_MODEL=1,跳过"
        return 0
    fi

    local holder; holder=$(port_pid "$MODEL_PORT")
    if [ -n "$holder" ]; then
        if http_ok "http://127.0.0.1:$MODEL_PORT/v1/models"; then
            ok "端口 $MODEL_PORT 上已有可用的模型服务(pid $holder),不重起"
            echo "$holder" > "$(pidfile model)"
            return 0
        fi
        bad "端口 $MODEL_PORT 被 pid $holder 占着,但它不应答 /v1/models"
        info "先跑 $0 stop,或者换 HG_MODEL_PORT"
        return 1
    fi

    [ -d "$MODEL_DIR" ] || { bad "模型目录不存在:$MODEL_DIR"; return 1; }

    info "启动 $MODEL_DIR(首次加载约 2 分钟)"
    setsid nohup "$ROOT/tools/serve_local_model.sh" \
        --model "$MODEL_DIR" --port "$MODEL_PORT" --name "$MODEL_NAME" \
        > "$STATE/model.log" 2>&1 < /dev/null &
    echo $! > "$(pidfile model)"

    # 等"能答话",不是等端口。加载 4 个分片、捕 CUDA 图都要时间,而这段时间里
    # 端口可能已经 bind 但服务还不能推理 —— 拿端口当就绪会让第一批实验失败。
    local waited=0
    while [ "$waited" -lt 300 ]; do
        if http_ok "http://127.0.0.1:$MODEL_PORT/v1/models"; then
            ok "就绪(${waited}s)$(curl -s -m 3 http://127.0.0.1:$MODEL_PORT/v1/models \
                | python3 -c 'import json,sys;print(" — "+", ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null)"
            return 0
        fi
        sleep 5; waited=$((waited + 5))
    done
    bad "等了 ${waited}s 还没就绪,看 $STATE/model.log"
    return 1
}

start_ui() {
    step "控制台"
    local holder; holder=$(port_pid "$UI_PORT")
    if [ -n "$holder" ]; then
        if http_ok "http://127.0.0.1:$UI_PORT/api/options"; then
            ok "端口 $UI_PORT 上已有控制台(pid $holder),不重起"
            echo "$holder" > "$(pidfile ui)"
            return 0
        fi
        bad "端口 $UI_PORT 被 pid $holder 占着,但它不是控制台"
        info "换 HG_UI_PORT,或先 $0 stop"
        return 1
    fi

    setsid nohup python3 "$ROOT/tools/serve_ui.py" --port "$UI_PORT" \
        > "$STATE/ui.log" 2>&1 < /dev/null &
    echo $! > "$(pidfile ui)"

    local waited=0
    while [ "$waited" -lt 30 ]; do
        if http_ok "http://127.0.0.1:$UI_PORT/api/options"; then
            ok "就绪 http://127.0.0.1:$UI_PORT"
            return 0
        fi
        sleep 1; waited=$((waited + 1))
    done
    bad "控制台没起来,看 $STATE/ui.log"
    return 1
}

# ----------------------------------------------------------------- stop ---

stop_one() {  # $1 = name, $2 = port
    local f; f=$(pidfile "$1")
    local pid=""
    [ -f "$f" ] && pid=$(cat "$f" 2>/dev/null)
    [ -z "$pid" ] && pid=$(port_pid "$2")

    if [ -z "$pid" ]; then
        info "$1 没在跑"
        rm -f "$f"
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null
    sleep 2
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    rm -f "$f"
    ok "已停 $1 (pid $pid)"
}

stop_all() {
    step "停止"
    stop_one ui "$UI_PORT"
    # 模型是 vLLM 的进程组,单独用它的停止脚本(它按可执行文件精确识别,
    # 不会误杀这台共享机器上别人的进程)
    if [ -x "$ROOT/tools/stop_local_model.sh" ]; then
        "$ROOT/tools/stop_local_model.sh" | sed 's/^/  /'
    fi
    rm -f "$(pidfile model)"
}

# --------------------------------------------------------------- status ---

status_all() {
    local rc=0
    step "状态"

    local mp; mp=$(port_pid "$MODEL_PORT")
    if [ -n "$mp" ] && http_ok "http://127.0.0.1:$MODEL_PORT/v1/models"; then
        ok "模型服务  http://127.0.0.1:$MODEL_PORT  (pid $mp)"
        curl -s -m 3 "http://127.0.0.1:$MODEL_PORT/v1/models" | python3 -c \
            'import json,sys; print("      " + ", ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null
    elif [ -n "$mp" ]; then
        bad "模型服务  端口 $MODEL_PORT 被 pid $mp 占着,但不应答"; rc=1
    else
        bad "模型服务  未运行"; rc=1
    fi

    local up; up=$(port_pid "$UI_PORT")
    if [ -n "$up" ] && http_ok "http://127.0.0.1:$UI_PORT/api/options"; then
        ok "控制台    http://127.0.0.1:$UI_PORT  (pid $up)"
    elif [ -n "$up" ]; then
        bad "控制台    端口 $UI_PORT 被 pid $up 占着,但它不是控制台"; rc=1
    else
        bad "控制台    未运行"; rc=1
    fi

    local n; n=$(ls "$ROOT/runs"/*/curve.jsonl 2>/dev/null | wc -l)
    info "实验记录  $n 个($ROOT/runs)"
    return $rc
}

# ------------------------------------------------------------------ main ---

case "${1:-}" in
    start)
        start_model
        start_ui
        echo
        status_all || true
        ;;
    stop)    stop_all ;;
    restart) stop_all; sleep 2; start_model; start_ui ;;
    status)  status_all ;;
    *)
        sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
esac
