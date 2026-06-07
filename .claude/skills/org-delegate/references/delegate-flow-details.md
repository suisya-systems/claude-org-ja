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

**Pattern B sub-mode — `live_repo_worktree` (Issue #289)**: when the role
resolves to `claude-org-self-edit` (i.e. the project is claude-org itself and
mode is `edit`), the resolver automatically substitutes the worktree base
with `{claude_org_root}/.worktrees/{task_id}/` and sets
`pattern_variant='live_repo_worktree'`. This codifies the de facto convention
used by all claude-org self-edit workers since session #11 (single `.git/`
shared between Secretary and worker — no two-clone sync). See
`references/claude-org-self-edit.md` §3 for rationale and TOML override
shape.

`queued` is included in the active set because Issue #283's T1 reservation
writes that status before any pane spawns. Two back-to-back delegations on
the same project would otherwise both choose Pattern A and collide on the
base clone.

`A` reuse vs new clone is not a Pattern split — both share the same
`worker_dir` shape. Stage 3 `apply` checks the filesystem at execute time
and either reuses or clones.

---

## 1.5 Pre-dispatch verification checks (Secretary, manual)

These checks are **not** performed by `gen_delegate_payload.py`. Secretary
runs them by hand before `preview` / `apply`. When a check fails, the
dispatch is **not viable**: do not `apply` — resolve the cause on the
Secretary side (commit the base, fix the citations, re-scope) or escalate
to the user, then restart from Step 0.

### (a) Committed-base existence check

The worker only sees the **committed** base: Pattern B worktrees are cut
from committed history (`origin/HEAD` or local main) and Pattern A clones
likewise. Uncommitted live-tree state is invisible to the worker.

- **File existence — always**: for every `--target`, confirm the path
  exists in the committed base the worker will receive, e.g.
  `git -C <project_path> cat-file -e <base>:<target-path>`. A target that
  exists only as an uncommitted live-tree file means the delegation is not
  viable as written — commit first, or drop the target.
- **Line existence — only when the input carries line numbers**: when the
  delegation input includes line-numbered review findings or a patch (a
  Codex review file citing `file:line`, a PR review comment, a diff to
  apply), additionally verify the cited lines exist in the committed base
  (`git -C <project_path> show <base>:<path>` and inspect the cited
  region). Delegations without line-numbered inputs skip this — there is
  no input surface to validate (Codex design review Blocker 1).
- **Live-tree-only edits are not delegable**: if the requested change
  targets uncommitted modifications in the Secretary's live tree, the
  delegation fails by construction. Return it to the Secretary flow and
  commit (with user approval where required) before re-delegating.

### (b) Contracts grep for org-behavior changes

When the delegation changes **org behavior** — cadence, lifecycle (spawn /
close / retire), or responsibility boundaries between roles — grep
`docs/contracts/` for the behavior keywords the task touches, e.g.:

```bash
grep -rn -E "loop|cadence|curator|close|spawn|blocking|monitor" docs/contracts/
```

Pick keywords from the task description (the list above is a starting set,
not exhaustive). For every hit:

1. Read the matching contract section and **follow the cited sources** the
   contract points at (`.dispatcher/CLAUDE.md`,
   [`.dispatcher/references/worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md),
   etc.). The contract docs are the minimum bar; the SoT for cadence /
   non-blocking-wait behavior lives on the dispatcher side (Codex design
   review Major 1).
2. Carry the hits into the brief via `--knowledge <path>` (plus a short
   `--impl-guidance` summary when the worker must obey a specific clause).
   **Never add contract docs to `--target`** — that contaminates the edit
   scope (Codex design review Major 2).

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

## 4. When the standard path returns unexpected output

If `gen_delegate_payload.py apply` errors or produces a wrong layout
(Pattern misjudgment / resolver error / brief inconsistency / etc.),
Secretary **must not** reproduce the work by hand. The canonical response
is to file an Issue against `gen_delegate_payload.py` (or its resolver)
and pause the affected delegation **until the underlying bug is fixed**.
Whether to invoke any manual workaround is a user judgment call;
Secretary must not self-grant the exception. The standard path's own
degraded mode (`--skip-settings` for runtime-CLI-less environments) is
the supported way to keep going without leaving the skill.

The pre-Issue-283 hand-typed procedure has been moved out of the active
skill to `docs/legacy/hand-typed-delegate-path.md` as a museum copy. That
document is for archaeological reference only; reaching it during normal
operation is a protocol violation. Note that the legacy procedure itself
depends on `claude-org-runtime settings generate`, so it is not a valid
fallback for runtime-CLI-unavailable scenarios either. Failure modes
historically introduced by the legacy reach (settings env mismatch,
drift_check breakage, T1 reservation skipped) are listed there.
