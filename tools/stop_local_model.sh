#!/bin/sh
# Stop local inference servers, then verify the GPU is actually free.
#
# Every rule below exists because breaking it cost a retry, and the failures were all
# of one kind: a pattern that matched something other than the intended process.
#
#   1. `pgrep -f vllm` matches the shell running the command, because the pattern is
#      in its own argv. Result: the caller is SIGKILLed before doing anything.
#
#   2. `*serve*` matched the wrapper script that called this one -- its argv contains
#      `serve_local_model.sh` -- so the launch killed itself.
#
#   3. `pgrep -f` is case sensitive, and the orphaned engine calls itself
#      `VLLM::EngineCore`. The pattern `vllm` never matched it, so it survived with no
#      parent and kept 36 GB of the device. The next launch then failed with "Free
#      memory on device cuda:0 (3.94/39.49 GiB) ... is less than desired", which reads
#      like a model too large for the card.
#
#   4. Widening to `-i` matched *any* process mentioning vllm anywhere in its argv,
#      including a parent shell whose command line contained a log path. So the match
#      is now on the **executable** first, and only then on the arguments.
#
# The lesson the four share: identify a process by what it is running, not by text
# that happens to appear near it.
me=$$
killed=0

kill_pid() {
    [ -z "$1" ] && return
    [ "$1" = "$me" ] && return
    kill -9 "$1" 2>/dev/null && killed=$((killed + 1))
}

# 1. Anything whose executable is vLLM's.
for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    [ "$pid" = "$me" ] && continue

    exe=$(readlink "$d/exe" 2>/dev/null) || continue
    case "$exe" in
        */bin/vllm|*/vllm) kill_pid "$pid"; continue ;;
    esac

    # The engine core renames itself, and its exe is still python, so the process name
    # is the only handle. Matched on the exact prefix so a shell is never caught.
    name=$(cat "$d/comm" 2>/dev/null) || continue
    case "$name" in
        VLLM::*) kill_pid "$pid" ;;
    esac
done

# 2. A python process running vllm as a module or entrypoint.
for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    [ "$pid" = "$me" ] && continue
    # `[ -r ]` before the redirect: a process can exit between the glob and the open,
    # and the shell reports that as "cannot open /proc/NNN/cmdline: No such file" even
    # with stderr redirected, because the redirect itself is what fails. Harmless, but
    # noise during a stop looks like something went wrong.
    [ -r "$d/cmdline" ] || continue
    cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
    case "$cmd" in
        *"-m vllm"*|*"/vllm serve "*|*"vllm.entrypoints"*) kill_pid "$pid" ;;
    esac
done

# 3. Whatever is bound to the ports we use.
for port in 8000 8001 8002; do
    pid=$(ss -ltnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | head -1)
    kill_pid "$pid"
done

[ "$killed" -gt 0 ] && sleep 3

# 4. Report rather than assume -- but do not mistake someone else's process for our own
#    failure. This machine is shared, and a strict "the device must be empty" check
#    reported a passing retrieval job as our leak:
#
#      warning: GPU still held after killing 0 process(es):
#      3844793, 8958 MiB     <- python experiments/aml/evaluate_retrieval.py
#
#    So the check is informational. What matters is whether anything *we* started is
#    still alive, and the loops above already answered that.
busy=$(nvidia-smi --query-compute-apps=pid,process_name,used_memory \
       --format=csv,noheader 2>/dev/null)
if [ -n "$busy" ]; then
    echo "note: GPU memory still in use (not necessarily by us):"
    echo "$busy" | sed 's/^/  /'
fi
echo "stopped $killed process(es)"
exit 0
