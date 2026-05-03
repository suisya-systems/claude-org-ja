# Contract Set C — State Schema

> **Status**: Ratified (2026-05-03). Lead-confirmed decisions for all 14 open questions. This contract defines the on-disk state surface that the `claude-org` harness reads and writes.
>
> **Scope**: Phase 1 Contract Set C only. Sets A (roles), B (delegation lifecycle), D (backend interface), and E (knowledge) are tracked in #121 / #122 / #123 / #125 and out of scope here.
>
> **Subject**: Set C defines the schemas of the files under `.state/` (and the closely-coupled `registry/` and dashboard-snapshot artifacts), the cross-file invariants that span them, and the migration strategy for evolving those schemas. Set C does NOT cover knowledge/ (Set E), the messaging surface (Set D), or per-role responsibilities (Set A) — it covers the persistent state surface only.
>
> **Method**: Each file is filled from empirical sources (current files in the working repo, the converter, the journal-event catalog, the org-state schema doc, the suspend/resume skills, the worker template, and the runtime settings package). Sentences sourced from current behavior are written as facts. Open design questions are marked inline for Lead fill-in.
>
> **Empirical sources consulted**:
> - `docs/org-state-schema.md` (existing JSON-schema doc for `org-state.json`, including the `peerId` / `paneId` distinction)
> - `docs/journal-events.md` (event catalog, reserved-envelope keys, writer attribution table)
> - `tools/journal_append.sh` and `tools/journal_append.py` (the only sanctioned event-writers; schema enforced at write-time)
> - `dashboard/org_state_converter.py` (markdown → JSON projection logic; declares `SCHEMA_VERSION = 1` for `org-state.json`)
> - `.claude/skills/org-suspend/SKILL.md` (Phase 1–4 flush list and write order)
> - `.claude/skills/org-delegate/SKILL.md` (Step 1.5 worker-dir prep, Step 4 state record)
> - `.claude/skills/org-delegate/references/worker-claude-template.md` (per-worker state file expectations)
> - `tools/gen_worker_brief.py` (writes per-worker `CLAUDE.md` / `CLAUDE.local.md` from a TOML brief — referenced for the "schema-as-data" precedent)
> - `tools/role_configs_schema.json` (now provided by the `claude-org-runtime` package; referenced as the schema-as-data precedent for runtime-provided schemas)
> - `claude-org-runtime` package (settings generator schema; `claude-org-runtime settings generate` is the sanctioned writer for worker `settings.local.json`)
> - Existing files under `.state/` and `registry/` of this repo (sample shapes)
> - `docs/contracts/role-contract.md` (Set A), `docs/contracts/delegation-lifecycle-contract.md` (Set B), `docs/contracts/backend-interface-contract.md` (Set D) — structural template
>
> **Refs**: #124 (this issue), parent epic #101.

---

## 1. State files inventory

The harness's persistent state surface comprises the files listed below. Each entry names: **path** (repo-relative), **format**, **schema** (key fields and value vocabulary), **owner** (the role authorized to write — consistent with Set A), **reader(s)** (roles that read), and **update cadence / triggers**. Files marked `runtime-created` are not committed to the tree; they appear when the harness has been run.

### 1.1 `.state/org-state.md`

- **Path**: `.state/org-state.md`
- **Format**: Markdown with structured top-level fields (`Status:`, `Updated:`, `Current Objective:`, plus session-lifecycle metadata `Started:` / `Suspended:` / `Resumed:` written by `/org-start`, `/org-suspend`, `/org-resume`) followed by named H2 sections. The session-lifecycle metadata is not currently enumerated in `docs/org-state-schema.md`'s JSON projection; since §1.2 ratifies the JSON projection as DERIVED (Markdown is canonical), folding session-lifecycle metadata into the JSON projection is a non-blocking documentation follow-up rather than a contract-level requirement.
- **Schema**: `org-state.md` is the canonical state file (Markdown is normative; the JSON projection in 1.2 is derived per `docs/org-state-schema.md` § "Source of truth ルール"). Sections present in the markdown:
  - Header fields: `Status` ∈ `{ACTIVE, SUSPENDED, IDLE}`, `Updated` (ISO-8601), `Current Objective` (free text).
  - `## Active Work Items` — bullet list `- {task_id}: {title} [{STATUS}]` with `STATUS` ∈ the canonical Set B vocabulary `{IN_PROGRESS, REVIEW, COMPLETED, ABANDONED}` (per Set B §1 / §1 contract-state table). The wider enum currently documented in `docs/org-state-schema.md` (which additionally lists `PENDING` / `BLOCKED`) predates Set B's ratification; reconciling the schema doc to the Set B canonical set is tracked as a follow-up. Sub-bullets: `- ワーカー: {peerId}` and `- 結果: {progress note}`.
  - `## Worker Directory Registry` — Markdown table with columns `taskId | pattern | directory | project | status` (`pattern` ∈ `{A, B, C}`, `status` ∈ `{in_use, available}`).
  - `## Dispatcher` / `## Curator` — `Peer ID:` / `Pane ID:` lines.
  - `## Resume Instructions` — free text written by `/org-suspend`.
