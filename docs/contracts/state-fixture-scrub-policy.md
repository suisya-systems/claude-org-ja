# `.state/` Fixture PII / Narrative Scrub Policy

> **Status**: **Normative target + current gap = none yet, this is the first
> version**.
>
> This contract fixes the design decisions for scrubbing operator-personal
> content from `.state/` (and the closely-coupled `registry/`) snapshots
> before they are committed as Phase 4 schema-migration test fixtures
> against the published `claude-org-runtime` package. It is a **design-only**
> deliverable: no scrubber code is introduced in this revision, and no
> fixture is checked in. The scrubber implementation, the fixture commits,
> and the migrate-script test wiring are tracked as follow-up work whose
> shape is constrained by §1–§4 below.
>
> The four decisions ratified here are:
>
> 1. **Scrub categories — MUST / SHOULD / MAY** (§1).
> 2. **Manual vs. automated scrub, and where the automated scrubber lives**
>    (§2).
> 3. **Fixture count and selection criteria** (§3).
> 4. **Storage location and `.gitignore` policy** (§4).
>
> **Related authoritative docs**:
> [`docs/contracts/state-schema-contract.md`](./state-schema-contract.md)
> defines the `.state/` schema surface (Set C); this contract layers on
> top and inherits the §1 file inventory and §4 migration-policy framing.
> [`docs/journal-events.md`](../journal-events.md) is the catalog of
> journal events that §1 below carves scrub categories out of.
> [`docs/contracts/knowledge-curation-contract.md`](./knowledge-curation-contract.md)
> §3.4 is the sibling policy for `knowledge/`; same shareable-by-default
> principle, different surface.
>
> **Out of scope**: scrubbing of `knowledge/` content, of source code, of
> commit messages, or of anything outside `.state/` and `registry/`. The
> scrubber implementation, regex catalogs, and CI wiring are deferred to
> the implementation PR that follows this design.

---

## 1. Decision 1 — Scrub category classification (MUST / SHOULD / MAY)

The fixture scrubber operates on snapshots of the file inventory defined
in [`docs/contracts/state-schema-contract.md`](./state-schema-contract.md)
§1: `.state/org-state.md`, `.state/journal.jsonl` (or, post-M4, the
`events` table inside `.state/state.db`), `.state/workers/worker-*.md`,
`.state/dispatcher/inbox/*.json`, `.state/dispatcher/outbox/*.md`,
`.state/dispatcher-event-cursor.txt`, plus `registry/projects.md` and
`registry/org-config.md`. Categories below are keyed by content shape,
not by file — most appear in multiple files.

### 1.1 MUST scrub (failure = block fixture commit)

- **Absolute filesystem paths under `/home/<user>/`** — appears in
  `.state/workers/worker-*.md` `Directory:` headers, in
  `.state/dispatcher/inbox/*.json` `worker_dir`, and in `Progress Log`
  free text. Leaks the maintainer's local account name.
  Replacement: `/home/<user>/` → `/home/USER/`, with downstream paths
  preserved structurally.
- **Authentication tokens and API keys** — `gh` OAuth tokens, GitHub-app
  PATs, AWS access keys, or any secret-shaped string that may have
  leaked into journal `note=` fields, `dispatcher.log` tails copied
  into worker reports, or error-log fragments. The sandbox-probe notes
  (`docs/sandbox-probe/`) record multiple historical `oauth_token`
  leaks into dispatcher stdout; assume historical snapshots have been
  touched by similar leaks. Replacement: `<REDACTED:token>`.
- **Private-repo URLs and internal hosts** — GitHub issue / PR URLs
  for `<org>/<repo>` outside the OSS allowlist; ngrok tunnel URLs
  (`*.ngrok.io`, `*.ngrok-free.app`); `*.internal` hostnames;
  `localhost:<port>` references to operator-personal services.
  Replacement: `https://github.com/<ORG>/<REPO>/...` placeholder;
  ngrok URLs collapse to `https://<TUNNEL>.example/`.
- **`Suspended` section session narrative in `.state/org-state.md`** —
  the free-text Lead-conversation transcript `/org-suspend` writes
  into `## Resume Instructions`. The most operator-personal surface in
  `.state/` (verbatim conversation excerpts, in-flight intentions,
  Lead voice). Replaced wholesale with
  `## Resume Instructions\n<scrubbed>`, preserving section structure
  without content.

