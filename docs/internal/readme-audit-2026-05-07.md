# README.md audit report — 2026-05-07

- Branch: `fix/readme-audit-20260507`
- Scope: README.md only. Compared against `.claude/skills/`, `tools/`, `.dispatcher/`, `.curator/`, `docs/sync-policy.md`, `docs/internal/phase5-decisions-2026-05-03.md`, `tools/state_db/` and recent commits (auto-mirror P2 #336, state-db M4 cutover #267, org-delegate carve-out #320).
- Method: section-by-section verification of every external claim against the current tree.

## Severity legend

- **factual error** — current README contains a statement that is verifiably wrong against the tree as of HEAD.
- **stale** — once-true statement that no longer reflects current reality after a downstream change.
- **minor** — cosmetic / wording / non-load-bearing.
- **defer** — design judgment required; not safe to fix in this audit.

## Findings

### F1. [factual error] Skill table missing `/org-escalation` and `/org-pull-request`

- **Location**: README.md §「組織ランタイム操作（`/org-*`）」table (formerly lines 241-250).
- **Evidence**: `.claude/skills/org-escalation/SKILL.md` and `.claude/skills/org-pull-request/SKILL.md` both exist as first-class skills with frontmatter. Both are introduced in commit `2edff0c` "carve org-delegate into focused skills (Closes #320)" and are referenced from `CLAUDE.md` (escalation flow + pending-decisions register). The README skill table listed only 8 org-* skills.
- **Action**: **fixed in this PR.** Added two rows.

### F2. [stale] "全 10 skill が in-tree" — actual count is 12

- **Location**: README.md §「このリポジトリに残るもの (ja-specific)」, the bullet about `.claude/skills/`.
- **Evidence**: `ls .claude/skills/` returns 12 directories (10 `org-*` + 2 `skill-*`). The "10" figure dates from `docs/internal/phase5-decisions-2026-05-03.md` (Q10/§Next-action note "残り 8 skill"), written before #320 carved out `org-escalation` and `org-pull-request` and before any later additions. Phase 5 doc itself is a frozen snapshot; the README claim is what becomes stale.
- **Action**: **fixed in this PR.** "全 10 skill" → "全 12 skill".

### F3. [stale, minor] `tools/pr_watch.*` glob does not match the actual filenames

- **Location**: README.md §「このリポジトリに残るもの (ja-specific)」, the ja 固有運用ツール bullet.
- **Evidence**: actual files are `tools/pr_watch.py`, `tools/pr-watch.ps1`, `tools/pr-watch.sh` — the underscore vs hyphen split is mixed, so the single glob `pr_watch.*` would not match the two POSIX/PowerShell entry points. CLAUDE.md correctly writes `tools/pr-watch.ps1` / `tools/pr-watch.sh`.
- **Action**: **fixed in this PR.** Replaced with `tools/pr_watch.py / tools/pr-watch.{ps1,sh}` and also expanded `tools/journal_*` → `tools/journal_append.{py,sh}` for symmetry (the only files matching that prefix today).

## Verified (no change needed)

The following README claims were checked and confirmed against HEAD:

- §用語集 entries: every one-primary-source link target exists (`CLAUDE.md`, `.dispatcher/CLAUDE.md`, `.curator/CLAUDE.md`, `.claude/skills/org-delegate/SKILL.md`, `docs/operations/m3-migration-runbook.md`).
- §前提ツール: `scripts/install.sh` does fail-close validate exactly the 4 tools listed (`git`, `claude`, `renga`, `gh`); Python is warn-only; `jq` and Node.js are not validated.
- §クイックスタート: `npm install -g @suisya-systems/renga@0.18.0` matches `tools/check_renga_compat.py` (parses "renga 0.18.0") and `scripts/install.{sh,ps1}` install hint.
- §セキュリティと許可境界: every named hook file exists in `.hooks/` (`block-no-verify.sh`, `block-dangerous-git.sh`, `block-org-structure.sh`, `block-git-push.sh`, `check-worker-boundary.sh`).
- §Layer 4 アーキテクチャ — Q1=b defer / Q5=a / Q6=b / Q7=a / Q9=c references all match `docs/internal/phase5-decisions-2026-05-03.md`.
- M4 / Issue #267: README's "`.state/journal.jsonl` は M4 で廃止" is accurate. `tools/journal_append.{py,sh}` writes to `.state/state.db` only; jsonl side-output is decommissioned per the file headers (`tools/journal_append.py` line 10 and `tools/journal_append.sh` line 6). `.state/org-state.md` regeneration via `StateWriter.transaction()` is implemented in `tools/state_db/writer.py` + `snapshotter.py`.
- §ドキュメント: every linked doc path resolves (`docs/getting-started.md`, `docs/non-goals.md`, `docs/oss-comparison.md`, `docs/verification.md`, `CONTRIBUTING.md`).
- §困ったとき: `tools/check_renga_compat.py` exists.

## Issue proposals (deferred — design judgment required)

These were deliberately **not fixed** because they require Lead-level judgment about README scope. Recommend filing as separate issues.

### Proposed Issue A — Surface auto-mirror P2 phase status in README

- **Why**: README links to `docs/sync-policy.md` from the very first banner ("英語版: ... 両リポジトリの同期ルール") but does not mention that ja → en mirroring is now actively running in **P2 (mirror PR, manual merge)** as of #336. A reader landing on the en repo today will see auto-generated `auto-mirror: ja#<N>` PRs without any context from the ja README.
- **Defer reason**: whether to surface auto-mirror phase in the top-level README (and at what depth) vs. keeping it buried in `docs/sync-policy.md` is an editorial / Lead decision. Multiple framings are possible (1-line note in banner, dedicated subsection, or no change).
- **Suggested form**: 1-line note in the banner block citing `docs/sync-policy.md` §「現在のフェーズ: P2」.

### Proposed Issue B — Reconcile worker raw-knowledge recording phrasing

- **Why**: README §「仕組み」 line "ワーカー...完了後に**生の知見を記録する**" is stated as default behavior, but worker `CLAUDE.local.md` template (this very task) phrases it as "振り返り記録: **任意**（非自明な学びがあれば...）". `.claude/skills/org-retro/SKILL.md` line 16 says "自動的に `knowledge/raw/` に記録する。ここでは扱わない" and lines 128/132 add "ワーカーが既に記録している場合はスキップ" — implying the worker is expected to record by default. The current dispatcher-generated worker brief makes it optional.
- **Defer reason**: This is an actual contract ambiguity (is raw recording mandatory or opportunistic?), not a wording issue. Resolving it requires a Lead call about the worker contract, after which README + worker brief template + org-retro SKILL all need to move together.
- **Suggested resolution path**: pin the worker contract in `docs/contracts/role-contract.md` first, then sync README phrasing.

### Proposed Issue C — README does not list `tools/pending_decisions.py`

- **Why**: the pending-decisions register (#297 / #301) is now load-bearing in CLAUDE.md (§「pending-decisions register（必須）」) and is the SoT for SECRETARY_RELAY_GAP_SUSPECTED detection. README §「ja 固有の運用ツール」 enumerates many smaller tools but omits this one.
- **Defer reason**: the README list is already long; adding one more requires a curation decision about which tools belong in the top-level enumeration vs. in the operations docs.

### Proposed Issue D — README does not advertise `org-escalation` / `org-pull-request` carve-out in the §仕組み diagram

- **Why**: §「仕組み」 still describes only Secretary / Dispatcher / Curator / Worker. After #320 the Secretary's responsibility was carved into delegation + escalation + PR-management as separate skills with distinct SKILL.md files. The skill table now mentions them (after this PR), but the prose model in §「仕組み」 / §「役割の境界」 is still a 4-role mental model.
- **Defer reason**: changing the architectural diagram / role explanation is editorial; needs Lead sign-off.

## Files changed in this PR

- `README.md` — F1, F2, F3 fixes only.
- `docs/internal/readme-audit-2026-05-07.md` — this report.

## Out of scope

- Did not audit linked docs themselves (`docs/non-goals.md`, `docs/overview-technical.md`, etc.) — only that the links resolve.
- Did not audit the en mirror (`suisya-systems/claude-org`).
- Did not change skill SKILL.md, CLAUDE.md, or contracts files.
