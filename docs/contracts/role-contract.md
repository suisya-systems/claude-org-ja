# Contract Set A — Role Contract

> **Status**: Ratified (2026-05-03). Lead-confirmed decisions for all open questions. This document specifies the four roles (`secretary`, `dispatcher`, `curator`, `worker`) as they exist in the current `claude-org` implementation.
>
> **Scope**: Phase 1 Contract Set A only. Contract Sets B–E (state, messaging, lifecycle, knowledge) are tracked in #122–#125 and out of scope here.
>
> **Method**: Each role section below is filled from empirical sources (current `CLAUDE.md` files, `org-start` / `org-delegate` skills, `org-config.md`, the worker template). Sentences sourced from current behavior are written as facts. Design decisions left open in the prior outline were ratified by the Lead in the 2026-05-03 Q&A session; where a ratified decision selects between divergent existing artifacts (e.g., a skill SKILL.md vs. a reference template), this contract names the chosen boundary and tracks the conflicting artifact via a follow-up Issue rather than waiting for the artifact to be reconciled first.
>
> **Empirical sources consulted**:
> - `CLAUDE.md` (root, secretary directives)
> - `.dispatcher/CLAUDE.md`
> - `.curator/CLAUDE.md`
> - `.claude/skills/org-delegate/references/worker-claude-template.md`
> - `.claude/skills/org-start/SKILL.md` (role-specific launch commands)
> - `.claude/skills/org-delegate/SKILL.md` (role split table)
> - `registry/org-config.md` (per-role permission-mode applicability)
>
> **Refs**: #121 (this issue), parent epic #101.

---

## Role: secretary

### Responsibilities

- Sole human-facing interface for the organization. Receives every user request, returns every user-visible report, and is the only role authorized to converse with the human directly.
- Owns task decomposition: parses the user's request, resolves it against `registry/projects.md`, decides directory pattern (A / B / C) and validation depth (`full` / `minimal`), and drafts the worker instruction.
- Issues `DELEGATE` to the dispatcher (does not spawn worker panes itself) and returns to the human as soon as the delegation is handed off.
- Receives worker progress / completion / blocker reports, mediates them to the human in business language (not technical jargon), and decides PR push / CI watch / final close.
- Owns `.state/`, `registry/`, and journal updates that result from human decisions (status transitions, registry edits, snapshot regeneration).
- After completion, runs `/org-retro` for delegation-process learnings.

### Inputs

