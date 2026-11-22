#!/usr/bin/env python3
"""Pre-submit check from settings, and the push / PR steps it gates (Issue 1298).

Usage::

    python3 tools/presubmit.py run  --task T
    python3 tools/presubmit.py push --task T [--test-commit] [--force]
    python3 tools/presubmit.py pr   --task T (--create [--base B] | --edit N) --title X --body-file F [--closes N ...]
    python3 tools/presubmit.py hook < payload.json      # PreToolUse; prints ALLOW / DENY: <msg>

Global options ``--root`` (the org root holding ``.state/`` and ``registry/``;
default: this checkout) and ``--db-path`` (default: discovery).

``run`` -- the secretary runs it on a worker's completion report. The steps
come from settings: the ``presubmit`` column of ``registry/projects.md`` for
the run's project, or ``home_presubmit:`` in ``registry/org-config.md`` when
the worker dir is a worktree of the org repository itself. Steps are
separated by ``;`` (a table cell cannot hold ``|``) and each runs with
``bash -c`` in the worker dir, all of them even after a failure. ``{base}``
is the run's base (``base_ref`` of its ``delegate_sent``, default
``origin/main``) and ``{head}`` the worker dir's HEAD, both as SHAs. The output
goes to ``.state/presubmit/<task>-<head12>.log`` and one ``presubmit_checked``
event records ``task, head, base, test_commit, steps [{cmd, rc, seconds}],
result, log``. ``test_commit`` is the branch's commit with an
``Acceptance-Test`` trailer, if any. The record names a commit, so ``run``
refuses (records nothing) when tracked files have uncommitted changes, or when
HEAD or the tracked files changed while it ran. Last line: ``PRESUBMIT:
passed|failed|not-configured``; exit 0 / 1 / 3.

``push`` -- pushes the worker dir's HEAD (or, with ``--test-commit``, the
recorded test commit alone) to ``origin`` as ``refs/heads/<runs.branch>``, only
when the latest ``presubmit_checked`` for the task is at that HEAD and
``passed`` (or ``not_configured``: no check is set for the project; the push
line then carries a ``WARNING``, and ``presubmit_required: yes`` in
``registry/org-config.md`` refuses it instead). ``--force`` replaces the branch
(after a rebase, or to put the test commit back as head) with
``--force-with-lease`` on the commit the remote has right now. Last
line ``PRESUBMIT_PUSH: pushed <sha> to <branch>`` or ``PRESUBMIT_PUSH:
refused: <reason>``; exit 0 / 1.

``pr`` -- creates (``gh pr create --repo R --head <branch>``) or edits
(``gh pr edit N --repo R``) the PR with ``--title`` and ``--body-file``, after
reading every GitHub closing reference in the title, the body and the commit
messages of every commit HEAD has and the base has not (also against
``--base`` when given, so a PR to another branch is covered): a closing word (close, closes, closed, fix, fixes,
fixed, resolve, resolves, resolved; any case) followed, after optional ``:``
and spaces, by ``#N``, ``owner/repo#N`` or an Issue URL. Any reference that is
not ``#N`` / this repository's N with N given by ``--closes`` stops the call
before gh runs, with each line shown. Negation is not read, because GitHub
does not read it. Last line ``PRESUBMIT_PR: created <url>`` /
``PRESUBMIT_PR: edited #N`` / ``PRESUBMIT_PR: refused: <reason>``; exit 0 / 1.

``hook`` -- the decision of ``.hooks/gate-presubmit-before-push.sh``. For an
open run (``queued`` / ``in_use`` / ``review``) it refuses a raw ``git push``
whose target is the run's worker dir (cwd or ``-C``) or that names the run's
branch, a ``gh pr create`` whose ``--head`` is the run's branch or whose cwd is
the worker dir, and a ``gh pr edit N`` of the run's PR, so those go through
``push`` and ``pr``. A ``git push`` / ``gh pr create|edit`` whose target cannot
be read (a variable, a command substitution) is refused. Everything else is
allowed. The words are read with shlex, also inside ``bash -c`` / ``eval``
strings and ``$(...)`` bodies, after ``VAR=value`` assignments and wrappers
(``env``, ``command``, ``exec``, ``nohup``, ``time``, ``sudo``, ``nice``,
``builtin``) in front are dropped; ``cd`` / ``pushd`` move the directory the later
commands are resolved against; a PR given by number is matched with its
repository when ``--repo`` or a PR URL names one; what the text cannot show (aliases, a script
file that pushes) is not seen.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.registry_parser import parse_projects  # noqa: E402
from tools.resolve_run_repo import RepoResolutionError, resolve_repo_for_task  # noqa: E402
from tools.state_db import connect  # noqa: E402
from tools.state_db.discover import resolve_state_db_path  # noqa: E402
from tools.state_db.writer import StateWriter  # noqa: E402

OPEN = ("queued", "in_use", "review")
_CLOSING = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[ \t]*:?[ \t]*"
    r"(?:(?P<slug>[\w.-]+/[\w.-]+)?#(?P<n>\d+)"
    r"|https?://github\.com/(?P<uslug>[\w.-]+/[\w.-]+)/issues/(?P<un>\d+))",
    re.IGNORECASE,
)


class Refused(Exception):
    pass


# -- shared ------------------------------------------------------------------

def _git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", "-C", cwd, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _run_row(conn, task: str) -> dict:
    row = conn.execute(
        "SELECT r.status, r.branch, r.pr_url, w.abs_path, p.slug FROM runs r "
        "LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id "
        "LEFT JOIN projects p ON p.id = r.project_id WHERE r.task_id = ?", (task,)).fetchone()
    if row is None:
        raise Refused(f"no run {task!r}")
    keys = ("status", "branch", "pr_url", "wdir", "slug")
    run = dict(zip(keys, (row[i] for i in range(5))))
    if not run["wdir"] or not Path(run["wdir"]).is_dir():
        raise Refused(f"the run's worker dir is missing ({run['wdir']})")
    if not run["branch"]:
        raise Refused("the run has no branch recorded")
    return run


def _base_ref(conn, task: str) -> str:
    base = "origin/main"
    for (raw,) in conn.execute("SELECT payload_json FROM events WHERE kind = 'delegate_sent' ORDER BY id"):
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            continue
        if p.get("task") == task and p.get("base_ref"):
            base = p["base_ref"]
    return base


def _resolve_base(wdir: str, base_ref: str) -> str:
    """SHA of the base; ``origin/<b>`` falls back to ``<b>`` when the remote ref is absent."""
    try:
        return _git(wdir, "rev-parse", "--verify", base_ref + "^{commit}")
    except subprocess.CalledProcessError:
        if not base_ref.startswith("origin/"):
            raise
        return _git(wdir, "rev-parse", "--verify", base_ref[len("origin/"):] + "^{commit}")


def _dirty(wdir: str) -> bool:
    return bool(_git(wdir, "status", "--porcelain", "--untracked-files=no"))


def _required(root: Path) -> bool:
    """``presubmit_required: yes`` in registry/org-config.md refuses pushes with no check set."""
    config = root / "registry" / "org-config.md"
    if not config.is_file():
        return False
    m = re.search(r"^presubmit_required:[ \t]*(\S+)", config.read_text(encoding="utf-8"), re.M)
    return bool(m) and m.group(1).lower() in ("yes", "true", "on")


def _own_commits(wdir: str, base: str) -> list[str]:
    out = _git(wdir, "rev-list", "--reverse", "--first-parent", "HEAD", "--not", base)
    return [c for c in out.split() if c]


# -- run ---------------------------------------------------------------------

def _steps(root: Path, slug: Optional[str], wdir: str) -> list[str]:
    cell = ""
    registry = root / "registry" / "projects.md"
    if slug and registry.is_file():
        for project in parse_projects(registry):
            if project.name == slug:
                cell = project.presubmit
    if not cell:
        try:
            same_repo = (Path(_git(wdir, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                         == Path(_git(str(root), "rev-parse", "--path-format=absolute",
                                      "--git-common-dir")))
        except subprocess.CalledProcessError:
            same_repo = False
        config = root / "registry" / "org-config.md"
        if same_repo and config.is_file():
            m = re.search(r"^home_presubmit:[ \t]*(.+?)[ \t]*$",
                          config.read_text(encoding="utf-8"), re.M)
            cell = m.group(1) if m else ""
    cell = cell.strip().strip("`")
    return [s.strip() for s in cell.split(";") if s.strip() and s.strip() != "-"]


def cmd_run(root: Path, db_path: Path, task: str) -> int:
    conn = connect(db_path)
    try:
        run = _run_row(conn, task)
        wdir = run["wdir"]
        base_ref = _base_ref(conn, task)
        head = _git(wdir, "rev-parse", "HEAD")
        if _dirty(wdir):
            raise Refused("the worker dir has uncommitted changes to tracked files; the check"
                          " records a commit, so commit or drop them first")
        base = _resolve_base(wdir, base_ref)
        test_commit = None
        for c in _own_commits(wdir, base):
            if _git(wdir, "log", "-1", "--format=%(trailers:key=Acceptance-Test,valueonly)", c):
                test_commit = c
                break
        steps = _steps(root, run["slug"], wdir)
        log = root / ".state" / "presubmit" / f"{task}-{head[:12]}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        done = []
        with log.open("w", encoding="utf-8") as fh:
            fh.write(f"# presubmit {task} head={head} base={base} ({base_ref})\n")
            for step in steps:
                cmd = step.replace("{base}", base).replace("{head}", head)
                fh.write(f"\n$ {cmd}\n")
                fh.flush()
                start = time.monotonic()
                rc = subprocess.run(["bash", "-c", cmd], cwd=wdir, stdout=fh,
                                    stderr=subprocess.STDOUT).returncode
                done.append({"cmd": cmd, "rc": rc, "seconds": round(time.monotonic() - start, 1)})
                fh.write(f"# rc={rc}\n")
                print(f"presubmit: rc={rc} {cmd}", flush=True)
        # The record names a commit, so what ran must be exactly that commit.
        if _git(wdir, "rev-parse", "HEAD") != head or _dirty(wdir):
            raise Refused("HEAD or the tracked files changed while the check ran; nothing recorded")
        result = ("not_configured" if not steps
                  else "passed" if all(s["rc"] == 0 for s in done) else "failed")
        payload = {"task": task, "head": head, "base": base, "test_commit": test_commit,
                   "steps": done, "result": result, "log": str(log)}
        with StateWriter(conn).transaction() as w:
            w.append_event(kind="presubmit_checked", actor="secretary", payload=payload,
                           run_task_id=task)
    except (Refused, subprocess.CalledProcessError, OSError) as exc:
        print(f"presubmit: {getattr(exc, 'stderr', '') or exc}")
        print("PRESUBMIT: failed")
        return 1
    finally:
        conn.close()
    print(f"presubmit: log {log}")
    print(f"PRESUBMIT: {result.replace('_', '-')}")
    return {"passed": 0, "failed": 1, "not_configured": 3}[result]


# -- push --------------------------------------------------------------------

def _latest_check(conn, task: str) -> Optional[dict]:
    latest = None
    for (raw,) in conn.execute(
            "SELECT payload_json FROM events WHERE kind = 'presubmit_checked' ORDER BY id"):
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            continue
        if p.get("task") == task:
            latest = p
    return latest


def cmd_push(root: Path, db_path: Path, task: str, test_commit: bool, force: bool) -> int:
    conn = connect(db_path)
    warning = ""
    try:
        run = _run_row(conn, task)
        head = _git(run["wdir"], "rev-parse", "HEAD")
        check = _latest_check(conn, task)
        if check is None:
            raise Refused("no presubmit_checked for this task; run `presubmit.py run` first")
        if check.get("head") != head:
            raise Refused(f"the check was at {str(check.get('head'))[:12]}, HEAD is now {head[:12]};"
                          " run `presubmit.py run` again")
        if check.get("result") == "not_configured":
            if _required(root):
                raise Refused("no pre-submit check is set for this project and registry/org-config.md"
                              " has presubmit_required: yes; add the project's presubmit column")
            warning = (" -- WARNING: no pre-submit check is set for this project (nothing was run);"
                       " presubmit_required: yes in registry/org-config.md refuses this")
        elif check.get("result") != "passed":
            raise Refused(f"the check at {head[:12]} is {check.get('result')} (log {check.get('log')})")
        sha = head
        if test_commit:
            sha = check.get("test_commit")
            if not sha:
                raise Refused("the check found no commit with an Acceptance-Test trailer")
        ref = f"refs/heads/{run['branch']}"
        cmd = ["push", "origin", f"{sha}:{ref}"]
        if force:
            # Replace the branch (after a rebase, or to put the test commit back as head), but
            # only over the commit that is on the remote right now.
            remote = _git(run["wdir"], "ls-remote", "origin", ref).split()
            cmd = ["push", f"--force-with-lease={ref}:{remote[0] if remote else ''}", "origin", f"{sha}:{ref}"]
        _git(run["wdir"], *cmd)
    except Refused as exc:
        print(f"PRESUBMIT_PUSH: refused: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"PRESUBMIT_PUSH: refused: git push failed: {exc.stderr.strip()}")
        return 1
    finally:
        conn.close()
    print(f"PRESUBMIT_PUSH: pushed {sha[:12]} to {run['branch']}{warning}")
    return 0


# -- pr ----------------------------------------------------------------------

def closing_refs(text: str) -> list[tuple[str, Optional[str], int]]:
    """(matched text, owner/repo or None, number) for every closing reference."""
    out = []
    for m in _CLOSING.finditer(text):
        slug = m.group("slug") or m.group("uslug")
        out.append((m.group(0), slug, int(m.group("n") or m.group("un"))))
    return out


def cmd_pr(root: Path, db_path: Path, args) -> int:
    conn = connect(db_path)
    try:
        run = _run_row(conn, args.task)
        wdir = run["wdir"]
        try:
            repo = resolve_repo_for_task(conn, args.task, claude_org_root=root).repo
        except RepoResolutionError as exc:
            raise Refused(f"cannot resolve the repository: {exc}")
        body = Path(args.body_file).read_text(encoding="utf-8")
        sources = [("title", args.title)]
        sources += [(f"body:{i}", line) for i, line in enumerate(body.splitlines(), 1)]
        base = _resolve_base(wdir, _base_ref(conn, args.task))
        # Every commit the PR brings, including ones that came in through a merge of
        # another branch (each can close an Issue when the PR lands). With --base the
        # commits not on that branch count too, so a PR to another branch is covered.
        commits = set(_git(wdir, "rev-list", "HEAD", "--not", base).split())
        if args.create and args.base:
            other = _resolve_base(wdir, "origin/" + args.base.removeprefix("origin/"))
            commits |= set(_git(wdir, "rev-list", "HEAD", "--not", other).split())
        for c in sorted(commits):
            for line in _git(wdir, "log", "-1", "--format=%B", c).splitlines():
                sources.append((f"commit {c[:12]}", line))
        allowed = set(args.closes or [])
        bad = []
        for where, line in sources:
            for text, slug, n in closing_refs(line):
                if (slug is None or slug.lower() == repo.lower()) and n in allowed:
                    continue
                bad.append(f"{where}: {text!r} in: {line.strip()}")
        if bad:
            for b in bad:
                print(f"presubmit: closing reference not listed with --closes: {b}")
            raise Refused(f"{len(bad)} closing reference(s) would close an Issue on merge;"
                          " rewrite them (e.g. 'Refs #N') or list the Issue with --closes")
        if args.create:
            gh = ["gh", "pr", "create", "--repo", repo, "--head", run["branch"],
                  "--title", args.title, "--body-file", args.body_file]
            if args.base:
                gh += ["--base", args.base]
        else:
            gh = ["gh", "pr", "edit", str(args.edit), "--repo", repo,
                  "--title", args.title, "--body-file", args.body_file]
        proc = subprocess.run(gh, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Refused(f"gh failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}")
    except Refused as exc:
        print(f"PRESUBMIT_PR: refused: {exc}")
        return 1
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"PRESUBMIT_PR: refused: {getattr(exc, 'stderr', '') or exc}")
        return 1
    finally:
        conn.close()
    if args.create:
        print(f"PRESUBMIT_PR: created {proc.stdout.strip()}")
    else:
        print(f"PRESUBMIT_PR: edited #{args.edit}")
    return 0


# -- hook --------------------------------------------------------------------

_OPS = ";&|()<>\n"
_RUNNERS = {"bash", "sh", "zsh", "eval", "env", "sudo", "command", "exec", "xargs", "nohup", "time"}
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _simple_commands(code: str, depth: int = 0) -> list[list[str]]:
    lex = shlex.shlex(code, posix=True, punctuation_chars=_OPS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    out: list[list[str]] = []
    cur: list[str] = []
    for tok in lex:
        if tok and all(c in _OPS for c in tok):
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(tok)
    if cur:
        out.append(cur)
    if depth < 3:
        nested = []
        for words in out:
            # Look past VAR=value assignments too (`X=1 bash -c '...'`).
            head = [w for w in words if not _ASSIGN.match(w)] or words
            if Path(head[0]).name in _RUNNERS:
                for w in head[1:]:
                    if " " in w or "\t" in w:
                        nested += _simple_commands(w, depth + 1)
        for m in _SUBST.finditer(code):
            nested += _simple_commands(m.group(1) or m.group(2) or "", depth + 1)
        out += nested
    return out


_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"env", "command", "exec", "nohup", "time", "sudo", "nice", "builtin"}


def _unwrap(words: list[str]) -> list[str]:
    """Drop ``VAR=value`` assignments and wrapper programs (env, command, ...) in front."""
    while words:
        if _ASSIGN.match(words[0]):
            words = words[1:]
        elif Path(words[0]).name in _WRAPPERS:
            words = words[1:]
            while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
                words = words[1:]
        else:
            break
    return words


def _real(path: str) -> str:
    return os.path.realpath(path)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _ref_branch(word: str) -> str:
    ref = word.lstrip("+").split(":")[-1]
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def decide(conn, command: str, cwd: str) -> str:
    runs = [dict(zip(("task", "branch", "pr_url", "wdir"), (r[i] for i in range(4))))
            for r in conn.execute(
                "SELECT r.task_id, r.branch, r.pr_url, w.abs_path FROM runs r "
                "LEFT JOIN worker_dirs w ON w.id = r.worker_dir_id "
                f"WHERE r.status IN ({','.join('?' * len(OPEN))})", OPEN)]
    dirs = {_real(r["wdir"]): r["task"] for r in runs if r["wdir"]}
    branches = {r["branch"]: r["task"] for r in runs if r["branch"]}
    # PR numbers are per repository: key by (owner/repo, number).
    prs = {(m.group(1).lower(), m.group(2)): r["task"] for r in runs
           if r["pr_url"] and (m := re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", r["pr_url"]))}
    here: Optional[str] = cwd  # follows cd / pushd through the command; None = unknown
    via = "use `python3 tools/presubmit.py push|pr --task {t}` (it checks the pre-submit record first)"

    def task_of_dir(d: str) -> Optional[str]:
        if "$" in d or (here is None and not os.path.isabs(d)):
            return "?"
        rd = _real(os.path.join(here or "/", d))
        return next((t for wd, t in dirs.items() if _inside(rd, wd)), None)

    for words in _simple_commands(command):
        words = _unwrap(words)
        if not words:
            continue
        name = Path(words[0]).name
        if name in ("cd", "pushd"):
            arg = next((w for w in words[1:] if not w.startswith("-")), None)
            if "(" in command or ")" in command:
                # The words do not show which cd sits inside a subshell; do not guess.
                here = None
            elif arg is None:
                here = os.path.expanduser("~")
            elif "$" in arg or here is None and not os.path.isabs(os.path.expanduser(arg)):
                here = None
            else:
                here = os.path.join(here or "/", os.path.expanduser(arg))
            continue
        if name == "git":
            i, targets = 1, ["."]
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
            if i >= len(words) or words[i] != "push":
                continue
            target = os.path.join(*targets) if len(targets) > 1 else "."
            task = task_of_dir(target)
            if task is None:
                for w in words[i + 1:]:
                    if "$" in w:
                        task = "?"
                    elif not w.startswith("-") and _ref_branch(w) in branches:
                        task = branches[_ref_branch(w)]
            if task == "?":
                return "DENY: cannot tell what this git push sends (a variable or substitution);" \
                       " push an open run's work with `python3 tools/presubmit.py push --task <task>`"
            if task:
                return f"DENY: this git push sends the work of open run {task}; " + via.format(t=task)
        elif name == "gh" and len(words) >= 3 and words[1] == "pr" and words[2] in ("create", "edit"):
            rest = words[3:]
            task = task_of_dir(".")
            if words[2] == "create":
                for j, w in enumerate(rest):
                    head = rest[j + 1] if w in ("--head", "-H") and j + 1 < len(rest) else (
                        w.split("=", 1)[1] if w.startswith("--head=") else None)
                    if head is not None:
                        task = "?" if "$" in head else branches.get(head, task)
            else:
                repo = None
                for j, w in enumerate(rest):
                    if w in ("--repo", "-R") and j + 1 < len(rest):
                        repo = rest[j + 1].lower()
                    elif w.startswith("--repo="):
                        repo = w.split("=", 1)[1].lower()
                for j, w in enumerate(rest):
                    if w.startswith("-") or (j and rest[j - 1] in ("--repo", "-R")):
                        continue
                    if "$" in w:
                        task = task or "?"
                        break
                    url = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)$", w)
                    num = url.group(2) if url else (w if w.isdigit() else None)
                    if num:
                        want = url.group(1).lower() if url else repo
                        hit = next((t for (r, n), t in prs.items()
                                    if n == num and (want is None or r == want)), None)
                        task = hit or task
                        break
            if task == "?":
                return f"DENY: cannot tell which PR this gh pr {words[2]} touches;" \
                       " use `python3 tools/presubmit.py pr --task <task>`"
            if task:
                return f"DENY: this gh pr {words[2]} is for open run {task}; " + via.format(t=task)
    return "ALLOW"


def cmd_hook(db_path: Path) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            print("ALLOW")
            return 0
        command = str((payload.get("tool_input") or {}).get("command") or "")
        if not re.search(r"\bpush\b|\bpr\b", command):
            print("ALLOW")
            return 0
        conn = connect(db_path)
        try:
            print(decide(conn, command, payload.get("cwd") or os.getcwd()))
        finally:
            conn.close()
    except Exception as exc:  # fail closed: the shell wrapper turns this into a deny
        print(f"DENY: presubmit hook failed: {type(exc).__name__}: {exc}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/presubmit.py",
                                 description="Pre-submit check from settings; push and PR steps gated on it.")
    ap.add_argument("--root", type=Path, default=_REPO_ROOT, help="org root (default: this checkout)")
    ap.add_argument("--db-path", type=Path, help="state.db override (default: discovery)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="run the project's pre-submit check in the worker dir")
    p.add_argument("--task", required=True)
    p = sub.add_parser("push", help="push HEAD (or the test commit) once the check passed at HEAD")
    p.add_argument("--task", required=True)
    p.add_argument("--test-commit", action="store_true", help="push only the recorded test commit")
    p.add_argument("--force", action="store_true",
                   help="replace the branch, leased on the commit the remote has now")
    p = sub.add_parser("pr", help="create or edit the PR after the closing-reference check")
    p.add_argument("--task", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--create", action="store_true")
    g.add_argument("--edit", type=int, metavar="N")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--base", help="base branch for --create")
    p.add_argument("--closes", type=int, action="append", metavar="N",
                   help="an Issue this PR closes on merge (repeatable)")
    sub.add_parser("hook", help="PreToolUse decision (payload on stdin)")
    args = ap.parse_args(argv)

    db_path = resolve_state_db_path(args.db_path)
    root = args.root.resolve()
    if args.cmd == "run":
        return cmd_run(root, db_path, args.task)
    if args.cmd == "push":
        return cmd_push(root, db_path, args.task, args.test_commit, args.force)
    if args.cmd == "pr":
        return cmd_pr(root, db_path, args)
    return cmd_hook(db_path)


if __name__ == "__main__":
    sys.exit(main())