- **Owner**: secretary is the primary writer (`/org-start`, `/org-delegate` Step 4 for Worker Directory Registry, status transitions, `/org-suspend`, `/org-resume`). Dispatcher is a co-writer for the Active Work Items section: per Set B §2 T2, the dispatcher adds the Active Work Items row when a worker is spawned. No other role writes this file.
- **Readers**: secretary (state recovery), dispatcher (Active Work Items context, in addition to its T2 write), `dashboard/org_state_converter.py` (regeneration source), `dashboard/server.py` (fallback regex parse when JSON snapshot is stale or missing).
- **Update cadence**: per state-changing event (delegation, completion, suspend/resume, registry mutation). After each update the JSON snapshot (1.2) MUST be regenerated.

### 1.2 `.state/org-state.json` (a.k.a. `dashboard/org-state.json` snapshot)

- **Path**: `.state/org-state.json` (generated by `dashboard/org_state_converter.py`).
- **Format**: JSON, schema version field `"version": 1`.
- **Schema**: Defined in `docs/org-state-schema.md` § "スキーマ（version 1）". Top-level keys: `version`, `updated`, `status`, `currentObjective`, `workItems[]`, `workerDirectoryRegistry[]`, `dispatcher`, `curator`, `resumeInstructions`. The converter encodes `SCHEMA_VERSION = 1` literally.
- **Owner**: derived — written ONLY by `dashboard/org_state_converter.py`. No role hand-edits this file.
- **Readers**: `dashboard/server.py` (primary); other programmatic consumers in the future.
- **Update cadence**: regenerated after every `org-state.md` write, per `docs/org-state-schema.md` § "更新ポイント" and `/org-suspend` Phase 3 step 3.
- **Normativity**: DERIVED. Set C inherits the `docs/org-state-schema.md` ruling that Markdown (`org-state.md`) is canonical and the JSON projection is derived. Set C does NOT separately normativize the JSON projection; the converter's `SCHEMA_VERSION = 1` is informational for downstream consumers (e.g., `dashboard/server.py`) only. Migration policy (§4) applies to the JSON shape solely insofar as it tracks the canonical Markdown.

### 1.3 `.state/journal.jsonl`

- **Path**: `.state/journal.jsonl`
- **Format**: JSON Lines (one JSON object per line, `\n`-terminated). UTF-8.
- **Schema**: Reserved envelope keys `ts` (ISO-8601 UTC, second precision) and `event` (snake_case string). All other keys are event-specific and catalogued in `docs/journal-events.md`. Field shapes there are described as "descriptive" (consumers tolerate unknown fields).
- **Owner**: secretary, dispatcher, and `org-start` identity recovery (per `docs/journal-events.md` § Writers). Workers do NOT write the journal directly.
- **Readers**: retros (`/org-retro`, `org-curate`), ad-hoc `tail` / `jq`, `tools/pr_watch.py` consumers (read-after-append for CI signaling), future dashboard readers.
- **Update cadence**: append-only, per event. Writes MUST go through `tools/journal_append.sh` or `tools/journal_append.py` (raw `>>` is forbidden by Set A constraints).
- **Append-only obligation**: CONVENTION-ONLY. Appends MUST go through `tools/journal_append.{sh,py}`; raw `>>` is forbidden. Manual edits during `/org-retro` are permitted ONLY to correct factual errors, and the corrected line MUST add a reserved `_correction` field (JSON string) on the same line recording the rationale, so the line remains valid JSON consumable by `dashboard/server.py` and `jq` (no JSONL-illegal inline comments). The contract does NOT impose filesystem-level enforcement (no chattr `+a`, no checksum chain). Per-line schema versioning beyond the `ts` / `event` envelope is not introduced; see §4.1.

