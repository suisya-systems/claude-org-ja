# Contract Set E — Knowledge & Curation Boundaries

> **Status**: Ratified (2026-05-03). Lead-confirmed decisions for all 9 open questions. This contract defines the knowledge-and-curation surface that the `claude-org` harness reads and writes.
>
> **Scope**: Phase 1 Contract Set E only. Sets A (roles), B (delegation lifecycle), C (state schema), and D (backend interface) are tracked in #121 / #122 / #124 / #123 and out of scope here. Set E is marked "optional, small" in Issue #125: the open-questions surface is intentionally narrower than the larger sets.
>
> **Subject**: Set E defines the knowledge artifacts under `knowledge/` (raw learnings, archived raw entries, curated notes, skill-candidate queue), the lifecycle that moves entries between them, and the role-write boundaries on each surface. The project-level `knowledge/` tree is the only knowledge layer Set E contracts; any operator-personal memory layer that may exist outside the repo (in the operator's Claude Code installation) is explicitly out of scope and not asserted to exist by this contract. Set E does NOT cover: per-role responsibilities (Set A — though it cites them for the curator / worker write-surface invariants), `.state/` files (Set C), the messaging or backend transport (Set D), or the delegation lifecycle (Set B).
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

The harness's knowledge surface comprises the artifacts listed below. Each entry names: **path** (repo-relative), **format**, **schema** (key sections / vocabulary), **owner** (the role authorized to write — consistent with Set A), **reader(s)** (roles that read), and **lifecycle / triggers**. Set E covers the project-level `knowledge/` tree only; any operator-personal memory layer maintained outside the repo by the operator's Claude Code installation is out of scope and is discussed only as an adjacent boundary in §3.

### 1.1 `knowledge/raw/{YYYY-MM-DD}-{topic}.md`

- **Path**: `knowledge/raw/{YYYY-MM-DD}-{topic}.md` — date prefix is the calendar date the entry is recorded; `{topic}` is English kebab-case. The dispatcher's post-retro entries use the namespaced topic prefix `delegation-{topic}` to distinguish process learnings from worker technical learnings (per `org-retro` Step 3).
- **Format**: Markdown. Body conforms to the four-heading record format in `.claude/skills/org-curate/references/knowledge-standards.md`: `## 事実`, `## 判断`, `## 根拠`, `## 適用場面`.
- **Schema**: Free-form Markdown bodies under the four canonical headings. The active `knowledge/raw/` set carries no curator-applied marker; consumed entries are removed from this directory by the move-then-mark rule below, and the `<!-- curated -->` marker (used by `org-curate` to skip already-consumed entries) lives on the archived copy in `knowledge/raw/archive/`.
- **Owner**: workers (full-validation mode only — minimal mode skips per Set A § Role: worker) and the dispatcher (post-retro process learnings, with the `delegation-` topic prefix per `org-retro` Step 3) author file contents. Secretary and the human do not write to `knowledge/raw/` in the normal flow. The curator MUST NOT mutate any entry in the active `knowledge/raw/` set in place: per the move-then-mark rule below, marking happens on the archived copy after the entry has been moved into `knowledge/raw/archive/`. The active raw set is contractually immutable from the curator's side.
- **Curator marking rule (move-then-mark)**: when the curator consumes a raw entry during `/org-curate`, it MUST first move the entry into `knowledge/raw/archive/` and only then apply the `<!-- curated -->` marker (or any other annotation) on the archived copy. In-place mutation on the active `knowledge/raw/` set is prohibited. This is consistent with Set A § Role: curator's archive-only authority over raw entries. The current `org-curate` Step 4 implementation marks in place and is therefore non-conformant; tracked as a follow-up Issue ("feat(org-curate): switch curated marking from in-place mutation to move-then-mark"). The contract describes the end state; the implementation switch is the follow-up.
- **Readers**: curator (`org-curate` reads all unmarked entries), `skill-eligibility-check` (consumes `raw_files` arg as evidence), worker (read-only reference per Set A worker section), `skill-audit` (greps for skill-name mentions over a 90-day window per `skill-audit` Step 2).
- **Lifecycle**: created at the moment of recording (worker post-task or dispatcher post-retro). After being merged into a curated note, the curator moves the file into `knowledge/raw/archive/` and then applies the `<!-- curated -->` marker on the archived copy (move-then-mark, per the curator marking rule above and Set A Q9). Outright deletion is forbidden.

### 1.2 `knowledge/raw/archive/`

- **Path**: `knowledge/raw/archive/` — destination for raw entries that have been consumed by curation.
- **Format**: Same as 1.1 (the file is moved, not transformed); files retain their `<!-- curated -->` marker.
- **Owner**: curator (move-only authority; per Set A § Role: curator constraints and Q9 ratified decision, the curator may archive but MUST NOT delete raw entries).
- **Readers**: curator (occasional re-read for context when a related raw re-appears); `skill-audit` (in scope: the 90-day grep window covers archived entries too).
- **Lifecycle**: append-only by move. Entries are never moved back out of `archive/`.
- **Retention**: `knowledge/raw/archive/` is contractually permanent. Archived entries are NOT subject to any retention bound: storage cost is negligible and historical value is high. Pruning, if ever undertaken, is a one-shot human-driven maintenance action and is explicitly NOT automated; no skill, hook, or scheduled job in this harness is permitted to delete archived raw entries.

### 1.3 `knowledge/curated/{topic}.md`

- **Path**: `knowledge/curated/{topic}.md` — `{topic}` is English kebab-case. Topic granularity guidance lives in `org-curate` Step 2 (technical area / tool-or-service / process).
- **Format**: Markdown. Body conforms to `org-curate` Step 3: `# {テーマ名}` H1, then per-knowledge `## {知見タイトル}` H2 sections that synthesize the four-heading content from the underlying raw entries.
- **Schema**: Free-form Markdown under the H1 / H2 structure above. No version field today.
- **Owner**: curator only. Workers, dispatcher, and secretary MUST NOT write here (per Set A § Role: curator: "write surface is `knowledge/curated/` and the skill-candidate queue only").
- **Readers**: secretary (read-only reference per Set A § Role: secretary "Local files (read)"), worker (read-only reference per Set A § Role: worker), human via direct view, the curator itself (dedup checks during the next curation cycle).
- **Lifecycle**: created or appended to during `org-curate` Step 3. Existing sections may be merged or rewritten when consolidating new raw entries (per the merge / conflict rules in `knowledge-standards.md`).
- **Edit policy**: the curator MAY freely restructure, dedup, merge, or delete sections within `knowledge/curated/{topic}.md` both during a `/org-curate` cycle AND out-of-cycle. An append-only constraint is explicitly rejected because it contradicts the curator's purpose (dedup is the curator's job). The only constraint is that restructures MUST preserve the substantive content of consumed inputs: silent removal of curated learnings without rationale is forbidden.
- **Naming convention**: `{topic}` is recommended to follow one of the three advisory categories in `org-curate` Step 2 (technical-area / tool-or-service / process), but a single curated file MAY mix categories where that yields a more cohesive note (e.g., `renga-peers.md` covering both tool usage and tool-failure recovery). Forced per-axis splitting is not required and is treated as an over-segmentation hazard.

