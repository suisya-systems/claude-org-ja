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

### 2.1 推奨: skill 経由の起動 / 停止

renga タブ内（窓口セッション）からは、設定ファイルの自動配置・dispatcher ペインへの split 起動・pane_id 記録までを一括で扱う **2 つのスキル**を使うのが推奨経路:

| 操作 | スキル | 副作用 |
|---|---|---|
| 起動 | [`/org-attention-start`](../../.claude/skills/org-attention-start/SKILL.md) | `.state/attention.json` 未配置時は `tools/templates/attention.example.json` から自動コピー → dispatcher ペインの右側を vertical split → `claude-org-runtime attention watch ...` を常駐起動 → pane_id を `.state/attention_pane.json` sidecar に記録 |
| 停止 | [`/org-attention-stop`](../../.claude/skills/org-attention-stop/SKILL.md) | sidecar を読んで `mcp__renga-peers__close_pane` でペイン破棄 → sidecar 削除 |

`/org-start` からの自動起動はしない（OS 通知 backend は環境依存が強く、勝手に音が鳴ると不快になりやすいため。設計 [`docs/design/attention-notification.md`](../design/attention-notification.md) §11 Q1）。`/org-start` 完了後に明示的に `/org-attention-start` を発火するか、必要なときだけ手動配置（§2.2）する。

sidecar (`.state/attention_pane.json`) は `.state/dashboard.pid` / `.state/attention_notified.json` と同じ「補助プロセス追跡」パターンで、`.state/state.db` schema は拡張しない（importer / writer / snapshotter / converter / drift_check への波及を避けるため）。`.state/` が gitignored なので sidecar も commit には乗らない。

### 2.2 手動配置（renga 外 / 別ターミナルから起動したい場合）

`tools/templates/attention.example.json` は ja 既定のテンプレート集を含む tracked example。renga タブを使わず別ターミナルから常駐させたい場合や、テンプレートを手動編集してから配置したい場合は次の通り:

```bash
mkdir -p .state
cp tools/templates/attention.example.json .state/attention.json
```

OS 通知や音の挙動を変えたい場合は `.state/attention.json` を編集する（template strings の上書き、`sound` の切替、`cooldown_sec` の調整など）。テンプレートの placeholder allowlist は `{task_id}` / `{worker}` / `{kind}` / `{status}` / `{pr}` / `{summary}` の 6 種で、未知 placeholder は literal のまま残るか runtime の fallback template で補われる（設計 §6 参照）。

### 2.3 1 回限りの動作確認 (`scan`)

`watch` を常駐させる前に、現状の `.state/` から想定どおりに attention event が抽出されるか確認する:

```bash
claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json
```

`--config .state/attention.json` を必ず付ける（外すと runtime 中立の英語 default が title/body に出てしまい、ja テンプレートが効いているかの確認にならない）。`--dry-run` は OS notification subprocess を呼ばないので、CI 環境や音を鳴らしたくない時間帯でも安全。出力は `{ "events": [{ "key": ..., "kind": ..., "severity": ..., "title": ..., "body": ...}, ...] }` 形式（詳細は [`docs/verification.md` の attention scan 検証ブロック](../verification.md) を参照）。

### 2.4 常駐 (`watch`) — 手動起動

renga タブの外（別ターミナル / バックグラウンド）から直接 watcher を起動したい場合の素の CLI:

```bash
claude-org-runtime attention watch --state-dir .state --config .state/attention.json
```

通常運用では §2.1 の `/org-attention-start` を使う（pane_id 記録・sidecar 管理・二重起動チェックを skill が担当）。停止は Ctrl-C（dedup state の書き込みは atomic replace で行われるため、強制終了でも次回起動時に復旧可能。§5 参照）。

### 2.5 無効化