### 1.4 `.state/workers/worker-{task_id}.md`

- **Path**: `.state/workers/worker-{task_id}.md`
- **Format**: Markdown with structured fields. Per `delegate-plan` helper output: header fields including `Status:`, `Pane Name:`, `Directory:`, `Validation:`, plus `## Progress Log` (chronological bulleted entries) and (during/after suspend) `## Current State at Suspend`.
- **Schema**: `Status` ∈ `{planned, active, pane_closed, completed}` per Set B §1 and `docs/internal/phase4-inventory-2026-05-02.md` §2.7. Pane Name follows `worker-{task_id}` with `task_id` kebab-case English; Directory is an absolute path; Validation ∈ `{full, minimal}`.
- **Owner**: dispatcher (creation at T2, Status transitions, final update on T7 / CLOSE_PANE) and secretary (Progress Log appends on each report — per Set B T3, T4).
- **Readers**: secretary (resume / progress review), dispatcher (close-pane retro), `/org-resume` and `/org-suspend` (state collection).
- **Update cadence**: per delegation transition (Set B T2, T3, T4, T5, T7) and each progress / suspend / resume message.
- **Authoritative format**: FREE-FORM Markdown enforced by helper convention. The `delegate-plan` helper output template is the de facto shape; no separate generator analogous to `tools/gen_worker_brief.py` is required for the per-worker state file. Format-language uniformity is addressed in §2; versioning in §4.1.

### 1.5 `.state/dispatcher/inbox/{task_id}.json`

- **Path**: `.state/dispatcher/inbox/{task_id}.json` (runtime-created; the `dispatcher/` directory is not committed in-tree).
- **Format**: JSON.
- **Schema**: Task spec consumed by `claude-org-runtime dispatcher delegate-plan`. Fields include `task_id`, `worker_dir`, `pattern`, `project`, `permission_mode`, `validation_depth`, `instruction` (or instruction reference). The authoritative schema is the one declared by the `claude-org-runtime` `delegate-plan` helper.
- **Owner**: secretary (writes per `/org-delegate` Step 1.5 / before sending `DELEGATE`).
- **Readers**: dispatcher (consumed by `delegate-plan` helper at T2).
- **Update cadence**: one-shot per delegation; written before T1, consumed at T2.
- **Lifecycle**: RETAIN AS AUDIT TRAIL. Inbox JSON files are kept on disk after dispatcher consumption; deletion is not contracted. This makes the Set B §2 T8 `SPLIT_CAPACITY_EXCEEDED` retry behavior fall out naturally (the file is already on disk for re-attempt). Storage cost is negligible.

### 1.6 `.state/dispatcher/outbox/{task_id}-instruction.md`

- **Path**: `.state/dispatcher/outbox/{task_id}-instruction.md` (runtime-created).
- **Format**: Markdown — the instruction body delivered to the worker via `send_message`.
- **Schema**: Free-form Markdown, conforming to `.claude/skills/org-delegate/references/instruction-template.md` (the authoritative shape for instruction bodies; Set A treats this template as single-source-of-truth).
- **Owner**: dispatcher (`delegate-plan` helper writes at T2).
- **Readers**: dispatcher (re-reads when re-sending), human (audit trail).
- **Update cadence**: written once per delegation at T2; not mutated thereafter.

### 1.7 `.state/dispatcher-event-cursor.txt`

- **Path**: `.state/dispatcher-event-cursor.txt` (runtime-created).
- **Format**: Plain text — opaque cursor token returned by the backend's `poll_events.next_since` (per Set D §3.1).
- **Schema**: Single line, opaque to the harness.
- **Owner**: dispatcher (advances each watch-loop cycle).
- **Readers**: dispatcher (resume after pane restart).
- **Update cadence**: each `poll_events` round (per Set A "Journal append discipline" — written via the helper-or-direct-rewrite pattern, NOT via the journal helper).
- **Scope**: HYBRID (in-scope). The dispatcher event cursor IS normative state — its loss measurably degrades dispatcher recovery, so the §3.5 atomic-write enumeration and §4 migration policy apply (see §3.5 for the specific atomicity ruling on this single-line transient cursor). The dashboard runtime artifacts in §1.8 are decided separately below as OUT of scope.

