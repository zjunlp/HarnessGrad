#!/usr/bin/env bash
# Make an improver available, then prove it works.
#
# Why this is a script and not an image
# -------------------------------------
# The improver is the agent that edits a harness. Codex is the first one we ship, and
# the obvious question is whether to bake it into a container image.
#
# No, and the reason is the same one that decides every other packaging question on this
# platform: an image is a copy of somebody else's program frozen at a moment we chose,
# and codex reads its credentials and its model from `$HOME/.codex` -- pointing at a
# third-party endpoint (`base_url` in `config.toml`). Putting that in an image means
# publishing those credentials with it, and it means the improver's behaviour is frozen
# where nobody can see it. The platform's own doctrine is "nothing is built here, no run
# pulls anything" (§2.5.5) -- but that is a rule about *task* images, which are the
# measurement's environment. The improver is not the measured thing; it is the tool that
# writes the diff, and the honest way to govern a tool is to run the one that is
# installed here and record which one that was.
#
# So: this script checks what is present, prints exactly what a session would use, and
# `tools/improver.py --doctor` proves it by editing one file in a throwaway directory.
#
#     ./tools/setup_improver.sh              check only; change nothing
#     ./tools/setup_improver.sh --install    install the latest codex with npm if missing
#     ./tools/setup_improver.sh --json       machine-readable, for a launcher
#
# It never edits `~/.codex`: credentials and model choice belong to the operator.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DO_INSTALL=0
AS_JSON=0
for arg in "$@"; do
  case "$arg" in
    --install) DO_INSTALL=1 ;;
    --json)    AS_JSON=1 ;;
    -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { [ "$AS_JSON" = 1 ] || echo "$@"; }

say "HarnessGrad — improver check"
say "  (the improver is the agent that writes the diff a method applies; see improvers/)"
say ""

# ---- 1. what is on this machine, verified by running it ------------------------------
if ! python3 "$ROOT/tools/improver.py" --list; then
  say ""
  say "  no working codex found."
  if [ "$DO_INSTALL" = 1 ]; then
    say "  installing with npm: @openai/codex@latest"
    if command -v npm >/dev/null 2>&1; then
      npm install -g @openai/codex@latest || {
        say "  npm install failed — see its output above"; exit 1; }
      # A global npm install puts the wrapper on PATH but the platform binary is an
      # optional dependency; the wrapper re-execs it, so a *partial* install looks
      # installed and crashes on `--version`. The doctor below is what catches that.
      hash -r 2>/dev/null || true
    else
      say "  npm is not on PATH; install node+npm, or point --version at an existing binary"
      exit 1
    fi
  else
    say ""
    say "  Re-run with --install to install it, or set a path in improvers/improvers.json."
    say "  A method that carries its own improver does not need any of this."
    [ "$AS_JSON" = 1 ] && echo '{"ok": false, "reason": "no working codex found"}'
    exit 1
  fi
fi

# ---- 2. which one would a session use, and does it work -----------------------------
say ""
if [ "$AS_JSON" = 1 ]; then
  python3 "$ROOT/tools/improver.py" --doctor --json
else
  python3 "$ROOT/tools/improver.py" --doctor || exit 1
  say ""
  say "  Next: python3 tools/improver.py --doctor --smoke"
  say "        (the smoke runs one real session: it edits a throwaway file)"
fi
