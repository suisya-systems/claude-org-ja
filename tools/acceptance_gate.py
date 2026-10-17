#!/usr/bin/env python3
"""Acceptance-test gate run just before a PR merge (Issue #1167, L4 pilot).

A PR passes only when all three hold:

(a) the protected tree entries at head equal those at the commit that added
    the acceptance test (the "manifest commit"),
(b) the acceptance test fails on the base code, and
(c) it passes on head.

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
slipped in undeclared and edited later.

Both runs use the same test side: the base run is the base tree with every
protected entry replaced by the manifest commit's, so a fixture added in
the test commit cannot make base fail and head pass on its own. Trees are
materialised from raw git objects (not ``git archive``, which honours
export attributes). Outcomes come from pytest's junit report, not the exit
code: base needs an assertion failure in a declared test (collection or
fixture errors do not count), head needs every collected test to pass. A run
cut short by ``pytest.exit`` (at collection, in a test or in a teardown) is
rejected whatever exit code it asked for: a plugin loaded from outside the
materialised tree records the interruption.

The PR's code runs under bubblewrap (``sandbox``): no network, no HOME, no
environment, and none of this repo (state.db). Without ``bwrap`` (Windows,
macOS) the gate rejects. A test that needs the network fails here.

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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
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
JS_TEST_GLOBS = ("*.test.*", "*.spec.*")
RANK = ["passed", "skipped", "failed", "error"]
EXIT = {"passed": 0, "rejected": 1, "no-manifest": 3}
GUARD_PLUGIN = """import os


def pytest_keyboard_interrupt(excinfo):
    with open(os.environ["ACCEPTANCE_GATE_INTERRUPTED"], "w") as f:
        f.write(repr(excinfo.value))
"""


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


def materialize(repo: Path, entries: dict[str, tuple[str, str]], root: Path) -> None:
    paths = set(entries)
    for p in paths:
        parent = PurePosixPath(p).parent
        while str(parent) != ".":
            if str(parent) in paths:
                raise Reject(f"path conflict: {str(parent)!r} is a file but {p!r} needs a directory")
            parent = parent.parent
    root.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    try:
        for p, (mode, obj) in sorted(entries.items()):
            dest = root / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            if mode == "160000":
                dest.mkdir(exist_ok=True)
                continue
            proc.stdin.write(obj.encode() + b"\n")
            proc.stdin.flush()
            size = int(proc.stdout.readline().split()[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)
            if mode == "120000":
                try:
                    os.symlink(data.decode(), dest)
                    continue
                except OSError:
                    pass  # no symlink privilege (Windows): fall back to a file
            dest.write_bytes(data)
            if mode == "100755":
                dest.chmod(0o755)
    finally:
        proc.stdin.close()
        proc.wait()


HIDDEN = {str(_REPO_ROOT), str(_REPO_ROOT / "tools")}


def visible_sys_path() -> list[str]:
    return [p for p in dict.fromkeys(os.path.abspath(p) for p in sys.path if p)
            if os.path.isdir(p) and p not in HIDDEN]


def sandbox(root: Path, out: Path, plugin_dir: Path) -> list[str]:
    """bwrap prefix: PR code runs with no network, no HOME, no state.db, no env.

    Only the system dirs and this interpreter's own dirs are visible (read-only);
    the materialised tree and the run's output dir are the only writable paths.
    The gate's own repo (state.db, tools/) is never bound.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise Reject("bubblewrap (bwrap) is required to run the PR's tests in isolation;"
                     " not found, so the gate cannot run here - merge by hand after review")
    ro = ["/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc",
          sys.prefix, sys.base_prefix, os.path.dirname(os.path.realpath(sys.executable)),
          *visible_sys_path()]
    cmd = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
           "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp",
           "--setenv", "LANG", "C.UTF-8", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for d in dict.fromkeys(os.path.abspath(d) for d in ro):
        if d not in HIDDEN and os.path.exists(d):
            cmd += ["--ro-bind", d, d]
    return cmd + ["--bind", str(root), str(root), "--bind", str(out), str(out),
                  "--ro-bind", str(plugin_dir), str(plugin_dir), "--chdir", str(root)]


