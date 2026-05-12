# attention-watch 運用ガイド

`claude-org-runtime attention scan` / `attention watch` は、`.state/state.db` と `.state/pending_decisions.json` を監視して、人間の反応が必要な状態（承認待ち / 判断待ち / CI 失敗 / silent stop など）を desktop notification + 音 + terminal bell fallback で通知する watcher である。本ドキュメントは Layer 4 (`claude-org-ja`) 側の運用手順を扱う。設計の前提・分類ルール・dedup 仕様は [`docs/design/attention-notification.md`](../design/attention-notification.md) を参照。

## 1. 役割と入出力

- **入力**:
  - `.state/state.db` の `events` テーブル（`notify_sent` / `ci_completed` / `worker_completed` / `pr_merged` など）
  - `.state/pending_decisions.json`（人間判断仰ぎ register）
  - optional config: `.state/attention.json`（`tools/templates/attention.example.json` を雛形にする）
- **出力**:
  - desktop notification（OS 別 backend は §3 参照）
  - sound（`urgent-only` / `all` / `off` の 3 値、§4 参照）
  - terminal bell fallback（`\a`）
  - stdout の構造化ログ
  - dedup state: `.state/attention_notified.json`（runtime が自動管理）

## 2. 有効化 / 無効化

### 2.1 設定ファイルの配置

`tools/templates/attention.example.json` は ja 既定のテンプレート集を含む tracked example。実運用では `.state/attention.json` にコピーする（`.state/` は gitignored）:

```bash
cp tools/templates/attention.example.json .state/attention.json
```

OS 通知や音の挙動を変えたい場合は `.state/attention.json` を編集する（template strings の上書き、`sound` の切替、`cooldown_sec` の調整など）。テンプレートの placeholder allowlist は `{task_id}` / `{worker}` / `{kind}` / `{status}` / `{pr}` / `{summary}` の 6 種で、未知 placeholder は literal のまま残るか runtime の fallback template で補われる（設計 §6 参照）。

### 2.2 1 回限りの動作確認 (`scan`)

`watch` を常駐させる前に、現状の `.state/` から想定どおりに attention event が抽出されるか確認する:

```bash
claude-org-runtime attention scan --state-dir .state --dry-run --json
```

`--dry-run` は OS notification subprocess を呼ばないので、CI 環境や音を鳴らしたくない時間帯でも安全。出力は `{ "events": [{ "key": ..., "kind": ..., "severity": ..., "title": ..., "body": ...}, ...] }` 形式（詳細は [`docs/verification.md` の attention scan 検証ブロック](../verification.md) を参照）。

### 2.3 常駐 (`watch`)

```bash
claude-org-runtime attention watch --state-dir .state --config .state/attention.json
```

別ターミナル or バックグラウンドで起動する。`/org-start` から自動起動はしない（環境依存が強いため明示起動が推奨。設計 §11 Q1 / §7 "org-start guidance"）。停止は通常通り Ctrl-C（dedup state の書き込みは atomic replace で行われるため、強制終了でも次回起動時に復旧可能。§5 参照）。

### 2.4 無効化

恒久的に止める場合は単に `watch` プロセスを終了するだけでよい。設定ファイル（`.state/attention.json`）の削除は不要。一時的に通知を抑えたい場合は `.state/attention.json` で `"desktop": false` / `"sound": "off"` に切り替えるか、`notify` テーブルの個別 kind を `"normal"` 以下に落とす。

## 3. OS 別の notification backend 挙動

runtime は追加 dependency なしで OS の標準コマンドを subprocess 経由で呼び出す（timeout 付き）。利用不可な backend は stdout + terminal bell に fallback し、watcher 自体は落ちない。

| OS / 環境 | desktop backend | sound backend | 備考 |
|---|---|---|---|
| macOS | `osascript -e 'display notification ...'` | `afplay <file>` (`sound` で path 指定時) / terminal bell | macOS は標準で desktop notification が出る。サウンドは音声ファイル path を指定した場合のみ `afplay`、未指定なら bell |
| Linux | `notify-send <title> <body>` | `paplay <file>` → `canberra-gtk-play -i bell` の順で試行 / terminal bell | `notify-send` は `libnotify-bin` パッケージ。GNOME / KDE どちらでも動く |
| Windows native | PowerShell の toast notification、不可なら console fallback | PowerShell `[console]::Beep()` | PowerShell 5+ 前提。レガシー cmd.exe では console fallback に落ちる |
| WSL | `powershell.exe` 経由で Windows ホストに toast を表示 | `powershell.exe` 経由の beep | WSL 内から Windows 側の通知センターに出る。`powershell.exe` が PATH にあること |
| fallback (上記不可 / コンテナ等) | stdout に構造化ログ | terminal bell `\a` | watcher は落ちない |

