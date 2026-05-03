# Core-harness extraction (Layer 1)

> 関連 Issue: [#128](https://github.com/suisya-systems/claude-org-ja/issues/128)
> ステータス: **design only**（本 PR は実装を含まない。実装は Step B 以降の別 Issue で切り出す）
> 対象: claude-org-ja の **permission / sandbox / hooks / journal** プリミティブを Layer 1 = `core-harness` として抽出する設計
> 依存ドキュメント:
> - [core-harness inventory](https://github.com/suisya-systems/claude-org-ja-private/blob/main/core-harness-inventory.md)（測定 doc、worker dir 外）
> - strategic-analysis §13.4 / §15.2 / §16.4.1（4 層スタックの Layer 1 位置づけ）
> - [docs/design/phase-2b-guardrails-refactor.md](./phase-2b-guardrails-refactor.md)（bash/awk 採用継続の前提）
>
> **歴史的注記 (Issue [#230](https://github.com/suisya-systems/claude-org-ja/issues/230))**: 本 doc は抽出**前**の状態を出発点に書かれているため、`tools/role_configs_schema.json` および `tools/generate_worker_settings.py` を in-tree パスとして参照する箇所がある。これらは現在 [`claude-org-runtime`](https://github.com/suisya-systems/claude-org-runtime) パッケージに移管済みで、本リポジトリには存在しない（実体は `src/claude_org_runtime/settings/role_configs_schema.json` と `src/claude_org_runtime/settings/generator.py`）。以下の記述は抽出プロセスの史料として原文を保持する。

本 PR は core-harness 抽出のための **Lead 決定 12 問の確定回答** + **schema 分割の具体線** + **段階的 migration** + **test/CI 戦略** + **残存リスク** を文書化する。実装は本 PR では行わない。後続の Step B〜E のワーカーが本 doc から直接コードを書き起こせる粒度を目標とする。

---

## 1. Background

### 1.1 なぜ抽出するのか

claude-org-ja は 4 層スタック (README 参照) の Layer 4 に位置するリファレンス配布物。Layer 4 が Layer 3 (`renga`) を依存先として既に持つのと同じ構造で、本 Issue (#128) は **Layer 1 = `core-harness`** を切り出す。

切り出し対象は **safety primitives**:

- **permission schema** (`tools/role_configs_schema.json`、448 LOC) — 役割ごとの allow/deny/hook 規約
- **schema validator + generator** (`tools/check_role_configs.py` 718 LOC + `tools/generate_worker_settings.py` 140 LOC)
- **hook framework** (`.hooks/*.sh` deny scripts 893 LOC + `.hooks/lib/segment-split.sh` 325 LOC)
- **audit journal** (`.state/journal.jsonl` 規約 + 書き込み helper)

inventory §Appendix の総量は **~2,520 LOC** + 12 のテストファイル。これを org doctrine から分離して再利用可能な package にする。

### 1.2 設計目標

1. **片方向依存**: `claude-org-ja → core-harness` のみ。core-harness は claude-org doctrine（`secretary` / `dispatcher` / `.dispatcher/` / `block-workers-delete.sh` 等）を一切知らない。
2. **shim 経由の互換維持**: 既存 ja CI を破壊せず、`tools/check_role_configs.py` は薄い CLI shim として残し内部で `core_harness` を呼ぶ。
3. **外部 org が core-harness 単独で schema を組み立て可能**: claude-org-ja を読まずに別の組織が role schema を作れることを線引きの判定基準とする。
4. **段階的に出荷可能**: Step B (schema) → C (hooks) → D (journal) → E (consumer pin) の順で、各段階が単独でマージ可能であること。

### 1.3 非目標

- 本 PR では **実装に着手しない**（design only）。
- Rust 化検討は本 PR スコープ外（[ja#194](https://github.com/suisya-systems/claude-org-ja/issues/194)）。
- plugin 形式 (Q12 C 案) への進化も本 PR スコープ外（[ja#195](https://github.com/suisya-systems/claude-org-ja/issues/195)）。
- dispatcher / dashboard の抽出は本 PR スコープ外（Layer 2 = org-runtime の責務）。

---

## 2. Lead decisions (12 questions)

inventory §5.3 で Lead に提示した 12 問について、本 design に至る合意を以下に確定する。実装ワーカーは原則これに従う。

| # | Question | Decision | Rationale |
|---|---|---|---|
| Q1 | Implementation language | **Python only**（pure-Python package、bash hooks は維持） | inventory §5.3-1。Rust 化は bash 1,160 LOC の rewrite cost が大きく、配布の楽さよりも先に「分離の正しさ」を確立すべき。Rust 化検討は ja#194 で別途 track |
| Q2 | Permission schema SOT | **A — schema split**: framework schema (型定義) は core-harness が SOT、org-specific entries は claude-org-ja に残す | inventory §5.1-6 (org doctrine と framework rule が同居) を解く唯一の方法。詳細は §3 |
| Q3 | bash/awk hook 依存 | **維持**（Phase 2b decisions と整合） | [phase-2b-guardrails-refactor.md §1](./phase-2b-guardrails-refactor.md) で awk 継続を確定済。再 litigation の理由なし |
| Q4 | Layer 1 → claude-org の依存方向 | **A — 一方向**: core-harness は claude-org-ja を知らない。org-shaped hooks (`block-workers-delete.sh`, `block-dispatcher-out-of-scope.sh`, `block-org-structure.sh`) は ja に残す | inventory §5.1-1〜3 のブロッカーを「lift しない」で回避。core-harness の OSS 価値を最優先 |
| Q5 | Hooks の置き場 (`.hooks/` vs `.claude/hooks/`) | **C — path 設定可能**: core-harness は中立、利用者が選ぶ | inventory §5.1-7。ja は既に `.hooks/` を canonical 採用済（auto-mirror 経緯 in canonical-ownership.md）。en 系の `.claude/hooks/` 採用も阻害しない |
| Q6 | OSS 公開タイミング | **A — empty repo bootstrap 先行** + design PR (本 PR)。実装 (Step B-D) は week 7-12 | inventory §5.3-6。bootstrap は別ワーカーが本日並行実施。契約面 (本 doc) を先に固めることで、実装 PR が「決定済の design に従う」形に揃う |
| Q7 | Test 移管範囲 | **B**: `test-check-worker-boundary.sh` 等 org 文字列依存の test は ja 残置。core-harness は generic な hook framework test (exit code / stdin/stdout 規約) を持つ | 詳細は §5。inventory §5.3-7 |
| Q8 | CI 構成 | **A — shim 経由**: `tools/check_role_configs.py` を薄い CLI shim 化、内部で `from core_harness import ...` を呼ぶ。CI スクリプトは無変更 | 詳細は §6。ja CI 破壊コストを最小化 |
| Q9 | semver 方針 | **C — pre-1.0 開始 + 1.0 移行条件明示**（renga 流派、ただし条件を初版から定義） | 詳細は §7.1。schema が `version: 1` と書かれているが migration story が未定なため、まず pre-1.0 で破壊変更を許容し、条件を満たした時点で 1.0 |
| Q10 | 配布方式 | **A — GitHub Release のみ**: pre-1.0 期間は `pip install git+https://github.com/suisya-systems/core-harness@v0.x.y`。PyPI publish は 1.0 移行時 | inventory §5.3-10。pre-1.0 で PyPI namespace を取る motivation が薄く、削除も難しいため後回し |
| Q11 | Dispatcher / journal のスコープ | **B**: dispatcher は ja 残置、**journal API のみ core-harness が提供** (Layer 1 の audit primitive) | 詳細は §3.4。`dispatcher_runner.py` は org-runtime 責務、journal の append/iter は素のプリミティブ |
| Q12 | Plugin / slot interface | **B — lib 提供方式**: org-runtime が schema 全体を組み立て、core-harness は `roles{}` / `worker_roles{}` 型定義 + validator + generator を lib 提供。Layer 2 着工時に C (schema-merger) へ進化検討 | 詳細は ja#195。今は最小 surface に絞る |

---

## 3. Schema 分割の具体線 (Q2 の実装)

inventory §5.1-6 が指摘した「framework rule と org doctrine が同じ schema に同居している」問題への回答。**判定基準**: 「core-harness 単独で外部 org が schema を作成・検証可能か」。Yes なら framework、No なら org-specific。

### 3.1 framework schema (core-harness が SOT)

`core_harness.schema.framework_schema_v0`（モジュール定数 / JSON Schema）として提供する以下の **型定義** が core-harness の SOT。具体値 (path / command / role 名) は持たず、構造のみを定義する。

| Concept | core-harness が持つもの | 備考 |
|---|---|---|
| `forbidden_allow_exact` / `forbidden_allow_regex` | **型定義のみ** (`list[str]` の意味論 + match algorithm) | 具体パターンは org が inject。例: `"Bash(git *)"` は「ja の組織方針」であり framework 不要 |
| `required_hook_scripts[]` | **型定義 + integrity check ロジック** (「`required_hooks[].command_contains` の少なくとも 1 つから参照されること」)。さらに **一般的 ban パターン** として `block-no-verify.sh` / `block-dangerous-git.sh` を framework が **推奨デフォルト** として提示する | 具体 hook 名 (`block-workers-delete.sh` 等) は framework 不要 |
| `roles{}` schema | role エントリの **構造定義** (`required_allow`, `required_deny`, `disallow_allow_regex`, `closed_world` flag, `required_hooks[]`, `settings_paths[]`, `docs_section`) | 個別 role 名 (`secretary`, `dispatcher`, ...) は持たない |
| `worker_roles{}` schema | worker template の **構造定義** (`permissions{allow,deny}`, `hooks{PreToolUse[]}`, `env{}`, placeholder `{worker_dir}` / `{consumer_root}` / `{core_harness_path}`（org 固有 alias `{claude_org_path}` は ja 側で resolve）) | 個別 template 名 (`default`, `claude-org-self-edit`, `doc-audit`) は持たない |
| Hook framework wire-up 規約 | **exit code / stdin / stderr 規約**: `exit 0` allow / `exit 2` + stderr deny / stdin = PreToolUse JSON / `.tool_name` + `.tool_input.command` 抽出 | inventory §3.2 の de-facto contract を明文化 |
| Generic deny library | `lib/segment-split.sh` 系 (`split_segments`, `flatten_substitutions`, `collect_assignments`, `expand_known_vars`, `unwrap_eval_and_bashc`) + `lib/path-normalize.sh` (新規、現在 hook 4 本に重複) | inventory §2.2 / §3.4 |
| Generic deny hooks | `block-no-verify.sh` / `block-dangerous-git.sh` / `check-worker-boundary.sh` (org 文字列依存を env 経由で外出ししたバージョン) | inventory §3.4 |
| Schema validator engine | `validate_config()` / `validate_schema_integrity()` / `closed_world` 計算 / `disallow_allow_regex` / placeholder match (`{worker_dir}` capture) | 現在の `tools/check_role_configs.py` の **ロジック部分**。CLI shim は ja 残置 |
| Generator | `generate_worker_settings()` 関数 (placeholder substitution + `description`/`$comment` strip) | 現在の `tools/generate_worker_settings.py` |
| `settings.local.override.json` 規約 | sibling escape hatch の **意味論** (override の `allow` セットは closed-world 検査から除外) | inventory §1.4 |
| Journal event-line schema | append-only JSONL 規約 + `ts` (ISO-8601 UTC) + `event` (string) + 自由 keyset。reader は malformed line / blank line を skip | inventory §4 全体 |
| Journal API | `journal.append(event_type, **fields)` + `journal.iter(path)` | inventory §4.5 |

### 3.2 org-specific schema (claude-org-ja に残る)

以下は **claude-org の組織方針** であって、外部 org が core-harness を採用する際に必要としない。よって ja の `tools/role_configs_schema.json`（または後継ファイル）に残す。

| Concept | 具体値 (例) | なぜ ja 側か |
|---|---|---|
| 役割名カタログ | `secretary`, `dispatcher`, `curator`, `worker`, `repo_shared`, `user_common` | claude-org doctrine の中核。他 org は別の役割分担を取る |
| `secretary.required_allow` 38 entry | `Bash(gh issue:*)`, `Bash(codex exec:*)`, `mcp__renga-peers__*` 群, etc. | 「窓口」という claude-org 固有の概念に紐付く |
| `^mcp__claude-peers__` ban list | `forbidden_allow_regex` 1 件 | claude-peers (現 renga-peers) は renga の前身 MCP server。claude-org が組織方針として禁止しているもので、framework default ではない |
| Org-structure 名 list | `.dispatcher/`, `.curator/`, `.state/`, `registry/`, `dashboard/`, `knowledge/` の 6 名 | inventory §5.1-3。`block-org-structure.sh` の `ALWAYS_BLOCKED` + `ROOT_ONLY_BLOCKED` 配列の中身 |
| Workers dir 概念 | `registry/org-config.md` の `workers_dir:` 行 | `block-workers-delete.sh` が依存。「workers をまとめて消すな」は claude-org 固有のルール |
| Dispatcher 許可パス | `.dispatcher/`, `.state/`, `knowledge/raw/<YYYY-MM-DD>-<kebab>.md` | `block-dispatcher-out-of-scope.sh` の policy。「dispatcher は限定 path のみ書ける」は claude-org 固有 |
| Org-shaped hooks 本体 | `block-workers-delete.sh` (109 LOC), `block-dispatcher-out-of-scope.sh` (108 LOC), `block-org-structure.sh` (154 LOC), `block-git-push.sh` (51 LOC) | 上記 4 つのいずれかに依存 |
| Concrete worker_role 名 | `default`, `claude-org-self-edit`, `doc-audit` | claude-org-ja が運用する template 名。core-harness は型のみ提供 |
| `permissions.md` (docs projection) | `.claude/skills/org-setup/references/permissions.md` | ja の skill 内に存在し日本語見出し (`## 窓口` 等) が `docs_section` marker。docs projection の **メカニズム** は core-harness が提供するが、**doc 本体** は ja に残る |
| `secretary` の Japanese deny reason | `echo "ブロック: …" >&2` 文字列 | 多言語化は ja スコープ。core-harness は **deny reason の format 規約** のみ規定し、文字列は org が決める |

### 3.3 線引きの判定基準（再掲）

> **「core-harness 単独で、外部 org が schema を作成・検証可能であること」**

この判定を機械的に適用すると以下が導かれる:

- 「`block-workers-delete.sh` を core-harness が持つか」 → **No**。`registry/org-config.md` という claude-org 固有 file を読むため。
- 「`block-no-verify.sh` を core-harness が持つか」 → **Yes**。`git commit --no-verify` は外部 org も等しく止めたいはず（generic git-safety）。
- 「`secretary` role 名を core-harness が知っているか」 → **No**。「窓口」概念は claude-org doctrine。
- 「`roles{}` schema の `closed_world: bool` を core-harness が知っているか」 → **Yes**。これは「audit constraint の一形式」という framework primitive。

### 3.4 Journal の責務分割 (Q11)

| Layer | 持つもの |
|---|---|
| **core-harness** (Layer 1) | (a) `journal.jsonl` の append-only 規約、(b) reader tolerance for malformed/blank lines、(c) `append_event(event_type, **fields)` + `iter_events(path)` の最小 API、(d) `ts` (ISO-8601 UTC) と `event` (string) のみ必須とする minimal envelope |
| **claude-org-ja** (Layer 4 / 将来 Layer 2) | (a) 35 種の event type catalog (`worker_spawned` / `pr_merged` / 等)、(b) per-event field 規約 (現在 `org-delegate/SKILL.md` 等に分散)、(c) dispatcher / secretary skills 側の writer 呼び出し、(d) dashboard reader (`dashboard/server.py` は `org-state.json` を読むため journal とは別線) |

要点: **core-harness は「どう書くか」、ja は「何を書くか」**。inventory §4.5 と整合。

---

## 4. Migration phasing (Section 6 worker first-cut の確定版)

inventory §6 の段階分割を、本 design の Lead 決定を反映して確定する。各 Step は独立 PR としてマージ可能、各 Step 終了時点で ja CI が通ることを必須とする。

### Step A — bootstrap empty repo

- **対象**: `suisya-systems/core-harness` の empty repo を作成
- **成果物**: README + LICENSE (MIT、ja と整合) + CI skeleton (pytest + ruff + bash test runner) + `pyproject.toml` (`name = "core-harness"`, `version = "0.0.0"`) + `CONTRIBUTING.md` の placeholder
- **依存**: なし
- **状態**: 並行ワーカーが本日 (2026-05-02) 実施中
- **AC**: `pip install -e .` が空の package として通ること、CI green

### Step B — permission schema + generator + validator の移管

- **対象**:
  - `tools/role_configs_schema.json` を **2 ファイルに分割** (inventory §5.2-1 への対応):
    - `core_harness/schemas/role_audit_schema.json` (framework: `roles{}` 構造 + `forbidden_allow_*` 型 + `required_hook_scripts[]` 型 + `disallow_allow_regex` 型)
    - `core_harness/schemas/worker_role_templates_schema.json` (framework: `worker_roles{}` 構造 + placeholder 仕様)
  - `tools/generate_worker_settings.py` → `core_harness.generator.generate_worker_settings`
  - `tools/check_role_configs.py` の **ロジック層** → `core_harness.validator.validate_config` / `validate_schema_integrity`
  - placeholder を **neutral 命名に整理**（inventory §5.2-6 への対応）:
    - framework が定義する placeholder: `{worker_dir}`, `{consumer_root}` (= 旧 `{claude_org_path}` の汎用化), `{core_harness_path}` (新規、core-harness install location)
    - org-specific placeholder alias: ja は `{claude_org_path}` を `{consumer_root}` の alias として利用可能 (Step B サブ Issue で migration path を確定)
  - **org 側に残る `required_hook_scripts[]` 列挙**: ja の縮退 schema には少なくとも以下 7 件を明示的に保持し、`validate_schema_integrity()` がそれらを `roles[*].required_hooks[].command_contains` から参照されているか cross-check できるようにする:
    - org-specific (本体も ja 残置): `block-git-push.sh`, `block-workers-delete.sh`, `block-org-structure.sh`, `block-dispatcher-out-of-scope.sh`
    - generic (本体は core-harness 移管、参照は ja からも継続): `block-no-verify.sh`, `block-dangerous-git.sh`, `check-worker-boundary.sh`
- **ja 側変更**:
  - `tools/role_configs_schema.json` を **org-specific entries 専用** に縮退（concrete role 名 + `secretary.required_allow` 38 entry + `^mcp__claude-peers__` ban + `worker_roles{default,claude-org-self-edit,doc-audit}`）。先頭で `"$framework_schema_ref": "core-harness==0.1.0"` を宣言（exact pin、§7.1）
  - `tools/check_role_configs.py` は **薄い CLI shim** に縮退（argparse + `from core_harness.validator import validate_config` + 既存の終了コード規約維持）
  - `tools/generate_worker_settings.py` は同様に CLI shim
- **依存**: Step A
- **AC**:
  - `pytest tests/test_check_role_configs.py tests/test_generate_worker_settings.py` が ja 側で green
  - `core-harness` 側で `pytest tests/test_validator.py tests/test_generator.py` (新規 unit test) が green
  - `bash scripts/install-hooks.sh` 等の既存 CLI 呼び出しが無変更で動く

### Step C — hook framework の移管

- **対象**:
  - `.hooks/lib/segment-split.sh` (325 LOC) → `core_harness/hooks/lib/segment-split.sh`
  - 新規 `core_harness/hooks/lib/path-normalize.sh` を作成し、現在 hook 4 本 (`check-worker-boundary.sh`, `block-org-structure.sh`, `block-dispatcher-out-of-scope.sh`, `block-workers-delete.sh`) に重複している `portable_realpath` / `normalize_slashes` / `normalize_drive_letter` を集約 (inventory §5.2-4)
  - **generic な deny hooks**: `block-no-verify.sh`, `block-dangerous-git.sh`, `check-worker-boundary.sh` を `core_harness/hooks/` に移管。`check-worker-boundary.sh` の org-specific allow path (`<CLAUDE_ORG_PATH>/knowledge/raw/...` 等) は env 変数 `EXTRA_WRITE_ALLOWLIST_GLOBS` 経由で外注する形に refactor
  - hook framework wire-up 規約 doc を `core_harness/docs/hook-contract.md` として明文化（exit code / stdin JSON / stderr format）
- **ja 側変更**:
  - `worker_roles[*].hooks` の `command` 文字列を `bash "{core_harness_path}/hooks/<script>"` に書き換え（generic hook のみ）。org-specific hooks (`block-org-structure.sh`, `block-git-push.sh` 等) は引き続き `{claude_org_path}/.hooks/...` を指す
  - `.hooks/` には org-specific hooks (`block-workers-delete.sh`, `block-dispatcher-out-of-scope.sh`, `block-org-structure.sh`, `block-git-push.sh`) と test harness 用の `test-always-block.sh` が残る
- **依存**: Step B（schema 側で `{core_harness_path}` placeholder が定義済であること）
- **AC**:
  - 既存の hook test 8 本が ja 側で green（test 自体は §5 に従い ja 残置）
  - core-harness 側で hook framework test (exit-code + stdin/stderr 契約) が新規追加され green

### Step D — journal API の抽出

- **対象**:
  - `core_harness.journal.append_event(path, event_type, **fields)` (Python)
  - `core_harness.journal.iter_events(path)` (Python、malformed/blank line skip)
  - bash one-liner equivalent: `core_harness/journal/append.sh` (`printf '%s\n' "$json" >> "$path"` を `flock` でラップしたもの)
  - reader tolerance の test fixture を `tests/fixtures/journal-sample.jsonl` から複製（`not-valid-json` line 含む）
- **ja 側変更**:
  - `dispatcher/CLAUDE.md` line 63 周辺の「`Bash` 経由で append」記述を helper 経由に切り替える（dispatcher の権限は変えない、helper を呼ぶだけ）
  - `org-suspend/SKILL.md` / `org-resume/SKILL.md` / `org-delegate/SKILL.md` / `org-start` の writer 呼び出しを helper 経由に書き換え
  - **event type catalog は ja に残置**。`docs/journal-events.md` を新規作成して 35 種を集約することを推奨（Step D の AC ではないが、follow-up Issue 候補）
- **依存**: Step A（Step B/C と独立してマージ可能）
- **AC**:
  - `tests/test_parsers.py` が ja 側で green
  - core-harness 側で `tests/test_journal.py` (append + iter + malformed tolerance) が新規追加され green

### Step E — claude-org-ja から core-harness を依存として pin する draft PR

- **対象**:
  - `requirements/core-harness.txt` で **exact ref を pin** (§7.1 / Q10): `core-harness @ git+https://github.com/suisya-systems/core-harness@v0.1.0`（pre-1.0 期間は PyPI publish しないため、`pyproject.toml` の `dependencies` に書く形ではなく requirements file で git URL を固定する）
  - `.github/workflows/tests.yml` に `pip install -r requirements/core-harness.txt` を追加
  - shim CLI が core-harness API を呼んで通ることを CI で確認
- **依存**: Step B + C + D 完了
- **AC**: ja#128 の "draft PR replacing dependencies" 要件を満たす

### Phasing 図

```
Step A (bootstrap)
  ├── Step B (schema)
  │     └── Step C (hooks)  ← Step B の placeholder 定義に依存
  │           └── Step E (consumer pin)
  └── Step D (journal)  ← Step A のみに依存、B/C と並行可
```

**Estimated effort** (rough、Lead retune 余地あり): A small / B medium / C medium / D small / E medium-large (CI + draft PR レビュー往復)。

---

## 5. Test strategy (Q7 B)

inventory §Appendix の 12 テストファイルを以下のように分配する。

### 5.1 core-harness に移管する test

| File | 移管後の場所 | 理由 |
|---|---|---|
| `tests/test_generate_worker_settings.py` | `core_harness/tests/test_generator.py` | 純粋に generator のロジックを検査。org 文字列依存なし |
| `tests/test_parsers.py` (journal-sample 部分) | `core_harness/tests/test_journal.py` | journal reader の malformed-line tolerance を検査 |
| `tests/test-block-pretooluse-hooks.sh` (generic 部分) | `core_harness/tests/test-pretooluse-contract.sh` | exit code / stdin / stderr 契約の検査。org 文字列に依存しない部分のみ移管 |
| `tests/test-unwrap-eval-bashc.sh` | `core_harness/tests/test-unwrap-eval-bashc.sh` | `lib/segment-split.sh` の `unwrap_eval_and_bashc` 単体検査。generic |

### 5.2 claude-org-ja に残置する test

| File | 残置理由 |
|---|---|
| `tests/test-check-worker-boundary.sh` | path allow list に org-specific glob (`knowledge/raw/...`) が含まれる。core-harness 側に「env 経由で許可 path を渡せる contract」test だけは持たせ、ja 側で env を埋めた形で end-to-end test を維持 |
| `tests/test-block-workers-delete.sh` | `registry/org-config.md` の workers_dir 解決 + claude-org doctrine |
| `tests/test-block-dispatcher-out-of-scope.sh` | `.dispatcher/` / `.state/` 等の org 名依存 |
| `tests/test-block-git-push.sh` | 「workers は push しない」は org rule |
| `tests/test-block-org-structure.sh` | 8 名のハードコード dir 名に依存 |
| `tests/test-install-hooks.sh` | `scripts/install-hooks.sh` (precommit secret scanner、ja の運用) |
| `tests/test-precommit-secret-scanner.sh` | 同上 |
| `tests/test_check_role_configs.py` (org-specific assertion 部分) | `secretary` role の 38 entry を検査する部分は ja 残置。core-harness 側には schema validator の **engine** 単体テストを新規作成 |

### 5.3 新規追加する core-harness test

- `tests/test_validator.py`: closed_world / disallow_allow_regex / placeholder match / schema integrity の unit test (シンセティックな role schema を fixture として使用、claude-org 文字列を持ち込まない)
- `tests/test_generator.py`: placeholder substitution + `$comment` strip を fixture role で検証
- `tests/test_journal.py`: append + iter + malformed tolerance + `flock` 排他制御
- `tests/test-pretooluse-contract.sh`: 「`exit 0` で allow / `exit 2` + stderr で deny / 他 tool は `exit 0` (out-of-scope)」を fake hook で検証
- `tests/test-segment-split.sh`: `split_segments` / `flatten_substitutions` / `expand_known_vars` を ja から移してきた fixture で検証

---

## 6. CI strategy (Q8 A)

### 6.1 ja 側 CI の不変条件

`tools/check_role_configs.py` と `tools/generate_worker_settings.py` の **CLI 互換性を完全維持** する。具体的には:

- 終了コード規約 (0 = OK, 1 = validation failure, 2 = usage error) を変えない
- 標準出力フォーマット (現在の `[OK] role: ...` / `[FAIL] role: ...`) を変えない
- `--include-local`, `--include-worker-settings`, `--role`, `--out`, `--schema` 等の既存フラグを維持

これにより `.github/workflows/tests.yml` の既存ステップ・local の `make check` 相当・skill 内の bash 起動が **無変更** で動く。

### 6.2 shim 実装パターン

```python
# tools/check_role_configs.py (Step B 後)
from core_harness.validator import validate_config, validate_schema_integrity, ValidationError
from core_harness.cli import build_check_argparser  # CLI surface も core-harness が提供

def main():
    parser = build_check_argparser()
    args = parser.parse_args()
    schema = load_org_schema(args.schema or "tools/role_configs_schema.json")
    framework_schema = load_framework_schema_pinned()  # core-harness 同梱
    merged = merge(framework_schema, schema)  # org が framework を inject
    try:
        validate_schema_integrity(merged)
        for role in merged["roles"]:
            validate_config(merged, role, ...)
    except ValidationError as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)
```

### 6.3 core-harness 側 CI

- `pytest tests/` (Python unit tests)
- `bash tests/run-shell-tests.sh` (shell hook tests)
- ruff (lint) + mypy (任意、pre-1.0 では strict にしない)
- matrix: Linux (ubuntu-latest) + macOS (macos-latest) + Windows (windows-latest with Git Bash)
- bash version matrix は最低限 GNU bash 5.x のみ。古い macOS デフォルト bash 3.2 はサポート外明記（ja の現行ポリシーと整合）

### 6.4 互換性ゲート

ja 側の CI に **「core-harness を bump したら shim が壊れていないか」** の matrix を追加することを推奨（Step E の AC 内）:

```yaml
# .github/workflows/tests.yml に追加（Step E）
strategy:
  matrix:
    core_harness_ref: ["v0.1.0", "v0.2.0", "main"]  # main は warning 扱い
```

---

## 7. semver 方針と 1.0 移行条件 (Q9)

### 7.1 pre-1.0 期間の規約

- 0.x.y で運用、x bump = 破壊変更 OK、y bump = bugfix のみ
- ja は `core-harness>=0.1,<1.0` ではなく **必ず exact pin** (`core-harness==0.1.3` 相当) で固定する。x bump 時に ja 側で動作確認した上で pin を上げる
- `CHANGELOG.md` に **Breaking changes** セクションを毎リリース必須

### 7.2 1.0 移行条件（初版から明示）

以下の **3 条件すべて** を満たした時点で 1.0 を切る。renga が pre-1.0 に長く留まっている流派と同じだが、条件を曖昧にしないことで「いつまでも 1.0 にならない」を防ぐ:

1. **External consumer ≥ 2**: claude-org-ja 以外に core-harness を採用している org が 2 つ以上存在する（fork/star ではなく実際に schema を組み立てている repo）
2. **直近 2 quarter (6 ヶ月) 破壊変更ゼロ**: schema field の add/remove/rename がない
3. **配布チャネル確立**: PyPI publish 体制 (token 管理、release workflow) が ready

3 条件のいずれかが欠けても 0.x のままで良い。1.0 を急がない。

### 7.3 配布 (Q10)

- pre-1.0: GitHub Release のみ。`pip install git+https://github.com/suisya-systems/core-harness@v0.x.y` の形
- 1.0 移行時に PyPI へ publish。namespace squat の risk は 1.0 直前に namespace 取得することで軽減
- bash hook script は **Python package 内に同梱** (`core_harness/hooks/*.sh`) し、`importlib.resources` 経由で path を解決する helper (`core_harness.hooks_path()`) を提供。これにより worker_role template の `command` 文字列が `bash "$(python -m core_harness hooks-path)/check-worker-boundary.sh"` のような generic な解決ができる

---

## 8. Open risks

### 8.1 inventory §5.1 schema doctrine 分離の難しさ

- 現状の `tools/role_configs_schema.json` は 1 ファイルに framework + org を混載しており、Step B で **きれいに 2 ファイルに割れない可能性** がある
- 例: `closed_world: true` flag は framework concept だが、`secretary` の `closed_world: true` 値は org 判断
- **緩和策**: Step B のサブ Issue で「framework schema の最小 surface」を先に PoC として書き、その不足分をリスト化してから本実装に進む

### 8.2 inventory §5.2 命名 / API shape concerns

- `{worker_dir}` / `{claude_org_path}` placeholder は string substitution であり typed contract ではない (inventory §5.2-6)
- v0 では string 維持、v1 で typed Placeholder object 化を検討
- `settings.local.override.json` の sibling 概念は public 化するなら名前が悪い (inventory §5.2-7)。pre-1.0 中に `settings.local.user-extension.json` 等への rename 機会あり

### 8.3 12 問が解いていない領域

- **claude-peers (現 renga-peers) ban の所属**: `^mcp__claude-peers__` regex は ja 残置と決めたが、「Anthropic Claude Code とは関係ない過去の MCP サーバー」全般を core-harness が default deny するという別の選択肢もある。今は ja 残置で確定するが、外部 org が同じ判断を再発明する可能性あり
- **`description` / `$comment` strip ロジック**: generator が schema から抜くキー名のホワイトリスト/ブラックリストの扱いが不明確。Step B サブ issue で固める
- **bash hook の Windows 動作**: ja は Git Bash 前提。core-harness としてもこれを継承するが、明示的に CI matrix で確認する必要あり

### 8.4 hook contract の typed protocol 化

- inventory §5.2-5: 現状 `exit 2 + stderr` は Claude Code 固有 convention
- 将来 codex 等の別 engine から呼ばれる可能性を考えると、stderr を JSON envelope (`{"deny": true, "reason": "..."}`) に切り替える選択肢がある
- pre-1.0 では現行 convention 維持、1.0 移行直前に検討（破壊変更を 1.0 で吸収する形）

---

## 9. Follow-up

本 PR では起票しない（Lead 確認後に判断）が、本 design に紐付く後続作業として以下を認識している:

| Issue | 内容 | 状態 |
|---|---|---|
| [ja#194](https://github.com/suisya-systems/claude-org-ja/issues/194) | core-harness の Rust 化検討 (Q1 で先送り) | 起票済み |
| [ja#195](https://github.com/suisya-systems/claude-org-ja/issues/195) | Plugin interface を Q12 B → C (schema-merger) に進化させる | 起票済み |
| (未起票) | core-harness 0.1.0 リリース計画 (Step B-D 完了後) | Lead 判断後に起票 |
| (未起票) | 1.0 移行条件の継続判定 (consumer count + 破壊変更頻度の追跡) | Lead 判断後に起票 |
| (未起票) | `docs/journal-events.md` を新規作成し 35 種の event type を集約 (Step D の自然な後続) | Lead 判断後に起票 |
| (未起票) | `settings.local.override.json` → `settings.local.user-extension.json` 改名 (8.2) | pre-1.0 期間中の任意のタイミング |

---

## 10. PR 本体での確認事項 (Lead レビュー観点)

レビューア向けのチェックリスト:

- [ ] Lead 決定 12 問の表 (§2) が他の section と矛盾していない
- [ ] §3 の framework / org-specific の線引きが「外部 org 単独で schema 構築可能」基準を満たしている
- [ ] §4 の Step A-E が独立してマージ可能で、各 AC が客観的に判定可能
- [ ] §5 の test 移管リストが inventory §Appendix の 12 ファイルすべてをカバーしている
- [ ] §6 の shim 戦略が ja CI を破壊しない（既存 CLI 互換性が維持されている）
- [ ] §7 の 1.0 移行条件が claude-org 全体の release 流派と整合している
- [ ] §8 の risks が Step B 着工までに解消すべきか、本 design のまま着工して良いかの判断
