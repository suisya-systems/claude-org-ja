"""Dispatcher state-machine helper for claude-org (Issue #60).

v1 scope: `delegate-plan` subcommand. Given a structured task description
and a renga `list_panes` snapshot, this script computes the deterministic
parts of the Dispatcher delegation state machine and emits a JSON action plan
that Dispatcher Claude reads and executes via MCP tool calls.

The helper does NOT call MCP tools directly. Dispatcher remains the actor that
receives Secretary's DELEGATE, invokes this helper, reads the returned plan,
and performs the `spawn_claude_pane` / `send_keys` / `send_message` / etc.
calls.

Deterministic operations this helper owns:
  - balanced split target/direction selection from pane rects
  - task / worker name validation (matches renga's `[A-Za-z0-9_-]` + not all-digit)
  - worker instruction file writing (.state/dispatcher/outbox/{task_id}-instruction.md)
  - worker state file seed (.state/workers/worker-{task_id}.md)
  - journal planned-event preparation

Everything that requires live MCP calls stays in the Claude side.

Usage:
  py -3 tools/dispatcher_runner.py delegate-plan \
      --task-json .state/dispatcher/inbox/{task_id}.json \
      --panes-json <(mcp_list_panes_output.json)

  # stdin form:
  cat task.json | py -3 tools/dispatcher_runner.py delegate-plan \
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

# Path of the instruction template, relative to the repo root (parent of tools/).
# The helper extracts the strict-format code fence between the AUTO-EXPAND
# markers and substitutes {var} placeholders from task.instruction_vars.
INSTRUCTION_TEMPLATE_PATH = (
    ".claude/skills/org-delegate/references/instruction-template.md"
)
_TEMPLATE_START_MARKER = "<!-- AUTO-EXPAND-TEMPLATE-START -->"
_TEMPLATE_END_MARKER = "<!-- AUTO-EXPAND-TEMPLATE-END -->"

# Variables understood by the auto-expand template. Keep in sync with
# instruction-template.md "## 自動展開テンプレート" section.
# branch_strategy is required: defaulting it would silently mis-instruct
# Pattern B (worktree) workers to commit on main.
_REQUIRED_VARS = (
    "task_description", "dir_setup", "branch_strategy", "verification_depth",
)
_OPTIONAL_VARS = {
    "constraints": "(なし)",
    "report_target": "secretary",
    # claude-org self-edit reads CLAUDE.local.md instead of CLAUDE.md
    # (root CLAUDE.md belongs to Secretary). Caller passes "CLAUDE.local.md"
    # for self-edit tasks; everyone else gets the default.
    "claude_md_filename": "CLAUDE.md",
}
_ALLOWED_VARS = set(_REQUIRED_VARS) | set(_OPTIONAL_VARS)
_VERIFICATION_DEPTHS = ("full", "minimal")


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
        if p.role not in ("secretary", "dispatcher", "worker"):
            continue

        # dispatcher: only if adjacent to curator (keeps dispatcher-curator pair intact)
        if p.role == "dispatcher":
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
# Instruction template auto-expansion (Issue #71)
# ----------------------------------------------------------------------------


def _repo_root() -> Path:
    # tools/dispatcher_runner.py → repo root is this file's parent.parent.
    return Path(__file__).resolve().parent.parent


def load_instruction_template(repo_root: Optional[Path] = None) -> str:
    """Read and extract the strict-format template body from instruction-template.md.

    Returns the raw template string with `{var}` placeholders intact (caller
    is expected to substitute them). Raises ValueError if the markers or
    fenced block cannot be located.
    """
    root = repo_root or _repo_root()
    src = (root / INSTRUCTION_TEMPLATE_PATH).read_text(encoding="utf-8")
    start = src.find(_TEMPLATE_START_MARKER)
    end = src.find(_TEMPLATE_END_MARKER)
    if start < 0 or end < 0 or end <= start:
        raise ValueError(
            f"AUTO-EXPAND markers not found in {INSTRUCTION_TEMPLATE_PATH}"
        )
    section = src[start + len(_TEMPLATE_START_MARKER):end]
    fence_open = section.find("```")
    if fence_open < 0:
        raise ValueError("opening code fence missing in auto-expand section")
    body_start = section.find("\n", fence_open) + 1
    fence_close = section.find("```", body_start)
    if fence_close < 0:
        raise ValueError("closing code fence missing in auto-expand section")
    return section[body_start:fence_close].rstrip("\n")


def validate_instruction_vars(
    raw: Any,
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """Normalize and validate `instruction_vars`. Returns (vars, error)."""
    if not isinstance(raw, dict):
        return None, "instruction_vars must be a JSON object"
    # Stringify values (numbers etc. allowed but normalized to str)
    norm: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            return None, f"instruction_vars key {k!r} is not a string"
        if v is None:
            return None, f"instruction_vars[{k!r}] is null"
        norm[k] = str(v)

    unknown = sorted(set(norm) - _ALLOWED_VARS)
    if unknown:
        return None, (
            f"instruction_vars contains unknown keys: {unknown}; "
            f"allowed: {sorted(_ALLOWED_VARS)}"
        )

    missing = [k for k in _REQUIRED_VARS if not norm.get(k, "").strip()]
    if missing:
        return None, f"instruction_vars missing required keys: {missing}"

    depth = norm["verification_depth"].strip()
    if depth not in _VERIFICATION_DEPTHS:
        return None, (
            f"instruction_vars.verification_depth must be one of "
            f"{list(_VERIFICATION_DEPTHS)}, got {depth!r}"
        )
    norm["verification_depth"] = depth

    # Apply optional defaults so the template substitution always succeeds
    for k, default in _OPTIONAL_VARS.items():
        if not norm.get(k, "").strip():
            norm[k] = default
    return norm, None


def render_instruction(
    instruction_vars: dict[str, str],
    repo_root: Optional[Path] = None,
) -> str:
    template = load_instruction_template(repo_root=repo_root)
    return template.format_map(instruction_vars)


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

    # Validate instruction / instruction_vars early so input errors surface
    # before any side-effects. Truthiness (not key-presence) decides whether
    # an explicit `instruction` wins, matching write_instruction below — so a
    # blank `instruction=""` falls through to instruction_vars expansion
    # instead of silently writing an empty outbox file.
    has_explicit = bool(str(task.get("instruction") or "").strip())
    has_vars = "instruction_vars" in task
    if not has_explicit and has_vars:
        norm_vars, vars_err = validate_instruction_vars(task["instruction_vars"])
        if vars_err:
            plan.status = "input_invalid"
            plan.errors.append(vars_err)
            return plan
        try:
            # Validate template renders cleanly. Keep result on the task dict
            # so write_instruction doesn't have to re-render.
            task["_rendered_instruction"] = render_instruction(norm_vars)
        except (KeyError, ValueError, OSError) as exc:
            plan.status = "input_invalid"
            plan.errors.append(
                f"failed to render instruction template: {exc}"
            )
            return plan
    elif has_explicit and has_vars:
        plan.warnings.append(
            "both `instruction` and `instruction_vars` provided; "
            "explicit `instruction` wins, `instruction_vars` ignored"
        )

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
    instr_path = state_dir / "dispatcher" / "outbox" / f"{task_id}-instruction.md"
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

    # after_spawn steps Dispatcher should perform in order
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
                state_dir / "dispatcher" / "outbox"
                / f"{task_id}-instruction.md"
            ),
            "reason": "deliver task instruction",
        },
    ]

    # Files the helper writes as a side-effect (callers read these back when
    # they execute the plan; we list them here so the plan is auditable)
    plan.state_writes = [
        str(state_dir / "workers" / f"{worker_name}.md"),
        str(state_dir / "dispatcher" / "outbox" / f"{task_id}-instruction.md"),
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
        "- [planned by dispatcher_runner] pane not yet spawned\n"
    )
    target.write_text(body, encoding="utf-8")
    return target


def write_instruction(
    state_dir: Path, task: dict[str, Any], task_id: str,
) -> Path:
    target = state_dir / "dispatcher" / "outbox" / f"{task_id}-instruction.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Mirror build_plan's whitespace-aware check: a blank `instruction` must
    # not crowd out a rendered template body.
    explicit = str(task.get("instruction") or "")
    instruction = (
        explicit if explicit.strip() else (
            task.get("_rendered_instruction")
            or task.get("task_description")
            or ""
        )
    )
    body = (
        f"# Task: {task_id}\n"
        "\n"
        "窓口からの指示を元にディスパッチャーが展開したワーカー向け作業指示。\n"
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
        description="Dispatcher state-machine helper for claude-org"
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
