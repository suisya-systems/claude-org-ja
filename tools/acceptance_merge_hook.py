#!/usr/bin/env python3
"""Decision for the secretary's pre-merge hook (Issue #1167).

Called by ``.hooks/gate-acceptance-before-merge.sh`` with the PreToolUse
payload on stdin. Prints ``ALLOW`` or ``DENY: <reason>`` as the first line.

A Bash command that merges a PR must be a lone ``gh pr merge [<pr>] [flags]``
(no chaining, pipes, substitutions or variables), so the PR it merges can be
read. Merging through ``gh api .../pulls/<n>/merge`` is denied. For a lone
merge the hook asks gh for the PR, fetches its head and base into the
project's clone under ``refs/acceptance-gate/``, and runs
``tools/acceptance_gate.py`` with ``--pr OWNER/REPO#N`` (which also pins the
test commit):

- passed: allowed, except a deferred merge (``--auto``): GitHub merges later
  whatever head is then, without this hook.
- no-manifest: allowed (pilot policy: recorded by the gate, not enforced).
- rejected, or any gh / git failure or timeout: denied.

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

GATE = Path(__file__).resolve().parent / "acceptance_gate.py"
DEADLINE_S = 840  # the hook is registered with timeout 900
PYTEST_TIMEOUT_S = 300
VALUE_FLAGS = {"-b", "--body", "-F", "--body-file", "-A", "--author-email",
               "--match-head-commit", "-t", "--subject", "-R", "--repo"}
GATE_EXIT = {"passed": 0, "rejected": 1, "no-manifest": 3}
STANDALONE = ("run the merge as a lone command: gh pr merge [<number>] [flags]"
              " (no &&, ;, |, $(...), variables or bash -c), so the acceptance gate can read it")


class Deny(Exception):
    pass


def parse(command: str) -> dict | None:
    """None when the command does not merge a PR; the merge's arguments otherwise."""
    if re.search(r"pulls/[^/\s]+/merge\b", command):
        raise Deny("merging through the API skips the acceptance gate; use gh pr merge")
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        raise Deny("cannot parse the command; " + STANDALONE)
    hits = [i for i in range(len(toks) - 1) if toks[i:i + 2] == ["pr", "merge"]]
    nested = any(re.search(r"\bgh\b[\s\S]*\bpr\s+merge\b", t) for t in toks if " " in t or "\n" in t)
    if not hits and not nested:
        return None
    if (nested or hits != [1] or toks[0] != "gh" or "$" in command or "`" in command
            or any(set(t) <= set("();<>|&") for t in toks)):
        raise Deny(STANDALONE)
    args = {"sel": None, "repo": None, "auto": False, "disable_auto": False}
    it = iter(toks[3:])
    for t in it:
        name, eq, val = t.partition("=") if t.startswith("--") else (t, "", "")
        if not t.startswith("--") and len(t) > 2 and t[:2] in VALUE_FLAGS:
            name, eq, val = t[:2], "=", t[2:]
        if name in VALUE_FLAGS:
            val = val if eq else next(it, None)
            if val is None:
                raise Deny(f"{name} needs a value")
            if name in ("-R", "--repo"):
                args["repo"] = val
        elif name == "--auto":
            args["auto"] = True
        elif name == "--disable-auto":
            args["disable_auto"] = True
        elif not t.startswith("-"):
            if args["sel"] is not None:
                raise Deny(f"more than one PR given ({args['sel']!r}, {t!r})")
            args["sel"] = t
    return args


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
    if args is None or args["disable_auto"]:
        return "ALLOW"
    deadline = time.monotonic() + DEADLINE_S
    view = ["gh", "pr", "view", *([args["sel"]] if args["sel"] else []),
            *(["--repo", args["repo"]] if args["repo"] else []),
            "--json", "number,url,baseRefName,headRefOid"]
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
    if verdict == "passed" and args["auto"]:
        raise Deny("a deferred merge (--auto) of a PR with an acceptance test is merged later"
                   " without this gate; merge it now without --auto")
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
