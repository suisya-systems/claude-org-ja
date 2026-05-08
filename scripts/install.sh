#!/usr/bin/env bash
# One-liner installer for claude-org-ja (Linux / macOS).
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/suisya-systems/claude-org-ja/main/scripts/install.sh | bash
#   bash scripts/install.sh [--dir <path>] [--dry-run] [--skip-mcp]
#
# This script:
#   1. Checks for required commands (git, claude, renga, gh) and prints
#      installation hints when something is missing.
#   2. Clones suisya-systems/claude-org-ja (asks before reusing an
#      existing directory).
#   3. Runs `renga mcp install` (user-scope) so the renga-peers MCP
#      server is registered with Claude Code.
#   4. Prints next steps.
#
# It never auto-installs missing tools and never bypasses Claude Code's
# permission prompts.
set -euo pipefail

REPO_URL="https://github.com/suisya-systems/claude-org-ja.git"
TARGET_DIR="claude-org-ja"
DRY_RUN=0
SKIP_MCP=0
# CLAUDE_ORG_REF pins the clone to a specific branch or tag for
# reproducibility. Default `main` keeps the latest-features behaviour
# unchanged for users who do not set it.
REF="${CLAUDE_ORG_REF:-main}"

usage() {
  cat <<'USAGE'
Usage: install.sh [--dir <path>] [--dry-run] [--skip-mcp] [--help]

Options:
  --dir <path>   Target directory for the clone (default: ./claude-org-ja).
  --dry-run      Print the commands that would run without executing them.
  --skip-mcp     Skip `renga mcp install` (use when already registered).
  -h, --help     Show this help and exit.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      [[ $# -ge 2 ]] || { echo "install.sh: --dir requires an argument" >&2; exit 2; }
      TARGET_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-mcp) SKIP_MCP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# Detect interactive input. When piped from curl, stdin is the pipe; the
# only reliable prompt path is /dev/tty, and only if it can actually be
# opened for both read and write. Probe once and cache the result so
# `set -e` doesn't kill the script on a failed write later.
HAS_TTY=0
if [[ -t 0 ]]; then
  HAS_TTY=1
elif { : > /dev/tty; } 2>/dev/null && { : < /dev/tty; } 2>/dev/null; then
  HAS_TTY=1
fi

prompt_yes_no() {
  # $1 = prompt, $2 = default (Y or N). Returns 0 for yes, 1 for no.
  local prompt="$1" default="$2" reply=""
  local hint
  if [[ "$default" == "Y" ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
  if [[ "$HAS_TTY" != "1" ]]; then
    echo "install.sh: non-interactive shell; assuming '$default' for: $prompt" >&2
    [[ "$default" == "Y" ]] && return 0 || return 1
  fi
  if [[ -t 0 ]]; then
    read -r -p "$prompt $hint " reply || reply=""
  else
    # Use /dev/tty for both prompt and response. Guard with `|| true` so
    # `set -e` doesn't terminate on a transient write failure.
    printf "%s %s " "$prompt" "$hint" > /dev/tty 2>/dev/null || true
    read -r reply < /dev/tty || reply=""
  fi
  reply="${reply:-$default}"
  case "$reply" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

run() {
  # Echo + execute, or just echo when --dry-run is set.
  echo "+ $*"
  if [[ "$DRY_RUN" != "1" ]]; then
    "$@"
  fi
}

# Detect Git Bash / MSYS2 / Cygwin running on Windows. The PowerShell
# `iwr | iex` flow uses install.ps1; the matching bash one-liner from
# PowerShell (`bash <(curl ...)`) and direct `bash scripts/install.sh`
# from a Git Bash shell both land here, where two Windows-specific gaps
# in the POSIX prerequisite check show up:
#   1. Bash's `command -v foo` tries `foo.exe` on MSYS but does NOT try
#      `foo.cmd` / `foo.bat`, so npm-installed shims (`renga.cmd`) are
#      invisible to a plain `command -v renga`.
#   2. Some installers (Claude Code's per-user installer, cargo,
#      scoop) drop binaries in dirs that aren't on the bash PATH when
#      bash is launched fresh from PowerShell, even though they are on
#      the user's Windows PATH.
# Both gaps need explicit handling; everywhere else, `IS_WINDOWS_BASH=0`
# keeps the legacy POSIX path intact.
case "${OSTYPE:-}" in
  msys*|cygwin*|win32*) IS_WINDOWS_BASH=1 ;;
  *) IS_WINDOWS_BASH=0 ;;
esac
if [[ "$IS_WINDOWS_BASH" != "1" ]]; then
  case "$(uname -s 2>/dev/null || true)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS_BASH=1 ;;
  esac
fi

# Linux / macOS classification for OS-specific install hints (node /
# npm tip, Python venv guidance). Windows / Git Bash is already
# handled by IS_WINDOWS_BASH above.
case "$(uname -s 2>/dev/null || true)" in
  Linux*)  IS_LINUX=1; IS_MAC=0 ;;
  Darwin*) IS_LINUX=0; IS_MAC=1 ;;
  *)       IS_LINUX=0; IS_MAC=0 ;;