### 1.8 `.state/dashboard.pid` / `.state/dashboard.log`

- **Path**: `.state/dashboard.pid`, `.state/dashboard.log` (runtime-created when `dashboard/server.py` is started by the secretary).
- **Format**: PID file (single-line PID); log file (free-form text).
- **Owner**: dashboard server process.
- **Readers**: `/org-suspend` Phase 3.5 reads the PID file to send shutdown.
- **Scope**: OUT of contract scope. `dashboard.pid` and `dashboard.log` are treated like PID and log files — operational ephemera, not normative state. §3.5 atomic-write rules and §4 migration policy do NOT apply to them.

### 1.9 `registry/projects.md`

- **Path**: `registry/projects.md`
- **Format**: Markdown, prose-with-table.
- **Schema**: Per-project entries documenting common name, repo URL, default `worker_dir` pattern, and project-specific notes. Authoritative shape lives in the file itself; no external schema.
- **Owner**: secretary (registers new projects on user request).
- **Readers**: secretary (resolves user-mentioned common names to projects per `CLAUDE.md` § Communication).
- **Update cadence**: at project registration / update.
- **Scope**: IN SCOPE for Set C as "state-adjacent configuration". `registry/projects.md` and `registry/org-config.md` are version-controlled alongside state artifacts, and the §4 migration policy applies to them. They are NOT moved to Set A; Set A retains them only as Set A's "config inputs" reference. This dual-listing is intentional.

### 1.10 `registry/org-config.md`

- **Path**: `registry/org-config.md`
- **Format**: Markdown.
- **Schema**: Includes `default_permission_mode` (per-role applicability, currently `auto`), workers_dir convention, and other org-wide configuration. Authoritative shape per the file.
- **Owner**: secretary (and the human via direct edit).
- **Readers**: secretary, dispatcher, curator (permission-mode resolution per Set A constraints).
- **Update cadence**: at organization configuration changes.
- **Scope**: IN SCOPE per the §1.9 registry-cluster decision (state-adjacent configuration; §4 migration policy applies).

### 1.11 Out-of-scope: worker-directory inputs

Worker-side `CLAUDE.md` / `CLAUDE.local.md` and `.claude/settings.local.json` are NOT in Set C scope. They are worker inputs (per Set A § Role: worker), generated by the secretary into the worker directory at `org-delegate` Step 1.5 via `tools/gen_worker_brief.py` and `claude-org-runtime settings generate`. The schemas of those generators are owned by Set A (worker role) and the `claude-org-runtime` package, respectively, not by Set C. They are noted here only because the cross-file invariants in §3 reference the worker directory; the schemas themselves are out of scope.

---

## 2. Schema definition format (per file)

The contract picks ONE normative schema-language per file, or accepts heterogeneity as deliberate. Today the implementation is heterogeneous:

- `org-state.json` — JSON Schema-shaped narrative in `docs/org-state-schema.md` (prose, not a machine-checkable JSON Schema document).
- `worker-{task_id}.md` — implicit, defined only by the `delegate-plan` helper's template.
- `journal.jsonl` — descriptive prose in `docs/journal-events.md` (deliberately loose so consumers tolerate unknown fields).
- `inbox/{task_id}.json` — declared by the runtime package's helper, not by an in-repo schema doc.
- `tools/role_configs_schema.json` — actual machine-readable JSON Schema (draft-07-style), but lives in `claude-org-runtime`, not this repo.

- **Decision**: ACCEPT THE CURRENT HETEROGENEITY AS DELIBERATE. Markdown is the schema language for human-edited files (`org-state.md`, `worker-*.md`, `registry/*.md`); JSON Schema-shaped prose is the language for runtime-helper-managed files (`org-state.json`, `inbox/*.json`); descriptive prose in `docs/journal-events.md` governs the journal (deliberately loose so consumers tolerate unknown fields). The contract does NOT mandate a single normative schema language. Migration mechanics in §4 therefore rely on manual review for Markdown-edited files and on the per-file top-level `version` integer for JSON files (see §4.1).

---

## 3. Cross-file invariants

These invariants span multiple files and MUST hold at quiescent state (i.e., when the harness is not mid-transition). The harness today enforces them by convention; the question of *mechanical* enforcement (drift CI, runtime checks) is left to per-invariant Lead decision below.