### 1.4 `knowledge/skill-candidates.md`

- **Path**: `knowledge/skill-candidates.md` — single-file queue of `skill_recommend` outputs.
- **Format**: Markdown. Per-candidate blocks delimited by `### {YYYY-MM-DD} {pattern-name}` H3 headings; bullet fields per the entry-format block in the file itself (`判定スコア`, `該当シグナル`, `根拠`, `関連タスク`, `関連 raw ファイル`, `呼び出し元`, `提案 skill 名`, `status`, `決定日`, `却下理由`, `統合先`).
- **Schema**: `status` ∈ `{pending, approved, rejected, merged-into-{existing-skill}}`. Once an entry leaves `pending`, it is retained as history (not deleted) per the file's "運用メモ" section.
- **Owner**: `skill-eligibility-check` Step 4 (auto-append on `skill_recommend` decisions). Status transitions (`pending` → `approved` / `rejected` / `merged-into-*`, plus `決定日` / `却下理由` / `統合先` fields) are written by the worker that performs the corresponding promotion task (per the §2.4 execution-path decision). The curator does NOT directly hand-edit this file; its writes happen through `skill-eligibility-check`. The secretary does NOT edit this file in place; for human-relayed batch decisions, the secretary delegates the status update to a worker alongside the skill creation.
- **Readers**: secretary (batch question to the human when `pending ≥ 5`, per Issue #68 batch-rationale cited in the file header), `skill-audit` Step 1 (counts `pending` entries to decide whether to fire), the curator (re-read during the next curation cycle to detect already-queued patterns).
- **Lifecycle**: append-only at the entry level. Same `pattern_name` while still `pending`: existing entry's `関連タスク` / `関連 raw ファイル` are merged in (no new entry). Same `pattern_name` after a terminal status (`approved` / `rejected` / `merged-into-*`): a new dated entry is added so the prior decision and rationale survive as history.
- **Clearing SLA**: best-effort, no contractual SLA. The N=5 `pending` threshold remains the trigger for the secretary to batch-prompt the human, but neither the secretary's relay time nor the human's response time is contracted. The human is NOT contractually obligated to decide within any number of curator cycles or any calendar-time bound. This is consistent with Set A's general "no human-response-time contract" principle.

### 1.5 Out-of-scope: operator-personal memory layers

Some Claude Code operator installations may carry a personal memory layer outside the repository (e.g., a per-operator memory store maintained by the operator's Claude Code harness). Such layers are operator-personal and travel with the workstation, not with the repo. Set E covers the project-level `knowledge/` tree only and makes no factual claim about whether any such operator-personal layer exists in a given installation. See §3 for the boundary statement.

- **Stance**: Set E is silent on operator-personal memory layers. They are out of Set E scope and are governed by neither Set E nor any other Phase 1 contract. The existence of such a layer is noted as a cross-reference for readers (see §3.3) but its boundary with `knowledge/` is NOT contracted here. Operator-private-content scrubbing is handled by §3.4 regardless of whether the content originated from an operator-personal memory layer or any other source.

---

## 2. Curation flow (raw → curated → skill candidate)

The lifecycle that moves a learning from initial capture to a reusable skill consists of four ordered transitions. Each transition is written as a fact; ratified Lead decisions are inlined.

### 2.1 R1 — Capture (worker / dispatcher → `knowledge/raw/`)

- **Trigger**: a worker finishes a task in `validation_depth: full` and has a reusable, non-obvious learning (per Set A § Role: worker and `knowledge-standards.md` "記録基準") **or** the dispatcher completes `org-retro` Step 1–3 with a reusable process learning.
- **Effect**: a new file is created at `knowledge/raw/{YYYY-MM-DD}-{topic}.md` (worker) or `knowledge/raw/{YYYY-MM-DD}-delegation-{topic}.md` (dispatcher), in the four-heading format.
- **Constraint**: minimal-mode workers skip this step. Workers MUST NOT write to `knowledge/curated/` or `knowledge/skill-candidates.md`.

### 2.2 R2 — Curate (`org-curate` → `knowledge/curated/` + move-then-mark into `knowledge/raw/archive/`)

- **Trigger**: curator's `/loop 30m /org-curate` cycle (Set A Q10 default cadence) **or** a manual prompt from the secretary. Threshold gate: at least 5 unmarked raw entries (`org-curate` Step 1); below threshold, the curator returns immediately.
- **Effect**: read all unmarked raw entries, classify by theme, dedup against `knowledge/curated/`, write merged content to `knowledge/curated/{topic}.md` (Step 3), then for each consumed raw entry move the file into `knowledge/raw/archive/` and apply the `<!-- curated -->` marker on the archived copy (Step 4, move-then-mark per §1.1). Deletion of raw entries is forbidden (Set A Q9).
- **Constraint**: curator's cwd is `.curator/`; paths to parent-repo `knowledge/` MUST be parent-repo-relative or absolute (per Set A § Role: curator "Path discipline").

### 2.3 R3 — Skill-candidate evaluation (`skill-eligibility-check` → `knowledge/skill-candidates.md`)

- **Trigger**: invoked from `org-retro` Step 4 (post-delegation, single-task evaluation) **or** from `org-curate` Step 2.5 (curation-time, theme-cluster evaluation). Curation-time gate per `org-curate` Step 2.5: same theme has ≥ 3 unmarked raw entries (the Set A Q11 ratified threshold), or the theme has procedural / step-shaped content not yet covered by a curated note.
- **Effect**: 5-signal scoring per `references/signals.md`; decision ∈ `{skill_recommend, candidate_queue, curated_only}`. On `skill_recommend`, Step 4 auto-appends to `knowledge/skill-candidates.md` (or merges into an existing `pending` entry of the same `pattern_name`).
- **Constraint**: `skill-eligibility-check` does NOT prompt the human and does NOT create skills. Curated-note creation in §2.2 proceeds independently of the decision (skill creation and curated-note recording coexist, per `org-curate` Step 2.5 commentary).

### 2.4 R4 — Skill promotion (secretary + human → `.claude/skills/{name}/SKILL.md`)

- **Trigger**: `skill-candidates.md` `pending` count crosses N=5 (per `skill-audit` Step 1 and the Issue #68 batch-question rationale). Secretary batch-prompts the human. On human approval, the secretary delegates skill creation (`.claude/skills/{name}/SKILL.md` from `.claude/skills/org-retro/references/work-skill-template.md`) and the corresponding `skill-candidates.md` status update to a worker via `org-delegate`, per the §2.4 execution-path decision below.
- **Effect**: new skill file under `.claude/skills/{name}/`; `skill-candidates.md` entry transitions to `approved` (with `決定日`) or `rejected` (with `却下理由`). `merged-into-{existing-skill}` is used when the candidate's value is folded into an existing skill rather than creating a new one.
- **Constraint**: terminal-status entries are retained as history, not deleted (per the file's "運用メモ").
- **Decision authority**: the human is the sole decision authority for skill promotion. The secretary's contracted role is to batch-prompt the human once the `pending` count crosses N=5 (per §1.4). Auto-approval by the secretary, and second-reviewer consultation by the dispatcher, are explicitly NOT contracted.
- **Execution path**: skill creation (writing `.claude/skills/{name}/SKILL.md` from the work-skill template, plus the corresponding `skill-candidates.md` status transition) is delegated to a worker task via `org-delegate`, consistent with Set A's principle "実作業は全てワーカーに委譲する". The secretary MUST NOT edit skill files in place, and the dispatcher MUST NOT write skill files even when the candidate originated from `org-retro` Step 4.2; in that case the dispatcher's role is limited to recording the queue transition through `skill-eligibility-check`'s normal append path.

---

## 3. Worker, curator, and operator-memory boundaries

### 3.1 Worker boundary

- **MAY write**: `knowledge/raw/{YYYY-MM-DD}-{topic}.md`, full-validation mode only. Topic prefix MUST NOT collide with the dispatcher's `delegation-` namespace.
- **MUST NOT write**: `knowledge/curated/`, `knowledge/raw/archive/`, `knowledge/skill-candidates.md`. Workers also cannot reproduce the `knowledge/` directory inside their `worker_dir` (per Set A § Role: worker constraints).
- **MAY read**: `knowledge/curated/` and `knowledge/raw/` as reference material (per Set A § Role: worker reads).

### 3.2 Curator boundary

- **MAY write**: `knowledge/curated/`, `knowledge/raw/archive/` (move-target, plus annotations such as the `<!-- curated -->` marker applied to entries already moved into `archive/`), `knowledge/skill-candidates.md` indirectly via `skill-eligibility-check`. Per the move-then-mark rule in §1.1, the curator MUST NOT mutate entries in the active `knowledge/raw/` set.
- **MUST NOT write**: `.state/`, `registry/`, worker directories (per Set A § Role: curator constraints).
- **MUST NOT delete**: any `knowledge/raw/{YYYY-MM-DD}-{topic}.md` entry. Archival via move into `knowledge/raw/archive/` is the only sanctioned removal from the active raw set.
- **No human dialogue**: per Set A § Role: curator. Promotion-question relay to the human is the secretary's responsibility (§2.4).

### 3.3 Operator-personal memory layers (out of scope)

Any operator-personal memory layer that may exist outside the repo (in the operator's Claude Code installation) is operator-local and travels with the workstation, not with the repo. Set E does not contract its schema, lifecycle, or contents, and does not assert it exists in any particular installation.

- **Implication today**: in installations where such a layer exists, information may flow from operator memory → harness `knowledge/` (when the operator paraphrases something they remember into a raw entry), but the reverse is not contractually required (the harness does not push curated knowledge back into operator memory).
- **Boundary**: Set E does not constrain cross-layer flow specifically; the §3.4 no-operator-private-content rule applies to all content entering `knowledge/` regardless of source.

### 3.4 Privacy / OSS-publication stance

- **Stance**: shareable by default. Both `knowledge/raw/` and `knowledge/curated/` are committed to the OSS repository and treated as publishable artifacts.
- **Forbidden content**: operator-private content MUST NOT appear in either directory. This includes operator names, internal-system identifiers, customer data, secrets, internal URLs, and any other content the operator would not publish to a public repository.
- **Worker obligation**: workers MUST be informed of this no-operator-private-content rule via their delegation brief; the `org-delegate` instruction template carries the rule (tracked as a follow-up Issue: "docs(skills): add 'no operator-private content in knowledge/' guidance to org-delegate instruction template" until the template is updated).
- **Scrub ownership**: on discovery of operator-private content in `knowledge/`, the secretary owns the scrub step — either inline (for trivial redactions) or by delegating to a worker (for substantive rewrites), per Set A's secretary-vs-worker editing boundary.

---

## 4. Versioning of knowledge artifacts

The knowledge tree today carries no schema-version field on any artifact. The four-heading raw format and the H1/H2 curated format are convention-only.

- **Decision**: NO `schema_version` field on Markdown knowledge files. Set E inherits Set C § 4.1's hybrid stance: JSON files are versioned individually, Markdown files are versioned implicitly via the contract. Format changes to `knowledge/raw/` or `knowledge/curated/` Markdown bodies are tracked via Set E version bumps rather than per-file headers. If a future knowledge artifact is introduced in JSON form, Set E will revisit this clause to add per-file `schema_version` for that artifact alone, mirroring Set C's hybrid pattern.

---

## 5. Decision rationale digest

The 9 Lead-confirmed decisions ratified on 2026-05-03 cluster as follows:

1. **Curator marking on raw entries (§1.1)** — move-then-mark. Active `knowledge/raw/` is immutable from the curator; marking happens on the archived copy. Reconciles with Set A § Role: curator's archive-only authority. Implementation in `org-curate` Step 4 currently mutates in place and is tracked as a follow-up.
2. **`knowledge/raw/archive/` retention (§1.2)** — contractually permanent, no automated pruning. Storage cost is negligible; historical value high.
3. **`knowledge/curated/` edit policy (§1.3)** — free restructure permitted in- and out-of-cycle. Append-only contradicts the curator's dedup purpose; the only invariant is no silent removal of substantive content.
4. **Curated `{topic}` naming (§1.3)** — topic-based recommendation with explicit permission to mix the three advisory categories in a single file. Forced splitting is treated as an over-segmentation hazard.
5. **Skill-candidate clearing SLA (§1.4)** — best-effort, no SLA. Inherits Set A's "no human-response-time contract" principle.
6. **Operator-personal memory layers (§1.5)** — Set E silent. Out of Set E scope; cross-layer flow is governed only by the §3.4 scrub rule, which is source-agnostic.
7. **Skill promotion (§2.4)** — human is sole decision authority; execution is delegated to a worker via `org-delegate`. The secretary does not edit skill files in place; the dispatcher does not write skill files.
8. **Privacy / OSS publication (§3.4)** — shareable by default, operator-private content forbidden. Workers informed via delegation brief; secretary owns the scrub step on discovery. Worker-side rule landing in the `org-delegate` template is tracked as a follow-up.
9. **`schema_version` on Markdown knowledge files (§4)** — none. Set E inherits Set C's hybrid stance (JSON versioned individually, Markdown versioned implicitly via the contract).