esac

resolve_command() {
  # Pure resolver: prints the resolved path on stdout and returns 0,
  # or returns 1 with no output. Side effects (PATH prepend) live in
  # require_or_warn so this stays composable for the explicit
  # `RENGA_BIN=$(resolve_command renga ...)` capture below.
  local cmd="$1" resolved="" ext prefix candidate
  if resolved=$(command -v "$cmd" 2>/dev/null); then
    printf '%s\n' "$resolved"; return 0
  fi
  [[ "$IS_WINDOWS_BASH" == "1" ]] || return 1
  # (b) Windows-specific extensions that bash's command -v skips.
  for ext in .exe .cmd .bat; do
    if resolved=$(command -v "$cmd$ext" 2>/dev/null); then
      printf '%s\n' "$resolved"; return 0
    fi
  done
  # (c) Well-known per-user install prefixes that may not be on bash's
  # PATH when invoked via `bash <(curl ...)` from PowerShell. Order
  # matters: npm comes first because both `renga` and (sometimes)
  # `claude` ship there as `.cmd` shims; cargo / scoop / Programs
  # follow for native `.exe` builds.
  for prefix in \
    "$HOME/AppData/Roaming/npm" \
    "$HOME/.cargo/bin" \
    "$HOME/.local/bin" \
    "$HOME/scoop/shims" \
    "$HOME/AppData/Local/Programs/$cmd"; do
    # Extension-bearing variants first so a real Windows install wins
    # over any stale bare-name POSIX wrapper npm sometimes leaves
    # alongside them; bare "" last so `node` / `python`-style POSIX
    # shims still resolve when no `.exe` / `.cmd` exists.
    for ext in .exe .cmd .bat ""; do
      candidate="$prefix/$cmd$ext"
      # `-f`, not `-x`: npm-generated `.cmd` shims ship without the
      # POSIX execute bit (Windows only cares about the extension),
      # so `-x` would skip exactly the case we're trying to catch.
      if [[ -f "$candidate" ]]; then
        printf '%s\n' "$candidate"; return 0
      fi
    done
  done
  return 1
}

require_or_warn() {
  # $1 = command name, $2 = install hint URL/text.
  local cmd="$1" hint="$2" resolved="" parent
  if resolved=$(resolve_command "$cmd"); then
    echo "  [ok]   $cmd: $resolved"
    # If the resolution came from a fallback prefix that isn't on
    # bash's PATH, prepend it so sibling tools / later `command -v`
    # lookups in this script can find it without re-running the
    # ladder. Skip when the dir is already there to keep PATH stable.
    if [[ "$IS_WINDOWS_BASH" == "1" ]]; then
      parent=$(dirname -- "$resolved")
      case ":$PATH:" in
        *":$parent:"*) ;;
        *) PATH="$parent:$PATH" ;;
      esac
    fi
    return 0
  fi
  echo "  [miss] $cmd not found. Install hint: $hint"
  return 1
}

echo "== claude-org-ja installer =="
echo

echo "Checking prerequisites..."
# Snapshot whether `renga` was already discoverable on the user's
# interactive bash PATH *before* any prerequisite probe runs. That
# is the right baseline for the trailing Git-Bash-on-Windows hint:
# require_or_warn below may prepend a fallback dir to PATH for the
# remainder of this process, but that change does not survive into
# the user's shell. If the snapshot says "no" and we still resolve
# renga via the fallback ladder, the user's `renga --layout ops`
# step needs the shell-PATH advice regardless of whether the
# resolved file was a `.exe` or a `.cmd`.
if command -v renga >/dev/null 2>&1; then
  RENGA_ON_USER_PATH=1
else
  RENGA_ON_USER_PATH=0