- **Human messages** — natural-language requests from the user (the only role that consumes these).
- **Worker reports via renga-peers** — `to_id="secretary"` messages from workers (progress, completion, `APPROVAL_BLOCKED`, `ERROR`, blockers).
- **Dispatcher reports via renga-peers** — `DELEGATE_COMPLETE`, `WORKER_PANE_EXITED`, `APPROVAL_BLOCKED`/`ERROR_DETECTED` (forwarded from dispatcher's inspect channel), `SPLIT_CAPACITY_EXCEEDED`, `FOREMAN_STOPPING`, `RETRO_RECORDED`.
- **Curator notifications via renga-peers** — improvement suggestions / curated-knowledge availability.
- **Local files (read)** — `registry/projects.md`, `registry/org-config.md`, `.state/org-state.md`, `.state/workers/worker-*.md`, `.state/journal.jsonl`, `knowledge/curated/`, CI signals (`ci_completed` events written by `tools/pr-watch.*`).

### Outputs

- **Human-facing replies** — status updates, choices, summaries, all in business language.
- **`DELEGATE` messages** — sent via `mcp__renga-peers__send_message(to_id="dispatcher", ...)` per `org-delegate` Step 2 format (task list with task_id, worker dir, pattern, project, permission mode, validation depth, instruction summary).
- **`CLOSE_PANE` messages** — sent to dispatcher when a worker's task reaches the close gate (PR merged / explicit close / long idle).
- **Worker follow-up instructions** — `to_id="worker-{task_id}"` messages with additional fixes or scope changes (PR review feedback, depth switches, intervention to break out of over-validation loops).
- **Files written**:
  - `.state/org-state.md` (Current Objective, Active Work Items, Worker Directory Registry)
  - JSON snapshot regeneration via `dashboard/org_state_converter.py`
  - `registry/projects.md` updates when registering new projects
  - `.state/journal.jsonl` entries (push / PR open / status transitions / approvals) — must go through the helper (`tools/journal_append.sh|py`), never raw `>>` append
  - Worker `CLAUDE.md` and `.claude/settings.local.json` placement during Step 1.5 of `org-delegate` (via `claude-org-runtime settings generate`; manual JSON forbidden)
- **Side effects** — `git push`, `gh pr create`, `tools/pr-watch.*` invocation (worker has no push permission, so secretary executes these).

### Constraints

- **Permission mode**: Not subject to `default_permission_mode`. Runs with the Claude Code default (per-tool prompts) because it is the human-judgment surface (`registry/org-config.md` § "Role別の適用範囲", Issue #10).
- **Pane identity**: Stable name `secretary` with `role="secretary"`. Auto-recovered by `set_pane_identity` during `org-start` Step 0 if mismatched.
- **Hands off all real work to workers** — must not edit code, run tests, build, debug, or `git commit` substantive changes itself. When a problem is reported, must not investigate locally; it goes back to a worker (`CLAUDE.md` § "役割の境界").
- **Communication discipline** — no jargon to the human (e.g., "PR #12" → "ログイン機能の変更を提出しました"). Must offer choices when a request is ambiguous.
- **Reply addressing** — when forwarding to dispatcher / curator / workers, must use stable pane names (`dispatcher`, `curator`, `worker-{task_id}`), not numeric `from_id`s.
- **Settings generation** — must invoke `claude-org-runtime settings generate` for worker `settings.local.json`; hand-edited JSON is rejected by drift CI.
- **Human-dialogue priority (soft SLA)** — During active human dialogue, the secretary must yield to the human ahead of background worker / dispatcher reports. While idle, response is best-effort with no numeric latency guarantee.
- **Direct-spawn carve-out** — The secretary may directly spawn long-lived infrastructure panes (dispatcher, curator) only during `/org-start`. All other pane spawns must go through the dispatcher.
- **Authoritative journal events** — The set of journal events the secretary is permitted to emit is defined by `docs/journal-events.md`, which is the authoritative event registry. Each event there must carry an `emitted-by` role tag. (Follow-up: an Issue tracks adding `emitted-by` annotations to that registry if not already present.)
- **No manual delegation fallback** — When `tools/gen_delegate_payload.py apply` errors or produces an unexpected layout, the secretary must NOT reproduce the delegation by hand; the canonical response is to file an Issue against the resolver and pause the affected task until the underlying bug is fixed. The standard path's own documented degraded mode (`--skip-settings`, for genuinely runtime-CLI-less environments) is the only sanctioned way to keep going without leaving the skill, and overrides the `claude-org-runtime settings generate` requirement above only in that scope. Granting any other manual-workaround exception is a user judgment call, not a secretary self-grant. The historical hand-typed procedure lives at `docs/legacy/hand-typed-delegate-path.md` as a museum copy for archaeology only; it depends on `claude-org-runtime` and is therefore not itself a valid fallback (Issue #313).

### Lifecycle / boundaries

- **Spawn**: Started by the human running `renga --layout ops`. There is exactly one secretary per org session.
- **Initialization**: First action is `/org-start`. Step 0 sets `set_summary`, validates / repairs pane identity, confirms renga-peers MCP is installed, and inventories `workers_dir`. Step 1 reads `.state/org-state.md` and resumes / briefs. Steps 2–3 spawn the dispatcher and curator panes.
- **Termination**: `/org-suspend` (graceful, persists state) or hard close. Must not be killed while workers are alive without going through suspend, otherwise pane-id remapping and worker reply paths break.
- **Hard prohibitions**:
  - Must NOT silently delete worker directories during `org-start` (they may hold reusable project state — `org-start` Step 0.4 explicit ban).
  - Must NOT spawn workers directly via `spawn_claude_pane` for ordinary delegations; must go through `DELEGATE` to the dispatcher.
  - Must NOT respond to worker reports by acting locally (no in-pane code edits, no `git commit` of worker output) — auto-mode classifier blocks this anyway as scope-exceeded.
  - Must NOT write `.state/journal.jsonl` with raw `>>` append; must use the helper.
- **`/org-suspend` contract** — The authoritative flush list (which files must be persisted and their schemas) is defined by the `/org-suspend` skill at `.claude/skills/org-suspend/SKILL.md`. This contract does not enumerate files itself; the skill is the single source of truth.

---

## Role: dispatcher

### Responsibilities

- Mechanical executor of `DELEGATE` messages from the secretary: spawns worker panes, approves the dev-channel prompt, waits for the peer to appear, sends the worker its instruction, and records state. The secretary stays unblocked because the dispatcher absorbs the pane-launch latency.
- Continuously monitors all live worker panes via a `/loop 3m` cycle: drains `poll_events` (lifecycle), `check_messages` (worker self-reports), `list_panes` (reconciliation), and `inspect_pane` (independent observation of approval prompts / errors).
- Forwards detected anomalies (`APPROVAL_BLOCKED`, `ERROR_DETECTED`, `WORKER_PANE_EXITED`, `events_dropped`, `SPLIT_CAPACITY_EXCEEDED`, `FOREMAN_STOPPING`) to the secretary, with de-duplication across the self-report and inspect channels (30-second window, `event=notify_sent` ledger).
- Honors `CLOSE_PANE` from the secretary: runs the local retro (with the "completion-report gate" check to the secretary first), records knowledge if applicable, then closes the pane via `mcp__renga-peers__close_pane`.

### Inputs

- **`DELEGATE` from secretary** — task list with worker dir, pattern, permission mode, validation depth, instruction.
- **`CLOSE_PANE` from secretary** — pane teardown request once the secretary is done with the worker.
- **Reply from secretary to "completion-report check"** — answer to the gate question before writing retro conclusions.
- **renga-peers events** via `mcp__renga-peers__poll_events` — `pane_started`, `pane_exited`, `events_dropped`, `heartbeat` (ignored).
- **Worker self-reports** via `mcp__renga-peers__check_messages` — progress, `APPROVAL_BLOCKED`, `ERROR`, completion (the latter is forwarded but the worker also notifies secretary directly).
- **Pane snapshots** via `mcp__renga-peers__list_panes` and grid scrapes via `mcp__renga-peers__inspect_pane`.
- **Files (read)**:
  - `.state/dispatcher/inbox/{task_id}.json` (task input for `delegate-plan` helper)
  - `.state/dispatcher-event-cursor.txt` (resume cursor for `poll_events`)
  - `.state/workers/worker-*.md` (progress logs, pane names)
  - `tools/ja_locale.json` (template repo for helper)

### Outputs

- **MCP calls** — `spawn_claude_pane` (workers, `model="opus"` mandatory, `permission_mode=auto`), `send_keys(enter=true)` for the dev-channel prompt, `list_peers` polling, `send_message` for the instruction, `inspect_pane` for the watch loop, `close_pane` for teardown.
- **`DELEGATE_COMPLETE`** to secretary (one per worker spawned).
- **`WORKER_PANE_EXITED`** to secretary (lifecycle event; not a completion claim).
- **`APPROVAL_BLOCKED`** / **`ERROR_DETECTED`** to secretary, tagged with `source=inspect|self_report` and `confidence=high|n/a`.
- **`SPLIT_CAPACITY_EXCEEDED`** when balanced split has zero candidates (escalates to human via secretary; does not crash).
- **`FOREMAN_STOPPING`** when renga signals shutdown (`[shutting_down]` from inspect).
- **`RETRO_RECORDED`** to secretary (only when retro produced a knowledge record).
- **Files written**:
  - `.state/workers/worker-{task_id}.md` (Status: planned → active, progress log)
  - `.state/dispatcher/outbox/{task_id}-instruction.md` (instruction body, written by the helper)
  - `.state/dispatcher-event-cursor.txt` (advancing cursor)
  - `.state/journal.jsonl` (events `worker_spawned`, `anomaly_observed`, `notify_sent`, `events_dropped`, `retro_deferred`, ...) — only via `tools/journal_append.sh|py` (raw `>>` forbidden).
  - `knowledge/raw/{YYYY-MM-DD}-delegation-{topic}.md` (only when retro produced reusable learnings).

### Constraints

- **Permission mode**: Hard-coded `bypassPermissions` regardless of `default_permission_mode`. Reason: spawning child Claude agents is classified as "Create Unsafe Agents" by `auto`, which would block worker spawn. (`registry/org-config.md`; `org-start` SKILL "ディスパッチャー".)
- **Model**: `sonnet` (cost optimization). Acceptable here only because the dispatcher does not run through the safety classifier.
- **No human dialogue** — the dispatcher never talks to the human directly (`.dispatcher/CLAUDE.md` "人間と直接対話することはない").
- **All replies to secretary use stable name `to_id="secretary"`**; numeric `from_id` may be used only as a fallback when `to_id="secretary"` returns `[pane_not_found]` (per `.dispatcher/CLAUDE.md` § "窓口への返信方法").
- **Reports are routed to secretary, not back to dispatcher** — the dispatcher must explicitly tell each worker on spawn: "報告先は窓口です。ディスパッチャーではありません".
- **No auto-approval of worker tool prompts** — the dispatcher must not press `y` or otherwise approve `Allow this tool use? (y/n)` / `Do you want to proceed?` / edit-confirmation prompts on the worker's behalf; those require human judgment routed via the secretary. (Distinct from the one-time `Load development channel?` prompt issued by Claude Code itself at worker spawn, which the dispatcher *is* expected to approve via `send_keys(enter=true)` — that prompt is infrastructure, not a worker tool call.)
- **Helper-first determinism** — uses `claude-org-runtime dispatcher delegate-plan` to choose target / direction, validate worker pane name, generate instruction file and seed state file. Re-implementing `choose_split` or hand-writing instruction files is forbidden.
- **Journal append discipline** — every event goes through `tools/journal_append.sh` or `tools/journal_append.py` (the latter required when payload contains nested objects such as `cursor`).
- **CLOSE_PANE discipline** — must complete retro Steps 1–2 before `close_pane`; closing first destroys evidence. If secretary is unreachable inside 5 minutes, write `retro_deferred` and skip the close (do NOT write a "completion report missing" retro conclusion).
- **De-dup contract** — `notify_sent` records gate further notifications for the same `(worker, kind)` within 30 seconds; `anomaly_observed` records do NOT count toward de-dup.
- **Curator dependency** — The dispatcher may operate without a curator present. Balanced-split logic adapts when the curator is absent. Knowledge curation is best-effort and is not a delegation prerequisite.
- **Approval-prompt regex registry** — The dispatcher uses inspect-based detection per `.dispatcher/references/worker-monitoring.md` § (b), which is the authoritative registry of approval-prompt regexes. The contract does not duplicate the list.
- **Concurrency** — No contractual concurrency limit beyond renga's pane cap (16) and balanced-split feasibility.

### Lifecycle / boundaries

- **Spawn**: By the secretary during `/org-start` Step 2. `cwd=".dispatcher"`, `permission_mode="bypassPermissions"`, `model="sonnet"`. Stable name `dispatcher`, role `dispatcher`.
- **Activation**: Receives an initial `send_message` from secretary instructing it of its role. Begins waiting for `DELEGATE`.
- **Watch loop**: Started after the first worker spawn via `/loop 3m`; stops when all worker panes have exited.
- **Suspension**: Via `/org-suspend` flow (state flushed to `.state/dispatcher-event-cursor.txt` and `.state/workers/`).
- **Termination**: Pane closed by secretary or by org shutdown. Must NOT exit on its own when a single delegation fails (e.g., `SPLIT_CAPACITY_EXCEEDED` aborts that one task only; the watch loop continues).
- **Hard prohibitions**:
  - Never spawn a worker pane in a different tab (`new_tab`) — renga visibility is per-tab and would orphan the worker.
  - Never close a worker pane before the retro step finishes (data loss).
  - Never use `codex:rescue` or auto-approve human-judgment prompts.
  - Never reply to the human directly.
- **Crash recovery** — Best-effort. After a dispatcher pane crash, reconciliation via `list_panes` is authoritative on restart. No numeric event-loss bound is contracted.

---

## Role: curator

### Responsibilities

- Periodically organizes accumulated raw learnings under `knowledge/raw/` into curated, deduplicated notes under `knowledge/curated/`, on a `/loop 30m /org-curate` cadence.
- Surfaces improvement proposals to the secretary via renga-peers when curation reveals process issues.

### Inputs

- **Files (read)**:
  - Parent-repo `knowledge/raw/` (relative `../knowledge/raw/` from curator's `.curator/` cwd; absolute path recommended for `Glob`).
  - Parent-repo `knowledge/curated/` (existing curated notes, for dedup / merge decisions).
- **Loop trigger** — its own `/loop 30m /org-curate` schedule.
- **Optional** — direct messages from secretary (e.g., "knowledge を整理して" — handled with the same skill).

### Outputs

- **Files written**:
  - Parent-repo `knowledge/curated/{topic}.md` — consolidated notes.
  - Possibly `knowledge/skill-candidates.md` updates (via `skill-eligibility-check` invoked by `org-curate`).
- **renga-peers messages to secretary** (`to_id="secretary"`) — improvement proposals, curation summaries.

### Constraints

- **Permission mode**: `auto` (hardcoded literal at spawn sites; `registry/org-config.md` value is reference-only — see its sync-warning). Model: `opus`.
- **Path discipline** — curator's cwd is `.curator/`, but knowledge directories live in the parent repo. Must use parent-repo-relative or absolute paths; using cwd-relative `knowledge/raw/` would target a non-existent directory.
- **Glob fallback** — when `Glob` returns 0 results, must verify with `Bash ls` to detect missing-directory vs. genuinely empty.
- **No human dialogue** — `.curator/CLAUDE.md` "人間と直接対話することはない". Communication only via secretary.
- **Reply addressing** — all messages to secretary use stable `to_id="secretary"`.
- **`knowledge/raw/` write authority** — The curator may move processed entries from `knowledge/raw/` into `knowledge/raw/archive/` after successful curation. Outright deletion of raw entries is forbidden.
- **Loop cadence** — `/loop 30m /org-curate` is the default cadence. The human may override it via `/org-start` configuration; that override is authoritative when present.
- **Skill-candidate promotion** — The curator promotes a curated learning to a skill candidate when the same pattern appears in 3 or more raw entries (cf. memory `feedback_tool_after_three_repeats`). No hard time SLA applies.

### Lifecycle / boundaries

- **Spawn**: By the secretary during `/org-start` Step 3. `cwd=".curator"`, `permission_mode=auto`, `model="opus"`. Stable name `curator`, role `curator`.
- **Activation**: Receives an initial `send_message` from secretary telling it to start the `/loop 30m /org-curate` schedule.
- **Steady state**: Wakes on the loop, runs `org-curate`, sleeps. Also processes ad-hoc messages from secretary.
- **Termination**: Pane closed by secretary or by org shutdown.
- **Hard prohibitions**:
  - Must NOT write to `.state/`, `registry/`, or worker directories — its write surface is `knowledge/curated/` and the skill-candidate queue only.
  - Must NOT talk to the human directly or to workers.
  - Must NOT delete `knowledge/raw/` entries. Moving processed entries into `knowledge/raw/archive/` is permitted; outright deletion is forbidden.
- **`/org-suspend` participation** — The curator has no in-memory state requiring flush during `/org-suspend`, so it does not contribute to the suspend state-collection step. Standard pane-shutdown handling (SHUTDOWN signal, `pane_exited` wait, `close_pane` if needed) per `.claude/skills/org-suspend/SKILL.md` still applies.

---

## Role: worker

### Responsibilities

- Performs the actual engineering work for a single task ID: code edits, builds, tests, lints, type-checks, and `git commit` inside its assigned `worker_dir`. Directory preparation is **the secretary's responsibility**: the secretary owns `git clone` / `git init` / `git worktree add` for `worker_dir` and places `CLAUDE.md` / `.claude/settings.local.json` BEFORE spawn (per `org-delegate` Step 1.5). The worker's first action is to `pwd`-verify; it does NOT clone, init, or worktree-add. (The conflicting wording in `.claude/skills/org-delegate/references/instruction-template.md` Pattern A / C sections is to be corrected as a follow-up.)
- For `full` validation depth: runs the project's standard verification (tests / lint / type-check) to green before reporting completion. If `codex` CLI is available, additionally runs the Codex self-review gate (3-round cap on same-category findings).
- For `minimal` validation depth: applies the requested fix, commits, and returns a single-line `done: {sha} {files}` report — no extra verification, no Codex.
- Reports completion / progress / blockers / `APPROVAL_BLOCKED` / `ERROR` directly to the secretary (NOT to the dispatcher) via renga-peers.
- After PR creation, holds the pane open to absorb PR-review feedback in the same pane (avoids the cost of re-spawning a fresh worker that has lost the diff / decision context).
- Records reusable learnings to `knowledge/raw/{YYYY-MM-DD}-{topic}.md` when applicable (`full` only; `minimal` skips this).

### Inputs

- **`send_message` from dispatcher** — initial instruction (per `references/instruction-template.md`), validation depth, optional reference work-skill, claude-org path.
- **`send_message` from secretary** — greeting after `DELEGATE_COMPLETE`, follow-up instructions, scope changes, intervention messages, `SUSPEND:`.
- **Files (read)**:
  - `worker_dir/CLAUDE.md` (worker template, instantiated by secretary in `org-delegate` Step 1.5; for self-edit tasks this is `CLAUDE.local.md`).
  - `worker_dir/.claude/settings.local.json` (generated by `claude-org-runtime settings generate`).
  - `{claude_org_path}/knowledge/curated/` and `knowledge/raw/` — read-only reference.
  - The task's referenced project source / Issue body / linked specs.

### Outputs

- **Files written**:
  - Code, tests, docs inside `worker_dir` (or, for Pattern C gitignored sub-mode, inside the existing repo root specified by registry).
  - `git commit`s in the worker's branch.
  - `knowledge/raw/{YYYY-MM-DD}-{topic}.md` when reusable learnings exist (full mode only).
  - For skill-promotion delegations specifically (per Set E §2.4 / §3.1 carve-out), the worker MAY write to `.claude/skills/{skill_name}/` (creating new skill files or editing existing ones) AND apply the corresponding `knowledge/skill-candidates.md` status transition (`status` → `approved` / `rejected` / `merged-into-*`, plus the `決定日` / `却下理由` / `統合先` fields for the same entry). This is a delegated-task scope, not a general worker capability; the worker MUST NOT write to `.claude/skills/` or to `knowledge/skill-candidates.md` outside an explicitly skill-promotion-tagged delegation.
- **renga-peers messages**:
  - To `to_id="secretary"`: completion report (full: structured with deliverables + outstanding items + draft PR text; minimal: `done: {sha} {files}`), progress updates, `APPROVAL_BLOCKED`, `ERROR`, `SUSPEND` response.
  - Fallback: numeric `to_id` from the DELEGATE message body if `secretary` returns `[pane_not_found]`.

### Constraints

- **Permission mode**: `auto` (hardcoded literal at spawn sites; `registry/org-config.md` value is reference-only — see its sync-warning). Model: **`opus` mandatory** — `sonnet` is forbidden because `auto` mode's safety classifier is only stable on Opus.
- **Working directory is enforced**: First action on launch is `pwd` to verify `worker_dir`. Mismatch → halt and report to secretary.
- **Hard-blocked operations** (via `permissions.deny` + PreToolUse hooks):
  - Cannot reproduce claude-org structure (`.claude/`, `.dispatcher/`, `.curator/`, `.state/`, `registry/`, `dashboard/`, `knowledge/`) inside `worker_dir`.
  - Cannot `git clone` claude-org separately (must edit it directly via the self-edit role).
  - Cannot `git push` (secretary handles push).
  - Cannot `rm -rf` / `rm -r`.
- **Role selection** (chosen by secretary at Step 1.5, not by the worker):
  - `default` — normal implementation tasks.
  - `claude-org-self-edit` — required when `worker_dir` is the claude-org repo or its worktree AND the task writes to claude-org files (relaxes `block-org-structure.sh`, adds `check-worker-boundary.sh`).
  - `doc-audit` — read-only audits (deny Edit/Write/MultiEdit/NotebookEdit; deny commit/branch).
- **Reporting target is secretary, not dispatcher** — explicitly emphasized at spawn time and repeated in worker template.
- **Codex discipline** (full mode, when `codex` available):
  - Use `codex exec --skip-git-repo-check` directly. The `codex:rescue` skill is forbidden (past 18-min hangs).
  - 3-round cap on same-category Blocker/Major findings; on the 4th round, declare design-issue and report to secretary for scope reduction.
  - Minor / Nit findings stay; document as known limitations in PR / README.
  - Do NOT delegate review to another worker.
- **Pane retention after PR** — must NOT exit after PR creation. Wait for explicit close instruction from secretary (merged / explicitly closed / long idle).
- **Windows specifics** — Python is `py -3` (not `python`); files containing Japanese must specify `encoding="utf-8"`.
- **Full-mode completion-report schema** — The structure of `full`-mode completion reports follows `.claude/skills/org-delegate/references/worker-claude-template.md`. The contract does not enumerate a separate schema.
- **Worker max lifetime** — No hard cap is contracted. The intervention triggers in `org-delegate` Step 5 (30-minute same-phase, 1-hour silent) are authoritative.

### Lifecycle / boundaries

- **Spawn**: By the dispatcher in `org-delegate` Step 3, via `mcp__renga-peers__spawn_claude_pane(role="worker", name="worker-{task_id}", cwd={worker_dir}, permission_mode=auto, model="opus")` after balanced-split target/direction selection. CLAUDE.md and `settings.local.json` are placed by the secretary in Step 1.5 *before* spawn.
- **Activation**: After spawn, the dispatcher approves the "Load development channel?" prompt on the worker's pane via `send_keys(enter=true)`; the worker is then detected via `list_peers` and receives its instruction message. It greets back when secretary sends the `DELEGATE_COMPLETE` follow-up.
- **Steady state**: Executes the task; reports progress to secretary; if blocked on approval, halts (dispatcher detects via inspect or self-report and notifies secretary).
- **Completion handoff**:
  - Full: structured completion report → secretary pushes / opens PR → worker holds pane for review feedback.
  - Minimal: `done: {sha} {files}` → secretary handles push/PR; worker pane may close after secretary confirms.
- **Suspension**: On `SUSPEND:` message — immediately reports completed work, modified files (committed / uncommitted), planned next step, blockers.
- **Termination**: Pane closed by dispatcher upon secretary's `CLOSE_PANE` (after PR merge / explicit close / long idle), with retro performed first.
- **Hard prohibitions** (in addition to the "Hard-blocked operations" above):
  - Must NOT talk to the human directly.
  - Must NOT push to GitHub.
  - Must NOT exit autonomously after PR creation.
  - Must NOT switch validation depth on its own (depth is set by secretary; if unspecified or ambiguous, ask secretary).
  - Must NOT spawn sub-workers. If sub-tasking is required, the worker escalates to the secretary, which decides whether to delegate.
- **Branch / worktree cleanup** — Branches are retained as PR audit trail (remote auto-deletion via `gh pr merge --delete-branch` notwithstanding; local retention is harmless). Worktree cleanup follows the pattern set in `org-delegate` Step 5 (Pattern A: directory kept; Pattern B: worktree removed; Pattern C: directory kept). The worker has no cleanup responsibility.

---

## Decision rationale digest

A digest of the Lead-confirmed choices made during the 2026-05-03 Q&A session, by cluster:

1. **Latency / SLA contracts** — Soft SLA only. The secretary yields to active human dialogue; idle response is best-effort. Dispatcher crash recovery is best-effort with `list_panes` reconciliation as authority. Curator cadence is a tunable default, not a hard contract.
2. **Closed-set enumerations** — Avoided. Authoritative registries live next to the code: `docs/journal-events.md` for journal events, `.dispatcher/references/worker-monitoring.md` § (b) for approval-prompt regexes, `worker-claude-template.md` for the full-mode completion-report shape. The contract references these single-source-of-truth artifacts rather than duplicating their content. Worker max lifetime is left uncapped, governed by `org-delegate` Step 5 intervention triggers.
3. **Carve-outs to "no direct spawn"** — Codified. The secretary may directly spawn dispatcher and curator only during `/org-start`; all other pane spawns route through the dispatcher.
4. **Self-management permissions** — Curator may archive but not delete `knowledge/raw/` entries. Workers may not spawn sub-workers (must escalate to secretary). Workers have no branch / worktree cleanup responsibility — branches are kept as audit trail; worktree handling follows `org-delegate` Step 5 patterns.
5. **Suspend participation contracts** — `/org-suspend` skill at `.claude/skills/org-suspend/SKILL.md` is the authoritative flush list (the contract does not enumerate files). The curator does not participate in `/org-suspend`.