Failure to scrub any of the above causes either a privacy leak or an
attribution leak. The scrubber treats unscrubbed instances as a hard
error and refuses to emit a fixture.

### 1.2 SHOULD scrub (failure = warning, fixture-commit allowed with explicit ack)

- **Internal repository / project / organization names not on the OSS
  allowlist** — bare identifiers in `registry/projects.md` and in
  `<workers_dir>/<project_slug>/` paths. The OSS allowlist (today:
  `claude-org-ja`, `claude-org-runtime`, `core-harness`, `renga`) is
  the set of slugs known intentionally public. SHOULD rather than
  MUST because some names are de-facto public on GitHub but not yet
  declared on the allowlist, so a hard-fail produces false positives
  at refresh time. Replacement: `project-N` monotonic placeholder,
  stable across slices.
- **Free-text `note=` / `reason=` / `summary=` fields in journal
  events** — typed in [`docs/journal-events.md`](../journal-events.md)
  as descriptive. Empirically these carry stack traces, partial Lead
  quotes, or copy-paste fragments. Replacement: `<scrubbed-note>`,
  preserving field presence and JSON validity. SHOULD rather than
  MUST because a subset carries only mechanical content
  (e.g., `reason="ci_completed"`); the scrubber may carve out a
  known-safe allowlist (initial: `reason ∈ {"ci_completed",
  "manual_close"}` passes through verbatim).
- **Worker report bodies in `.state/workers/worker-*.md` `## Progress
  Log`** — free-form Markdown that workers and the secretary
  alternately append to. Commonly carry task-specific verbiage; rarely
  tokens or Lead-narrative (those route to §1.1). Replacement: collapse
  body to `<N entries scrubbed>`, preserving the section header and
  bullet count for migrate-test assertions.

### 1.3 MAY scrub (preserved by default; off-by-default flags)

- **Task IDs / peer IDs / pane IDs / run IDs**. Operator-local but
  carry no schematic privacy weight (a leaked `task_id` reveals only
  "a task by that name existed"). Load-bearing for migration tests
  (joins between `org-state.md`, `worker-*.md`, and the `events`
  table all key on `task_id`). Default = preserve; `--strip-ids`
  remaps to monotonic `task-N` placeholders, stable per slice.
- **Timestamps** in `occurred_at`, `Suspended:`, `Resumed:`, etc.
  Default = preserve. Useful for migrate-test assertions about
  ordering. Operators who treat session cadence as private use
  `--shift-timestamps <delta>` to translate the slice to an arbitrary
  epoch (relative ordering preserved).

### 1.4 Alternatives considered

- **All categories MUST.** Rejected: the `note=` allowlist (§1.2) shows
  a blanket MUST produces tests that cannot distinguish field presence
  from absence.
- **Two-tier MUST / MAY only.** Rejected: the OSS allowlist boundary
  lives in a middle tier — hard-fail on every undeclared name blocks
  refreshes on transient metadata gaps; permissive MAY underprotects.
- **Per-file rather than per-category classification.** Rejected:
  categories cross multiple files; per-category aligns with how the
  scrubber regex catalog will be organized.

### 1.5 Chosen option for §1

Three-tier classification (MUST / SHOULD / MAY), keyed on content
category not on file. MUST → hard failure; SHOULD → warning +
explicit-ack; MAY → off by default and flag-gated. The scrubber
implementation owns the regex / parser-level encoding of each category
and of the OSS allowlist; this contract owns only the classification.

---

## 2. Decision 2 — Manual vs. automated scrub, and the scrubber's home

### 2.1 Manual vs. automated