fi
missing=0
# Build OS-aware install hints. The renga hint by itself ("npm install
# -g ...") is misleading on a fresh WSL2 / Ubuntu / macOS box where
# node + npm aren't installed at all — users follow it, hit a "command
# not found: npm" wall, and have to retrace. Surfacing node / npm as
# explicit prereqs ahead of renga puts the actual blocker first.
if [[ "$IS_LINUX" == "1" ]]; then
  NODE_HINT='install Node 20 LTS via nvm — https://github.com/nvm-sh/nvm (then: nvm install --lts)'
elif [[ "$IS_MAC" == "1" ]]; then
  NODE_HINT='brew install node  (or use nvm: https://github.com/nvm-sh/nvm)'
else
  NODE_HINT='https://nodejs.org/  (LTS) — or use nvm-windows / fnm / scoop'
fi
NPM_HINT="ships with Node — install Node first ($NODE_HINT)"
require_or_warn git    "https://git-scm.com/downloads" || missing=1
require_or_warn claude "https://claude.ai/code (Claude Code CLI)" || missing=1
require_or_warn node   "$NODE_HINT" || missing=1
require_or_warn npm    "$NPM_HINT" || missing=1
require_or_warn renga  "npm install -g @suisya-systems/renga@0.18.0" || missing=1
require_or_warn gh     "https://cli.github.com/" || missing=1
# Capture the absolute path so the later `run renga mcp install` can
# bypass bash's PATH-extension blind spot for `.cmd` shims (Git Bash
# on Windows tries `.exe` but not `.cmd` / `.bat`, so a PATH-only
# resolution would `exit 127` even after require_or_warn succeeded).
# 2>/dev/null + `|| true` keeps `set -e` happy when renga is genuinely
# missing — the missing= flag above already handles that case, and we
# fall back to bare `renga` so the failure mode stays familiar.
RENGA_BIN=$(resolve_command renga 2>/dev/null || true)
echo

if [[ "$missing" == "1" ]]; then
  cat <<'MSG' >&2
install.sh: one or more prerequisites are missing.
Install the listed tools, then re-run this installer.
(This script intentionally does not auto-install dependencies.)
MSG
  exit 1
fi

# --- Clone -----------------------------------------------------------------

if [[ -e "$TARGET_DIR" ]]; then
  # `.git` may be a directory (normal clone) or a file (worktree / submodule).
  if [[ -e "$TARGET_DIR/.git" ]]; then
    # Verify it's actually our repo, not some unrelated git checkout that
    # happens to share the directory name. Without this check, a curl|bash
    # invocation would silently run later steps against the wrong tree.
    existing_url=$(git -C "$TARGET_DIR" remote get-url origin 2>/dev/null || true)
    if [[ "$existing_url" != "$REPO_URL" ]]; then
      echo "install.sh: '$TARGET_DIR' is a git repo, but its 'origin' is:" >&2
      echo "  ${existing_url:-<unset>}" >&2
      echo "  expected: $REPO_URL" >&2
      echo "install.sh: refusing to reuse. Move/rename the directory or pass --dir <other>." >&2
      exit 1
    fi
    echo "install.sh: '$TARGET_DIR' already exists and points at $REPO_URL."
    # Fail-closed in non-interactive mode: the curl|bash flow cannot get a
    # meaningful Y/N answer, so we don't silently fall through to the
    # later steps on a pre-existing checkout.
    if [[ "$HAS_TTY" != "1" ]]; then
      echo "install.sh: non-interactive shell; refusing to reuse without confirmation." >&2
      echo "install.sh: re-run interactively, or move/rename the directory and re-run." >&2
      exit 1
    fi
    if prompt_yes_no "Skip clone and reuse existing directory?" "Y"; then
      echo "Reusing existing $TARGET_DIR (no clone)."
    else
      echo "install.sh: aborting so you can move or rename '$TARGET_DIR' first." >&2
      exit 1
    fi
  else
    echo "install.sh: '$TARGET_DIR' exists but is not a git repository." >&2
    echo "install.sh: refusing to overwrite. Move or rename it and re-run." >&2
    exit 1
  fi
