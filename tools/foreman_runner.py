"""Foreman state-machine helper for claude-org (Issue #60).

v1 scope: `delegate-plan` subcommand. Given a structured task description
and a renga `list_panes` snapshot, this script computes the deterministic
parts of the Foreman delegation state machine and emits a JSON action plan
that Foreman Claude reads and executes via MCP tool calls.

The helper does NOT call MCP tools directly. Foreman remains the actor that
receives Secretary's DELEGATE, invokes this helper, reads the returned plan,
and performs the `spawn_claude_pane` / `send_keys` / `send_message` / etc.
calls.

Deterministic operations this helper owns:
  - balanced split target/direction selection from pane rects
  - task / worker name validation (matches renga's `[A-Za-z0-9_-]` + not all-digit)
  - worker instruction file writing (.state/foreman/outbox/{task_id}-instruction.md)
  - worker state file seed (.state/workers/worker-{task_id}.md)
  - journal planned-event preparation

Everything that requires live MCP calls stays in the Claude side.

Usage:
  py -3 tools/foreman_runner.py delegate-plan \
      --task-json .state/foreman/inbox/{task_id}.json \
      --panes-json <(mcp_list_panes_output.json)

  # stdin form:
  cat task.json | py -3 tools/foreman_runner.py delegate-plan \
      --panes-json panes.json

Exit codes:
  0 — plan emitted OK (status = ready_to_spawn)
  1 — input validation failed (status = input_invalid)
  2 — algorithm produced no candidate and escalation is required
      (status = split_capacity_exceeded; exit 2 lets shell callers
       distinguish the case without re-parsing JSON)
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Matches renga's name/role validation (see `set_pane_identity` docs).
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ALL_DIGITS = re.compile(r"^\d+$")

# Balanced split constants — keep in sync with
# .claude/skills/org-delegate/references/pane-layout.md
MIN_PANE_WIDTH = 20
MIN_PANE_HEIGHT = 5
SECRETARY_MIN_WIDTH = 125
SECRETARY_MIN_HEIGHT = 45

# Default Claude model for worker panes. The auto-mode safety classifier
# is unstable on sonnet — opus-only per feedback_worker_model_opus.md.
DEFAULT_WORKER_MODEL = "opus"


# ----------------------------------------------------------------------------
# Pane model
# ----------------------------------------------------------------------------


@dataclass
class Pane:
    id: int
    name: Optional[str]
    role: Optional[str]
    focused: bool
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Pane":
        return cls(
            id=int(d["id"]),
            name=d.get("name"),
            role=d.get("role"),
            focused=bool(d.get("focused", False)),
            x=int(d["x"]),
            y=int(d["y"]),
            width=int(d["width"]),
            height=int(d["height"]),
        )


def rect_adjacent(a: Pane, b: Pane) -> bool:
    """Return True if `a` and `b` share a full edge (left-right or top-bottom)."""
    # Left-right adjacency: shared vertical edge + y-interval overlap
    horizontal_share = (
        a.x + a.width == b.x or b.x + b.width == a.x
    ) and (max(a.y, b.y) < min(a.y + a.height, b.y + b.height))
    # Top-bottom adjacency: shared horizontal edge + x-interval overlap
    vertical_share = (
        a.y + a.height == b.y or b.y + b.height == a.y
    ) and (max(a.x, b.x) < min(a.x + a.width, b.x + b.width))
    return horizontal_share or vertical_share


# ----------------------------------------------------------------------------
# Balanced-split algorithm (see pane-layout.md)
# ----------------------------------------------------------------------------


@dataclass
class SplitChoice:
    target_name: str
    target_id: int
    direction: str  # "vertical" | "horizontal"
    new_w: int
    new_h: int
    metric: int


def choose_split(panes: list[Pane]) -> Optional[SplitChoice]:
    """Select the next balanced-split target/direction, or None if no candidate.

    Mirrors Step 3-1b in .claude/skills/org-delegate/SKILL.md.
    """
    curator = next((p for p in panes if p.role == "curator"), None)

    candidates: list[SplitChoice] = []
    for p in panes:
        if p.role not in ("secretary", "foreman", "worker"):
            continue

        # foreman: only if adjacent to curator (keeps foreman-curator pair intact)
        if p.role == "foreman":
            if curator is None or not rect_adjacent(p, curator):
                continue

        # direction: terminal cells are ~2:1 tall. width > height*2 → horizontally
        # long in pixels → split vertically (left/right). Otherwise split horizontally.
        if p.width > p.height * 2:
            direction = "vertical"
            new_w = p.width // 2
            new_h = p.height
            metric = new_w
        else:
            direction = "horizontal"
            new_w = p.width
            new_h = p.height // 2
            metric = new_h

        # MIN_PANE constraint
        if new_w < MIN_PANE_WIDTH or new_h < MIN_PANE_HEIGHT:
            continue

        # secretary safety clause: only splittable if the new half would be
        # both wide AND tall enough to keep the secretary usable
        if p.role == "secretary" and (
            new_w < SECRETARY_MIN_WIDTH or new_h < SECRETARY_MIN_HEIGHT
        ):
            continue

        # Need a stable name to address by — require one
        if p.name is None:
            continue

        candidates.append(SplitChoice(
            target_name=p.name,
            target_id=p.id,
            direction=direction,
            new_w=new_w,
            new_h=new_h,
            metric=metric,
        ))

    if not candidates:
        return None

    # Sort: metric desc, then id asc
    candidates.sort(key=lambda c: (-c.metric, c.target_id))
    return candidates[0]


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------


def validate_task_id(task_id: str) -> Optional[str]:
    if not task_id:
        return "task_id is empty"
    if not _NAME_PATTERN.match(task_id):
        return (f"task_id {task_id!r} contains disallowed chars "
                "(allowed: [A-Za-z0-9_-])")
    # Derived worker pane name must not be all-digit
    worker_name = f"worker-{task_id}"
    if _ALL_DIGITS.match(worker_name):
        return f"derived worker name {worker_name!r} is all-digit"
    return None


def validate_cwd(cwd_str: str) -> Optional[str]:
    if not cwd_str:
        return "cwd is empty"
    p = Path(cwd_str)
    if not p.exists():
        return f"cwd {cwd_str!r} does not exist"
    if not p.is_dir():
        return f"cwd {cwd_str!r} is not a directory"
    return None


# ----------------------------------------------------------------------------
# Action plan
# ----------------------------------------------------------------------------


@dataclass
class ActionPlan:
    status: str  # "ready_to_spawn" | "split_capacity_exceeded" | "input_invalid"
    task_id: str
    spawn: Optional[dict[str, Any]] = None
    after_spawn: list[dict[str, Any]] = field(default_factory=list)
    state_writes: list[str] = field(default_factory=list)
    escalate: Optional[dict[str, Any]] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build_plan(
    task: dict[str, Any],
    panes: list[Pane],
    state_dir: Path,
) -> ActionPlan:
    task_id = task.get("task_id", "")
    plan = ActionPlan(status="ready_to_spawn", task_id=task_id)

    err = validate_task_id(task_id)
    if err:
        plan.status = "input_invalid"
        plan.errors.append(err)
        return plan

    cwd = task.get("worker_dir") or task.get("cwd")
    if not cwd:
        plan.status = "input_invalid"
        plan.errors.append("task.worker_dir (or .cwd) is required")
        return plan
    cwd_err = validate_cwd(cwd)
    if cwd_err:
        # Any cwd problem (empty / missing / not-a-directory) is a hard fail.
        # Letting renga catch it later is too late — the helper already wrote
        # worker state by then, and "not a directory" was silently passing as
        # a warning before.
        plan.status = "input_invalid"
        plan.errors.append(cwd_err)
        return plan

    # Disallow duplicate worker pane name
    worker_name = f"worker-{task_id}"
    if any(p.name == worker_name for p in panes):
        plan.status = "input_invalid"
        plan.errors.append(
            f"pane named {worker_name!r} already exists in the tab; "
            "close it first or pick a different task_id"
        )
        return plan

    # Disallow silent overwrite of prior worker state / instruction. Pane
    # duplicate check above only sees live panes — a prior task that ran and
    # exited would leave orphan files here and a re-used task_id would clobber
    # them. Caller must clean up (or rename) to replay.
    seed_path = state_dir / "workers" / f"{worker_name}.md"
    instr_path = state_dir / "foreman" / "outbox" / f"{task_id}-instruction.md"
    for existing in (seed_path, instr_path):
        if existing.exists():
            plan.status = "input_invalid"
            plan.errors.append(
                f"state file {str(existing)!r} already exists for task_id "
                f"{task_id!r}; remove it or pick a different task_id"
            )
            return plan

    choice = choose_split(panes)
    if choice is None:
        plan.status = "split_capacity_exceeded"
        plan.escalate = {
            "tool": "send_message",
            "to_id": "secretary",
            "message": (
                f"SPLIT_CAPACITY_EXCEEDED: {task_id} のワーカー分割対象が"
                "見つからない。rect ベース balanced split の MIN_PANE / 隣接条件を"
                "満たす候補が 0。ターミナルサイズ不足または想定外のレイアウトが"
                "疑われる。人間判断が必要です。"
            ),
        }
        return plan

    permission_mode = task.get("permission_mode", "auto")
    model = task.get("model") or DEFAULT_WORKER_MODEL
    extra_args = task.get("args") or []

    spawn: dict[str, Any] = {
        "tool": "spawn_claude_pane",
        "target": choice.target_name,
        "direction": choice.direction,
        "name": worker_name,
        "role": "worker",
        "cwd": cwd,
        "permission_mode": permission_mode,
        "model": model,
    }
    if extra_args:
        spawn["args"] = list(extra_args)
    plan.spawn = spawn

    # after_spawn steps Foreman should perform in order
    plan.after_spawn = [
        {
            "tool": "poll_events",
            "reason": "wait for pane_started",
            "types": ["pane_started"],
            "expect_name": worker_name,
            "deadline_ms": 3000,
        },
        {
            "tool": "send_keys",
            "target": worker_name,
            "enter": True,
            "reason": "approve 'Load development channel?' Y/n prompt",
        },
        {
            "tool": "list_peers",
            "reason": (f"wait for {worker_name} to appear as a peer "
                       "(retry up to ~30s)"),
            "expect_peer": worker_name,
        },
        {
            "tool": "send_message",
            "to_id": worker_name,
            "message_file": str(
                state_dir / "foreman" / "outbox"
                / f"{task_id}-instruction.md"
            ),
            "reason": "deliver task instruction",
        },
    ]

    # Files the helper writes as a side-effect (callers read these back when
    # they execute the plan; we list them here so the plan is auditable)
    plan.state_writes = [
        str(state_dir / "workers" / f"{worker_name}.md"),
        str(state_dir / "foreman" / "outbox" / f"{task_id}-instruction.md"),
    ]

    return plan


# ----------------------------------------------------------------------------
# Side-effect writers
# ----------------------------------------------------------------------------


def write_worker_seed(
    state_dir: Path, task: dict[str, Any], task_id: str,
    spawn: dict[str, Any],
) -> Path:
    target = state_dir / "workers" / f"worker-{task_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Worker: worker-{task_id}\n"
        f"Task: {task_id}\n"
        f"Directory: {spawn['cwd']}\n"
        f"Pane Name: worker-{task_id}\n"
        f"Status: planned\n"
        "\n"
        "## Assignment\n"
        f"{task.get('task_description', '(no description provided)')}\n"
        "\n"
        "## Progress Log\n"
        "- [planned by foreman_runner] pane not yet spawned\n"
    )
    target.write_text(body, encoding="utf-8")
    return target


def write_instruction(
    state_dir: Path, task: dict[str, Any], task_id: str,
) -> Path:
    target = state_dir / "foreman" / "outbox" / f"{task_id}-instruction.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    instruction = task.get("instruction") or task.get("task_description") or ""
    body = (
        f"# Task: {task_id}\n"
        "\n"
        "窓口からの指示を元にフォアマンが展開したワーカー向け作業指示。\n"
        f"作業ディレクトリ: `{task.get('worker_dir') or task.get('cwd')}`\n"
        "\n"
        "## 指示内容\n"
        f"{instruction}\n"
    )
    target.write_text(body, encoding="utf-8")
    return target


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _load_json(source: Optional[str], stdin: bool) -> Any:
    if stdin:
        return json.loads(sys.stdin.read())
    if source is None:
        raise SystemExit("missing JSON source (pass a path or use stdin)")
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _parse_panes(panes_data: Any) -> list[Pane]:
    if isinstance(panes_data, dict) and "panes" in panes_data:
        panes_list = panes_data["panes"]
    else:
        panes_list = panes_data
    if not isinstance(panes_list, list):
        raise SystemExit("panes JSON must be a list or {panes: [...]} object")
    return [Pane.from_dict(d) for d in panes_list]


def cmd_delegate_plan(args: argparse.Namespace) -> int:
    task = _load_json(args.task_json, stdin=args.task_stdin)
    if not isinstance(task, dict):
        print("task JSON must be an object", file=sys.stderr)
        return 1

    panes_raw = _load_json(args.panes_json, stdin=False)
    panes = _parse_panes(panes_raw)

    state_dir = Path(args.state_dir).resolve()

    plan = build_plan(task, panes, state_dir)

    if plan.status == "ready_to_spawn" and not args.dry_run:
        # Only write side-effect files when we actually have a plan and the
        # caller is not in dry-run mode. Worker state + instruction file.
        write_worker_seed(state_dir, task, plan.task_id, plan.spawn or {})
        write_instruction(state_dir, task, plan.task_id)

    json.dump(dataclasses.asdict(plan), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

    if plan.status == "input_invalid":
        return 1
    if plan.status == "split_capacity_exceeded":
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Foreman state-machine helper for claude-org"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    dp = sub.add_parser(
        "delegate-plan",
        help=("compute a worker delegation action plan from a task JSON "
              "and a list_panes snapshot"),
    )
    task_group = dp.add_mutually_exclusive_group(required=True)
    task_group.add_argument(
        "--task-json", help="path to the task JSON file",
    )
    task_group.add_argument(
        "--task-stdin", action="store_true",
        help="read task JSON from stdin",
    )
    dp.add_argument(
        "--panes-json", required=True,
        help=("path to a JSON file with renga `list_panes` output "
              "(a list of pane dicts, or {panes: [...]})"),
    )
    dp.add_argument(
        "--state-dir", default=".state",
        help="state directory root (default: .state)",
    )
    dp.add_argument(
        "--dry-run", action="store_true",
        help="do not write worker seed / instruction files; just print the plan",
    )
    dp.set_defaults(func=cmd_delegate_plan)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