renga タブ内から起動した場合は [`/org-attention-stop`](../../.claude/skills/org-attention-stop/SKILL.md) で sidecar とペインを一括クリーンアップする。手動起動した watcher は Ctrl-C で終了するだけでよい。設定ファイル（`.state/attention.json`）の削除は不要。一時的に通知を抑えたい場合は `.state/attention.json` で `"desktop": false` / `"sound": "off"` に切り替えるか、個別 kind の severity を `"urgent"` → `"normal"` に下げる（`notify.<kind>` に取れる値は `"urgent"` / `"normal"` の 2 値のみで、kind 単位の `off` は無い。完全に止めたい場合は全体 `desktop: false` を使う）。

## 3. OS 別の notification backend 挙動

runtime は追加 dependency なしで OS の標準コマンドを subprocess 経由で呼び出す（timeout 付き）。利用不可な backend は stdout + terminal bell に fallback し、watcher 自体は落ちない。**現行 runtime (0.1.x) はサウンドを再生する音声ファイル経路は持たず、`sound` 設定が effective なときは OS 通知の直後に terminal bell (`\a`) を鳴らす実装**（Windows / WSL のみ `[console]::beep` で簡易ビープ）。`afplay` / `paplay` / `canberra-gtk-play` 等のサウンド再生コマンドは現状未使用で、将来 enhancement として記載されているのみ。

| OS / 環境 | desktop backend | sound backend | 備考 |
|---|---|---|---|
| macOS | `osascript -e 'display notification ...'` | terminal bell `\a` | macOS は標準で desktop notification が出る。`sound` が effective なときは通知直後に bell |
| Linux | `notify-send <title> <body>` | terminal bell `\a` | `notify-send` は `libnotify-bin` パッケージ。GNOME / KDE どちらでも動く。DBus 不在時は `notify-send` が静かに失敗するが watcher は落ちず stdout に fallback |
| Windows native | PowerShell `Write-Host`（実体は可視 UI を出さない） | PowerShell `[console]::beep`（urgent 時のみ） | **現状 visible な通知サーフェスを持たない。** urgent severity の event は beep のみ可聴で、Windows 通知センターには出ない。BurntToast / WinRT を使った real toast 化は別の follow-up Issue として runtime 側に切り出し済み（PR #27 では未着手） |
| WSL（`wsl-notify-send.exe` あり、**推奨**） | `wsl-notify-send.exe --category <title> <body>` で Windows 通知センターに real toast | PowerShell `[console]::beep`（urgent 時のみ、別 subprocess） | runtime は WSL 内の `$PATH` に `wsl-notify-send.exe` があれば自動でこの経路を選ぶ。インストール手順は §3.1 を参照。toast 成功時は terminal bell を抑制して二重 beep を防ぐ |
| WSL（`powershell.exe` のみ、fallback） | PowerShell `Write-Host`（実体は可視 UI を出さない） | PowerShell `[console]::beep`（urgent 時のみ） | `wsl-notify-send.exe` 未導入で `powershell.exe` のみ PATH にあるときの legacy 経路。**可視 UI なし、urgent の beep のみ可聴**。Windows 通知センターに出したい場合は §3.1 のインストール手順を実施する |
| fallback (上記不可 / コンテナ等) | stdout に構造化ログ | terminal bell `\a` | watcher は落ちない |

`--dry-run` 時はどの環境でも OS subprocess を呼ばずに stdout のみ。

WSL の backend 選択は `wsl-notify-send.exe` → `powershell.exe` → stdout の 3 段階優先順位で、PATH 上の有無で自動切替される。runtime config 側で明示的に backend を指名する手段は持たず、環境を整えること（= `wsl-notify-send.exe` を入れる / 入れない）で挙動が決まる設計である。

### 3.1 wsl-notify-send.exe のインストール（WSL 推奨）

