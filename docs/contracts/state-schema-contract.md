# Contract Set C — State Schema (Outline)

> **Status**: Outline / skeleton — pending Lead Q&A (2026-05). Structural extraction of the on-disk state surface that the `claude-org` harness reads and writes, with placeholders left for design decisions the Lead must fill in before this contract is ratified.
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
- **Format**: Markdown with structured top-level fields (`Status:`, `Updated:`, `Current Objective:`, plus session-lifecycle metadata `Started:` / `Suspended:` / `Resumed:` written by `/org-start`, `/org-suspend`, `/org-resume`) followed by named H2 sections. The session-lifecycle metadata is not currently enumerated in `docs/org-state-schema.md`'s JSON projection — see the `[TBD by Lead]` in 1.2 on whether to fold it into the canonical schema.
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
- **`[TBD by Lead]`** — Whether `org-state.json` is a NORMATIVE state file (canonical schema lives in this contract) or a DOWNSTREAM ARTIFACT (canonical state is `org-state.md`; the JSON is reproducible from it and need not be contracted as state). Today `docs/org-state-schema.md` already declares "Markdown is canonical, JSON is derived"; the question is whether Set C inherits that ruling or asserts dual normativity.

### 1.3 `.state/journal.jsonl`

- **Path**: `.state/journal.jsonl`
- **Format**: JSON Lines (one JSON object per line, `\n`-terminated). UTF-8.
- **Schema**: Reserved envelope keys `ts` (ISO-8601 UTC, second precision) and `event` (snake_case string). All other keys are event-specific and catalogued in `docs/journal-events.md`. Field shapes there are described as "descriptive" (consumers tolerate unknown fields).
- **Owner**: secretary, dispatcher, and `org-start` identity recovery (per `docs/journal-events.md` § Writers). Workers do NOT write the journal directly.
- **Readers**: retros (`/org-retro`, `org-curate`), ad-hoc `tail` / `jq`, `tools/pr_watch.py` consumers (read-after-append for CI signaling), future dashboard readers.
- **Update cadence**: append-only, per event. Writes MUST go through `tools/journal_append.sh` or `tools/journal_append.py` (raw `>>` is forbidden by Set A constraints).
- **`[TBD by Lead]`** — Whether the append-only obligation is contracted at the schema level (i.e., manual edits during retro are forbidden) or whether retros may rewrite past entries to correct errors. Today the helper does not enforce append-only at the file-system level (no chattr `+a`, no checksum chain); the contract today is convention-only. Versioning of the line envelope (whether to add a per-line `schema_version` beyond the existing `ts` / `event` keys) is folded into §4.1.

### 1.4 `.state/workers/worker-{task_id}.md`

- **Path**: `.state/workers/worker-{task_id}.md`
- **Format**: Markdown with structured fields. Per `delegate-plan` helper output: header fields including `Status:`, `Pane Name:`, `Directory:`, `Validation:`, plus `## Progress Log` (chronological bulleted entries) and (during/after suspend) `## Current State at Suspend`.
- **Schema**: `Status` ∈ `{planned, active, pane_closed, completed}` per Set B §1 and `docs/internal/phase4-inventory-2026-05-02.md` §2.7. Pane Name follows `worker-{task_id}` with `task_id` kebab-case English; Directory is an absolute path; Validation ∈ `{full, minimal}`.
- **Owner**: dispatcher (creation at T2, Status transitions, final update on T7 / CLOSE_PANE) and secretary (Progress Log appends on each report — per Set B T3, T4).
- **Readers**: secretary (resume / progress review), dispatcher (close-pane retro), `/org-resume` and `/org-suspend` (state collection).
- **Update cadence**: per delegation transition (Set B T2, T3, T4, T5, T7) and each progress / suspend / resume message.
- **`[TBD by Lead]`** — Whether the per-worker file's authoritative format is normalized to a single shape (e.g., a Markdown-with-front-matter template rendered from a TOML brief by `tools/gen_worker_brief.py` — analogous to the `gen_worker_brief.py` precedent for `CLAUDE.md`) or remains free-form Markdown enforced only by helper convention. Versioning is folded into §4.1; format-language uniformity is folded into §2.

### 1.5 `.state/dispatcher/inbox/{task_id}.json`

