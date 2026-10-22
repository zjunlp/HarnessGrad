#!/usr/bin/env bash
# Install what HarnessGrad needs, then prove it works.
#
# The platform itself has no third-party Python dependencies -- everything it uses
# is in the standard library. What it needs from the machine is small, and one item
# is easy to get wrong:
#
#     bubblewrap   so a harness cannot see the platform while it runs
#     git          every checkpoint is a commit in the run's workspace
#     python3      >= 3.10 (dataclass syntax in the platform's own modules)
#
# `openai` is NOT installed here. It is imported only by a harness entrypoint that
# talks to a model (`base_harness/loop/agent.py:105`, lazily, inside the call), so a
# harness written in another language or against another provider never needs it.
# Install it yourself if you use the reference harness against an OpenAI-compatible
# endpoint.
#
# Why the last step is a self-check rather than a version check: having bubblewrap
# installed is not the same as the sandbox working. A kernel with unprivileged user
# namespaces disabled, or an AppArmor policy restricting them (the default on newer
# Ubuntu), leaves `bwrap` present and failing at runtime -- and a run that silently
# fell back to no sandbox would produce numbers that mean something other than what
# they say. So this script finishes by asking the platform to build a namespace and
# look for itself inside it.
#
#     ./install.sh            install what is missing, then verify
#     ./install.sh --check    verify only; change nothing
#     ./install.sh --yes      do not ask before installing (for CI)
#
set -uo pipefail

CHECK_ONLY=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ -t 1 ]; then
    BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
else
    BOLD=; RED=; GREEN=; YELLOW=; OFF=
fi

ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$RED" "$OFF" "$1"; }
step() { printf '\n%s%s%s\n' "$BOLD" "$1" "$OFF"; }

NEED_SUDO=()      # commands the user will have to run themselves
FAILED=0

# ---------------------------------------------------------------- python ---

step "Python"

PY=""
FIRST_PY3=""
for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    [ -n "$FIRST_PY3" ] || FIRST_PY3="$(command -v "$candidate")"
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PY="$(command -v "$candidate")"
        break
    fi
done

if [ -n "$PY" ]; then
    ok "$("$PY" -V 2>&1) at $PY"
    # Say so when the first `python3` on PATH is not the one that passed. Otherwise the
    # check would certify an interpreter the user is not going to run: `python3 driver.py`
    # resolves to the old one, fails on `X | None` annotations, and the installer's
    # "ready" turns out to have been about a different program.
    if [ -n "$FIRST_PY3" ] && [ "$FIRST_PY3" != "$PY" ]; then
        warn "but 'python3' on PATH is $FIRST_PY3 ($("$FIRST_PY3" -V 2>&1)), which is too old"
        warn "put the newer one first, or run the driver as: $PY driver.py"
    fi
else
    bad "no python3 >= 3.10 on PATH -- the platform uses \`X | None\` annotations"
    if [ -n "$FIRST_PY3" ]; then
        warn "'python3' resolves to $FIRST_PY3 ($("$FIRST_PY3" -V 2>&1))"
    fi
    case "$(uname -s)" in
        Linux) NEED_SUDO+=("apt-get install -y python3") ;;
        Darwin) NEED_SUDO+=("brew install python3") ;;
    esac
    FAILED=1
fi

# ------------------------------------------------------------------ git ---

step "git"
if command -v git >/dev/null 2>&1; then
    ok "$(git --version)"
else
    bad "git not found -- every checkpoint is a git commit, so runs cannot record state"
    case "$(uname -s)" in
        Linux) NEED_SUDO+=("apt-get install -y git") ;;
        Darwin) NEED_SUDO+=("brew install git") ;;
    esac
    FAILED=1
fi

# ------------------------------------------------------------ bubblewrap ---

step "bubblewrap (the sandbox)"

# `HG_BWRAP` lets a caller point at a specific binary -- useful when bwrap is installed
# somewhere unusual, and necessary for testing this script's failure paths without
# uninstalling anything.
#
# When it is set it is authoritative: a bad value is an error, not a reason to fall back
# to `PATH`. Falling back would mean the installer quietly checking a *different*
# bubblewrap than the one it was told to use, and then reporting on a program that had
# nothing to do with the failure.
have_bwrap() {
    if [ -n "${HG_BWRAP:-}" ]; then
        [ -x "${HG_BWRAP}" ]
    else
        command -v bwrap >/dev/null 2>&1
    fi
}
bwrap_bin() {
    if [ -n "${HG_BWRAP:-}" ]; then
        printf '%s' "$HG_BWRAP"
    else
        command -v bwrap
    fi
}

if [ -n "${HG_BWRAP:-}" ] && [ ! -x "${HG_BWRAP}" ]; then
    bad "HG_BWRAP=${HG_BWRAP} is not an executable"
    warn "unset it to use PATH, or point it at a real bubblewrap"
    exit 2
fi

# How to install it here, for the report at the end. Kept in one place so every path
# that fails to get bwrap can name the fix -- a diagnostic that does not say what to
# do next is only half a diagnostic.
bwrap_fix() {
    if command -v apt-get >/dev/null 2>&1; then
        NEED_SUDO+=("apt-get install -y bubblewrap")
    elif command -v dnf >/dev/null 2>&1; then
        NEED_SUDO+=("dnf install -y bubblewrap")
    elif command -v pacman >/dev/null 2>&1; then
        NEED_SUDO+=("pacman -S --noconfirm bubblewrap")
    elif command -v brew >/dev/null 2>&1; then
        NEED_SUDO=("brew install bubblewrap")
    else
        NEED_SUDO+=("install bubblewrap from your package manager")
    fi
}