else
  # `git clone --branch` accepts either a branch or a tag; an unknown ref
  # exits non-zero with "Remote branch <ref> not found", which `set -e`
  # propagates. Wrap it so the user sees a friendlier abort message
  # naming the ref they asked for.
  if [[ "$DRY_RUN" != "1" ]]; then
    echo "+ git clone --branch $REF $REPO_URL $TARGET_DIR"
    if ! git clone --branch "$REF" "$REPO_URL" "$TARGET_DIR"; then
      echo "install.sh: failed to clone ref '$REF' from $REPO_URL." >&2
      echo "install.sh: check that CLAUDE_ORG_REF names an existing branch or tag." >&2
      echo "install.sh: branches and tags are accepted; see https://github.com/suisya-systems/claude-org-ja/releases for stable tags." >&2
      exit 1
    fi
  else
    echo "+ git clone --branch $REF $REPO_URL $TARGET_DIR"
  fi
fi

# --- renga mcp install -----------------------------------------------------

if [[ "$SKIP_MCP" == "1" ]]; then
  echo "Skipping 'renga mcp install' (--skip-mcp)."
else
  echo
  echo "Registering renga-peers MCP with Claude Code (user-scope)..."
  echo "Note: Claude Code may show a permission prompt; approve it to continue."
  run "${RENGA_BIN:-renga}" mcp install
fi

# --- Python deps (core-harness pin) ----------------------------------------

# Step B (Issue #128) made tools/check_role_configs.py and
# tools/generate_worker_settings.py thin shims over the core-harness
# package; Phase 4 (Issue #129) then moved the dispatcher runner and
# the worker settings generator out of tools/ into the
# claude-org-runtime package. Phase 5c (Issue #130) moved the install
# path from `requirements.txt` to `pyproject.toml`; we prefer the
# editable install so the dep set comes from the canonical source.
# Probe each candidate with `--version` so the Microsoft Store App
# Execution Alias stub on Windows (`python.exe` under `WindowsApps\`
# that exits non-zero or pops the Store on real calls) doesn't get
# selected. `py -3` is the Windows-specific fallback for stock boxes
# that ship only the launcher; pinning `-3` skips any leftover 2.7.
# PY_LAUNCHER carries the optional `-3` so the unquoted expansion in
# `run $PY $PY_LAUNCHER ...` drops empty when not needed.
PY=""
PY_LAUNCHER=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" --version >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
if [[ -z "$PY" && "$IS_WINDOWS_BASH" == "1" ]]; then
  if command -v py >/dev/null 2>&1 && py -3 --version >/dev/null 2>&1; then
    PY="py"; PY_LAUNCHER="-3"
  fi
fi
PYPROJECT_FILE="$TARGET_DIR/pyproject.toml"
REQ_FILE="$TARGET_DIR/requirements.txt"

# PEP 668 ("externally-managed-environment", default on Debian 12+ /
# Ubuntu 23.04+ and on Homebrew Python) makes `pip install --user`
# abort. Detect by asking the resolved interpreter directly so the
# answer reflects whatever python `$PY` actually points at, not just
# `/usr/lib/...`. PEP668=1 switches to a project-local venv below.
# This is *not* an auto-install of a missing tool — Python itself is
# already on the system; the venv is just an isolated install
# location for this project's editable deps so the externally-managed
# system Python stays untouched.
PEP668=0
if [[ -n "$PY" ]] && $PY $PY_LAUNCHER -c \
    'import os, sysconfig; raise SystemExit(0 if os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")) else 1)' \
    >/dev/null 2>&1; then
  PEP668=1
fi

USED_VENV=0
if [[ ! -f "$PYPROJECT_FILE" && ! -f "$REQ_FILE" ]]; then
  # Older refs / fixtures predate Step B and ship neither file.
  # The shim CLIs only exist on Step-B-or-later commits, so skipping
  # here keeps the installer backward compatible.
  echo "Skipping Python deps (no pyproject.toml or requirements.txt)."
elif [[ -z "$PY" ]]; then
  echo "WARN: python not found; tools/check_role_configs.py will fail until you 'pip install -e .'."
elif [[ "$PEP668" == "1" ]]; then
  # Externally-managed: install editable into $TARGET_DIR/.venv.
  # `python -m venv` requires the `venv` module; on Debian / Ubuntu
  # that ships separately as `python3-venv` (or pulled in by
  # `python3-full`). Probe before creating so the failure mode is a
  # named apt package instead of a bare ensurepip traceback.
  if ! $PY $PY_LAUNCHER -m venv --help >/dev/null 2>&1; then
    cat <<'MSG' >&2
install.sh: this Python install is externally-managed (PEP 668), so
the installer needs `python -m venv` to create an isolated venv for
project deps — but the venv module is missing.
On Debian / Ubuntu, install:
  sudo apt install -y python3-full
