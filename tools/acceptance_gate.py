#!/usr/bin/env python3
"""Acceptance-test gate run just before a PR merge (Issue #1167, #1231).

The gate runs no test. It judges from git and from the CI records of the
PR's own repository, so any language works. A PR passes only when all hold:

(a) the protected tree entries at head equal those at the commit that added
    the acceptance test (the "manifest commit" M), and M is the PR's first
    commit (its parent is the merge base of base and head), so M is the base
    code plus the test;
(b) before the fix the test failed: state.db has a ``ci_completed`` event for
    this PR with ``status=failed`` and ``head_oid`` = M (the secretary pushes M
    alone first and watches CI on it), and
(c) the same CI job (GitHub app id + check name, latest run) concluded
    ``failure`` at M and ``success`` at head, and the GitHub Actions runs for
    M and for head were made against one and the same base SHA. Only jobs of
    the latest ``pull_request`` workflow run per workflow count (``pr_runs``),
    so the jobs compared are the ones whose base is checked, and a commit
    pushed again (the retry after the base moved) replaces its older run.

Head passing as a whole is the merge hook's CI binding (#1228): the latest
``ci_completed`` for the PR is ``passed`` at the merged head.

What this does NOT prove (Issue #1231, owner decision 2026-11-05): that the
declared test itself ran in CI. A change outside the protected paths (a
``tests/__init__.py``, a runner script, a language-specific config) can drop
the test from discovery so the job turns green with the bug still there. The
guarantee is "with the declared test and the test infrastructure unchanged,
one CI job failed at M and succeeded at head". Test-level evidence from CI
(JUnit artifacts) is a follow-up.

Manifest: a commit in ``base..head`` declares the test with one or more
``Acceptance-Test: <path>`` trailers (a file or a directory). The trailer
lives in the commit itself, so the commit SHA and the path list are one
object. Exactly one such commit is allowed; a second one is a rewritten
manifest and is rejected. No such commit means the gate does not apply
(``no-manifest``); whether that is allowed is the caller's policy.

Protected = the manifest paths plus the test infrastructure / CI config
(``conftest.py``, pytest / vitest config files, ``pyproject.toml``,
``package.json``, ``.github/workflows/**``). Everything the manifest commit
itself changes must be protected, so a helper the test needs cannot be
slipped in undeclared and edited later. The check compares M with head, so a
change made and reverted in between passes.

A rebase changes M's SHA, so the CI failure recorded at the old M no longer
counts: push the new M alone again and let CI fail on it.

Pin (with ``--pr``): the first run that sees a manifest records a digest of
the declared test files (path, mode, blob) and paths. A later run for the same
PR whose digest differs, or that finds no manifest at all, is rejected: the
test commit was rewritten (force push) after the gate first saw it. A rebase
keeps the digest. With ``--pr`` the event is the pin, so a failed write
rejects instead of only warning.

Output: a reason line, then ``ACCEPTANCE_GATE: passed|rejected|no-manifest``
as the last line. Exit 0 / 1 / 3. One ``acceptance_gate`` event is written
to state.db; a failed write only warns and never changes the verdict.
"""

from __future__ import annotations

import argparse
import fnmatch
import functools
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TRAILER = "Acceptance-Test"
INFRA_NAMES = {
    "conftest.py", "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml",
    "pyproject.toml", "setup.cfg", "tox.ini", "package.json",
}
INFRA_GLOBS = ("vitest.config.*", "vitest.workspace.*", "vite.config.*")
WORKFLOWS = ".github/workflows/"
EXIT = {"passed": 0, "rejected": 1, "no-manifest": 3}


