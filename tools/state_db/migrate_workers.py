"""M3 migration tooling: ../workers/ flat layout → 3-tier (<project>/_runs/<workstream>/<run>/).

CLI:
    python -m tools.state_db.migrate_workers --plan --inventory <path> [--workers-root <path>]
    python -m tools.state_db.migrate_workers --apply --confirm --inventory <path> [--workers-root <path>] [--keep-compat]
    python -m tools.state_db.migrate_workers --rollback [--manifest <path>] [--workers-root <path>]

Scope: tooling only. Live ../workers/ execution is performed separately by the
secretary in a follow-on ops issue (Issue #267 M3 §worker-scope).

Design source of truth:
    workers/state-db-hierarchy-design/directory-layout.md
    workers/state-db-hierarchy-design/migration-strategy.md §M3
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Project slug renames keyed by current dir name → target dir name (post-M3).
# Source: directory-layout.md §5 mapping table.
PROJECT_RENAMES: dict[str, str] = {
    "ccmux": "renga",
    # claude-org ⇄ claude-org-en swap, encoded as 3-step rename via intermediate.
    # Intermediate slug "claude-org-ja-tmp" matches migration-strategy.md §M3 step 1.
    "claude-org": "claude-org-ja",
    "claude-org-en": "claude-org",
}

# Intermediate slug used for the swap. Both directory-layout.md §5 and
# migration-strategy.md §M3 step 1 use "claude-org-ja-tmp"; we match that
# (CLAUDE.local.md mentions "claude-org-tmp1" but defers to the design SoT).
SWAP_INTERMEDIATE: str = "claude-org-ja-tmp"

ARCHIVE_QUARTER_DEFAULT = "2026-Q2"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Operation:
    """A single mv operation in the migration plan.

    op:
      - "rename_project": project-tier dir rename (worktree-fixup-aware)
      - "ensure_dir": mkdir -p; no rollback (idempotent)
      - "move_run": run / scratch / research / archive_candidate move
    """

    op: str
    src: str
    dst: str
    note: str = ""
    worktrees: list[str] = field(default_factory=list)
    has_worktrees: bool = False


@dataclass
class Plan:
    workers_root: str
    inventory_source: str
    archive_quarter: str
    operations: list[Operation]
    swap_intermediate: str = SWAP_INTERMEDIATE


# ---------------------------------------------------------------------------
# Inventory → target path computation
# ---------------------------------------------------------------------------


def _archive_quarter_for(now: Optional[datetime] = None) -> str:
    """Return YYYY-Qx for `now` (UTC). Default arg for testability."""
    n = now or datetime.now(timezone.utc)
    q = (n.month - 1) // 3 + 1
    return f"{n.year}-Q{q}"


def compute_target_path(
    entry: dict,
    workers_root: PurePosixPath,
    archive_quarter: str,
) -> PurePosixPath:
    """Compute the post-M3 absolute target path for an inventory entry.

    Rules (CLAUDE.local.md / directory-layout.md §5):
      - tier=project: target = <root>/<renamed_slug>/
      - tier=run: target = <root>/<parent_project>/_runs/<workstream or _solo>/<name>/
      - tier=scratch: target = <root>/_scratch/_runs/_solo/<name>/
      - tier=archive_candidate: target = <root>/_archive/<YYYY-Qx>/<parent_project>/<name>/
    """
    name: str = entry["name"]
    cls = entry["proposed_classification"]
    tier: str = cls["tier"]

    if tier == "project":
        target_slug = PROJECT_RENAMES.get(name, name)
        return workers_root / target_slug

    parent_project: str = cls.get("parent_project") or ""
    workstream: Optional[str] = cls.get("parent_workstream")

    if tier == "run":
        ws = workstream if workstream else "_solo"
        return workers_root / parent_project / "_runs" / ws / name

    if tier == "scratch":
        # Scratches collapse into a single _solo cluster regardless of inventory parent.
        return workers_root / "_scratch" / "_runs" / "_solo" / name

    if tier == "archive_candidate":
        return workers_root / "_archive" / archive_quarter / parent_project / name

    raise ValueError(f"unknown tier {tier!r} for entry {name!r}")


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------


def _git_worktrees(repo: Path) -> list[str]:
    """Return absolute paths of every worktree linked to `repo`. Empty if not a git repo."""
    if not (repo / ".git").exists():
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True, encoding="utf-8",
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    paths: list[str] = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            p = line[len("worktree "):].strip()
            # Skip the main worktree itself; we record only the linked ones,
            # because the main one moves implicitly with the project dir.
            if Path(p).resolve() != repo.resolve():
                paths.append(p)
    return paths


def build_plan(
    inventory: list[dict],
    workers_root: Path,
    archive_quarter: Optional[str] = None,
    inventory_source: str = "",
    *,
    detect_worktrees: bool = True,
) -> Plan:
    """Build an ordered migration plan from `inventory`.

    Ordering (advisor §4):
      1. ensure_dir for pseudo-project roots (_research/_runs, _scratch/_runs/_solo, _archive)
      2. project renames (ccmux→renga, then 3-step claude-org ⇄ claude-org-en swap)
      3. ensure_dir for each unique <project>/_runs/<workstream>/ target parent
      4. run moves
      5. scratch moves
      6. archive_candidate moves
    """
    aq = archive_quarter or _archive_quarter_for()
    root = PurePosixPath(workers_root.as_posix())
    ops: list[Operation] = []

    # --- pseudo-project roots ------------------------------------------------
    ops.append(Operation("ensure_dir", "", str(root / "_research" / "_runs"),
                         note="pseudo-project _research"))
    ops.append(Operation("ensure_dir", "", str(root / "_scratch" / "_runs" / "_solo"),
                         note="pseudo-project _scratch"))
    ops.append(Operation("ensure_dir", "", str(root / "_archive" / aq),
                         note=f"archive cold tier {aq}"))

    # --- project renames -----------------------------------------------------
    # ccmux → renga (single-step). Skip when src is gone but dst is present
    # (already-applied) so a re-plan after migration is clean.
    if any(e["name"] == "ccmux" for e in inventory):
        src = workers_root / "ccmux"
        dst = workers_root / "renga"
        if src.exists() or not dst.exists():
            wts = _git_worktrees(src) if detect_worktrees else []
            ops.append(Operation("rename_project", str(PurePosixPath(src.as_posix())),
                                 str(PurePosixPath(dst.as_posix())),
                                 note="ccmux → renga (rename)",
                                 worktrees=wts, has_worktrees=bool(wts)))

    # claude-org ⇄ claude-org-en swap, 3 steps:
    #   1. claude-org → claude-org-ja-tmp
    #   2. claude-org-en → claude-org
    #   3. claude-org-ja-tmp → claude-org-ja
    # Skip the entire swap when both source dirs are gone and the post-swap
    # dirs (claude-org-ja, claude-org) are present — already migrated.
    has_co = any(e["name"] == "claude-org" for e in inventory)
    has_coen = any(e["name"] == "claude-org-en" for e in inventory)
    # claude-org-en being absent + claude-org-ja being present is the
    # post-swap signature (claude-org is intentionally still present, but
    # now points at the former claude-org-en).
    swap_already_done = (
        not (workers_root / "claude-org-en").exists()
        and (workers_root / "claude-org-ja").exists()
    )
    if has_co and has_coen and not swap_already_done:
        co_path = workers_root / "claude-org"
        co_wts = _git_worktrees(co_path) if detect_worktrees else []
        coen_path = workers_root / "claude-org-en"
        coen_wts = _git_worktrees(coen_path) if detect_worktrees else []
        intermediate = workers_root / SWAP_INTERMEDIATE

        ops.append(Operation(
            "rename_project",
            str(PurePosixPath(co_path.as_posix())),
            str(PurePosixPath(intermediate.as_posix())),
            note="swap step 1/3: claude-org → " + SWAP_INTERMEDIATE,
            worktrees=co_wts, has_worktrees=bool(co_wts),
        ))
        ops.append(Operation(
            "rename_project",
            str(PurePosixPath(coen_path.as_posix())),
            str(PurePosixPath((workers_root / "claude-org").as_posix())),
            note="swap step 2/3: claude-org-en → claude-org",
            worktrees=coen_wts, has_worktrees=bool(coen_wts),
        ))
        ops.append(Operation(
            "rename_project",
            str(PurePosixPath(intermediate.as_posix())),
            str(PurePosixPath((workers_root / "claude-org-ja").as_posix())),
            note="swap step 3/3: " + SWAP_INTERMEDIATE + " → claude-org-ja",
            worktrees=co_wts, has_worktrees=bool(co_wts),
        ))

    # --- run / scratch / archive_candidate moves -----------------------------
    moves: list[tuple[str, Operation]] = []  # (sort key, op)
    seen_parents: set[str] = set()
    for entry in inventory:
        tier = entry["proposed_classification"]["tier"]
        if tier == "project":
            continue
        name = entry["name"]
        src = workers_root / name
        dst = compute_target_path(entry, root, aq)

        if str(PurePosixPath(src.as_posix())) == str(dst):
            continue  # already migrated, no-op
        # Already-migrated guard for re-runs: if src is gone but dst exists,
        # an earlier apply moved this entry. Skip silently so a post-migration
        # re-plan returns only ensure_dir ops.
        if not src.exists() and Path(str(dst)).exists():
            continue

        parent = str(PurePosixPath(dst).parent)
        if parent not in seen_parents:
            seen_parents.add(parent)
            moves.append((f"0:{parent}", Operation("ensure_dir", "", parent)))

        wts = _git_worktrees(src) if detect_worktrees else []
        sort_key = {"run": "1", "scratch": "2", "archive_candidate": "3"}.get(tier, "9")
        moves.append((f"{sort_key}:{name}", Operation(
            "move_run",
            str(PurePosixPath(src.as_posix())),
            str(dst),
            note=f"tier={tier}",
            worktrees=wts, has_worktrees=bool(wts),
        )))

    moves.sort(key=lambda kv: kv[0])
    ops.extend(op for _, op in moves)

    return Plan(
        workers_root=str(PurePosixPath(workers_root.as_posix())),
        inventory_source=inventory_source,
        archive_quarter=aq,
        operations=ops,
    )


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------


def render_plan_human(plan: Plan) -> str:
    lines = [
        f"# M3 migration plan",
        f"workers_root: {plan.workers_root}",
        f"archive_quarter: {plan.archive_quarter}",
        f"inventory: {plan.inventory_source}",
        f"swap_intermediate: {plan.swap_intermediate}",
        f"operations: {len(plan.operations)}",
        "",
    ]
    for i, op in enumerate(plan.operations, 1):
        if op.op == "ensure_dir":
            lines.append(f"{i:>3}. ensure_dir   {op.dst}")
        elif op.op == "rename_project":
            wt = f" [worktrees={len(op.worktrees)}]" if op.has_worktrees else ""
            lines.append(f"{i:>3}. rename_proj  {op.src}")
            lines.append(f"     →           {op.dst}{wt}  # {op.note}")
        elif op.op == "move_run":
            wt = f" [worktrees={len(op.worktrees)}]" if op.has_worktrees else ""
            lines.append(f"{i:>3}. move_run     {op.src}")
            lines.append(f"     →           {op.dst}{wt}  # {op.note}")
    return "\n".join(lines) + "\n"


def render_plan_manifest(plan: Plan) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workers_root": plan.workers_root,
        "inventory_source": plan.inventory_source,
        "archive_quarter": plan.archive_quarter,
        "swap_intermediate": plan.swap_intermediate,
        "operations": [asdict(op) for op in plan.operations],
    }


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def preflight(plan: Plan, workers_root: Path,
              *, db_path: Optional[Path] = None) -> list[str]:
    """Return list of human-readable warnings/errors. Empty list = OK to apply.

    Simulates the plan sequentially so the 3-step swap (which transiently
    claims `claude-org-ja-tmp`) does not raise spurious source-missing /
    target-exists warnings.

    Additional safety nets (round 1 review M1):
      - workers_root containment: every src/dst must live inside the
        `--workers-root` subtree. Reject paths that escape (e.g. a
        hand-edited manifest pointing at C:/Windows).
      - active-runs DB check: if `db_path` is provided, count rows in
        worker_dirs with lifecycle='active' that correspond to live in_use
        runs. Live runs during a mv lose their pwd. The check is reported
        as a warning (operator can override with --force at the CLI).
    """
    issues: list[str] = []

    root_drive = workers_root.resolve().drive.lower() if os.name == "nt" else ""
    root_resolved = str(workers_root.resolve()).replace("\\", "/").rstrip("/")

    def _outside_root(p: str) -> bool:
        if not p:
            return False
        try:
            resolved = str(Path(p).resolve()).replace("\\", "/").rstrip("/")
        except (OSError, ValueError):
            return True
        return not (resolved == root_resolved or resolved.startswith(root_resolved + "/"))

    # Track simulated existence: start from real FS, mutate as we walk ops.
    def _exists(p: str) -> bool:
        return Path(p).exists()

    # Snapshot which paths participate in the plan.
    touched: dict[str, bool] = {}
    def _is_present(p: str) -> bool:
        if p in touched:
            return touched[p]
        return _exists(p)

    for op in plan.operations:
        for path in (op.src, op.dst):
            if not path:
                continue
            if root_drive:
                d = Path(path).drive.lower()
                if d and d != root_drive:
                    issues.append(
                        f"cross-drive path detected ({d} vs {root_drive}): {path} — "
                        "junction --keep-compat will not work; use --manifest mode"
                    )
            if _outside_root(path):
                issues.append(
                    f"path escapes workers_root ({root_resolved}): {path} (op={op.op})"
                )
        if op.op == "ensure_dir":
            touched[op.dst] = True
            continue
        if op.op in ("rename_project", "move_run"):
            if not _is_present(op.src):
                issues.append(f"source missing: {op.src} (op={op.op})")
            if _is_present(op.dst):
                issues.append(f"target already exists: {op.dst} (op={op.op}, src={op.src})")
            touched[op.src] = False
            touched[op.dst] = True

    if db_path is not None:
        active = _count_active_runs(db_path)
        if active > 0:
            issues.append(
                f"{active} active run(s) (status in 'in_use'/'review') in {db_path} — "
                "suspend them before migrating, or pass --force to override"
            )

    return issues


def _filter_overridable(issues: list[str], *, force: bool) -> list[str]:
    """Drop the active-runs warning when --force is set; everything else stays fatal."""
    if not force:
        return issues
    return [i for i in issues if "active run" not in i]


def _count_active_runs(db_path: Path) -> int:
    """Count runs whose status is in_use or review. 0 means safe to migrate."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('in_use','review')"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return 0