### 3.1 Worker-directory ↔ worker-state-file consistency

**Invariant**: After the dispatcher has crossed Set B T2 for `taskId: T` (i.e., `worker_spawned` has been journaled and the Active Work Items row exists), if `org-state.md` Worker Directory Registry still has a row `Status: in_use, taskId: T`, then `.state/workers/worker-T.md` MUST exist with `Status` ∈ `{planned, active}`. The pre-T2 `pending` window (between Set B T1 — registry row added by secretary — and T2 — worker state file created by dispatcher) is exempt: registry-row-without-worker-file is valid in that window. Conversely, once `.state/workers/worker-T.md` has `Status: completed` or `pane_closed`, the corresponding registry row MUST have `Status: available` (Pattern A) or be removed (Pattern B / C), per Set B §2 T5.

### 3.2 Active-work-item ↔ journal-history consistency

**Invariant**: Every entry in `org-state.md` Active Work Items with `worker: P` MUST correspond to a peer-id `P` named in some prior `worker_spawned` journal event (i.e., the worker existed at some point in history). The reverse does not hold — past `worker_spawned` events may refer to workers no longer in Active Work Items.

### 3.3 Status / pane-liveness reconciliation

**Invariant**: `org-state.md` `Status: ACTIVE` is incompatible with all org-managed panes (secretary / dispatcher / curator / workers) being closed. `/org-resume` reconciles this by inspecting renga's pane list against the recorded Active Work Items and Worker Directory Registry.

### 3.4 Identifier conventions

The harness uses three identifier kinds (per `docs/org-state-schema.md` § dispatcher / curator and Set D § Identity & addressing):
- `peerId` — renga-peers stable peer identifier (used as `to_id` in `send_message`).
- `paneId` — renga numeric pane id (lifecycle-tied, NOT equivalent to peerId).
- `task_id` — kebab-case English string, owned by the harness; appears in worker-state filenames, registry rows, journal payloads.
- Pane `name` — backend-side stable name following the `[A-Za-z0-9_-]` alphabet (Set D §1.8). The harness convention is `worker-{task_id}`, `secretary`, `dispatcher`, `curator`.

**Invariant**: `peerId` and `paneId` MUST NOT be conflated in any state file. `org-state.md` Dispatcher / Curator sections record both; `Active Work Items.worker` field records `peerId` only.

### 3.5 Atomic-write requirement

- **Decision**: enumerated as follows.
  - MUST be tempfile + rename (atomic): `.state/org-state.md`, `.state/org-state.json` (already atomic via `tempfile` in the converter), `.state/workers/worker-{task_id}.md`.
  - APPEND-only is acceptable (POSIX line-atomicity for short lines via the journal helpers): `.state/journal.jsonl`.
  - In-place rewrite is acceptable: `.state/dispatcher-event-cursor.txt` (single-line transient cursor; partial-write risk is bounded by the small fixed-size payload, and the dispatcher reconciles via `list_panes` after restart).

### 3.6 Encoding and line endings

- **Decision**: REQUIRED. UTF-8 encoding is universally mandated on writes for all in-scope state files. Line-ending normalization to LF (`\n`) is required on writes. Readers MUST tolerate CRLF in legacy inputs, matching the converter's defensive `.replace("\r\n", "\n")` on read. This makes Windows-authored legacy state files readable while preventing new CRLF-bearing writes from entering the tree.

---

## 4. Migration strategy

Per Issue #124's acceptance criterion, the contract must declare how state schemas evolve. The current implementation has only one explicit version field: `SCHEMA_VERSION = 1` in `dashboard/org_state_converter.py` for `org-state.json`. Other state files have no version tag.

### 4.1 Versioning approach

- **Decision**: HYBRID. JSON files are versioned individually with a top-level integer `version` field. Today's `org-state.json` carries `version: 1` (set by `dashboard/org_state_converter.py` via `SCHEMA_VERSION`); the contract preserves the existing `version` key for `org-state.json` and standardizes on `version` as the field name for all in-scope JSON state files (no separate `schema_version` key is introduced — the precedent wins). The `inbox/{task_id}.json` shape ratified here is implicitly `version: 1`; the field becomes mandatory on emit at the first breaking change to the inbox schema, at which point the writing helper (`claude-org-runtime delegate-plan`) MUST start writing `version` and readers apply §4.3 tolerant-reader rules to legacy entries (treat absent `version` as `1`). The same retroactive-emit rule applies to any future JSON state file the contract picks up. Markdown files are NOT versioned in-file; their contract version is implicitly the Set C version of the harness build that wrote them. This keeps human-edited files clean while preserving machine-tractable versioning where it matters.