WSL から Windows 側の通知センターに real toast を出すには [`stuartleeks/wsl-notify-send`](https://github.com/stuartleeks/wsl-notify-send) を導入する。runtime 側は WSL の `$PATH` に exe があるかで自動選択するため、`.state/attention.json` 等の設定変更は不要。

```bash
mkdir -p ~/.local/bin
curl -L -o ~/.local/bin/wsl-notify-send.exe \
  https://github.com/stuartleeks/wsl-notify-send/releases/latest/download/wsl-notify-send.exe
chmod +x ~/.local/bin/wsl-notify-send.exe
wsl-notify-send.exe 'test'
```

- `~/.local/bin` が `$PATH` に入っていない場合は `.bashrc` / `.zshrc` 等で追加する（`export PATH="$HOME/.local/bin:$PATH"`）。
- 動作確認の `wsl-notify-send.exe 'test'` で Windows 側の通知センターに 'test' が表示されれば成功。
- 上流（`stuartleeks/wsl-notify-send`）は MIT ライセンスの Go 製。最終リリースは 2021 年で stable だが上流自体は dormant（新規機能の追加予定は無い）。
- 導入後、`claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json` を再実行しても JSON 出力は変わらない（dry-run は OS subprocess を呼ばないため）。実際の toast を確認したい場合は `--dry-run` を外して urgent severity の event を 1 件流す。

#### title/body の見え方

`wsl-notify-send.exe --category <title> <body>` の呼び出しは Windows toast の以下にマップされる:

- 通知の **タイトル行**（上段）← `--category` に渡された runtime template の `title`
- 通知の **本文**（下段）← positional argument に渡された runtime template の `body`

`--category` は本来「通知カテゴリ」用フラグだが、上流 `wsl-notify-send` の実装はこれをタイトル文字列としてレンダリングするため、claude-org-runtime は title を `--category` に乗せている（PR #27 worker の design judgment、上流 `main.go` のドキュメントに準拠）。`.state/attention.json` の `templates.<kind>.title` / `body` をそのまま toast の 2 行表示として読める。

### 3.2 Why no toast on Windows native / WSL fallback? — history

Issue #25 / PR #27 以前は、WSL / Windows native とも「PowerShell `Write-Host` 経由で toast を出す」と説明されていたが、実装は `Write-Host` を subprocess の captured stdout に書いて捨てるだけで、Windows の通知センターには何も到達していなかった（urgent 時の `[console]::beep` だけが可聴な合図）。

- PR #27（[suisya-systems/claude-org-runtime#27](https://github.com/suisya-systems/claude-org-runtime/pull/27)）で WSL backend を `wsl-notify-send.exe → powershell.exe → stdout` の 3 段階に分割し、上位の `wsl-notify-send.exe` 経路で real toast を出すよう修正した。
- Windows native backend は同じ defect を抱えたままで、PR #27 では未修正。BurntToast / WinRT 等で real toast 化する作業は runtime 側の別 Issue として切り出されている。
- 既存ユーザーへの影響: `wsl-notify-send.exe` を入れていない WSL ユーザーは PR #27 以前と同じ legacy 経路（Write-Host + beep）のまま。breaking change は無く、`§3.1` のインストール手順を踏んだユーザーのみ自動的に real toast を受け取る。

## 4. config の主要キー（運用視点）

詳細スキーマは [`docs/design/attention-notification.md`](../design/attention-notification.md) §5 / §6、SoT は runtime config schema。ここでは ja 運用でよく触るキーだけ示す。

| キー | 既定値 | 役割 |
|---|---|---|
| `desktop` | `true` | desktop notification を出すか。`false` で stdout + bell のみ |
| `sound` | `"urgent-only"` | `"off"` / `"urgent-only"` / `"all"`。urgent-only は urgent severity の event だけ音 |
| `cooldown_sec` | `300` | 同じ dedup key に対する再通知の最短間隔（秒） |
| `poll_interval_sec` | `10` | `watch` の polling 周期 |
| `pending_decision_min` | `15` | `pending_decisions.json` の pending を urgent と判定する経過分（4 段 ladder の入り口、§4.1 参照） |
| `pending_decision_max` | `1440` | urgent 期間の上限（分）。これを超えた pending は normal に降格する（24h） |
| `pending_decision_drop` | `10080` | normal 通知の終端（分）。これを超えた pending は通知抑止され `--json` 出力にのみ残る（7d） |
| `user_replied_min` | `15` | user replied だが worker 未転送の状態を urgent と判定する経過分 |
| `max_title_chars` / `max_body_chars` | `80` / `240` | template 出力の truncation 上限（secret-safe formatting の一環） |
| `notify.<kind>` | §4.1 参照 | event kind ごとの severity（`urgent` / `normal` の 2 値のみ、`off` は不可。完全に止めたい場合は全体 `desktop: false` を使う） |
| `templates.<kind>.{title,body}` | ja 既定文面 | placeholder allowlist は `{task_id} {worker} {kind} {status} {pr} {summary}` |

### 4.1 既定の severity 分類

`tools/templates/attention.example.json` の `notify` map に対応する各 kind の既定 severity を以下に示す。**urgent は「ユーザーだけが復旧経路の action-required moment」、normal は「自己復旧する可能性のある anomaly / 予兆シグナル」** に概ね対応する（taxonomy の根拠は [`docs/design/attention-notification.md`](../design/attention-notification.md) §12 を参照）。

| event kind | 既定 severity | 区分 | 備考 |
|---|---|---|---|
| `approval_blocked` | urgent | action-required | tool approval 等で worker が完全停止。ユーザー以外の経路で解除できない |
| `ci_failed` | urgent | action-required | CI 失敗。再 push / 修正の判断にユーザーが必要 |
| `pending_decision` | urgent | action-required | worker から判断仰ぎ。Secretary は人間に上げる責務（CLAUDE.md 参照） |
| `user_reply_not_forwarded` | urgent | action-required | ユーザーは返答済みだが worker に未転送。Secretary の運用ギャップ |
| `pane_crashed` | urgent | action-required | ペインが予期せず終了。再起動判断にユーザーが必要 |
| `relay_gap_suspected` | normal | anomaly / 予兆 | dispatcher monitoring の予兆検出。自己復旧する場合が多く urgent muting の主因だったため demote |
| `silent_worker_output` | normal | anomaly / 予兆 | ペイン出力ありだが peer message 未着。同上 |
| `pane_silent` | normal | anomaly / 予兆 | ペインが無反応。dispatcher 側で自己復旧する場合あり |
| `worker_stalled` | normal | anomaly / 予兆 | worker の進捗停滞推定。短期は自己復旧する場合あり |
| `worker_not_reported` | normal | anomaly / 予兆 | worker からの報告が未着。短期は遅延の可能性 |
| `worker_error` | normal | anomaly / 予兆 | worker のエラー報告。worker 内で recover する場合あり |
| `worker_completed` | normal | progress | 完了。即時 action は不要だが review 待ち |
| `pr_merged` | normal | progress | マージ済み。post-merge cleanup の trigger |

**※ ローカル上書き**: `.state/attention.json` で個別 kind の severity を上書きできる。ja 配布の template 既定値とユーザー個別の `.state/attention.json` overlay は別レイヤーである（template は tracked、overlay は gitignored）。たとえば `worker_completed` を「即見たい」ユーザーは `.state/attention.json` 側で urgent に上げてよく、template 既定（normal）はそれと独立に維持される。

### 4.2 pending_decisions の 4 段 TTL ladder

`pending_decision_min` / `pending_decision_max` / `pending_decision_drop` の 3 つの閾値は、判断仰ぎが register に登録されてからの経過時間に応じて **4 段階の通知 decay** を作る。これは「未対応の判断仰ぎを永遠に urgent で鳴らし続ける」運用が結果的に watcher 自体を mute されてしまう（noise → 全 ignore）失敗パターンを避けるための設計である（rationale は [`docs/design/attention-notification.md`](../design/attention-notification.md) §12 を参照）。

| 経過時間 | 段階 | 挙動 |
|---|---|---|
| `< pending_decision_min` (既定 15 分未満) | 猶予 | attention event を発火しない。Secretary が短時間で人間に転送できる前提の grace window |
| `pending_decision_min ≤ t < pending_decision_max` (既定 15 分〜24h) | urgent | desktop notification + urgent sound + terminal bell。一次対応窓 |
| `pending_decision_max ≤ t < pending_decision_drop` (既定 24h〜7d) | normal / visual-only | desktop notification は出るが urgent sound は鳴らさない。長期未対応案件として visual 残し |
| `≥ pending_decision_drop` (既定 7d 以上) | suppressed | desktop / sound 共に抑止。`attention scan --json` の出力にのみ残り、audit / dashboard 経路で参照可能 |

**runtime 側の整合性条件**: `pending_decision_min < pending_decision_max < pending_decision_drop` を満たさない config は runtime の `attention/config.py` が load 時に validation error を返す（`user_replied_min < pending_decision_max` も併せて検査される）。

**Tuning advice**:

- **noisier workflows で urgent 期間を絞りたい**: `pending_decision_max` を下げる（例: 24h → 4h）。早めに urgent → normal に降格させ、緊急度の高い新規 pending と区別する
- **長い audit trail を残したい**: `pending_decision_drop` を上げる（例: 7d → 30d）。完全 suppress までの猶予を伸ばし、長期 backlog を `attention scan --json` で監査可能にする
- **判断仰ぎが頻繁で 15 分の grace が短い**: `pending_decision_min` を伸ばす（例: 15 → 60）。Secretary の relay 余裕を見たい運用で urgent 過多を抑える
- `pending_decision_drop = pending_decision_max` に揃えると normal/visual 段が消え、超過時に即 suppressed になる。短い lifecycle の運用で「降格中の表示」が要らないチーム向け

## 5. トラブルシューティング

### 5.1 desktop notification が出ない

1. `--dry-run` で stdout に attention event が出るか確認:
   ```bash
   claude-org-runtime attention scan --state-dir .state --config .state/attention.json --dry-run --json
   ```
   event が出ない場合は分類器の上流（`.state/state.db` の event 投入 or `.state/pending_decisions.json` の状態）に原因がある。watcher 側ではなく `tools/journal_append.sh` / `tools/pending_decisions.py` 経路の追跡へ。
2. event は出るが OS 通知だけ出ない場合は backend が無効と判定された / 可視 UI を持たない backend が選ばれている可能性が高い:
   - **macOS**: 通知センターの設定でターミナル / iTerm への通知が許可されているか
   - **Linux**: `which notify-send`、出なければ `sudo apt install libnotify-bin` 等で導入
   - **WSL**: `which wsl-notify-send.exe` で導入有無を確認。未導入だと PowerShell `Write-Host` fallback に落ちて Windows 通知センターには出ない（urgent の beep のみ可聴）。§3.1 のインストール手順で `~/.local/bin/wsl-notify-send.exe` を入れる
   - **Windows native**: 現状 visible な通知サーフェス未実装（§3 表参照）。urgent の beep のみ可聴。real toast は別 follow-up Issue で対応予定
3. stdout に「fallback to terminal bell」相当のログがあれば backend は明示的に落ちている。上記 2 を確認

### 5.2 音が鳴らない

- `.state/attention.json` の `sound` を確認（`"off"` になっていないか、`"urgent-only"` で対象 event が `urgent` 分類か）
- 現行 runtime はサウンド再生コマンド (`afplay` / `paplay` 等) を呼ばず、`sound` が effective なときに terminal bell `\a` を鳴らす実装。terminal 設定でベル音が抑制されていないか確認（iTerm / Windows Terminal / GNOME Terminal は visual bell / silent bell のオプションあり）
- Windows / WSL では `powershell.exe -NoProfile -Command "[console]::beep(...)"` を別 subprocess として呼ぶ（WSL で `wsl-notify-send.exe` 経路が選ばれた場合も beep は同じ PowerShell 経由）。ホスト側のサウンド設定（システム音量 / ステレオミキサーのミュート）を確認
- WSL の real toast 経路（§3.1）では、toast 成功時に terminal bell が抑制される（PowerShell beep との二重 beep 回避）。toast が出ているのに beep だけ鳴らないと感じる場合は `sound` 設定 / event severity が urgent 分類か / システム音量、の順で確認

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
