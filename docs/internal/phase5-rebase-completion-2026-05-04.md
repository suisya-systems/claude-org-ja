# Phase 5 (ja rebase) — completion note

**Date**: 2026-05-04
**Epic**: #101 (claude-org-ja Layer 1/2/3 抽出後の rebase)
**Issue**: #130 (Phase 5d — Epic close PR)
**DoD**: README architecture が 4 層 post-rebase 状態を反映 + 完了ノート merge + Epic #101 と Issue #130 が close される。

## Background — what Phase 5 was for

Phase 3 (Layer 1 = `core-harness` 抽出) と Phase 4 (Layer 2 =
`claude-org-runtime` 抽出) で、claude-org-ja から framework primitives
と runtime SoT が外部 OSS パッケージへ移管された。Phase 5 は
**抽出後の ja repo を thin shim 化するための rebase 作業群** で、以下の
4 つの sub-phase に分割された:

| Sub-phase | PR | スコープ |
|---|---|---|
| 5a | [#258](https://github.com/suisya-systems/claude-org-ja/pull/258) | docs sweep — 古い Phase 4 path 参照を更新 |
| 5b | [#259](https://github.com/suisya-systems/claude-org-ja/pull/259) | shim audit — Phase 3/4 で残った in-tree shim が minimal か確認 |
| 5c | [#260](https://github.com/suisya-systems/claude-org-ja/pull/260) | packaging — `pyproject.toml` 移行 + drift CI tightening |
| 5d | this PR | README architecture + completion doc + Epic close |

並行して Layer 4 内部からの **orchestration glue 抽出 (`claude-org-skills`)**
（README の 4 層定義における Layer 3 = `renga` とは独立の追加抽出案件で、
phase5-decisions doc 内では便宜上「Layer 3」と呼称）の kill / proceed
判定は **2026-08-03 頃まで defer**
（[`phase5-decisions-2026-05-03.md`](phase5-decisions-2026-05-03.md) Q1=b）。
Phase 5 (本 rebase 群) 自体は本判定を含まない。

## Final inventory state

Phase 5 完了時点で claude-org-ja に **残っている** 構成要素:

- **`.claude/skills/`** — 全 10 skill (`/org-*` 8 個 + `/skill-*` 2 個) が
  in-tree。orchestration glue 抽出 (`claude-org-skills`、Q3=a narrow scope
  では `org-delegate` + `org-start` のみ対象) は defer 中のため全 10 個が
  残置。
- **日本語 prose template** — `.dispatcher/CLAUDE.md` /
  `.curator/CLAUDE.md` ([Q5=a](phase5-decisions-2026-05-03.md) consumer-side
  override)。
- **ja locale フック** — `.hooks/` / `.githooks/` 配下、deny-message を
  日本語化したもの ([Q6=b](phase5-decisions-2026-05-03.md) 物理 path 結合の
  ため Layer 3 持ち込みなし)。
- **`dashboard/`** — 組織状態の可視化 SPA (Phase 4 Q9=c で claude-org-ja
  残置、Phase 5 [Q7=a](phase5-decisions-2026-05-03.md) で再確認)。
- **ja-specific tools** — `tools/check_renga_compat.py` /
  `tools/gen_worker_brief.py` / `tools/org_setup_prune.py` /
  `tools/journal_*` / `tools/pr_watch.*` / `tools/state_migrate.py` /
  `tools/check_role_configs.py` (CLI shim、エンジン本体は
  `core_harness.validator`)。
- **ja-specific schema / locale data** — `tools/org_extension_schema.json`
  (org-extension entries SoT) と `tools/ja_locale.json` (`LocaleConfig`
  override)。
- **install scripts** — `scripts/install.sh` / `scripts/install.ps1` /
  `scripts/install-hooks.sh`。

## What was migrated / removed

Phase 4 (#209 / [`phase4-completion-2026-05-02.md`](phase4-completion-2026-05-02.md))
で in-tree から削除:

- `tools/dispatcher_runner.py` + test (`claude-org-runtime` の dispatcher
  CLI に移管)
- `tools/generate_worker_settings.py` + test (`claude-org-runtime` の
  settings generator に移管)
- bundled `role_configs_schema.json` (runtime に同梱、ja は
  `org_extension_schema.json` のみ保持)

Phase 5b (#259) で minimality 確認済みの Phase 3/4 shim:

- `tools/check_role_configs.py` — `core_harness.validator` への CLI shim
  + ja-specific I/O。Phase 5b で「これ以上薄くできない」と判定。
- `tools/journal_append.{py,sh}` — `core_harness.audit.Journal` への
  CLI shim、minimal。

Phase 5c (#260) で packaging 改修:

- `requirements.txt` → `pyproject.toml` 移行 (SoT 明確化)
- 最低 Python `>=3.10` 宣言 (Phase 4 互換、`requirements.txt` は薄い
  互換ファイルとして残置)
- runtime/core-harness pin の drift 検出 CI を tighten

Phase 5d (this PR) で実施:

- README architecture を 4 層 post-rebase 状態 (Layer 1/2/3 すべて
  shipped) に更新
- 「このリポジトリに残るもの」サブセクションを追加 (上記 inventory の
  要約)
- README の "Python 3.8+" 記載を "Python 3.10+" に修正 (Phase 5c 整合)
- `docs/getting-started.md` / `docs/non-goals.md` の forward-looking な
  `tools/role_configs_schema.json` 参照を `tools/org_extension_schema.json`
  へ修正 (Phase 5a 漏れ分のスイープ)
- 本完了ノートの追加

## Lead-confirmed reasoning for ja-side residuals

Phase 5 design Q&A ([`phase5-decisions-2026-05-03.md`](phase5-decisions-2026-05-03.md))
の主要決定:

- **Q5=a** prompt template はすべて Layer 2 へ寄せる。ja の
  `.dispatcher/` / `.curator/` 日本語 rich 版は Layer 2 英語 reference の
  consumer-side override として残す。
- **Q6=b** org-shaped hooks (`block-workers-delete.sh` / `block-org-structure.sh`
  / `block-git-push.sh` / `block-dispatcher-out-of-scope.sh`) は物理 path に
  強く結び付くため orchestration glue 抽出側 (phase5-decisions Layer 3) へ持ち込まない。claude-org-ja に残置。
- **Q7=a** Phase 4 Q9=c を維持。dashboard SPA は claude-org-ja に残し、
  orchestration glue 抽出側は dashboard を持たない。
- **Q9=a** skill SoT は将来の `claude-org-skills` (英語)。ja は consumer。
  ただし orchestration glue 抽出自体が Q1=b で defer のため、現時点では
  全 10 skill が ja in-tree。
- **Q10=b** claude-org-ja は narrow 抽出 (`org-delegate` + `org-start`
  の 2 skill のみが proceed 時に外部リポへ移管される想定) により thin
  shim として残る。proceed 後の残置内訳は 8 skill + dashboard + hooks +
  日本語 rich prompt。

## Open follow-ups (deferred to Phase 5e or later)

- **orchestration glue 抽出 (`claude-org-skills`) kill / proceed 判定** — 2026-08-03 頃の
  再評価セッションで実施。measurement (i) consumer 候補 inventory + (ii)
  skill churn 取得後に判定 ([phase5-decisions Q1=b](phase5-decisions-2026-05-03.md))。
- **`tools/org_setup_prune.py`** — Phase 5 inventory で UNCERTAIN 扱い
  となった ja-specific tool。`/org-setup` の冪等性確保用だが、利用頻度と
  維持コストの再評価を Phase 5e 以降で実施予定。
- **`tools/check_renga_compat.py`** — 同じく UNCERTAIN。`renga` バージョン
  check は重要だが、`renga` 側に self-check 機能が入った場合は廃止候補。
- **`docs/worker-permissions-design.md`** — `[x]` checkmark 付きの完了
  済み design doc。Phase 5b 棚卸し対象外で残置。完全な archive 化は
  Phase 6 で検討。
- **`#171` auto-mirror runtime の skill 層拡張** — Q9=a で
  `claude-org-skills` を新 SoT にする想定だが、translate runtime の射程
  拡張は orchestration glue 抽出の proceed 判定後に再開。
- **混在期間の skill 改修ルール** — orchestration glue 抽出 proceed 判定後、`org-delegate`
  / `org-start` のみ override 構造、残り 8 skill は in-tree のままという
  状態が長期化する。改修ルールは Phase 6 で別途決定
  ([phase5-decisions 未決事項 §4](phase5-decisions-2026-05-03.md))。

## References

- Phase 5a docs sweep: [#258](https://github.com/suisya-systems/claude-org-ja/pull/258)
- Phase 5b shim audit: [#259](https://github.com/suisya-systems/claude-org-ja/pull/259)
- Phase 5c packaging: [#260](https://github.com/suisya-systems/claude-org-ja/pull/260)
- Phase 5d (this PR): Epic close
- Phase 4 completion: [`phase4-completion-2026-05-02.md`](phase4-completion-2026-05-02.md)
- Phase 5 design Q&A: [`phase5-decisions-2026-05-03.md`](phase5-decisions-2026-05-03.md)
- Phase 5 design questions: [`phase5-questions-2026-05-03.md`](phase5-questions-2026-05-03.md)

## Closure

This PR closes Epic [#101](https://github.com/suisya-systems/claude-org-ja/issues/101)
(claude-org-ja Layer 1/2/3 抽出後の rebase) and Issue
[#130](https://github.com/suisya-systems/claude-org-ja/issues/130) (Phase 5d).
The 4-layer architecture is now fully shipped (Layer 1 = `core-harness`,
Layer 2 = `claude-org-runtime`, Layer 3 = `renga`, all independent OSS
packages); claude-org-ja Layer 4 is a thin shim over them. A separate,
Layer-4-internal extraction of orchestration glue into `claude-org-skills`
remains deferred per
the kill-gate measurement plan.