class Reject(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout


def is_infra(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name in INFRA_NAMES
        or any(fnmatch.fnmatch(name, g) for g in INFRA_GLOBS)
        or path.startswith(WORKFLOWS)
    )


def is_protected(path: str, manifest: list[str]) -> bool:
    return is_infra(path) or any(path == p or path.startswith(p + "/") for p in manifest)


def tree(repo: Path, sha: str) -> dict[str, tuple[str, str]]:
    """path -> (mode, object sha) for every entry of the commit's tree."""
    out = {}
    for rec in git(repo, "ls-tree", "-r", "-z", "--full-tree", sha).split("\0"):
        if rec:
            meta, path = rec.split("\t", 1)
            mode, _type, obj = meta.split()
            out[path] = (mode, obj)
    return out


def find_manifest(repo: Path, base: str, head: str) -> tuple[str, list[str]] | None:
    log = git(
        repo, "log", "--format=%H%x00%(trailers:key=" + TRAILER
        + ",valueonly,separator=%x1f)%x1e", f"{base}..{head}",
    )
    found = []
    for rec in log.split("\x1e"):
        sha, _, values = rec.strip().partition("\0")
        paths = [v.strip() for v in values.split("\x1f") if v.strip()]
        if sha and paths:
            found.append((sha, paths))
    if not found:
        return None
    if len(found) > 1:
        raise Reject(
            "more than one commit declares " + TRAILER + " ("
            + ", ".join(s[:12] for s, _ in found) + "): the manifest was rewritten"
        )
    sha, paths = found[0]
    clean = []
    for p in paths:
        pp = PurePosixPath(p)
        if pp.is_absolute() or ".." in pp.parts or "\\" in p or p.startswith("-"):
            raise Reject(f"invalid manifest path {p!r}")
        clean.append(str(pp))
    return sha, clean


def check_protected(repo: Path, msha: str, head: str, manifest: list[str]):
    """Condition (a). Returns the manifest commit's protected entries."""
    if len(git(repo, "rev-list", "--parents", "-n1", msha).split()) != 2:
        raise Reject(f"manifest commit {msha[:12]} must have exactly one parent")
    mtree = tree(repo, msha)
    for p in manifest:
        if not any(q == p or q.startswith(p + "/") for q in mtree):
            raise Reject(f"manifest path {p!r} does not exist in {msha[:12]}")
    pm = {p: e for p, e in mtree.items() if is_protected(p, manifest)}
    for p, (mode, _) in pm.items():
        if mode in ("120000", "160000"):
            raise Reject(f"protected path {p!r} is a symlink or submodule")
    touched = git(repo, "diff", "--no-renames", "--name-only", "-z", f"{msha}^", msha)
    undeclared = sorted(p for p in touched.split("\0") if p and not is_protected(p, manifest))
    if undeclared:
        raise Reject(
            f"manifest commit {msha[:12]} also changes undeclared paths "
            f"{undeclared}: declare them with {TRAILER} or move them to a later commit"
        )
    ph = {p: e for p, e in tree(repo, head).items() if is_protected(p, manifest)}
    changed = sorted(p for p in pm.keys() | ph.keys() if pm.get(p) != ph.get(p))
    if changed:
        raise Reject(
            f"protected paths changed after the test commit {msha[:12]}: {changed}"
            " (rebase so the test commit sits on the new base if the change came from base)"
        )
    return pm


def check_first(repo: Path, msha: str, base: str, head: str) -> None:
    """M is the PR's first commit: its parent is the merge base, so M = base code + test."""
    parent = git(repo, "rev-parse", f"{msha}^").strip()
    fork = git(repo, "merge-base", base, head).strip()
    if parent != fork:
        raise Reject(f"the test commit {msha[:12]} must be the first commit of the PR (its parent"
                     f" {parent[:12]} is not the merge base {fork[:12]}): put the test commit first")


def ci_failed_at(db_path: str | None, repo: str, number: int, sha: str) -> bool:
    """True when state.db has a failed ci_completed for this PR with head_oid == sha."""
    import sqlite3
    from tools.state_db.discover import resolve_state_db_path

    path = resolve_state_db_path(Path(db_path) if db_path else None)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT payload_json FROM events WHERE kind = 'ci_completed'"
                                " AND json_extract(payload_json, '$.status') = 'failed'"
                                " AND json_extract(payload_json, '$.head_oid') = ?", (sha,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise Reject(f"cannot read the CI records from {path}: {exc}")
    return any(json.loads(r)["pr"] == number and str(json.loads(r).get("repo", "")).lower()
               == repo.lower() for (r,) in rows)


def gh_lines(path: str, jq: str) -> list:
    """One JSON value per line of ``gh api --paginate --jq``; Reject when gh fails."""
    try:
        res = subprocess.run(["gh", "api", "--paginate", path, "--jq", jq],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Reject(f"cannot read {path} from GitHub: {exc}")
    if res.returncode != 0:
        raise Reject(f"cannot read {path} from GitHub: {res.stderr.strip()[-200:]}")
    return [json.loads(line) for line in res.stdout.splitlines() if line.strip()]


@functools.lru_cache(maxsize=None)
def pr_runs(repo: str, sha: str) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """(check suite id, base SHAs) of the latest pull_request workflow run per workflow at sha.

    Latest per workflow, so a run superseded by pushing the same commit again
    (the retry after the base moved) no longer counts.
    """
    latest: dict[int, dict] = {}
    for r in gh_lines(f"repos/{repo}/actions/runs?head_sha={sha}&event=pull_request&per_page=100",
                      ".workflow_runs[] | {wf: .workflow_id, suite: .check_suite_id,"
                      " created: .created_at, bases: [.pull_requests[].base.sha]}"):
        if r["wf"] not in latest or r["created"] > latest[r["wf"]]["created"]:
            latest[r["wf"]] = r
    return tuple((r["suite"], tuple(r["bases"])) for r in latest.values())


def check_runs(repo: str, sha: str) -> dict[tuple[int, str], str]:
    """(app id, check name) -> conclusion of the latest completed run of that check at sha.

    Only checks of the runs ``pr_runs`` selected, so the jobs compared are the
    ones whose base ``run_bases`` reports.
    """
    suites = {suite for suite, _ in pr_runs(repo, sha)}
    latest: dict[tuple[int, str], tuple[str, str]] = {}
    for r in gh_lines(f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
                      ".check_runs[] | {app: .app.id, name, conclusion, completed_at,"
                      " suite: .check_suite.id}"):
        key = (r["app"], r["name"])
        if r["suite"] in suites and r["completed_at"] and (
                key not in latest or r["completed_at"] > latest[key][0]):
            latest[key] = (r["completed_at"], r["conclusion"])
    return {k: c for k, (_, c) in latest.items()}


def run_bases(repo: str, sha: str) -> set[str]:
    """Base SHAs the runs ``pr_runs`` selected were made against."""
    return {b for _, bases in pr_runs(repo, sha) for b in bases}


def manifest_digest(repo: Path, msha: str, manifest: list[str]) -> str:
    """sha256 of the declared paths and their (mode, blob) in the manifest commit."""
    entries = sorted((p, *e) for p, e in tree(repo, msha).items()
                     if any(p == m or p.startswith(m + "/") for m in manifest))
    return hashlib.sha256(json.dumps([sorted(manifest), entries]).encode()).hexdigest()


def first_pin(db_path: str | None, pr: str) -> dict | None:
    """The first earlier event for this PR that saw a manifest, or None."""
    import sqlite3
    from tools.state_db.discover import resolve_state_db_path

    path = resolve_state_db_path(Path(db_path) if db_path else None)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT payload_json FROM events WHERE kind = 'acceptance_gate'"
                " AND json_extract(payload_json, '$.pr') = ?"
                " AND json_extract(payload_json, '$.manifest_digest') IS NOT NULL"
                " ORDER BY id LIMIT 1", (pr,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise Reject(f"cannot read the pin from {path}: {exc}")
    return json.loads(row[0]) if row else None


def evaluate(repo: Path, base: str, head: str, info: dict, pin: dict | None = None,
             pr: str | None = None, db_path: str | None = None) -> None:
    """Fill ``info`` with the verdict; raises Reject after recording what it found."""
    found = find_manifest(repo, base, head)
    if found is None:
        if pin:
            raise Reject(
                f"no commit declares {TRAILER} any more, but this PR had one "
                f"({pin.get('manifest_commit', '?')[:12]}) when the gate first saw it:"
                " the test commit was removed - ask the human")
        info.update(result="no-manifest", reason=f"no commit in base..head has an {TRAILER} trailer")
        return
    msha, manifest = found
    info.update(manifest_commit=msha, paths=manifest,
                manifest_digest=manifest_digest(repo, msha, manifest))
    if pin and pin["manifest_digest"] != info["manifest_digest"]:
        raise Reject(
            f"the test commit was rewritten since the gate first saw it "
            f"({pin.get('manifest_commit', '?')[:12]} -> {msha[:12]}, paths "
            f"{pin.get('paths')} -> {manifest}) - ask the human")
    check_protected(repo, msha, head, manifest)
    check_first(repo, msha, base, head)
    if not pr or "#" not in pr:
        raise Reject("the CI evidence is read per PR: pass --pr OWNER/REPO#N")
    slug, number = pr.rsplit("#", 1)
    if not ci_failed_at(db_path, slug, int(number), msha):
        raise Reject(f"no failed CI run recorded at the test commit {msha[:12]}: push the test"
                     " commit alone first and watch CI on it with /pr-watch-pane")
    at_m, at_head = check_runs(slug, msha), check_runs(slug, head)
    flipped = sorted(k for k, c in at_m.items() if c == "failure" and at_head.get(k) == "success")
    if not flipped:
        raise Reject(f"no CI job failed at the test commit and succeeded at head (test commit:"
                     f" {sorted(at_m.items())}, head: {sorted(at_head.items())})")
    bases_m, bases_head = run_bases(slug, msha), run_bases(slug, head)
    if len(bases_m) != 1 or bases_m != bases_head:
        raise Reject(f"the CI runs at the test commit and at head are not on one known base"
                     f" (different bases or unknown: {sorted(bases_m)} vs {sorted(bases_head)}):"
                     " push the test commit alone again on the current base")
    jobs = ", ".join(name for _, name in flipped)
    info.update(result="passed", reason=f"CI job(s) {jobs} failed at the test commit"
                f" {msha[:12]} and succeeded at head, on base {next(iter(bases_m))[:12]}")


def record(db_path: str | None, payload: dict) -> bool:
    try:
        from tools.journal_append import _db_append
        from tools.state_db.discover import resolve_state_db_path

        path = resolve_state_db_path(Path(db_path) if db_path else None)
        _db_append(path, "acceptance_gate", payload, watcher_guard=False)
        return True
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - the caller decides
        sys.stderr.write(f"acceptance_gate: event not recorded ({type(exc).__name__}: {exc})\n")
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/acceptance_gate.py",
        description="Merge-time acceptance-test gate from git and CI records (tests untouched, a CI job failed at the test commit and passed at head).",
    )
    ap.add_argument("--base", required=True, help="base ref (merge target)")
    ap.add_argument("--head", required=True, help="head ref (PR head)")
    ap.add_argument("--repo", default=".", help="repository path (default: cwd)")
    ap.add_argument("--pr", help="PR key (e.g. OWNER/REPO#N): recorded, and pins the test commit")
    ap.add_argument("--task", help="task id, recorded in the event")
    ap.add_argument("--db-path", help="state.db override (default: discovery)")
    ap.add_argument("--no-record", action="store_true", help="do not write the event")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    info: dict = {}
    try:
        base = git(repo, "rev-parse", "--verify", args.base + "^{commit}").strip()
        head = git(repo, "rev-parse", "--verify", args.head + "^{commit}").strip()
        info = {"base": base, "head": head}
        pin = first_pin(args.db_path, args.pr) if args.pr and not args.no_record else None
        evaluate(repo, base, head, info, pin, args.pr, args.db_path)
    except Reject as exc:
        info.update(result="rejected", reason=str(exc))
    except subprocess.CalledProcessError as exc:
        info.update(result="rejected", reason=f"git failed: {' '.join(exc.cmd[3:])}: {exc.stderr.strip()}")
    except (OSError, ValueError, IndexError, KeyError) as exc:
        info.update(result="rejected", reason=f"{type(exc).__name__}: {exc}")

    if not args.no_record:
        payload = {k: v for k, v in info.items() if v is not None}
        payload["result"] = info["result"].replace("-", "_")
        payload["repo"] = str(repo)
        for k in ("pr", "task"):
            if getattr(args, k):
                payload[k] = getattr(args, k)
        if not record(args.db_path, payload) and args.pr and info["result"] != "rejected":
            info.update(result="rejected", reason="the event (this PR's pin) could not be recorded")
    print(info["reason"])
    print(f"ACCEPTANCE_GATE: {info['result']}")
    return EXIT[info["result"]]


if __name__ == "__main__":
    sys.exit(main())
