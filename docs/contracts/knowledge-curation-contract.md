# Contract Set E — Knowledge & Curation Boundaries (Outline)

> **Status**: Outline / skeleton — pending Lead Q&A (2026-05). Structural extraction of the knowledge-and-curation surface that the `claude-org` harness reads and writes, with placeholders left for design decisions the Lead must fill in before this contract is ratified.
>
> **Scope**: Phase 1 Contract Set E only. Sets A (roles), B (delegation lifecycle), C (state schema), and D (backend interface) are tracked in #121 / #122 / #124 / #123 and out of scope here. Set E is marked "optional, small" in Issue #125: the open-questions surface is intentionally narrower than the larger sets.
>
> **Subject**: Set E defines the knowledge artifacts under `knowledge/` (raw learnings, archived raw entries, curated notes, skill-candidate queue), the lifecycle that moves entries between them, and the role-write boundaries on each surface. Set E does NOT cover: per-role responsibilities (Set A — though it cites them for the curator / worker write-surface invariants), `.state/` files (Set C), the messaging or backend transport (Set D), or the delegation lifecycle (Set B).
>
> **Method**: Each artifact and lifecycle step is filled from empirical sources (the curation / retro / audit / eligibility-check skills, the in-tree knowledge-standards reference, and the existing `knowledge/` tree). Sentences sourced from current behavior are written as facts. Open design questions are marked inline for Lead fill-in.
>
> **Empirical sources consulted**:
> - `.claude/skills/org-curate/SKILL.md` (curator's curation cycle: threshold check, classify, dedup, archive)
> - `.claude/skills/org-curate/references/knowledge-standards.md` (record format `事実 / 判断 / 根拠 / 適用場面`, merge / promotion criteria)
> - `.claude/skills/org-retro/SKILL.md` (post-delegation retro path that writes `knowledge/raw/{date}-delegation-{topic}.md` and may invoke `skill-eligibility-check`)
> - `.claude/skills/skill-eligibility-check/SKILL.md` (5-signal scorer, 3-value decision, `skill-candidates.md` writer)
> - `.claude/skills/skill-eligibility-check/references/signals.md` (signal definitions referenced from §2)
> - `.claude/skills/skill-audit/SKILL.md` (state-based audit firing on candidate-queue / skill-count thresholds)
> - `knowledge/skill-candidates.md` (entry format, status vocabulary, batch-question rationale)
> - Existing `knowledge/raw/` and `knowledge/curated/` samples in this worktree
> - `docs/contracts/role-contract.md` (Set A) — § Role: curator / § Role: worker for the knowledge-write surface
> - `docs/contracts/role-contract.md` § Decisions ratified — Q9 (raw archive, not delete), Q10 (curator `/loop 30m`), Q11 (3-entry skill-promotion threshold)
> - `docs/contracts/delegation-lifecycle-contract.md` (Set B), `docs/contracts/state-schema-contract.md` (Set C), `docs/contracts/backend-interface-contract.md` (Set D) — structural template
>
> **Refs**: #125 (this issue), parent epic #101.

---

## 1. Knowledge artifacts inventory

The harness's knowledge surface comprises the artifacts listed below. Each entry names: **path** (repo-relative), **format**, **schema** (key sections / vocabulary), **owner** (the role authorized to write — consistent with Set A), **reader(s)** (roles that read), and **lifecycle / triggers**. Set E covers the project-level knowledge tree only; the operator-personal auto-memory under `~/.claude/projects/.../memory/` (per the root `CLAUDE.md` "auto memory" section) is treated separately in §3.

### 1.1 `knowledge/raw/{YYYY-MM-DD}-{topic}.md`

- **Path**: `knowledge/raw/{YYYY-MM-DD}-{topic}.md` — date prefix is the calendar date the entry is recorded; `{topic}` is English kebab-case. The dispatcher's post-retro entries use the namespaced topic prefix `delegation-{topic}` to distinguish process learnings from worker technical learnings (per `org-retro` Step 3).
- **Format**: Markdown. Body conforms to the four-heading record format in `.claude/skills/org-curate/references/knowledge-standards.md`: `## 事実`, `## 判断`, `## 根拠`, `## 適用場面`.
- **Schema**: Free-form Markdown bodies under the four canonical headings. After curation, the file's first line carries the marker `<!-- curated -->` so subsequent `org-curate` threshold checks skip it (per `org-curate` Step 4).
- **Owner**: workers (full-validation mode only — minimal mode skips per Set A § Role: worker) and the dispatcher (post-retro process learnings, with the `delegation-` topic prefix per `org-retro` Step 3). Secretary, curator, and the human do not write to `knowledge/raw/` in the normal flow.
- **Readers**: curator (`org-curate` reads all unmarked entries), `skill-eligibility-check` (consumes `raw_files` arg as evidence), worker (read-only reference per Set A worker section), `skill-audit` (greps for skill-name mentions over a 90-day window per `skill-audit` Step 2).
- **Lifecycle**: created at the moment of recording (worker post-task or dispatcher post-retro). After being merged into a curated note, the file gains a `<!-- curated -->` marker and (per Set A Q9) MAY be moved to `knowledge/raw/archive/`; outright deletion is forbidden.

### 1.2 `knowledge/raw/archive/`

- **Path**: `knowledge/raw/archive/` — destination for raw entries that have been consumed by curation.
- **Format**: Same as 1.1 (the file is moved, not transformed); files retain their `<!-- curated -->` marker.
- **Owner**: curator (move-only authority; per Set A § Role: curator constraints and Q9 ratified decision, the curator may archive but MUST NOT delete raw entries).
- **Readers**: curator (occasional re-read for context when a related raw re-appears); `skill-audit` (in scope: the 90-day grep window covers archived entries too — see `[TBD by Lead]` in §1.5).
- **Lifecycle**: append-only by move. Entries are never moved back out of `archive/`.
- **`[TBD by Lead]`** — Whether `knowledge/raw/archive/` itself has a retention bound (e.g., entries older than N years may be removed by an explicit one-shot maintenance script run by the human) or is contractually permanent. Today no retention policy exists; the operative constraint is "curator MUST NOT delete," which leaves human-driven pruning unspecified.

### 1.3 `knowledge/curated/{topic}.md`

- **Path**: `knowledge/curated/{topic}.md` — `{topic}` is English kebab-case. Topic granularity guidance lives in `org-curate` Step 2 (technical area / tool-or-service / process).
- **Format**: Markdown. Body conforms to `org-curate` Step 3: `# {テーマ名}` H1, then per-knowledge `## {知見タイトル}` H2 sections that synthesize the four-heading content from the underlying raw entries.
- **Schema**: Free-form Markdown under the H1 / H2 structure above. No version field today.
- **Owner**: curator only. Workers, dispatcher, and secretary MUST NOT write here (per Set A § Role: curator: "write surface is `knowledge/curated/` and the skill-candidate queue only").
- **Readers**: secretary (read-only reference per Set A § Role: secretary "Local files (read)"), worker (read-only reference per Set A § Role: worker), human via direct view, the curator itself (dedup checks during the next curation cycle).
- **Lifecycle**: created or appended to during `org-curate` Step 3. Existing sections may be merged or rewritten when consolidating new raw entries (per the merge / conflict rules in `knowledge-standards.md`).
- **`[TBD by Lead]`** — Whether `knowledge/curated/{topic}.md` is contractually **append-only with edits permitted only via dedup-rewrite cycles run by `org-curate`**, or whether the curator may freely restructure / delete sections outside the scheduled curation cycle. Today `org-curate` Step 3 implies rewrite-on-merge but does not forbid out-of-cycle edits.
- **`[TBD by Lead]`** — Authoritative naming convention for `{topic}`: technical-area vs. tool-or-service vs. process (the three guidance categories in `org-curate` Step 2 are advisory). Whether a single file MAY mix categories (e.g., `renga-peers.md` covering both tool usage and tool-failure recovery) or MUST be split.

### 1.4 `knowledge/skill-candidates.md`

- **Path**: `knowledge/skill-candidates.md` — single-file queue of `skill_recommend` outputs.
- **Format**: Markdown. Per-candidate blocks delimited by `### {YYYY-MM-DD} {pattern-name}` H3 headings; bullet fields per the entry-format block in the file itself (`判定スコア`, `該当シグナル`, `根拠`, `関連タスク`, `関連 raw ファイル`, `呼び出し元`, `提案 skill 名`, `status`, `決定日`, `却下理由`, `統合先`).
- **Schema**: `status` ∈ `{pending, approved, rejected, merged-into-{existing-skill}}`. Once an entry leaves `pending`, it is retained as history (not deleted) per the file's "運用メモ" section.
- **Owner**: `skill-eligibility-check` Step 4 (auto-append on `skill_recommend` decisions); secretary (status transitions: `pending` → `approved` / `rejected` / `merged-into-*`, plus `決定日` / `却下理由` / `統合先` fields). The curator does NOT directly hand-edit this file; its writes happen through `skill-eligibility-check`.
- **Readers**: secretary (batch question to the human when `pending ≥ 5`, per Issue #68 batch-rationale cited in the file header), `skill-audit` Step 1 (counts `pending` entries to decide whether to fire), the curator (re-read during the next curation cycle to detect already-queued patterns).
- **Lifecycle**: append-only at the entry level. Same `pattern_name` while still `pending`: existing entry's `関連タスク` / `関連 raw ファイル` are merged in (no new entry). Same `pattern_name` after a terminal status (`approved` / `rejected` / `merged-into-*`): a new dated entry is added so the prior decision and rationale survive as history.
- **`[TBD by Lead]`** — Service-level expectation on the secretary for clearing the queue. Today the file says "pending エントリが 5 件以上になった時点で、人間にバッチで問い合わせる" but does not bound the time the human has to respond, nor the time the secretary has to relay the answer. Stances: (a) best-effort, no SLA; (b) the secretary MUST batch-prompt within one curator cycle of crossing N=5; (c) a calendar-time bound (e.g., within 7 days of crossing N=5). Whether the human is **contractually obligated** to decide within N cycles vs. best-effort is folded into the same marker.

### 1.5 Out-of-scope: operator-personal auto-memory

The auto-memory system documented in the root `CLAUDE.md` `# auto memory` section lives at `~/.claude/projects/{project-id}/memory/` and is operator-personal: it travels with the operator, not with the repo. Set E covers the project-level `knowledge/` tree only. See §3 for the boundary statement.

- **`[TBD by Lead]`** — Whether Set E acknowledges the operator-personal memory layer as adjacent-but-out-of-scope (today's stance: this section is descriptive only and does not contract anything about `~/.claude/projects/.../memory/`), or whether the contract should additionally restrict information flow between layers (e.g., forbid copying user-private memories into `knowledge/curated/` or `knowledge/raw/`).

---

## 2. Curation flow (raw → curated → skill candidate)

The lifecycle that moves a learning from initial capture to a reusable skill consists of four ordered transitions. Today's behavior is captured below; transitions are written as facts. Where the contract may want to tighten or loosen the implementation, an inline `[TBD by Lead]` is placed.

### 2.1 R1 — Capture (worker / dispatcher → `knowledge/raw/`)

- **Trigger**: a worker finishes a task in `validation_depth: full` and has a reusable, non-obvious learning (per Set A § Role: worker and `knowledge-standards.md` "記録基準") **or** the dispatcher completes `org-retro` Step 1–3 with a reusable process learning.
- **Effect**: a new file is created at `knowledge/raw/{YYYY-MM-DD}-{topic}.md` (worker) or `knowledge/raw/{YYYY-MM-DD}-delegation-{topic}.md` (dispatcher), in the four-heading format.
- **Constraint**: minimal-mode workers skip this step. Workers MUST NOT write to `knowledge/curated/` or `knowledge/skill-candidates.md`.

### 2.2 R2 — Curate (`org-curate` → `knowledge/curated/` + `<!-- curated -->` marker + archive)

- **Trigger**: curator's `/loop 30m /org-curate` cycle (Set A Q10 default cadence) **or** a manual prompt from the secretary. Threshold gate: at least 5 unmarked raw entries (`org-curate` Step 1); below threshold, the curator returns immediately.
- **Effect**: read all unmarked raw entries, classify by theme, dedup against `knowledge/curated/`, write merged content to `knowledge/curated/{topic}.md` (Step 3), then mark each consumed raw entry with `<!-- curated -->` (Step 4). Per Set A Q9, consumed raw entries MAY then be moved to `knowledge/raw/archive/`; deletion is forbidden.
- **Constraint**: curator's cwd is `.curator/`; paths to parent-repo `knowledge/` MUST be parent-repo-relative or absolute (per Set A § Role: curator "Path discipline").

### 2.3 R3 — Skill-candidate evaluation (`skill-eligibility-check` → `knowledge/skill-candidates.md`)

- **Trigger**: invoked from `org-retro` Step 4 (post-delegation, single-task evaluation) **or** from `org-curate` Step 2.5 (curation-time, theme-cluster evaluation). Curation-time gate per `org-curate` Step 2.5: same theme has ≥ 3 unmarked raw entries (the Set A Q11 ratified threshold), or the theme has procedural / step-shaped content not yet covered by a curated note.
- **Effect**: 5-signal scoring per `references/signals.md`; decision ∈ `{skill_recommend, candidate_queue, curated_only}`. On `skill_recommend`, Step 4 auto-appends to `knowledge/skill-candidates.md` (or merges into an existing `pending` entry of the same `pattern_name`).
- **Constraint**: `skill-eligibility-check` does NOT prompt the human and does NOT create skills. Curated-note creation in §2.2 proceeds independently of the decision (skill creation and curated-note recording coexist, per `org-curate` Step 2.5 commentary).

### 2.4 R4 — Skill promotion (secretary + human → `.claude/skills/{name}/SKILL.md`)

- **Trigger**: `skill-candidates.md` `pending` count crosses N=5 (per `skill-audit` Step 1 and the Issue #68 batch-question rationale). Secretary batch-prompts the human; for each candidate the human approves, the secretary updates `status: approved` and creates `.claude/skills/{name}/SKILL.md` from `.claude/skills/org-retro/references/work-skill-template.md`.
- **Effect**: new skill file under `.claude/skills/{name}/`; `skill-candidates.md` entry transitions to `approved` (with `決定日`) or `rejected` (with `却下理由`). `merged-into-{existing-skill}` is used when the candidate's value is folded into an existing skill rather than creating a new one.
- **Constraint**: terminal-status entries are retained as history, not deleted (per the file's "運用メモ").
- **`[TBD by Lead]`** — Decision authority and approval mechanics for promotion. Today the secretary relays and the human decides. Stances: (a) human is sole authority, secretary only batches; (b) secretary may auto-approve `score: 5/5` candidates and only batches the rest; (c) an explicit second reviewer (e.g., dispatcher) is consulted for high-impact promotions. Today's behavior is (a); the contract should pin which.

---

## 3. Worker, curator, and operator-memory boundaries

### 3.1 Worker boundary

- **MAY write**: `knowledge/raw/{YYYY-MM-DD}-{topic}.md`, full-validation mode only. Topic prefix MUST NOT collide with the dispatcher's `delegation-` namespace.
- **MUST NOT write**: `knowledge/curated/`, `knowledge/raw/archive/`, `knowledge/skill-candidates.md`. Workers also cannot reproduce the `knowledge/` directory inside their `worker_dir` (per Set A § Role: worker constraints).
- **MAY read**: `knowledge/curated/` and `knowledge/raw/` as reference material (per Set A § Role: worker reads).

### 3.2 Curator boundary

- **MAY write**: `knowledge/curated/`, `knowledge/raw/archive/` (move-target only), `knowledge/skill-candidates.md` indirectly via `skill-eligibility-check`. The `<!-- curated -->` marker prepended to a raw entry is the one in-place mutation the curator performs on `knowledge/raw/` content during R2.
- **MUST NOT write**: `.state/`, `registry/`, worker directories (per Set A § Role: curator constraints).
- **MUST NOT delete**: any `knowledge/raw/{YYYY-MM-DD}-{topic}.md` entry. Archival via move into `knowledge/raw/archive/` is the only sanctioned removal from the active raw set.
- **No human dialogue**: per Set A § Role: curator. Promotion-question relay to the human is the secretary's responsibility (§2.4).

### 3.3 Operator-personal memory layer

The auto-memory layer at `~/.claude/projects/{project-id}/memory/` is operator-local and travels with the operator's workstation, not with the repo. Set E does not contract its schema, lifecycle, or contents.

- **Implication today**: information may flow from operator memory → harness `knowledge/` (when the operator paraphrases something they remember into a raw entry), but the reverse is not contractually required (the harness does not push curated knowledge back into operator memory).
- See `[TBD by Lead]` in §1.5 on whether Set E should additionally constrain cross-layer flow.

### 3.4 Privacy / OSS-publication stance

- **`[TBD by Lead]`** — Whether `knowledge/raw/` and `knowledge/curated/` are considered shareable artifacts (committable to a public OSS repo) or may contain operator-private content (operator names, internal-system identifiers, etc.) that requires scrubbing before publication. Today both directories sit in version control with no scrubbing convention; the contract should pin the privacy stance and, if "shareable," who owns the scrub step.

---

## 4. Versioning of knowledge artifacts

The knowledge tree today carries no schema-version field on any artifact. The four-heading raw format and the H1/H2 curated format are convention-only.

- **`[TBD by Lead]`** — Whether `knowledge/curated/{topic}.md` (and, by extension, raw entries) should carry a `schema_version` field (HTML-comment header or YAML front-matter). Set C § 4.1 Q3 takes a "hybrid" stance for state files (JSON versioned individually, Markdown versioned implicitly via the contract); Set E should either inherit that stance or diverge with an explicit reason. Default candidate: NO `schema_version` on Markdown knowledge files (consistent with Set C hybrid), with format changes contracted via Set E version bumps.

---

## 5. Decisions to ratify (open questions consolidated)

The Lead-fill-in markers above are the explicit fill-in points. They cluster as follows:

1. **Retention of `knowledge/raw/archive/`** — permanent, or human-driven pruning bound (§1.2).
2. **Append-only obligation for `knowledge/curated/`** — dedup-rewrite-only inside `org-curate`, or free in-place edit by curator (§1.3).
3. **Curated naming convention** — per-axis split vs. permitted mixing of technical-area / tool / process axes within a single `{topic}.md` (§1.3).
4. **Skill-candidate clearing SLA** — best-effort vs. cycle-bounded vs. calendar-bounded; whether the human is contractually obligated to decide within N cycles (§1.4).
5. **Operator-memory boundary acknowledgment** — descriptive-only (today) vs. additional cross-layer flow restrictions (§1.5).
6. **Skill-promotion decision authority** — sole human, secretary auto-approval for top-scoring candidates, or multi-reviewer (§2.4).
7. **Privacy / OSS-publication stance** — shareable vs. operator-private-permitted, plus scrub ownership (§3.4).
8. **`schema_version` on knowledge Markdown files** — adopt or refuse, alignment with Set C hybrid stance (§4).

These are the design decisions that must be settled before Contract Set E is ratified; the structural skeleton above (artifact inventory, curation flow, role-write boundaries) is fixed.
