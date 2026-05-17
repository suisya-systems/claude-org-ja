---
name: org-attention-start
description: >
  attention notification watcher（承認待ち / 判断待ち / CI 失敗等を OS 通知 + 音で能動通知）を
  dispatcher ペインの右側に split で常駐起動する。`.state/attention.json` が未配置なら
  ja 既定テンプレートを `tools/templates/attention.example.json` から自動コピーする。
  起動後のペイン id は `.state/attention_pane.json` に記録し、`/org-attention-stop` から参照する。
  「attention 起動」「通知監視を始めて」「watcher を立てて」等で発動。
  `/org-start` からの auto-start はしない（明示起動推奨ポリシー）。
effort: low
allowed-tools:
  - Read
  - Write
  - Bash(mkdir:*)
  - Bash(cp:*)
  - Bash(copy:*)
  - Bash(test:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - mcp__renga-peers__list_panes
  - mcp__renga-peers__spawn_pane
---

# org-attention-start: attention watcher の常駐起動

`claude-org-runtime attention watch` を dispatcher ペインの右側に split で常駐起動し、
pane_id を `.state/attention_pane.json` に sidecar として記録する。停止は
[`/org-attention-stop`](../org-attention-stop/SKILL.md) を使う。

> **前提**: この skill は窓口（Secretary）の cwd（= claude-org-ja リポジトリ root）から呼ばれる。
> attention watcher 自体は `claude-org-runtime` の console_scripts entrypoint で、`--state-dir .state` /
> `--config .state/attention.json` の相対パスが repo root から resolve される必要がある。
> dispatcher ペインの cwd は `.dispatcher` なので、spawn_pane では `cwd="."` を明示して
> Secretary 側の cwd（= repo root）にバインドする。
>
> **設計判断 (sidecar)**: pane_id 記録は `.state/state.db` の schema 拡張ではなく
> sidecar JSON (`.state/attention_pane.json`) を採用する。`.state/dashboard.pid` /
> `.state/attention_notified.json` と同じ「補助プロセス追跡」パターンに揃え、
> importer / writer / snapshotter / converter / drift_check への波及を回避するため。
> attention watcher は OS subprocess であり Claude peer ではないため peer_id は持たず、
> ダッシュボードへの状況表示も現状不要（人間判断、2026-05-17）。

## Step 1: 二重起動チェック

1. `.state/attention_pane.json` が存在するか確認:
   ```bash
   test -f .state/attention_pane.json && echo exists || echo absent
   ```
2. **存在する場合**: `mcp__renga-peers__list_panes` を呼び、sidecar に書かれた `pane_id` が
   現在のタブに生存しているか照合する:
   - **生存している** → 既に起動中。ユーザーに「attention watcher は既に pane id={N} で稼働中です。
     再起動したい場合は `/org-attention-stop` を先に実行してください」と報告して **abort**
   - **生存していない** (sidecar に stale な id) → 旧 sidecar を削除して Step 2 へ進む:
     ```bash
     rm .state/attention_pane.json
     ```
3. **存在しない場合**: Step 2 へ進む

## Step 2: 設定ファイルの配置（未配置時のみ）

1. `.state/attention.json` の有無を確認:
   ```bash
   test -f .state/attention.json && echo exists || echo absent
   ```
2. **未配置の場合**: ja 既定テンプレートを copy する（`.state/` は gitignored、fresh clone
   直後だと未作成のことがあるので mkdir も同時に走らせる）:
   ```bash
   mkdir -p .state
   cp tools/templates/attention.example.json .state/attention.json
   ```
   Windows native (PowerShell) で実行する場合は `copy` コマンドでも可:
   ```powershell
   if (!(Test-Path .state)) { mkdir .state }
   copy tools\templates\attention.example.json .state\attention.json
   ```
3. **既に配置されている場合**: 上書きしない（ユーザーの個別調整を尊重する）。Step 3 へ進む

## Step 3: dispatcher を split して watcher を起動

dispatcher ペインの右半分（vertical split）に attention watcher 用の pane を作る:

```
mcp__renga-peers__spawn_pane(
  target="dispatcher",
  direction="vertical",
  role="attention",
  name="attention",
  cwd=".",
  command="claude-org-runtime attention watch --state-dir .state --config .state/attention.json"
)
```

- `target="dispatcher"`: org-start Block A-1 で確立した安定名で解決
- `direction="vertical"`: dispatcher ペイン = 左、attention ペイン = 右
- `cwd="."`: Secretary の cwd（= repo root）基点で相対 resolve され、`.state/` パスが正しく当たる。
  省略すると dispatcher の cwd (`.dispatcher`) を継承して `.state` が `.dispatcher/.state` に解決され
  watcher が空の state を見ることになる
- `name="attention"`: 後続の `/org-attention-stop` で `close_pane(target="attention")` または記録した
  pane_id で参照する
- 戻り値: `"Spawned pane id=N."` テキスト。N が attention watcher の pane_id

**失敗時の分岐**:
- `[split_refused]`: dispatcher pane が分割下限 (MIN_PANE_WIDTH=20) を下回っている。
  ユーザーに「dispatcher ペインが分割不能サイズです。secretary 側を縮めるか、ターミナル幅を
  広げてから再実行してください」と報告して abort
- `[pane_not_found]`: dispatcher pane が存在しない（org-start 未実行 or dispatcher 落ち）。
  ユーザーに「dispatcher pane が見つかりません。`/org-start` を先に実行してください」と
  報告して abort
- その他 `[<code>]`: [`renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) を参照

## Step 4: pane_id を sidecar に記録

返り値からパースした pane_id を `.state/attention_pane.json` に書き出す:

```json
{
  "pane_id": "<N>",
  "name": "attention",
  "started_at": "<ISO8601 UTC>",
  "config_path": ".state/attention.json"
}
```

`Write` ツールで上書き保存する（既存ファイルは Step 1 で削除済み）。

journal event を 1 行追記する:

```bash
bash tools/journal_append.sh attention_watch_started pane_id=<N> config=.state/attention.json
```

Windows native では `py -3 tools/journal_append.py attention_watch_started pane_id=<N> config=.state/attention.json`。

## Step 5: 報告

```
attention watcher を起動しました（pane id={N}、dispatcher の右側）。
設定: .state/attention.json
停止するときは /org-attention-stop を実行してください。
```

WSL の Windows 通知センター連携 (`wsl-notify-send.exe`) や OS 別の backend 挙動、トラブル
シューティングは [`docs/operations/attention-watch.md`](../../../docs/operations/attention-watch.md)
を参照。