def run_pytest(root: Path, files: list[str], timeout: int) -> tuple[int, dict[str, str]]:
    """Exit code and test id -> passed / skipped / failed / error, from the junit report.

    Both are needed: the report tells an assertion failure from a fixture error,
    the exit code tells a finished run from an interrupted one (``pytest.exit``,
    internal error), whose report only holds the tests that ran.
    """
    out = root.parent / f"{root.name}-out"
    plugin_dir = root.parent / "guard-plugin"
    out.mkdir()
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "acceptance_gate_guard.py").write_text(GUARD_PLUGIN, encoding="utf-8")
    junit, marker = out / "junit.xml", out / "interrupted"
    cmd = [
        *sandbox(root, out, plugin_dir),
        "--setenv", "ACCEPTANCE_GATE_INTERRUPTED", str(marker),
        # HOME is hidden, so user site-packages come back only through PYTHONPATH.
        "--setenv", "PYTHONPATH", os.pathsep.join([str(plugin_dir), *visible_sys_path()]),
        sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "acceptance_gate_guard",
        "-o", "junit_family=xunit1", f"--junitxml={junit}", f"--rootdir={root}",
        *files,
    ]
    try:
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Reject(f"pytest timed out after {timeout}s in {root.name}")
    if marker.exists():
        raise Reject(f"pytest was interrupted in {root.name} (rc={res.returncode}): "
                     f"{marker.read_text(encoding='utf-8')[:200]}")
    if not junit.exists():
        tail = (res.stdout + res.stderr).strip().splitlines()[-5:]
        raise Reject(f"pytest produced no report in {root.name} (rc={res.returncode}): {tail}")
    outcomes: dict[str, str] = {}
    for tc in ET.parse(junit).iter("testcase"):
        tags = {c.tag for c in tc}
        outcome = next((o for t, o in (("error", "error"), ("failure", "failed"),
                                       ("skipped", "skipped")) if t in tags), "passed")
        tid = f"{tc.get('file')}::{tc.get('classname')}::{tc.get('name')}"
        # A test can appear twice (call failure + teardown error); keep the worst.
        outcomes[tid] = max(outcomes.get(tid, "passed"), outcome, key=RANK.index)
    return res.returncode, outcomes


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


def evaluate(repo: Path, base: str, head: str, timeout: int, info: dict,
             pin: dict | None = None) -> None:
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
    pm = check_protected(repo, msha, head, manifest)
    declared = [p for p in pm if not is_infra(p)]
    js = [p for p in declared if any(fnmatch.fnmatch(PurePosixPath(p).name, g) for g in JS_TEST_GLOBS)]
    if js:
        # ponytail: pytest only for the pilot; add a vitest junit runner when a JS project joins.
        raise Reject(f"JavaScript tests are not supported by the gate yet: {js}")
    files = sorted(p for p in declared if fnmatch.fnmatch(PurePosixPath(p).name, "test_*.py")
                   or fnmatch.fnmatch(PurePosixPath(p).name, "*_test.py"))
    if not files:
        raise Reject("the manifest declares no pytest test file (test_*.py / *_test.py)")

    base_entries = {p: e for p, e in tree(repo, base).items() if not is_protected(p, manifest)}
    base_entries.update(pm)
    with tempfile.TemporaryDirectory(prefix="acceptance-gate-") as tmp:
        materialize(repo, base_entries, Path(tmp) / "base")
        rc, on_base = run_pytest(Path(tmp) / "base", files, timeout)
        failed = {t for t, o in on_base.items() if o == "failed" and t.split("::")[0] in files}
        if rc != 1 or not failed:
            summary = sorted(set(on_base.values())) or ["no tests"]
            raise Reject(
                f"the test does not fail on base (rc={rc}, outcomes: {summary}); a completed run"
                " with an assertion failure is"
                " required - collection / fixture errors do not count (import new symbols"
                " inside the test function)"
            )
        materialize(repo, tree(repo, head), Path(tmp) / "head")
        rc, on_head = run_pytest(Path(tmp) / "head", files, timeout)
    bad = sorted(t for t, o in on_head.items() if o != "passed")
    if rc != 0 or bad or not on_head:
        raise Reject(f"the test does not pass on head (rc={rc}): {bad or 'no tests collected'}")
    missing = sorted(failed - on_head.keys())
    if missing:
        raise Reject(f"tests that failed on base were not run on head: {missing}")
    info.update(result="passed", reason=f"{len(failed)} test(s) fail on base and pass on head")


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
        description="Merge-time acceptance-test gate (base fails, head passes, tests untouched).",
    )
    ap.add_argument("--base", required=True, help="base ref (merge target)")
    ap.add_argument("--head", required=True, help="head ref (PR head)")
    ap.add_argument("--repo", default=".", help="repository path (default: cwd)")
    ap.add_argument("--pr", help="PR key (e.g. OWNER/REPO#N): recorded, and pins the test commit")
    ap.add_argument("--task", help="task id, recorded in the event")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per pytest run")
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
        evaluate(repo, base, head, args.timeout, info, pin)
    except Reject as exc:
        info.update(result="rejected", reason=str(exc))
    except subprocess.CalledProcessError as exc:
        info.update(result="rejected", reason=f"git failed: {' '.join(exc.cmd[3:])}: {exc.stderr.strip()}")
    except (OSError, ET.ParseError, ValueError, IndexError) as exc:
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