(or: sudo apt install -y python3-venv python3-pip)
Then re-run this installer.
MSG
    exit 1
  fi
  VENV_DIR="$TARGET_DIR/.venv"
  if [[ "$IS_WINDOWS_BASH" == "1" ]]; then
    VENV_PY="$VENV_DIR/Scripts/python.exe"
  else
    VENV_PY="$VENV_DIR/bin/python"
  fi
  echo
  echo "Detected externally-managed Python (PEP 668)."
  if [[ -d "$VENV_DIR" ]]; then
    echo "Reusing existing venv at $VENV_DIR ..."
  else
    echo "Creating venv at $VENV_DIR ..."
    run $PY $PY_LAUNCHER -m venv "$VENV_DIR"
  fi
  if [[ -f "$PYPROJECT_FILE" ]]; then
    echo "Installing Python deps into venv (editable, pyproject.toml) ..."
    run "$VENV_PY" -m pip install -e "$TARGET_DIR"
  else
    echo "Installing Python deps into venv (requirements.txt) ..."
    run "$VENV_PY" -m pip install -r "$REQ_FILE"
  fi
  USED_VENV=1
else
  # Legacy --user path. Probe the pip module first so a stock Ubuntu
  # without `python3-pip` fails with installable guidance instead of
  # a bare "No module named pip" from set -e.
  if ! $PY $PY_LAUNCHER -m pip --version >/dev/null 2>&1; then
    cat <<'MSG' >&2
install.sh: python is on PATH but its `pip` module is missing.
On Debian / Ubuntu, install:
  sudo apt install -y python3-pip
(or python3-full to also get the venv module)
Then re-run this installer.
MSG
    exit 1
  fi
  if [[ -f "$PYPROJECT_FILE" ]]; then
    echo
    echo "Installing Python deps via pyproject.toml (editable) ..."
    run $PY $PY_LAUNCHER -m pip install --user -e "$TARGET_DIR"
  else
    # Backward-compat path for refs that predate Phase 5c (no
    # pyproject.toml) but post-date Step B (have requirements.txt).
    echo
    echo "Installing Python deps (core-harness pin, requirements.txt) ..."
    run $PY $PY_LAUNCHER -m pip install --user -r "$REQ_FILE"
  fi
fi

# --- Done ------------------------------------------------------------------

# When the PEP 668 path created a project-local venv, the user's
# interactive shell still doesn't know about it (we ran pip via the
# venv's python directly, not by sourcing activate). Tell them to
# activate before any later `python tools/...` step. Empty otherwise.
VENV_HINT=""
if [[ "$USED_VENV" == "1" ]]; then
  if [[ "$IS_WINDOWS_BASH" == "1" ]]; then
    VENV_HINT="
  source .venv/Scripts/activate   # activate Python venv (this terminal)"
  else
    VENV_HINT="
  source .venv/bin/activate       # activate Python venv (this terminal)"
  fi
fi
cat <<MSG

Done. Next steps:

  cd $TARGET_DIR$VENV_HINT
  bash scripts/install-hooks.sh   # enable pre-commit secret scanner
  renga --layout ops              # launch the Secretary pane

Inside the Secretary's Claude Code pane, run:

  /org-setup    # first time only: place per-role permissions and hooks
  /org-start    # bring dispatcher + curator online

For details see docs/getting-started.md.
MSG

# Final hint: the PATH prepend done by require_or_warn only affected
# this script's process. Use the pre-resolution snapshot taken before
# any prerequisite probe ran (RENGA_ON_USER_PATH) as the baseline —
# that's the user's interactive shell view, which `command -v renga`
# checked here would misrepresent after the prepend. This catches
# both `.cmd` shims (which MSYS bash never auto-resolves) AND `.exe`
# binaries reached via a fallback dir that isn't on the user's
# interactive bash PATH. POSIX environments skip this entirely.
if [[ "$IS_WINDOWS_BASH" == "1" && -n "${RENGA_BIN:-}" \
      && "$RENGA_ON_USER_PATH" != "1" ]]; then
  renga_dir=$(dirname -- "$RENGA_BIN")
  cat <<MSG

Note (Windows / Git Bash): bash on this shell can't find 'renga' on
PATH. The installer resolved it via fallback to:
  $RENGA_BIN
Before running 'renga --layout ops' interactively, either invoke it by
full path or add its directory to your bash PATH (e.g. in ~/.bashrc):
  export PATH="$renga_dir:\$PATH"
MSG
fi
