#!/usr/bin/env python3
"""Decision for the secretary's pre-merge hook (Issue #1167).

Called by ``.hooks/gate-acceptance-before-merge.sh`` with the PreToolUse
payload on stdin. Prints ``ALLOW`` or ``DENY: <reason>`` as the first line.

The only merge command allowed is the one form

    gh pr merge <number> --repo <OWNER/REPO> (--squash|--merge|--rebase) --match-head-commit <40-hex SHA> [--delete-branch]

as the whole command, so the PR and the head it merges are read exactly
(Issue #1228). ``--match-head-commit`` makes GitHub refuse the merge if the
head moved after the hook looked at it.

Other commands are denied only when they could merge (Issue #1229), judged on
the command with quotes and backslashes removed and lowercased (so
``m'erge'`` and ``bash -c "..."`` stay visible) and newlines read as command
separators. ``mergePullRequest``, ``enablePullRequestAutoMerge`` and
``pulls/<n>/merge`` are denied anywhere. Otherwise a command containing
``merge`` (anywhere, so ``${X:-merge}`` counts too) is denied only if it runs
``gh`` with anything but a read / create subcommand (``SAFE_GH``; a ``$`` or
backtick in the subcommand is not safe), has ``merge`` right after ``pr`` or
after / inside a ``$`` / backtick word (``env "$GH" pr merge``,
``${X:-merge}``), or uses ``eval`` or ``$`` / a backtick as the command name. So reads (``gh pr view --json mergedAt``),
PR titles, file names and heredoc text pass,
while ``gh pr merge`` in any other form, ``gh pr $x``, ``gh api`` and
``$g pr merge`` stay denied. This stops mistakes, not an adversary: gh
aliases, the GitHub web UI and other sessions stay outside.

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
- no-manifest: allowed (pilot policy: recorded by the gate, not enforced).
- rejected, or any gh / git / state DB failure or timeout: denied.

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

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # the wrapper runs this file as a script
    sys.path.insert(0, str(_REPO_ROOT))

GATE = Path(__file__).resolve().parent / "acceptance_gate.py"
DEADLINE_S = 840  # the hook is registered with timeout 900
PYTEST_TIMEOUT_S = 300
GATE_EXIT = {"passed": 0, "rejected": 1, "no-manifest": 3}
MERGE = re.compile(r"gh pr merge ([0-9]+) --repo ([\w.-]+/[\w.-]+) --(?:squash|merge|rebase)"
                   r" --match-head-commit ([0-9a-f]{40})(?: --delete-branch)?", re.ASCII)
STRONG = re.compile(r"mergepullrequest|enablepullrequestautomerge|pulls/[^\s/]+/merge(?![\w.-])", re.ASCII)
# `$` or a backtick where a command name goes (`$g pr merge`, `$(echo gh) pr merge`), or eval.
# A bare `(` is left out: it is mostly prose in heredocs, and `( $g pr merge )` is a residual.
DYNAMIC_COMMAND = re.compile(r"(?:^|[;&|]|\$\(|`)\s*[$`]|\beval\b")
# `merge` as a value inside an expansion (`${X:-merge}`), not part of a path like `$D/before-merge.sh`.
MERGE_IN_EXPANSION = re.compile(r"(?<![\w.])merge(?![\w.-])", re.ASCII)
TOKEN_SPLIT =re.compile(r"[\s,;&|()\[\]{}<>=]+")
SAFE_GH = {"issue": None, "search": None,
           "pr": {"view", "list", "checks", "status", "diff", "create", "comment", "edit", "review"}}
FORM = ("merge with exactly: gh pr merge <number> --repo <OWNER/REPO> --squash|--merge|--rebase"
        " --match-head-commit <full head SHA> [--delete-branch], alone on one line,"
        " so the acceptance gate can read it")


class Deny(Exception):
    pass


def unsafe_gh(tokens: list[str]) -> bool:
    """True if any `gh` in the tokens runs something other than a SAFE_GH subcommand."""
    for i, tok in enumerate(tokens):
        # `gh`, `/usr/bin/gh`, `gh.exe`; a Windows `C:\...\gh.exe` lost its backslashes above.
        if re.split(r"[/:]", tok)[-1] not in ("gh", "gh.exe") and not tok.endswith("gh.exe"):
            continue
        rest = tokens[i + 1:]
        while rest and rest[0].startswith("-"):  # global flags; -R / --repo take a value
            rest = rest[2:] if rest[0] in ("-r", "--repo") else rest[1:]
        group, sub = (rest + ["", ""])[:2]
        allowed = SAFE_GH.get(group, ())
        if not (allowed is None or sub in allowed):
            return True
    return False


def merge_verb(tokens: list[str]) -> bool:
    """True if a token reads as the merge subcommand whatever runs it (`env $GH pr merge`,
    `command $GH pr merge`, `$GH $P merge`, `gh pr ${X:-merge}`)."""
    for prev, tok in zip([""] + tokens, tokens):
        dynamic = "$" in tok or "`" in tok
        if dynamic and MERGE_IN_EXPANSION.search(tok):
            return True
        if tok == "merge" and (prev == "pr" or "$" in prev or "`" in prev):
            return True
    return False


def parse(command: str) -> tuple[str, str, str] | None:
    """(number, OWNER/REPO, head SHA) for the allowed merge form; None for a command that cannot merge."""
    m = MERGE.fullmatch(command.strip())
    if m:
        return m.group(1), m.group(2), m.group(3)
    # Line continuations vanish (bash joins `mer\<newline>ge` into one word);
    # other newlines separate commands. Quotes and backslashes go so
    # `m'erge'` and `bash -c "gh pr merge"` read as written.
    plain = re.sub(r"[\"'\\]", "", command.replace("\\\n", "").replace("\n", ";")).lower()
    if STRONG.search(plain):
        raise Deny(FORM)
    if "merge" in plain:
        tokens = TOKEN_SPLIT.split(plain)
        if unsafe_gh(tokens) or merge_verb(tokens) or DYNAMIC_COMMAND.search(plain):
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
