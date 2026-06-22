#!/usr/bin/env bash
# Thin POSIX wrapper around tools/pr_watch.py.
#
# Usage:
#   tools/pr-watch.sh <PR> [--repo OWNER/REPO] [--interval SEC] \
#                          [--merge-watch] [--no-merge-watch] [--no-detach]
#   tools/pr-watch.sh --pr <PR> [...]
#
# Detach behavior (Issue #641)
# ----------------------------
# By default this wrapper self-re-execs into a fully detached session
# (setsid + nohup + stdin </dev/null + log redirect) and returns
# immediately. This makes a bare
#
#   bash tools/pr-watch.sh <PR> --repo <owner/repo> --merge-watch
#
# survive the exit of a short-lived parent session (e.g. Claude Code's
# Bash tool), which otherwise SIGHUPs the watcher within seconds. `disown`
# alone does not help — it only drops the job from the shell's table, it
# does not shield the child from the parent session's SIGHUP. The re-exec
# is idempotent: the child runs with PR_WATCH_DETACHED=1 set and takes the
# foreground branch, so an outer `nohup ... &` wrapper from an existing
# caller becomes a harmless no-op.
#
# Wrapper-only options (consumed here, NOT forwarded to pr_watch.py):
#   --no-detach   Run the watcher in the foreground (tests / debugging).
#
# Environment:
#   PR_WATCH_LOG       Override the detached log path. Default:
#                      <repo-root>/.state/pr-watch-<PR>.log
#   PR_WATCH_DETACHED  Internal re-entry guard set by the re-exec. Do not
#                      set this by hand.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Split wrapper-only flags (--no-detach) out of the arguments forwarded to
# pr_watch.py, which does not recognize them.
no_detach=0
forward_args=()
for arg in "$@"; do
  case "$arg" in
    --no-detach) no_detach=1 ;;
    *) forward_args+=("$arg") ;;
  esac
done

# Already detached, or the caller asked for foreground: run pr_watch.py in
# place and let its exit code propagate.
if [[ -n "${PR_WATCH_DETACHED:-}" || "$no_detach" == 1 ]]; then
  exec python3 "$SCRIPT_DIR/pr_watch.py" ${forward_args[@]+"${forward_args[@]}"}
fi

# --- Detach path: self re-exec in a new, SIGHUP-immune session. ---

# Best-effort PR label for the default log filename. Handles `--pr N`,
# `--pr=N`, and a bare positional PR while skipping the values of other
# value-taking flags, so e.g. `--repo owner/name` is not mistaken for it.
pr_label="unknown"
i=0
n=${#forward_args[@]}
while (( i < n )); do
  a="${forward_args[i]}"
  case "$a" in
    --pr=*) pr_label="${a#--pr=}"; break ;;
    --pr) if (( i + 1 < n )); then pr_label="${forward_args[i+1]}"; fi; break ;;
    --repo|--interval) i=$((i + 2)) ;;
    --repo=*|--interval=*|--merge-watch|--no-merge-watch) i=$((i + 1)) ;;
    -*) i=$((i + 1)) ;;
    *) pr_label="$a"; break ;;
  esac
done
# Sanitize for filesystem safety (keep the log inside .state, never let a
# stray arg escape the directory via slashes).
pr_label="${pr_label//[^A-Za-z0-9._-]/_}"

log_path="${PR_WATCH_LOG:-$REPO_ROOT/.state/pr-watch-${pr_label}.log}"
mkdir -p "$(dirname "$log_path")"

# Re-exec the script itself via bash (the file is not marked executable),
# in a fresh session with no controlling terminal and SIGHUP ignored.
#
# setsid gives the watcher its own session (no controlling tty) — the
# strongest SIGHUP shield — but it is not installed everywhere (notably
# stock macOS). When it is absent, fall back to nohup-only detachment:
# `nohup` ignores SIGHUP and `</dev/null` + the log redirect still detach
# the child from the parent's stdio, which covers the common case.
setsid_prefix=()
if command -v setsid >/dev/null 2>&1; then
  setsid_prefix=(setsid)
fi
${setsid_prefix[@]+"${setsid_prefix[@]}"} env PR_WATCH_DETACHED=1 nohup \
  "${BASH:-bash}" "$0" ${forward_args[@]+"${forward_args[@]}"} \
  </dev/null >"$log_path" 2>&1 &
detached_pid=$!
disown 2>/dev/null || true
echo "pr-watch detached: pid=$detached_pid log=$log_path"
exit 0