`--dry-run` 時はどの環境でも OS subprocess を呼ばずに stdout のみ。

## 4. config の主要キー（運用視点）

詳細スキーマは [`docs/design/attention-notification.md`](../design/attention-notification.md) §5 / §6、SoT は runtime config schema。ここでは ja 運用でよく触るキーだけ示す。

| キー | 既定値 | 役割 |
|---|---|---|
| `desktop` | `true` | desktop notification を出すか。`false` で stdout + bell のみ |
| `sound` | `"urgent-only"` | `"off"` / `"urgent-only"` / `"all"`。urgent-only は urgent severity の event だけ音 |
| `cooldown_sec` | `300` | 同じ dedup key に対する再通知の最短間隔（秒） |
| `poll_interval_sec` | `10` | `watch` の polling 周期 |
| `pending_decision_min` | `15` | `pending_decisions.json` の pending を urgent と判定する経過分 |
| `user_replied_min` | `15` | user replied だが worker 未転送の状態を urgent と判定する経過分 |
| `max_title_chars` / `max_body_chars` | `80` / `240` | template 出力の truncation 上限（secret-safe formatting の一環） |
| `notify.<kind>` | §1 参照 | event kind ごとの severity（`urgent` / `normal` / `off`） |
| `templates.<kind>.{title,body}` | ja 既定文面 | placeholder allowlist は `{task_id} {worker} {kind} {status} {pr} {summary}` |

## 5. トラブルシューティング

### 5.1 desktop notification が出ない

1. `--dry-run` で stdout に attention event が出るか確認:
   ```bash
   claude-org-runtime attention scan --state-dir .state --dry-run --json
   ```
   event が出ない場合は分類器の上流（`.state/state.db` の event 投入 or `.state/pending_decisions.json` の状態）に原因がある。watcher 側ではなく `tools/journal_append.sh` / `tools/pending_decisions.py` 経路の追跡へ。
2. event は出るが OS 通知だけ出ない場合は backend が無効と判定された可能性が高い:
   - **macOS**: 通知センターの設定でターミナル / iTerm への通知が許可されているか
   - **Linux**: `which notify-send`、出なければ `sudo apt install libnotify-bin` 等で導入
   - **Windows / WSL**: `powershell.exe -Command "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')"` が成功するか
3. stdout に「fallback to terminal bell」相当のログがあれば backend は明示的に落ちている。上記 2 を確認

### 5.2 音が鳴らない

- `.state/attention.json` の `sound` を確認（`"off"` になっていないか、`"urgent-only"` で対象 event が `urgent` 分類か）
- macOS で音声ファイルを指定する設定にしている場合、ファイル path が存在するか
- Linux で `paplay` / `canberra-gtk-play` がどちらも未導入だと bell に落ちる。`pulseaudio-utils` か `libcanberra-gtk3-module` を導入
- terminal 設定でベル音が抑制されていないか（iTerm / Windows Terminal は visual bell 等のオプションあり）

### 5.3 同じ event が鳴り続ける / 鳴らない

dedup state は `.state/attention_notified.json` で runtime が管理する。

- **同じ event が cooldown を無視して鳴り続ける** → `.state/attention_notified.json` が broken JSON になっている可能性。runtime は破損検出時に warning を出して再生成するが、強制リセットしたい場合は手動で削除する:
  ```bash
  rm .state/attention_notified.json
  ```
  次回 scan / watch で再生成される（atomic replace）。
- **鳴ってほしい event が鳴らない** → cooldown 内で抑止されている可能性。`cooldown_sec` を一時的に短くするか、当該 key を `.state/attention_notified.json` から手動で取り除いて再 scan する。

### 5.4 通知本文が想定と違う / 文字化け

- placeholder が literal のまま出る → 設計 §6 の placeholder allowlist (`{task_id} {worker} {kind} {status} {pr} {summary}`) 以外を template に書いた場合、runtime は literal のまま残すか fallback template を使う。allowlist 内に書き直す
- 本文が途切れる → `max_title_chars` / `max_body_chars` で truncation されている。secret-safe formatting の一環なので、本文を伸ばすより summary を短くするのが望ましい
- 日本語が `?` になる / 文字化けする → Linux で locale が `C` の場合に `notify-send` が壊すことがある。`LANG=ja_JP.UTF-8` などを export してから `watch` を起動

## 6. 関連

- 設計: [`docs/design/attention-notification.md`](../design/attention-notification.md)
- 検証手順: [`docs/verification.md`](../verification.md) の `attention scan --dry-run` 検証ブロック
- `/org-start` からの起動案内: [`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md)
- 外部 sink（Slack / Discord / ntfy）は初回スコープ外（設計 §9、将来 opt-in）