### 4.2 Backward-compat (renames / removals)

- **Decision**: N = 2 minor versions of overlap.
  - **Rename**: writer emits both the old and new keys for 2 minor versions, then drops the old key.
  - **Removal**: writer emits the deprecated key with a sentinel value for 2 minor versions, then drops it.
  - **Operator window**: operators may skip at most one upgrade within this window without losing readability; this aligns with §4.5's "N-1 and N" simultaneous-readable bound.

### 4.3 Forward-compat

- **Decision**: TOLERANT READERS. Readers ignore unknown keys and fall back to documented defaults for missing keys. This is consistent with `docs/journal-events.md` § "consumers should tolerate unknown fields gracefully" and Set D's ruling that callers MUST treat unknown types as non-fatal. Hard-fail readers are explicitly NOT contracted; an old harness reading a newer file degrades gracefully rather than refusing to load.

### 4.4 Migration hooks

- **Decision**: A dedicated `tools/state_migrate.py` is the long-term home for migrations. It runs on `/org-resume` and on first read of an old-version file when the harness encounters one. Per-reader inline parsing (today's converter approach, where `SCHEMA_VERSION` is a constant in the converter) is permitted as a transitional shim until `tools/state_migrate.py` lands. A follow-up Issue ("feat(tools): introduce `tools/state_migrate.py` as central migration entry point") tracks the introduction of that script.

### 4.5 Migration breadth (how many versions readable simultaneously)

- **Decision**: N-1 AND N. A single harness build MUST be able to read its current schema version (N) and the immediately previous one (N-1). Operators upgrading from N-2 must perform an intermediate upgrade. This bound interacts with §4.2's deprecation-window length: 2 minor versions of overlap suffices to cover the N-1 read requirement.

---

## 5. Decision rationale digest

Lead-confirmed decisions for the 14 questions raised in the outline (2026-05-03 Q&A session #11). The detail lives inline at each section above; this digest summarizes the rationale by cluster.

1. **In-scope vs. out-of-scope artifacts** (§1.2, §1.7, §1.8, §1.9, §1.10) — `org-state.json` is DERIVED (Markdown is canonical, inheriting the `docs/org-state-schema.md` ruling). The dispatcher event cursor is IN scope (its loss measurably degrades recovery). `dashboard.pid` / `dashboard.log` are OUT of scope (operational ephemera). `registry/projects.md` and `registry/org-config.md` are IN scope as state-adjacent configuration, dual-listed in Set A as config inputs.
2. **Schema-language uniformity** (§2) — Heterogeneity is accepted as deliberate. Markdown for human-edited files, JSON Schema-shaped prose for runtime-helper-managed files, descriptive prose for the journal. No single normative schema language.
3. **Append-only obligation for `journal.jsonl`** (§1.3) — Convention-only. Helper-mediated appends are mandatory; retro corrections are permitted with an inline marker. No filesystem-level enforcement.
4. **Per-worker state file shape** (§1.4) — Free-form Markdown enforced by the `delegate-plan` helper template; no separate generator required.
5. **Inbox lifecycle** (§1.5) — Retained as audit trail; `SPLIT_CAPACITY_EXCEEDED` retry falls out naturally.
6. **Atomic-write requirements** (§3.5) — Enumerated: tempfile + rename for `org-state.md`, `org-state.json`, `worker-{task_id}.md`; append for `journal.jsonl`; in-place rewrite acceptable for the dispatcher event cursor.
7. **Encoding / line endings** (§3.6) — UTF-8 universally; LF on writes; readers tolerate CRLF in legacy inputs.
8. **Migration policy** (§4) — Hybrid versioning (per-file top-level `version` integer for JSON, preserving the `org-state.json` precedent; implicit for Markdown). Deprecation window N = 2 minor versions. Tolerant readers (ignore unknown keys, default missing keys). Centralized `tools/state_migrate.py` as the long-term migration entry point (per-reader shims permitted transitionally; tracked as a follow-up Issue). Simultaneous-readable bound: N-1 and N.
