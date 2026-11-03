#!/usr/bin/env python3
"""Decision for the secretary's pre-merge hook (Issue #1167).

Called by ``.hooks/gate-acceptance-before-merge.sh`` with the PreToolUse
payload on stdin. Prints ``ALLOW`` or ``DENY: <reason>`` as the first line.

The only merge command allowed is the one form

    gh pr merge <number> --repo <OWNER/REPO> (--squash|--merge|--rebase) --match-head-commit <40-hex SHA> [--delete-branch]

as the whole command, so the PR and the head it merges are read exactly
(Issue #1228). ``--match-head-commit`` makes GitHub refuse the merge if the
head moved after the hook looked at it.

Any other command with the word merge on its own (``MERGE_WORD``: not inside a
longer name such as ``run_complete_on_merge.py``, ``merge_approval_mode``,
``mergedAt`` or ``before-merge.sh``, which cannot be the subcommand; the GraphQL
``mergePullRequest`` / ``...AutoMerge`` count) is denied when it also has ``gh``,
a pull request (``pulls/...``, ``mergePullRequest``), ``$`` or a backtick
(blocking too much is accepted, letting a merge past the gate is not), unless
it is one allow-listed command on its own on one line (Issue #1229): ``gh``
with a read / create subcommand (``SAFE_GH``), a reader such as ``grep`` /
``cat`` / ``head`` (``READERS``) or ``sed -n <lines>p``, with a literal
program and ``gh`` subcommand and no newline (so no heredoc), ``#``, ``;``
``&`` ``|`` ``(`` ``)``, command substitution, backtick or process
substitution. Multi-line commands are always judged by the broad rule.
So ``gh pr view --json mergedAt``,
a PR title with the word merge, and ``grep`` on the hook's own file pass,
while ``gh.exe``, ``env "$GH" pr merge``, ``gh pr ${X:-merge}`` and chained
commands stay denied. This stops mistakes, not an adversary: gh aliases, the
GitHub web UI and other sessions stay outside.

For the allowed form the hook asks gh for the PR and requires, before
running the gate:

- the PR head equals the ``--match-head-commit`` SHA, and
- the latest ``ci_completed`` event for this PR in the org's state DB is
  ``passed`` with ``head_oid`` equal to that SHA (``tools/pr_watch.py``
  records the full sha only when the head did not move during CI).

It then fetches the head and base into the project's clone under
``refs/acceptance-gate/``, and runs ``tools/acceptance_gate.py`` with
``--pr OWNER/REPO#N`` (which also pins the test commit):

- passed: allowed.
- no-manifest: allowed when the merge approval mode (``tools/merge_approval_mode.py``,
  read from the project's ``registry/org-config.md``) is ``human`` (recorded by the
  gate, not enforced); denied when it is ``acceptance`` (Issue #1236).
- rejected, or any gh / git / state DB failure or timeout: denied.

Every subprocess shares one deadline below the hook's timeout in settings
(a hook that times out does not block the tool call).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # the wrapper runs this file as a script
    sys.path.insert(0, str(_REPO_ROOT))

from tools.merge_approval_mode import read_mode  # noqa: E402

GATE = Path(__file__).resolve().parent / "acceptance_gate.py"
DEADLINE_S = 840  # the hook is registered with timeout 900
PYTEST_TIMEOUT_S = 300
GATE_EXIT = {"passed": 0, "rejected": 1, "no-manifest": 3}
MERGE = re.compile(r"gh pr merge ([0-9]+) --repo ([\w.-]+/[\w.-]+) --(?:squash|merge|rebase)"
                   r" --match-head-commit ([0-9a-f]{40})(?: --delete-branch)?", re.ASCII)
# The word on its own, as the gh subcommand, the REST path or a shell value would have it. Inside a
# longer name (`run_complete_on_merge.py`, `merge_approval_mode`, `mergedAt`, `before-merge.sh`)
# it cannot be the subcommand, so those are not judged; the GraphQL mutation names are the exception.
MERGE_WORD = re.compile(r"(?<![a-z0-9])(?<![a-z0-9][_-])merge(?![_-]?[a-z0-9])|mergepullrequest|automerge")
SUSPECT = re.compile(r"\bgh\b|gh\.exe|pull|[$`]")  # `C:\...\gh.exe` loses its backslashes
SAFE_GH = {"issue": None, "search": None,
           "pr": {"view", "list", "checks", "status", "diff", "create", "comment", "edit", "review"}}
# No `less` (`+!cmd` runs a shell) or `rg` (`--pre cmd` runs a program).
READERS = {"grep", "cat", "head", "tail", "wc", "ls", "diff", "tee"}
SED_PRINT = re.compile(r"[0-9]+(,[0-9$]+)?p|\$p")  # `sed -n 10,20p`; sed scripts can run commands (`e`)
FORM = ("merge with exactly: gh pr merge <number> --repo <OWNER/REPO> --squash|--merge|--rebase"
        " --match-head-commit <full head SHA> [--delete-branch], alone on one line,"
        " so the acceptance gate can read it. A PR this form cannot merge (e.g. a repo without CI)"
        " is merged by a human in the GitHub web UI")


class Deny(Exception):
    pass


def allow_listed(command: str) -> bool:
    """True for one allow-listed read / create command on its own (see the module docstring)."""
    first = command.strip()
    # One line only (no heredoc, no continuation) and no `#`: a comment can hide shell syntax.
    if "\n" in first or "#" in first or "$(" in first or "`" in first:
        return False
    try:
        lex = shlex.shlex(first, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        argv = list(lex)
    except ValueError:
        return False
    if not argv or any(t and set(t) <= set("();<>|&") and t not in (">", ">>", "<") for t in argv):
        return False
    prog = argv[0]
    if prog == "gh":
        rest = argv[1:]
        while rest and rest[0].startswith("-"):  # global flags; -R / --repo take a value
            rest = rest[2:] if rest[0] in ("-R", "--repo") else rest[1:]
        group, sub = (rest + ["", ""])[:2]
        allowed = SAFE_GH.get(group, ())
        return allowed is None or sub in allowed
    if prog == "sed":
        # Exactly `sed -n <lines>p <files>`: no other option, so no second -e / -f script.
        args = argv[1:]
        return len(args) >= 2 and args[0] == "-n" and SED_PRINT.fullmatch(args[1]) is not None \
            and not any(a.startswith("-") for a in args[2:])
    return prog in READERS


def parse(command: str) -> tuple[str, str, str] | None:
    """(number, OWNER/REPO, head SHA) for the allowed merge form; None for a command that cannot merge."""
    m = MERGE.fullmatch(command.strip())
    if m:
        return m.group(1), m.group(2), m.group(3)
    # Continuations join words (`mer\<newline>ge`); other newlines stay word breaks so `EOF\ngh` shows `gh`.
    plain = re.sub(r"[\"'\\]", "", command.replace("\\\n", "").replace("\n", " ")).lower()
    if MERGE_WORD.search(plain) and SUSPECT.search(plain) and not allow_listed(command):
        raise Deny(FORM)
    return None


def ci_passed_head(repo: str, number: int) -> str | None:
    """Full head sha of the latest ci_completed event for the PR if it passed, else None."""
    from tools.state_db import connect
    from tools.state_db.discover import resolve_state_db_path

    db = Path(resolve_state_db_path())
    if not db.exists():
        return None
    conn = connect(db)
    try:
        rows = conn.execute("SELECT payload_json FROM events WHERE kind='ci_completed'"
                            " ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    for (raw,) in rows:
        p = json.loads(raw)
        if p.get("pr") == number and str(p.get("repo", "")).lower() == repo.lower():
            return p.get("head_oid") if p.get("status") == "passed" else None
    return None


def run(cmd: list[str], cwd: Path, deadline: float, limit: float) -> subprocess.CompletedProcess:
    """Run with a timeout; on expiry kill the whole process group (pytest children too)."""
    timeout = min(limit, deadline - time.monotonic())
    if timeout <= 0:
        raise Deny("the acceptance gate ran out of time")
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # ponytail: Windows kills only the child; a job object would take the tree
            proc.kill()
        proc.communicate()
        raise Deny(f"{cmd[0]} {cmd[1]} timed out after {int(timeout)}s")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def decide(command: str, project: Path) -> str:
    args = parse(command)
    if args is None:
        return "ALLOW"
    number, repo, want = args
    deadline = time.monotonic() + DEADLINE_S
    view = ["gh", "pr", "view", number, "--repo", repo, "--json", "number,url,baseRefName,headRefOid"]
    res = run(view, project, deadline, 60)
    if res.returncode != 0:
        raise Deny(f"gh pr view failed: {res.stderr.strip()[-300:]}")
    pr = json.loads(res.stdout)
    if pr["headRefOid"] != want:
        raise Deny(f"the PR head is {pr['headRefOid'][:12]}, not {want[:12]}; wait for CI on the current head")
    ci_head = ci_passed_head(repo, int(pr["number"]))
    if ci_head != want:
        raise Deny(f"no passed ci_completed for {repo}#{pr['number']} at {want[:12]}"
                   f" (latest: {(ci_head or 'not passed / no full head')[:12]}); watch CI with /pr-watch-pane first")
    prefix = pr["url"].rsplit("/pull/", 1)[0]
    slug = "/".join(prefix.split("/")[-2:])
    remote = prefix + ".git" if prefix.startswith("https://") else prefix
    ref = f"refs/acceptance-gate/{slug}/{pr['number']}"
    res = run(["git", "fetch", "--no-tags", "--quiet", remote,
               f"+refs/pull/{pr['number']}/head:{ref}/head",
               f"+refs/heads/{pr['baseRefName']}:{ref}/base"], project, deadline, 120)
    if res.returncode != 0:
        raise Deny(f"git fetch failed: {res.stderr.strip()[-300:]}")
    head = run(["git", "rev-parse", f"{ref}/head"], project, deadline, 30).stdout.strip()
    if head != want:
        raise Deny(f"the PR head moved while fetching ({want[:12]} -> {head[:12]}); retry")
    res = run([sys.executable, str(GATE), "--repo", str(project), "--base", f"{ref}/base",
               "--head", f"{ref}/head", "--pr", f"{slug}#{pr['number']}",
               "--timeout", str(PYTEST_TIMEOUT_S)], project, deadline, DEADLINE_S)
    lines = res.stdout.strip().splitlines()
    verdict = lines[-1].removeprefix("ACCEPTANCE_GATE: ") if lines else ""
    if GATE_EXIT.get(verdict) != res.returncode:
        raise Deny(f"acceptance gate gave no verdict (rc={res.returncode}): "
                   f"{(res.stdout + res.stderr).strip()[-300:]}")
    reason = " ".join(lines[:-1])
    if verdict == "rejected":
        raise Deny(f"ACCEPTANCE_GATE rejected {slug}#{pr['number']}: {reason}")
    if verdict == "no-manifest" and read_mode(project)[0] == "acceptance":
        raise Deny(f"ACCEPTANCE_GATE no-manifest for {slug}#{pr['number']} under merge_approval_mode"
                   " acceptance: a PR without a declared acceptance test needs a human. Have a human"
                   " decide and merge it in the GitHub web UI, or set merge_approval_mode to human"
                   " in registry/org-config.md")
    return "ALLOW"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError("command is not a string")
        project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        print(decide(command, project))
    except Deny as exc:
        print(f"DENY: {exc}")
    except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
        print(f"DENY: acceptance gate hook failed ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
