# org-delegate: detailed flow reference

`gen_delegate_payload.py` (Issue #283) automates Steps 0.7 / 1 / 1.5 / 2 of the
`org-delegate` skill. The compressed steps in `SKILL.md` cover the day-to-day
path; this document is the long-form explanation Secretary consults when the
automation hits an edge case or when reviewing why the resolver chose a
particular Pattern / Role.

The contract this entire flow conforms to lives in
`docs/contracts/delegation-lifecycle-contract.md` (Set B). Per that contract,
this skill's writes are limited to **T1 reservation only**: a `runs.status='queued'`
row plus the `worker_dirs` registry entry, plus the brief and settings files
on disk. **Active Work Items remains the dispatcher's T2 responsibility** and is
not touched here.

---

## 1. Pattern judgment (Step 0.7 + Step 1)

### Step 0.7 — gitignored target check (highest-precedence)

The resolver checks each `--target` against `git -C <project_path> check-ignore -q --`.
If any target is ignored, the dispatch is forced into **Pattern C — gitignored
sub-mode** (`pattern_variant='gitignored_repo_root'`):

- `worker_dir` is the registered project's repo root (not `{workers_dir}/{task_id}/`).
- The brief is written as `CLAUDE.local.md` (never `CLAUDE.md`) so the host
  repo's own `CLAUDE.md` is preserved.
- `planned_branch` is `null` — the worker operates on the existing branch.
- Secretary must serialise: do not run two gitignored-sub-mode workers
  concurrently against the same repo (file-name collisions on
  `CLAUDE.local.md` / `.claude/settings.local.json`).

The check is skipped when the project's path is a URL, `-`, or otherwise not a
local git repo. When skipped, the normal Pattern A/B judgment runs.

`git check-ignore` matches `.gitignore` rules even when the file does not yet
exist; do not substitute `ls-files --error-unmatch` (which would treat any new
file as untracked and silently route it to Pattern C).

### Step 1 — Pattern A vs B vs C

When Step 0.7 does not force the gitignored sub-mode, the resolver reads
state.db via `runs JOIN worker_dirs` (matching the snapshotter's view) and
uses the active-status set `{queued, in_use, review}`:

| Condition | Pattern | Worker dir |
|---|---|---|
| Project not in `registry/projects.md` | C — ephemeral | `{workers_dir}/{task_id}/` |
| Project in registry, ≥1 active run on this project | B — worktree | `{workers_dir}/{project_slug}/.worktrees/{task_id}/` |
| Project in registry, no active run | A — project dir | `{workers_dir}/{project_slug}/` |

`queued` is included in the active set because Issue #283's T1 reservation
writes that status before any pane spawns. Two back-to-back delegations on
the same project would otherwise both choose Pattern A and collide on the
base clone.

`A` reuse vs new clone is not a Pattern split — both share the same
`worker_dir` shape. Stage 3 `apply` checks the filesystem at execute time
and either reuses or clones.

---

## 2. Role detection (Step 1.5 — "Role の選び方")

| `--mode` | Project = claude-org | Project ≠ claude-org |
|---|---|---|
| `edit` (default) | `claude-org-self-edit` | `default` |
| `audit` | `doc-audit` | `doc-audit` |

`--mode audit` always selects `doc-audit` (read-only Edit/Write/MultiEdit denies)
regardless of which project is being inspected. This avoids the historical
mistake of misclassifying a claude-org **read** as a self-edit and granting
write hooks the worker doesn't need (Codex Design Review M-4).

"Project = claude-org" means the registry row's path resolves to the same
directory as `claude_org_root` (resolved absolute path comparison; the
`gen_delegate_payload` resolver handles this).

Pattern C `gitignored_repo_root` additionally forces the brief filename to
`CLAUDE.local.md` even when the role is `default` — we are inside someone
else's repo and must not clobber their `CLAUDE.md`. This decouples
"writes-self-edit-style brief" from "is the claude-org-self-edit role".

---

## 3. DELEGATE body template (Step 2)

`gen_delegate_payload.py` formats the body to match the historical template
exactly. The required rows, in order, are:

1. `DELEGATE: 以下のワーカーを派遣してください。`
2. `タスク一覧:` header followed by `- {task_id}: {description}` line.
3. `- ワーカーディレクトリ:` row.
4. `- ディレクトリパターン:` row (carries the variant label for Pattern C
   sub-modes).
5. `- プロジェクト:` row (clone source / reuse / worktree base).
6. `- ブランチ (planned):` row (null for Pattern C).
7. `- Permission Mode:` row (read from `registry/org-config.md`).
8. `- 検証深度:` row (`full` or `minimal`, matching the value Secretary
   passed to `--verification-depth`).
9. `- 指示内容:` row pointing the dispatcher at `CLAUDE.md` /
   `CLAUDE.local.md` plus a one-line summary.
10. `窓口ペイン名: secretary` trailer.

The rows are not optional. The script's snapshot tests
(`tests/fixtures/delegate_payload/`) lock this format down so that the
historical "verification_depth row dropped" failures stay impossible.

---

## 4. Legacy hand-typed paths

Two pre-Issue-283 paths remain supported for callers that already work in
that idiom:

- `python tools/gen_worker_brief.py --config <path>.toml --out <CLAUDE.md>`
  — the original brief renderer. Still works exactly as before. New code
  should prefer the `from-task` subcommand because it derives `worker.dir`
  / `worker.pattern` / `worker.role` deterministically from registry and
  state.db rather than asking the operator to fill them in.
- Manually issuing the `DELEGATE:` message via `mcp__renga-peers__send_message`
  — fine for one-off ad-hoc dispatches. The `gen_delegate_payload preview`
  command can still be used to draft the body without writing anything.

Both paths skip the T1 reservation and therefore do not surface the queued
state to the dispatcher's watch loop. Use them sparingly.