**Chosen: automated.** Manual scrub ("operator applies §1 rules by
eye") is rejected because (a) §1.1 token patterns are mechanically
detectable and the human eye demonstrably misses them — the
sandbox-probe history records repeated leaks slipping past manual
review; (b) repeated fixture refresh is only viable if the scrub step
is a one-line command; (c) the consuming runtime test suite is itself
automated, so an asymmetry where consumption is automated but
production is manual is operationally fragile.

The CLI surface, at minimum:

- `scrub <input-dir> <output-dir>` — apply MUST + SHOULD by default;
  fail on any MUST instance the regex catalog does not match (unknown
  shapes block by default, not pass through).
- `--strip-ids` / `--shift-timestamps <delta>` — opt-in MAY-tier flags
  per §1.3.
- `--ack <category>=<count>` — explicit acknowledgement of N expected
  SHOULD-tier rewrites in this run; mismatches fail. This prevents
  silent drift from "scrubber removed two notes" to "scrubber removed
  twenty notes" without operator review.

### 2.2 Where the scrubber lives — three candidates

- **(a) `tools/state_fixture_scrub.py` in this repo.** Pro: fixtures
  originate here; operator running the scrub is already in this repo;
  the regex catalog co-locates with the schema-of-record at
  [`docs/contracts/state-schema-contract.md`](./state-schema-contract.md)
  §1. Con: the consuming runtime cannot regenerate the fixture from a
  clean checkout of the runtime package alone — it needs a checkout of
  this repo. Acceptable because fixture refresh is an operator action,
  not a CI-from-zero action.
- **(b) `tests/scrub.py` in `claude-org-runtime`.** Pro: co-locates the
  scrubber with the test that consumes the fixture. Con: fixtures
  originate in this repo's schema; the runtime would need to vendor a
  copy of the file inventory to know what to scrub, and drift between
  the schema-of-record and the vendored catalog is a real failure
  mode.
- **(c) Tiny independent package depended on by both.** Pro: a single
  source of truth for the regex catalog. Con: adds a third release
  surface (versioning, changelog, tag) for what is a single-file tool.
  Premature for today's problem size.

### 2.3 Chosen option for §2

**(a) — `tools/state_fixture_scrub.py` in this repo.** The
schema-of-record and the operator-trigger surface are both here; the
runtime test suite consumes static, pre-scrubbed artifacts, which is
the natural producer / consumer split. The scrubber's regex catalog
references the file inventory in
[`docs/contracts/state-schema-contract.md`](./state-schema-contract.md)
§1 by section anchor (no copy); when the schema evolves, scrubber and
contract co-update in this repo. If the catalog later grows past one
file, option (c) becomes attractive as a future migration; this
contract does not preclude it.

### 2.4 Alternatives considered

- **(b) inverted — runtime CI re-scrubs on every run.** Rejected:
  would require importing operator-personal snapshots into the
  runtime — exactly the boundary the scrubber exists to enforce. The
  static-artifact split (this repo scrubs, runtime consumes pre-scrubbed)
  is the right one.
- **No CLI — one-shot script per fixture refresh.** Rejected: precludes
  the `--ack` invariant in §2.1, the main lever against silent drift.

---

## 3. Decision 3 — Fixture count and selection criteria

### 3.1 Recommendation: **3 slices**, spanning schema eras

A migrate script's reason for existing is to handle multiple on-disk
versions. A single-slice fixture only exercises the no-op
"current → current" path. Multiple slices spanning eras exercise the
actual migration logic. Three is the minimum slice count that
exercises the migrator's *two* schema-version transitions
(`pre → mid` and `mid → post`); two slices force the migrate test to
choose one transition or the other. The three eras chosen mirror the
schema evolution recorded in
[`docs/contracts/state-schema-contract.md`](./state-schema-contract.md)
§4 and [`docs/journal-events.md`](../journal-events.md):

- **`pre-phase3`** — `.state/journal.jsonl` flat-file era; `org-state.md`
  with the wider `STATUS` enum (`PENDING` / `BLOCKED` documented in the
  schema doc but pre-Set-B-ratification).
- **`mid-phase3`** — `.state/journal.jsonl` still flat-file but with
  the Set B canonical `STATUS` vocabulary
  (`{IN_PROGRESS, REVIEW, COMPLETED, ABANDONED}`); `org-state.json`
  snapshot shape v1 stable; pre-M4 journal storage.
- **`post-phase3`** — post-M4: journal events live in
  `.state/state.db` (`events` table); `.state/journal.jsonl`
  retired / migration-only.

### 3.2 Selection criteria per slice

For each slice the fixture is the **smallest viable** snapshot that
exercises every distinct row schema the migrate script must handle:

- At least one row in `## Active Work Items` per `STATUS` value
  available in that era.
- At least one entry in `## Worker Directory Registry` per `pattern`
  ∈ `{A, B, C}`.
- At least one journal event per event-type group per
  [`docs/journal-events.md`](../journal-events.md) (worker lifecycle,
  delegation, planning, CI, knowledge-curation), with payload shapes
  representative of that era.
- At least one `.state/workers/worker-*.md` per `Status` ∈
  `{planned, active, pane_closed, completed}`.

"Smallest viable" means: do **not** include the full inventory's
worker files, only the minimum set covering the combinatorial
requirement above. The scrubber emits a `fixture-manifest.json` per
slice listing which rows / events / files were retained and why; the
manifest is itself part of the fixture and is consumed by the
migrate-test assertions.

### 3.3 Alternatives considered

- **One slice (smallest viable post-phase3 only).** Rejected: a
  single-version fixture is a tautological test surface for a migrator.
- **Per-era full snapshot, no minimization.** Rejected: bloats the
  fixture and embeds incidental content the scrubber must then strip.
- **More than three eras (one slice per `org-state.json` `version`
  bump).** Deferred: today only one `version` integer exists. The
  contract rule is "≥ 1 slice per schema-version transition
  exercised", which yields three today and yields more later.

### 3.4 Chosen option for §3

Three slices (`pre-phase3`, `mid-phase3`, `post-phase3`), each the
smallest viable snapshot per §3.2, each accompanied by a
`fixture-manifest.json`. Slice count grows with schema-version
transitions, not with calendar time.

---

## 4. Decision 4 — Storage location and `.gitignore` policy

### 4.1 Recommendation: **in-repo, committed (post-scrub) under the runtime's `tests/fixtures/state-migration/`**

For an open-source project, in-repo committed fixtures are the most
reproducible: a fresh checkout of the runtime package, paired with a
fresh checkout of this repo, regenerates every Phase 4 migrate-test
result deterministically. There is no out-of-band artifact store, no
expiring URL, no "you also need access to <internal-bucket>". Same
principle as
[`docs/contracts/knowledge-curation-contract.md`](./knowledge-curation-contract.md)
§3.4: shareable by default after operator-private content has been
scrubbed.

The fixture lives at `tests/fixtures/state-migration/<slice>/` in the
consuming runtime repo (per the §2.3 producer / consumer split), with
each `<slice>` ∈ `{pre-phase3, mid-phase3, post-phase3}`. Every file
under that subtree is post-scrub by construction; pre-scrub snapshots
never enter the runtime repo's working tree.

### 4.2 `.gitignore` policy in this (producer) repo

Operator-personal `.state/` snapshots remain gitignored exactly as
today — `.state/` is gitignored at repo root modulo `.gitkeep` files.
The scrubber operates on a copy of `.state/` into a tmpdir specified
at the CLI; nothing the scrubber reads becomes tracked here.
Specifically:

- `.state/` — gitignored (status quo).
- `tools/state_fixture_scrub.py` — committed (the tool itself, no
  fixtures).
- `<TMPDIR>/state-fixture-out/` — operator-chosen out-dir; never
  inside the worktree of this repo. The operator copies the scrubbed
  output into the runtime repo's `tests/fixtures/state-migration/`
  manually after a final `git diff` review, and commits it there.

### 4.3 `.gitignore` policy in the (consumer) runtime repo

`tests/fixtures/state-migration/` is **committed**, not gitignored.
The committed content is post-scrub by construction: the scrubber's
hard-fail behavior on unscrubbed §1.1 entries is the mechanism that
prevents an accidental commit of operator-personal content. A
committed-but-gitignored hybrid is rejected (§4.4).

### 4.4 Alternatives considered

- **In-repo, gitignored.** Rejected: defeats reproducibility — every
  consumer would need to reproduce the fixture independently;
  collapses to out-of-repo without the explicit fetch step.
- **Out-of-repo, fetched from a private store.** Rejected: introduces
  a credential boundary and an availability dependency. Inappropriate
  for an open-source test suite.
- **Out-of-repo, fetched from a public store** (e.g., a GitHub release
  asset). Deferred: not better than committed at today's size (each
  slice < 200 KB after scrub); revisit only if fixture size grows past
  a threshold where runtime-repo history bloat becomes material.
- **Committed in this repo, not the runtime.** Rejected: the consumer
  is the runtime test suite; co-locating with the consumer matches
  the §2.3 split and avoids a cross-repo build dependency in runtime
  CI.

### 4.5 Chosen option for §4

Fixtures committed in the runtime repo at
`tests/fixtures/state-migration/<slice>/`, post-scrub by construction.
This repo's `.state/` remains gitignored. The scrubber emits to an
operator-chosen tmpdir that is never inside any tracked worktree.