- **Path**: `.state/dispatcher/inbox/{task_id}.json` (runtime-created; the `dispatcher/` directory is not committed in-tree).
- **Format**: JSON.
- **Schema**: Task spec consumed by `claude-org-runtime dispatcher delegate-plan`. Fields include `task_id`, `worker_dir`, `pattern`, `project`, `permission_mode`, `validation_depth`, `instruction` (or instruction reference). The authoritative schema is the one declared by the `claude-org-runtime` `delegate-plan` helper.
- **Owner**: secretary (writes per `/org-delegate` Step 1.5 / before sending `DELEGATE`).
- **Readers**: dispatcher (consumed by `delegate-plan` helper at T2).
- **Update cadence**: one-shot per delegation; written before T1, consumed at T2.
- **`[TBD by Lead]`** — Lifecycle of the inbox file after consumption: deleted by the dispatcher post-spawn, retained as audit trail, or moved to a `processed/` subdirectory. Today behavior: per Set B §2 T8, the file MAY remain on disk for re-attempt after `SPLIT_CAPACITY_EXCEEDED`; otherwise lifecycle is implementation-defined in the runtime.

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
- **`[TBD by Lead]`** — In-scope-vs-runtime question for the cursor file and the dashboard runtime artifacts in §1.8 (treated as one cluster). Stances: (a) all runtime artifacts are out of contract scope (treated like PID files); (b) the event-cursor is normative state (since loss degrades dispatcher recovery, even if `list_panes` reconciliation absorbs the gap); (c) all listed runtime artifacts are normative. Decision affects whether §3.5 atomic-write rules and §4 migration apply to them.

### 1.8 `.state/dashboard.pid` / `.state/dashboard.log`

- **Path**: `.state/dashboard.pid`, `.state/dashboard.log` (runtime-created when `dashboard/server.py` is started by the secretary).
- **Format**: PID file (single-line PID); log file (free-form text).
- **Owner**: dashboard server process.
- **Readers**: `/org-suspend` Phase 3.5 reads the PID file to send shutdown.
- Coverage decision is folded into the §1.7 runtime-artifact cluster (the brief flagged these as "possibly out of contract scope"; whether they sit in or out is decided in one place).

### 1.9 `registry/projects.md`

- **Path**: `registry/projects.md`
- **Format**: Markdown, prose-with-table.
- **Schema**: Per-project entries documenting common name, repo URL, default `worker_dir` pattern, and project-specific notes. Authoritative shape lives in the file itself; no external schema.
- **Owner**: secretary (registers new projects on user request).
- **Readers**: secretary (resolves user-mentioned common names to projects per `CLAUDE.md` § Communication).
- **Update cadence**: at project registration / update.
- **`[TBD by Lead]`** — Whether `registry/` files (this file and `registry/org-config.md` — treated as one cluster) are in scope for Set C (state) or Set A (configuration / role-input). Today they sit under version control alongside state-shape artifacts but are functionally configuration. Decision affects whether migration policy (§4) applies to them.

### 1.10 `registry/org-config.md`

- **Path**: `registry/org-config.md`
- **Format**: Markdown.
- **Schema**: Includes `default_permission_mode` (per-role applicability, currently `auto`), workers_dir convention, and other org-wide configuration. Authoritative shape per the file.
- **Owner**: secretary (and the human via direct edit).
- **Readers**: secretary, dispatcher, curator (permission-mode resolution per Set A constraints).
- **Update cadence**: at organization configuration changes.
- Coverage decision folded into §1.9 (registry-files cluster).

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

- **`[TBD by Lead]`** — Authoritative format choice per file. Candidate stances:
  1. Normalize all state schemas to **machine-checkable JSON Schema** (draft-07), accepting that Markdown files like `org-state.md` and `worker-*.md` need a structured-fields-extracted JSON projection to be checkable.
  2. Accept the current heterogeneity as deliberate (Markdown for human-edited files; JSON Schema for runtime-helper-managed files; JSONL with descriptive event catalog for the journal).
  3. Adopt **TypeScript interfaces** or **Pydantic models** as the lingua franca, generating both runtime validation and prose docs from a single source.
  
  The Lead's choice constrains the migration mechanics in §4 (machine-checkable schemas can be diffed automatically; prose schemas require manual review).

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

- **`[TBD by Lead]`** — Which files MUST be written via tempfile + rename (atomic) to avoid mid-write reads. Today `dashboard/org_state_converter.py` writes `org-state.json` atomically (via `tempfile`); journal helpers append (not atomic-rename, but POSIX append semantics make line-granularity atomic on small lines). The Lead's call: enumerate the files that MUST be atomic-write (e.g., `org-state.md`, `org-state.json`, per-worker state file) vs. those for which append/in-place rewrite is acceptable.

### 3.6 Encoding and line endings