if have_bwrap; then
    ok "bwrap $("$(bwrap_bin)" --version 2>/dev/null | awk '{print $2}') at $(bwrap_bin)"
elif [ "$CHECK_ONLY" = 1 ]; then
    bad "bwrap not found (--check: not installing)"
    warn "without it the driver refuses to start; --no-sandbox would let a harness"
    warn "read the driver and .env"
    bwrap_fix
    FAILED=1
elif command -v apt-get >/dev/null 2>&1; then
    warn "bwrap not found; trying apt-get"
    if [ "$ASSUME_YES" = 0 ] && [ -t 0 ]; then
        printf '  install bubblewrap with apt-get? [y/N] '
        read -r reply || reply=""
        case "$reply" in [yY]*) ;; *) reply="" ;; esac
    elif [ "$ASSUME_YES" = 0 ]; then
        # No terminal to ask on, and `--yes` is how a caller says "go ahead". Guessing
        # would mean a script silently invoking apt-get, so say what to do instead.
        warn "not a terminal, so nothing was installed; re-run with --yes to allow it"
        reply=""
    else
        reply=y
    fi
    if [ -z "${reply:-}" ]; then
        bwrap_fix
        FAILED=1
    elif sudo -n true 2>/dev/null; then
        if sudo apt-get install -y bubblewrap; then
            ok "installed"
        else
            bad "apt-get failed"
            bwrap_fix
            FAILED=1
        fi
    else
        warn "sudo needs a password, which this script will not ask for"
        bwrap_fix
        FAILED=1
    fi
else
    bad "bwrap not found and no apt-get here"
    bwrap_fix
    FAILED=1
fi

# --------------------------------------------------------------- verify ---

step "Sandbox self-check"

if [ -z "$PY" ]; then
    bad "skipped: no usable python3"
    FAILED=1
elif ! have_bwrap; then
    bad "skipped: bwrap is not installed"
else
    # The platform's own check, not a re-implementation of it: this asks bwrap to
    # build the namespace the driver will build, then looks for the platform from
    # inside. `|| true` keeps `set -e`-style exits from hiding the message.
    detail="$("$PY" - <<'PY' 2>&1
import sys
from pathlib import Path
sys.path.insert(0, ".")
try:
    import eval.containers.sandbox as sandbox
except Exception as exc:
    print(f"could not import the platform's sandbox module: {exc}")
    raise SystemExit(1)
report = sandbox.self_check(Path(".").resolve(), Path("../harnessgrad_work").resolve())
print(report["detail"])
raise SystemExit(0 if report["ok"] else 1)
PY
)" || FAILED=1

    if [ "$FAILED" = 0 ]; then
        ok "$detail"
    else
        bad "$detail"
        printf '\n'
        warn "bwrap is present but the namespace did not apply."
        warn "the cause is the kernel or an LSM here, not the package:"
        hint=0
        if [ -r /proc/sys/kernel/unprivileged_userns_clone ] && \
           [ "$(cat /proc/sys/kernel/unprivileged_userns_clone)" = 0 ]; then
            warn "  unprivileged user namespaces are disabled:"
            NEED_SUDO+=("sysctl -w kernel.unprivileged_userns_clone=1")
            hint=1
        fi
        if [ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ] && \
           [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns)" != 0 ]; then
            warn "  AppArmor restricts unprivileged user namespaces:"
            NEED_SUDO+=("sysctl -w kernel.apparmor_restrict_unprivileged_userns=0")
            hint=1
        fi
        if [ "$hint" = 0 ]; then
            # The switches look permissive, so the reason is elsewhere and worth
            # naming: a container without CAP_SYS_ADMIN, a seccomp profile, or a
            # grsecurity kernel all present this way.
            warn "  both sysctls look permissive, so check:"
            warn "    dmesg | tail          for a recent denial"
            warn "    are you inside a container without CAP_SYS_ADMIN?"
            warn "    unshare --user --pid true   as a direct test of the kernel"
        fi
        warn "you can still run with --no-sandbox, but the harness can then read"
        warn "the driver and .env, and only the hash check stands in its way."
    fi
fi

# ---------------------------------------------------------------- report ---

step "Summary"

if [ "$FAILED" = 0 ]; then
    printf '  %sready.%s Next:\n\n' "$GREEN$BOLD" "$OFF"
    printf '  Local demo only; no model credentials needed. No production/H0 authorization.\n\n'
    printf '      HG_AGENT_BACKEND=mock python3 driver.py --dataset demo --sampling all \\\n'
    printf '          --harness loop --mode A --rounds 2 \\\n'
    printf '          --run-id control --method-entrypoint "python %s/methods/noop/run.py"\n\n' "$ROOT"
    exit 0
fi

printf '  %snot ready.%s\n' "$RED$BOLD" "$OFF"
if [ "${#NEED_SUDO[@]}" -gt 0 ]; then
    printf '\n  run these yourself, then re-run this script:\n\n'
    for cmd in "${NEED_SUDO[@]}"; do
        printf '      sudo %s\n' "$cmd"
    done
fi
printf '\n'
exit 1
