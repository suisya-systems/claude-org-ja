#!/usr/bin/env python3
"""Keep a reported test commit on the worker's branch (Issue 1298).

Usage::

    python3 tools/test_commit_guard.py [--root R] [--db-path D] hook < payload.json   # ALLOW / DENY: <msg>

The worker hook ``.hooks/block-test-commit-rewrite.sh`` runs ``hook`` for
every Bash command (outside the worker: the decision reads the ja-root
state.db and git, which the worker cannot rewrite).

The guarded commit M is the first of the branch's own commits (first-parent,
not on the task's base: ``base_ref`` of the run's ``delegate_sent``, default
``origin/main``) that has an ``Acceptance-Test`` trailer. M is guarded once any
of these holds, all read from state: it is reachable from ``origin/<branch>``
(the secretary pushed it alone for the CI failure record), a
``presubmit_checked`` event of the task names it as ``test_commit``, or HEAD is
not M (the worker's own commits sit on top: the implementation has started).
A new SHA for M throws away the CI failure the acceptance gate needs, so while
M is guarded these are refused:

* ``rebase`` onto an upstream (and ``--onto``) that does not contain M, and
  ``pull --rebase``; ``--abort`` / ``--quit`` / ``--continue`` / ``--skip``
  pass;
* ``commit --amend`` while HEAD is M;
* ``reset`` to a commit that does not contain M (a path reset passes);
* moving the current branch to a commit without M: ``branch -f`` / ``-C`` /
  ``-M``, ``checkout -B``, ``switch -C``, ``update-ref``;
* ``filter-branch`` / ``filter-repo`` / ``replace``;
* any of these whose target is a variable or a substitution.

Catching up with the base is ``git merge <base>``, which keeps M (the gate
accepts a base merged after M). Everything else passes, and nothing is
refused while M is HEAD and neither pushed nor recorded (the test may still be
rewritten), or in a dir that is no task's worker dir. A mistake in the test
found after the implementation is on top is fixed on a new branch (cut from
the base, corrected test first, implementation cherry-picked); creating a
branch and cherry-picking are not refused.
Commands are read with the parser of ``tools/presubmit.py`` (shlex, nested
``bash -c`` / ``$(...)``, ``VAR=value`` / ``env`` / ``command`` prefixes,
``-C``, ``cd``); what the text cannot show (aliases, scripts) is not seen.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.presubmit import _real, _simple_commands, _unwrap  # noqa: E402
from tools.state_db import connect  # noqa: E402

MERGE_HINT = ("catch up with the base by `git merge <base>` instead (it keeps the test commit);"
              " ask the secretary if the test commit really has to change")


def _git(cwd: str, *args: str) -> Optional[str]:
    proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _contains(cwd: str, rev: str, m: str) -> Optional[bool]:
    """True if ``rev`` contains ``m``; None if ``rev`` does not resolve."""
    if "$" in rev or "`" in rev:
        return None
    sha = _git(cwd, "rev-parse", "--verify", "-q", rev + "^{commit}")
    if not sha:
        return None
    return subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", m, sha],
                          capture_output=True).returncode == 0


class Guard:
    """The task and test commit of one repository dir, read once per dir."""

    def __init__(self, conn, repo: str):
        self.repo = repo
        self.m: Optional[str] = None
        self.task: Optional[str] = None
        top = _git(repo, "rev-parse", "--show-toplevel")
        if not top:
            return
        top = _real(top)
        base_ref = None
        for (raw,) in conn.execute("SELECT payload_json FROM events WHERE kind = 'delegate_sent' ORDER BY id"):
            try:
                p = json.loads(raw or "{}")
            except ValueError:
                continue
            if p.get("dir") and _real(p["dir"]) == top:
                self.task, base_ref = p.get("task"), p.get("base_ref") or "origin/main"
        if not self.task:
            return
        base = _git(top, "rev-parse", "--verify", "-q", base_ref + "^{commit}")
        if not base and base_ref.startswith("origin/"):
            base = _git(top, "rev-parse", "--verify", "-q", base_ref[len("origin/"):] + "^{commit}")
        if not base:
            return
        for c in (_git(top, "rev-list", "--reverse", "--first-parent", "HEAD", "--not", base) or "").split():
            if _git(top, "log", "-1", "--format=%(trailers:key=Acceptance-Test,valueonly)", c):
                self.m = c
                break
        if not self.m:
            return
        branch = _git(top, "symbolic-ref", "-q", "--short", "HEAD")
        pushed = bool(branch) and _contains(top, f"refs/remotes/origin/{branch}", self.m) is True
        recorded = False
        for (raw,) in conn.execute("SELECT payload_json FROM events WHERE kind = 'presubmit_checked'"):
            try:
                p = json.loads(raw or "{}")
            except ValueError:
                continue
            if p.get("task") == self.task and p.get("test_commit") == self.m:
                recorded = True
        # M was found among HEAD's own commits, so HEAD != M means work sits on top of it.
        on_top = _git(top, "rev-parse", "HEAD") != self.m
        if not (pushed or recorded or on_top):
            self.m = None  # M is still HEAD and not reported: the test may be rewritten
        self.branch = branch

    def keeps(self, rev: str) -> Optional[bool]:
        return _contains(self.repo, rev, self.m)


def _refusal(g: Guard, what: str) -> str:
    return (f"DENY: {what} would drop the reported test commit {g.m[:12]} of task {g.task} from"
            f" the branch (its CI failure record would no longer count); {MERGE_HINT}")


def _check_git(g: Guard, args: list[str]) -> Optional[str]:
    sub, rest = args[0], args[1:]
    opts = [w for w in rest if w.startswith("-")]
    plain = [w for w in rest if not w.startswith("-")]
    if sub in ("filter-branch", "filter-repo", "replace"):
        return _refusal(g, f"git {sub}")
    if sub == "rebase":
        if any(o in ("--abort", "--quit", "--continue", "--skip", "--edit-todo", "--show-current-patch")
               for o in opts):
            return None
        targets = []
        for j, w in enumerate(rest):
            if w == "--onto" and j + 1 < len(rest):
                targets.append(rest[j + 1])
            elif w.startswith("--onto="):
                targets.append(w.split("=", 1)[1])
        onto_values = set(targets)
        upstream = next((w for w in plain if w not in onto_values), None)
        targets.append(upstream or "@{upstream}")
        if any(g.keeps(t) is not True for t in targets):
            return _refusal(g, "this git rebase")
        return None
    if sub == "pull":
        if any(o in ("--rebase", "-r") or o.startswith("--rebase=") and not o.endswith("=false")
               for o in opts):
            return _refusal(g, "git pull --rebase")
        return None
    if sub == "commit":
        if "--amend" in opts and _git(g.repo, "rev-parse", "HEAD") == g.m:
            return _refusal(g, "git commit --amend at the test commit")
        return None
    if sub == "reset":
        if "--" in rest:
            return None
        target = next((w for w in plain), None)
        if target is None:
            return None  # resets to HEAD
        kept = g.keeps(target)
        if kept is None and "$" not in target and "`" not in target:
            return None  # not a commit: a path reset, HEAD does not move
        if kept is not True:
            return _refusal(g, f"git reset to {target}")
        return None
    moves = ((sub == "branch" and any(o in ("-f", "--force", "-C", "-M") for o in opts))
             or (sub == "checkout" and "-B" in opts) or (sub == "switch" and any(o in ("-C", "--force-create") for o in opts)))
    if moves or sub == "update-ref":
        if sub == "update-ref":
            if "-d" in opts or len(plain) < 2:
                return None
            name, target = plain[0], plain[1]
        else:
            name = plain[0] if plain else None
            target = plain[1] if len(plain) > 1 else "HEAD"
        if name is None:
            return None
        current = g.branch or ""
        if name not in (current, f"refs/heads/{current}") and "$" not in name:
            return None
        if g.keeps(target) is not True:
            return _refusal(g, f"moving branch {current} to {target}")
    return None


def decide(conn, command: str, cwd: str) -> str:
    guards: dict[str, Guard] = {}
    here: Optional[str] = cwd
    for words in _simple_commands(command):
        words = _unwrap(words)
        if not words:
            continue
        name = Path(words[0]).name
        if name in ("cd", "pushd"):
            arg = next((w for w in words[1:] if not w.startswith("-")), None)
            if "(" in command or ")" in command or (arg and "$" in arg):
                here = None
            elif arg is None:
                here = os.path.expanduser("~")
            elif here is not None or os.path.isabs(os.path.expanduser(arg)):
                here = os.path.join(here or "/", os.path.expanduser(arg))
            continue
        if name != "git":
            continue
        i, targets = 1, []
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                targets.append(words[i + 1])
                i += 2
            elif words[i].startswith("-C"):
                targets.append(words[i][2:])
                i += 1
            elif words[i] in ("-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                i += 2
            else:
                i += 1
        if i >= len(words):
            continue
        sub = words[i]
        if sub not in ("rebase", "pull", "commit", "reset", "branch", "checkout", "switch",
                       "update-ref", "filter-branch", "filter-repo", "replace"):
            continue
        if here is None or any("$" in t for t in targets):
            # Cannot tell which repository: guard every worker dir the hook knows of.
            base = os.environ.get("WORKER_DIR") or cwd
            repo = os.path.join(base, *targets) if targets and not any("$" in t for t in targets) else base
        else:
            repo = os.path.join(here, *targets) if targets else here
        repo = _real(repo)
        g = guards.get(repo) or guards.setdefault(repo, Guard(conn, repo))
        if not g.m:
            continue
        verdict = _check_git(g, words[i:])
        if verdict:
            return verdict
    return "ALLOW"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/test_commit_guard.py",
                                 description="Refuse git commands that drop a reported test commit.")
    ap.add_argument("--root", type=Path, default=_REPO_ROOT, help="org root (default: this checkout)")
    ap.add_argument("--db-path", type=Path, help="state.db (default: <root>/.state/state.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook", help="PreToolUse decision (payload on stdin)")
    args = ap.parse_args(argv)
    # STATE_DB_PATH is ignored on purpose: the hook inherits the worker's environment.
    db_path = args.db_path or (args.root / ".state" / "state.db")
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            print("ALLOW")
            return 0
        command = str((payload.get("tool_input") or {}).get("command") or "")
        if "git" not in command or not db_path.is_file():
            print("ALLOW")
            return 0
        conn = connect(db_path)
        try:
            print(decide(conn, command, payload.get("cwd") or os.getcwd()))
        finally:
            conn.close()
    except Exception as exc:  # fail closed: the shell wrapper turns this into a deny
        print(f"DENY: test commit guard failed: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
