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
  - Bash(rm:*)
  - Bash(del:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - mcp__renga-peers__*
---

# org-attention-start: attention watcher の常駐起動

`claude-org-runtime attention watch` を dispatcher ペインの右側に split で常駐起動し、
pane_id を `.state/attention_pane.json` に sidecar として記録する。停止は
[`/org-attention-stop`](../org-attention-stop/SKILL.md) を使う。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: 本スキルの `mcp__renga-peers__*`（`spawn_pane` / `list_panes` / `inspect_pane` / `close_pane`）は **既定 `renga`** で書いてあり、`ORG_TRANSPORT` 無設定ならそのまま従えばよい（既定挙動不変）。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では完全修飾名が **`mcp__renga-peers__*` → `mcp__org-broker__*`** に機械置換される（引数形・セマンティクスは同一なので spawn / 監視操作の論理は変わらない）。attention watcher は Claude ペインではなく `claude-org-runtime` CLI なので **dev-channel / folder-trust の承認は両系とも不要**（spawn 儀式の差は Claude ペイン spawn にのみ効く）。エラーは broker 追加コード（`[no_backend]`（= adapter_unavailable）/ `[token_invalid]` 等、[`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) の broker 節）が加わる。`new_tab` / `focus_pane` は broker surface に**無い**が本スキルは使わない。契約面は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14）、設計 SoT は transport-lab `docs/design/ja-migration-plan.md` §5.2(ii)。既定 renga の手順は不変（broker は加算）。（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

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

二重起動の判定は **sidecar の有無と live pane の両方** を見る必要がある（sidecar 不在でも
過去の手動 spawn / クラッシュ後の孤児 `name="attention"` ペインが live で残るケースがあり、
sidecar だけ見ると Step 3 の `spawn_pane(..., name="attention")` が `[name_in_use]` で失敗する）。

1. `mcp__renga-peers__list_panes` を呼び、`name="attention"` / `role="attention"` の pane が
   live で存在するか確認する
2. `.state/attention_pane.json` が存在するか確認:
   ```bash
   test -f .state/attention_pane.json && echo exists || echo absent
   ```
3. 分岐:
   - **live pane あり + sidecar あり + sidecar の pane_id が live pane と一致** → 正常に稼働中。
     「attention watcher は既に pane id={N} で稼働中です。再起動したい場合は
     `/org-attention-stop` を先に実行してください」と報告して **abort**
   - **live pane あり + sidecar 無し / pane_id 不一致** → 孤児ペインまたは sidecar drift。
     ユーザーに状況を報告し「`/org-attention-stop` で孤児 pane を掃除してから再実行してください」
     と案内して **abort**（自動 close はしない。孤児ペインに何が動いているか不明なため）
   - **live pane 無し + sidecar あり** → stale sidecar。削除して Step 2 へ進む:
     ```bash
     rm .state/attention_pane.json
     ```
     Windows native: `del .state\attention_pane.json`
   - **live pane 無し + sidecar 無し** → クリーン状態。Step 2 へ進む

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
- `[name_in_use]`: Step 1 の二重起動チェックが取りこぼした live pane が直前に再出現した race。
  ユーザーに「attention pane が並走で先に立ち上がりました。`/org-attention-stop` で掃除して
  ください」と報告して abort
- その他 `[<code>]`: [`renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) を参照

## Step 4: 起動 health check（即時クラッシュの negative-signal 検出のみ）

`spawn_pane` の成功は「shell が立ち上がって command を発火した」までしか保証しない。
`claude-org-runtime` 未導入 / 設定不正 / import error で watcher 本体が即時終了すると、
sidecar に stale pane_id が記録されて以後の判定を壊す。一方、watcher が **正常起動時に
何を出力するか** は `claude-org-runtime` 側の契約で固定されておらず（polling loop に入って
静かに待機する実装もあり得る）、「起動バナーが見えるまで待つ」「無出力なら fail」と扱うと
**正常な quiet start を誤って kill する**経路になる。よって本 skill は **明示的な失敗
シグナルだけを fail と判定**し、それ以外（空出力 / 不明な出力）は「起動成功扱い」とする:

```
mcp__renga-peers__inspect_pane(target="attention", format="text", lines=60)
```

inspect 自体のラウンドトリップ（数百 ms）で shell が即時終了するケースは pty buffer に
残った prompt や error が cap される。**以下のいずれかに該当した場合のみ「起動失敗」**:

- 出力末尾に shell prompt が露出している（`PS C:\...> ` / `$ ` / `% ` / `> ` の末尾露出）
  → command が即時終了して shell に戻った
- 出力に以下のいずれかの文字列を含む:
  - `command not found` / `is not recognized` / `not recognized as an internal or external command`
  - `ModuleNotFoundError` / `ImportError` / `Traceback (most recent call last)`
  - `[error]` / `[ERROR]` / `FATAL` / `fatal:`

**上記いずれも検出されない場合**（空出力 / 起動バナー / polling 待機 / 不明な行のみ等）
→ **起動成功扱い** で Step 5 に進む。固定 sleep を入れて再 inspect する経路は持たない
（quiet な健全起動を誤殺する経路を作らないため）。

**起動失敗時**:
1. `mcp__renga-peers__close_pane(target="<spawn 返り値の pane_id>")` で死んだペインを掃除
2. sidecar は **書き込まない**
3. journal に `attention_watch_start_failed` を記録:
   ```bash
   bash tools/journal_append.sh attention_watch_start_failed pane_id=<N> reason=immediate_exit
   ```
4. ユーザーに「watcher が起動直後に終了しました（出力: <inspect 抜粋>）。
   `claude-org-runtime --version` で導入を確認し、`.state/attention.json` の構文も
   `claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json`
   で検査してください」と報告して abort

## Step 5: pane_id を sidecar に記録

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

## Step 6: 報告

```
attention watcher を起動しました（pane id={N}、dispatcher の右側）。
設定: .state/attention.json
停止するときは /org-attention-stop を実行してください。
```

WSL の Windows 通知センター連携 (`wsl-notify-send.exe`) や OS 別の backend 挙動、トラブル
シューティングは [`docs/operations/attention-watch.md`](../../../docs/operations/attention-watch.md)
を参照。