# ---------------------------------------------------------------------------
# Apply / Rollback
# ---------------------------------------------------------------------------


def _git_worktree_repair(repo: Path) -> None:
    """Run `git worktree repair` inside `repo` — best effort, no raise on failure."""
    if not (repo / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "repair"],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Surface but don't abort — secretary inspects manifest + worktree list afterwards.
        pass


def _make_junction(link: Path, target: Path) -> None:
    """Windows junction (mklink /J). Best-effort; same-drive only."""
    if os.name != "nt":
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            pass
        return
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _remove_junction(link: Path) -> None:
    if not link.exists() and not link.is_symlink():
        return
    try:
        # On Windows, junctions are removed with rmdir.
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, check=True)
        else:
            link.unlink()
    except (subprocess.CalledProcessError, OSError):
        pass


def apply_plan(
    plan: Plan,
    *,
    keep_compat: bool = False,
    manifest_path: Optional[Path] = None,
    runner: Optional[Any] = None,
) -> Path:
    """Execute `plan` and return the path of the written manifest.

    The manifest is written **incrementally** after every successful op so
    that a mid-batch failure still leaves a usable rollback record on disk.
    """
    rename = (runner.rename if runner else os.rename)
    mkdir = (runner.makedirs if runner else (lambda p: os.makedirs(p, exist_ok=True)))
    repair = (runner.repair if runner else _git_worktree_repair)
    junction = (runner.junction if runner else _make_junction)

    out = manifest_path or _default_manifest_path(plan)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = render_plan_manifest(plan)
    payload["executed"] = []
    executed: list[dict[str, Any]] = payload["executed"]

    def _persist() -> None:
        # Atomic-ish: write to .tmp then replace, so the manifest on disk is
        # never half-written even if the process is killed mid-write.
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)

    _persist()  # write empty-executed shell up front so a 1st-op crash leaves a manifest

    try:
        for op in plan.operations:
            if op.op == "ensure_dir":
                mkdir(op.dst)
                executed.append({"op": op.op, "dst": op.dst, "state": "completed"})
                _persist()
                continue

            # Write an in_progress entry BEFORE rename so a SIGKILL between
            # rename() and append() still leaves a manifest record. Rollback
            # treats in_progress as "the FS may be at src OR dst" and probes
            # both. (Codex round 1 review B1.)
            entry: dict[str, Any] = {
                "op": op.op,
                "src": op.src,
                "dst": op.dst,
                "worktrees": op.worktrees,
                "compat_junction": bool(keep_compat),
                "state": "in_progress",
            }
            executed.append(entry)
            _persist()

            rename(op.src, op.dst)
            if op.has_worktrees and op.op == "rename_project":
                repair(Path(op.dst))
            if keep_compat and op.op in ("rename_project", "move_run"):
                junction(Path(op.src), Path(op.dst))
            entry["state"] = "completed"
            _persist()
    except BaseException:
        # Even on Ctrl-C / OSError, the manifest is already up to date.
        _persist()
        raise

    return out