- **`[TBD by Lead]`** — Whether the contract requires UTF-8 encoding universally (today's de-facto standard, per `tools/journal_append.py` and the `encoding="utf-8"` Windows note in `worker-claude-template.md`) and whether line-ending normalization (`\n`) is contracted. The converter today does `.replace("\r\n", "\n")` defensively on read, suggesting in-the-wild CRLF is tolerated; the question is whether write-side normalization is normative.

---

## 4. Migration strategy

Per Issue #124's acceptance criterion, the contract must declare how state schemas evolve. The current implementation has only one explicit version field: `SCHEMA_VERSION = 1` in `dashboard/org_state_converter.py` for `org-state.json`. Other state files have no version tag.

### 4.1 Versioning approach

- **`[TBD by Lead]`** — Whether each state file MUST carry an explicit `schema_version` field, or the contract version is applied implicitly (one global "Set C version" that bumps when any file's schema changes). Candidate stances:
  1. **Per-file `schema_version`** — every JSON file carries a top-level `schema_version`; every Markdown file carries an HTML-comment / front-matter `schema_version: N` line. Allows independent evolution.
  2. **Global Set C version** — a single contract version is bumped on any schema change; the converter and helpers read the global version from a single config (e.g., `docs/contracts/state-schema-contract.md` itself). Forces lock-step evolution.
  3. **Hybrid** — JSON files versioned individually (today's `org-state.json` precedent); Markdown files versioned implicitly via the contract.

### 4.2 Backward-compat (renames / removals)

- **`[TBD by Lead]`** — Deprecation-window policy for renames and removals. Set D's error-code vocabulary contracts a deprecation window (old + new codes emitted in parallel). Set C must decide the analogous policy for state-file fields:
  - **Rename**: writer emits both old and new keys for N minor versions, then drops the old key.
  - **Removal**: writer emits the deprecated key with a sentinel value for N minor versions, then drops it.
  - **N**: number of minor versions of overlap. Set D leaves this to the deprecation window; Set C must pick a concrete bound or defer to Set D's policy.

### 4.3 Forward-compat

- **`[TBD by Lead]`** — How does an old harness read a state file written by a newer schema. Two candidates:
  1. **Tolerant readers** — readers ignore unknown keys, fall back to documented defaults for missing keys (consistent with `docs/journal-events.md` § "consumers should tolerate unknown fields gracefully" and Set D § "callers MUST treat unknown types as non-fatal").
  2. **Hard-fail readers** — readers refuse to process a file whose `schema_version` exceeds their compiled-in maximum, forcing the operator to upgrade the harness.

### 4.4 Migration hooks

- **`[TBD by Lead]`** — Where migration code lives. Candidate stances:
  1. A dedicated `tools/state_migrate.py` runs migrations on `/org-resume` (or on first read of an old-version file).
  2. Each reader inlines its own version-aware parsing (today's converter approach: `SCHEMA_VERSION` is a constant in the converter, not a separate migration script).
  3. Migrations are out-of-band — the operator runs a one-shot script provided in release notes.

### 4.5 Migration breadth (how many versions readable simultaneously)

- **`[TBD by Lead]`** — How many minor versions of each schema must a single harness build be able to read. A common stance is "N-1 and N" (read previous version, write current); a more conservative stance is "N-2 through N" (allow operators to skip one upgrade). The bound interacts with §4.2 deprecation-window length.

---

## 5. Decisions to ratify (open questions consolidated)

The Lead-fill-in markers above are the explicit fill-in points. They cluster as follows:

1. **In-scope vs. out-of-scope artifacts** — `org-state.json` (normative or derived?), `dispatcher-event-cursor.txt` (state or runtime?), `dashboard.pid` / `dashboard.log` (state or ephemera?), `registry/projects.md` and `registry/org-config.md` (state or configuration?). Each carries a separate marker because the answers may diverge.
2. **Schema-language uniformity** — Whether to normalize to one normative schema language (machine-checkable JSON Schema, TypeScript interfaces, Pydantic) or accept the current heterogeneity (prose for Markdown-edited files, JSON Schema for runtime-helper-managed files, descriptive prose for the journal).
3. **Per-file `schema_version` field** — Required on every file vs. global Set C version vs. hybrid.
4. **Append-only obligation for `journal.jsonl`** — Codified as contract (forbids retro rewrites) or convention-only.
5. **Per-worker state file authoritative shape** — Free-form Markdown vs. helper-rendered template (gen_worker_brief precedent).
6. **Inbox lifecycle post-consumption** — Deleted, retained, or moved to `processed/`.
7. **Atomic-write requirements** — Which files MUST use tempfile + rename.
8. **Encoding / line-ending normativity** — UTF-8 mandated; LF mandated on write?
9. **Migration policy** — Deprecation window length, forward-compat read policy, migration hook location, simultaneous-version breadth (cluster of four markers in §4).

These are the design decisions that must be settled before Contract Set C is ratified; the structural skeleton above (file inventory, cross-file invariants, migration-strategy headings) is fixed.
