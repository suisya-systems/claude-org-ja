#!/usr/bin/env python3
"""Decision for the secretary's pre-merge hook (Issue #1167).

Called by ``.hooks/gate-acceptance-before-merge.sh`` with the PreToolUse
payload on stdin. Prints ``ALLOW`` or ``DENY: <reason>`` as the first line.

The only merge command allowed is the one form

    gh pr merge <number> --repo <OWNER/REPO> (--squash|--merge|--rebase) [--delete-branch]

as the whole command, so the PR it merges is read exactly. Any other command
that mentions a merge together with ``gh``, a pull request (``pulls/...``,
``mergePullRequest``), ``$`` or a backtick is denied: blocking too much is
accepted, letting a merge past the gate is not. This stops mistakes, not an
adversary: gh aliases, the GitHub web UI and other sessions stay outside.

For the allowed form the hook asks gh for the PR, fetches its head and base
into the project's clone under ``refs/acceptance-gate/``, and runs
``tools/acceptance_gate.py`` with ``--pr OWNER/REPO#N`` (which also pins the
test commit):

- passed: allowed.
- no-manifest: allowed (pilot policy: recorded by the gate, not enforced).
- rejected, or any gh / git failure or timeout: denied.

Every subprocess shares one deadline below the hook's timeout in settings
(a hook that times out does not block the tool call).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

GATE = Path(__file__).resolve().parent / "acceptance_gate.py"
DEADLINE_S = 840  # the hook is registered with timeout 900
PYTEST_TIMEOUT_S = 300
GATE_EXIT = {"passed": 0, "rejected": 1, "no-manifest": 3}
MERGE = re.compile(r"gh pr merge ([0-9]+) --repo ([\w.-]+/[\w.-]+) --(?:squash|merge|rebase)(?: --delete-branch)?", re.ASCII)
SUSPECT = re.compile(r"\bgh\b|pull|[$`]")
FORM = ("merge with exactly: gh pr merge <number> --repo <OWNER/REPO> --squash|--merge|--rebase"
        " [--delete-branch], alone on one line, so the acceptance gate can read it")


class Deny(Exception):
    pass


def parse(command: str) -> tuple[str, str] | None:
    """(number, OWNER/REPO) for the allowed merge form; None for a command that is not a merge."""
    m = MERGE.fullmatch(command.strip())
    if m:
        return m.group(1), m.group(2)
    plain = re.sub(r"[\"'\\\n]", "", command).lower()  # what the wrapper's fast path looks at
    if "merge" in plain and SUSPECT.search(plain):
        raise Deny(FORM)
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
    deadline = time.monotonic() + DEADLINE_S
    view = ["gh", "pr", "view", args[0], "--repo", args[1], "--json", "number,url,baseRefName,headRefOid"]
    res = run(view, project, deadline, 60)
    if res.returncode != 0:
        raise Deny(f"gh pr view failed: {res.stderr.strip()[-300:]}")
    pr = json.loads(res.stdout)
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
    if head != pr["headRefOid"]:
        raise Deny(f"the PR head moved while fetching ({pr['headRefOid'][:12]} -> {head[:12]}); retry")
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