def apply_from_manifest(
    manifest_path: Path,
    *,
    keep_compat: bool = False,
    out_manifest: Optional[Path] = None,
    runner: Optional[Any] = None,
) -> Path:
    """Replay a previously generated `--plan --json` manifest.

    Lets the operator stage the migration tier-by-tier: edit the planned
    JSON to drop ops, then feed it back here. The new executed-op log is
    written to `out_manifest` (or a fresh timestamped file beside the
    workers root) — the original input manifest is left untouched.
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    ops = [Operation(**op) for op in data.get("operations", [])]
    plan = Plan(
        workers_root=data["workers_root"],
        inventory_source=data.get("inventory_source", str(manifest_path)),
        archive_quarter=data.get("archive_quarter", _archive_quarter_for()),
        operations=ops,
        swap_intermediate=data.get("swap_intermediate", SWAP_INTERMEDIATE),
    )
    return apply_plan(plan, keep_compat=keep_compat,
                      manifest_path=out_manifest, runner=runner)


def _default_manifest_path(plan: Plan) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(plan.workers_root) / f".m3-migration-manifest-{ts}.json"


def rollback(manifest_path: Path, *, runner: Optional[Any] = None) -> None:
    """Reverse the operations recorded in `manifest_path`.

    Handles both "completed" entries (rename was confirmed) and
    "in_progress" entries (manifest was persisted before rename, so the
    FS may be at src OR dst — probe and undo only if dst is present).
    """
    rename = (runner.rename if runner else os.rename)
    repair = (runner.repair if runner else _git_worktree_repair)
    remove_junction = (runner.remove_junction if runner else _remove_junction)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    executed = data.get("executed", [])
    for entry in reversed(executed):
        op = entry["op"]
        if op == "ensure_dir":
            # Idempotent; leave dirs alone (they may now contain content).
            continue
        state = entry.get("state", "completed")
        src = entry["src"]
        dst = entry["dst"]
        # Old manifests (pre-state-tracking) lacked the field; treat as completed.
        if state == "in_progress":
            # SIGKILL window between persist and rename, OR between rename
            # and the second persist. Decide based on FS observation.
            if Path(dst).exists() and not Path(src).exists():
                pass  # rename did happen; fall through to undo
            else:
                # rename never happened (or was already undone) — nothing to do.
                continue
        if entry.get("compat_junction"):
            remove_junction(Path(src))
        rename(dst, src)
        if op == "rename_project" and entry.get("worktrees"):
            repair(Path(src))


def find_latest_manifest(workers_root: Path) -> Optional[Path]:
    cands = sorted(workers_root.glob(".m3-migration-manifest-*.json"))
    return cands[-1] if cands else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_inventory(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="migrate_workers", description=__doc__.splitlines()[0])
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--plan", action="store_true", help="dry-run: print plan, do not move")
    grp.add_argument("--apply", action="store_true", help="execute (requires --confirm)")
    grp.add_argument("--rollback", action="store_true", help="undo latest applied manifest")
    p.add_argument("--confirm", action="store_true", help="required with --apply")
    p.add_argument("--inventory", type=Path, help="inventory.json path (required for --plan/--apply)")
    p.add_argument("--workers-root", type=Path, help="../workers/ root (default: parent of cwd)")
    p.add_argument("--archive-quarter", default=None, help=f"override quarter slug (default: {ARCHIVE_QUARTER_DEFAULT})")
    p.add_argument("--keep-compat", action="store_true",
                   help="create old-path → new-path junction for backward compat (Windows)")
    p.add_argument("--manifest", type=Path, help="manifest path: write target for --apply, read source for --rollback")
    p.add_argument("--from-manifest", type=Path,
                   help="--apply input: replay a previously generated --plan --json manifest "
                        "(supports tier-by-tier staging by hand-editing the manifest)")
    p.add_argument("--db", type=Path, default=None,
                   help="state.db path; preflight checks for active runs (status in 'in_use'/'review')")
    p.add_argument("--force", action="store_true",
                   help="treat preflight warnings about active runs as non-blocking")
    p.add_argument("--json", action="store_true", help="emit JSON manifest to stdout (with --plan)")
    args = p.parse_args(argv)

    workers_root: Path = (args.workers_root or Path.cwd().parent).resolve()

    if args.rollback:
        manifest = args.manifest or find_latest_manifest(workers_root)
        if manifest is None or not manifest.exists():
            print("error: no manifest found for rollback", file=sys.stderr)
            return 2
        rollback(manifest)
        print(f"rollback complete from {manifest}")
        return 0

    if args.apply and args.from_manifest:
        if not args.confirm:
            print("error: --apply requires --confirm", file=sys.stderr)
            return 2
        # preflight the loaded manifest before replaying — staged manifests
        # are hand-edited, so cross-drive / source-missing / target-conflict
        # checks must run here too, not only on the inventory-driven path.
        data = json.loads(args.from_manifest.read_text(encoding="utf-8"))
        loaded_plan = Plan(
            workers_root=data["workers_root"],
            inventory_source=data.get("inventory_source", str(args.from_manifest)),
            archive_quarter=data.get("archive_quarter", _archive_quarter_for()),
            operations=[Operation(**op) for op in data.get("operations", [])],
            swap_intermediate=data.get("swap_intermediate", SWAP_INTERMEDIATE),
        )
        issues = preflight(loaded_plan, Path(loaded_plan.workers_root),
                           db_path=args.db)
        issues = _filter_overridable(issues, force=args.force)
        if issues:
            print("preflight issues:", file=sys.stderr)
            for i in issues:
                print(f"  - {i}", file=sys.stderr)
            return 3
        out = apply_from_manifest(
            args.from_manifest,
            keep_compat=args.keep_compat,
            out_manifest=args.manifest,
        )
        print(f"apply (from manifest) complete; manifest={out}")
        return 0

    if args.inventory is None or not args.inventory.exists():
        print("error: --inventory <path> required and must exist", file=sys.stderr)
        return 2

    inv = _load_inventory(args.inventory)
    plan = build_plan(
        inv, workers_root,
        archive_quarter=args.archive_quarter,
        inventory_source=str(args.inventory),
    )

    if args.plan:
        if args.json:
            print(json.dumps(render_plan_manifest(plan), indent=2, ensure_ascii=False))
        else:
            print(render_plan_human(plan))
        # surface preflight as warnings, non-fatal for plan
        for issue in preflight(plan, workers_root):
            print(f"warning: {issue}", file=sys.stderr)
        return 0

    if args.apply:
        if not args.confirm:
            print("error: --apply requires --confirm", file=sys.stderr)
            return 2
        issues = preflight(plan, workers_root, db_path=args.db)
        issues = _filter_overridable(issues, force=args.force)
        if issues:
            print("preflight issues:", file=sys.stderr)
            for i in issues:
                print(f"  - {i}", file=sys.stderr)
            return 3
        manifest = apply_plan(plan, keep_compat=args.keep_compat, manifest_path=args.manifest)
        print(f"apply complete; manifest={manifest}")
        return 0

    return 1  # unreachable


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
