# ペインクローズ（CLOSE_PANE 受信時） — 詳細

`.dispatcher/CLAUDE.md` の「ペインクローズ」エントリポイントから参照される本体仕様。retro ゲート、知見記録、`close_pane` 呼び出し、窓口報告までの全手順を含む。

## ペインクローズ（CLOSE_PANE 受信時）

**重要: Step 1〜2 の振り返りが完全に終わるまで、絶対にペインを閉じないこと。**
ペインを閉じるとワーカーの出力が失われ、振り返りに必要な情報が取得できなくなる。
必ず以下の順序で実行する:

### 1. 振り返り（org-retro 相当）

#### ⚠️ 完了報告ゲート（結論を書く前に必ず実行）

「完了報告未着」「報告が届かなかった」「ワーカーが報告しなかった」等の結論を retro に書く **前に**、必ず `tools/dispatcher_retro_gate.py` を使って secretary の ack を待つこと。

CLI は **1 attempt あたり 1 回起動する単発判定**（Issue #285、Claude Code の Bash tool が一往復であるため、長寿命の双方向プロセスは想定しない）。各 attempt の cadence（30 秒スリープ）はディスパッチャー側が `Bash sleep 30` で挟む。

#### 1. 初回送信（attempt=1 の前に 1 度だけ）

`--print-initial-prompt` で task_id 込みの定型文を取り出し、`mcp__renga-peers__send_message` で secretary に送る:

```bash
# ディスパッチャー cwd は .dispatcher/ なので 1 段上がリポジトリルート。
python ../tools/dispatcher_retro_gate.py --task-id <task_id> --print-initial-prompt
# stdout: <task_id> の完了報告は届いていますか？
```

```
mcp__renga-peers__send_message(to_id="secretary", message="<上記 stdout>")
```

#### 2. polling ループ（attempt=1..10、合計 5 分上限）

各 attempt で:

1. `mcp__renga-peers__check_messages` で受信を取得し、戻り値を `{"messages": [...]}` の形に整える（`state` は前 attempt の `polling` 出力から引き継ぐ。attempt=1 では省略可）。
2. CLI を起動して stdin に渡す:

   ```bash
   echo '<json>' | python ../tools/dispatcher_retro_gate.py \
       --task-id <task_id> --attempt <n> --max-attempts 10
   ```

3. stdout は単一 JSON。exit code で switch:

   - `0 / status=acked` → retro を続行する。
   - `1 / status=timeout` → secretary から 1 度も返信が無く打ち切り。下の「secretary unreachable 時の fallback」フローに入る（retro に「未着」と書かない）。
   - `2 / status=error` → CLI スキーマ不整合 / regex compile 失敗。`reason` を確認して呼び出し側を修正する。retro は保留扱い。
   - `3 / status=replied_no_ack` → secretary は到達したが本文が ack regex に一致せず最終 attempt まで進んだ。`raw` を読んで内容に応じて判断する（「届いていない」旨の確定返信なら retro に未着を確定的に書いてよい。曖昧なら secretary に追問する）。`secretary_unreachable` フローには入らない。
   - `4 / status=polling` → まだ attempt が残っている。`Bash sleep 30` を挟み、`state` フィールドを次回呼び出しに渡して continue する。

**理由**: ワーカーのレポートチャネルは secretary 直送である。dispatcher のメッセージキュー（`check_messages` の戻り）に完了報告が無いことは、「システム上に存在しない」ことを意味しない。secretary 側に既に届いていることがしばしばあり、確認を怠ると「完了報告未着」と誤った結論を retro に残してしまう（実インシデント: `knowledge/raw/2026-05-03-delegation-smoke-completion-report.md`）。

**secretary unreachable 時の fallback**: 上記送信が `[pane_not_found]` 等で失敗する、または 5 分以内に応答が返らない場合は、retro に「未着」と誤った結論を書かない。代わりに以下の手順で **CLOSE_PANE フローを中断** する:

1. journal helper 経由で `retro_deferred` を追記する（生 JSON を `>>` で書かない。helper 契約は `.dispatcher/CLAUDE.md`「delegate-plan helper」節末尾の `tools/journal_append.sh` 説明を参照）:
   ```bash
   bash ../tools/journal_append.sh retro_deferred worker=worker-{task_id} reason=secretary_unreachable
   ```
2. ワーカーペインは **閉じない**（Step 3 の `close_pane` をスキップする）。「振り返りが完全に終わるまで絶対にペインを閉じない」原則（本セクション冒頭）に従い、確認不能な状態でペインを破棄して最終状況の証拠を失わないようにする。
3. 後続の `/org-resume` または窓口復帰時に、本ワーカーの完了確認と retro 続行を再試行する。それまで CLOSE_PANE は保留扱い。

#### 観点

以下の観点でこのワーカーへの委譲を振り返る:
- **指示は明確だったか**: ワーカーが迷わず作業できたか（進捗ログや renga-peers の履歴を参考にする）
- **タスク分解は適切だったか**: 粒度が大きすぎ/小さすぎなかったか
- **承認待ちブロックが発生したか**: 発生した場合、permission 設定の改善余地はあるか

情報収集:
- `.state/workers/worker-{peer_id}.md` を読み、進捗ログを確認する
- `mcp__renga-peers__send_message` でワーカーに最終状況のサマリーを問い合わせる
- または `mcp__renga-peers__inspect_pane(target="worker-{task_id}", format="text")` で画面内容を読む

### 2. 知見の記録（該当する場合のみ）

再利用可能な学びがあれば記録する:
- パス: `knowledge/raw/{YYYY-MM-DD}-delegation-{topic}.md`
- フォーマット: `.claude/skills/org-curate/references/knowledge-standards.md` の「記録フォーマット」を参照
- 記録基準: 同じ種類の委譲で再び遭遇しそうなパターンのみ。一度きりの問題は記録しない

### 3. ペインを閉じる

`mcp__renga-peers__close_pane` で明示的にペインを破棄する:

```
mcp__renga-peers__close_pane(target="worker-{task_id}")
```

成功時は `"Closed pane id=N."` テキストが返り、renga が `Event::PaneExited` を (exit_event_emitted ガード経由で) 正確に 1 回 emit する。
エラー時は結果テキストの `[<code>]` で分岐する (詳細は `.claude/skills/org-delegate/references/renga-error-codes.md`):
- `[pane_not_found]` / `[pane_vanished]` — 既に閉じた扱いで skip (`WORKER_PANE_EXITED` 経路に回す)
- `[last_pane]` — 唯一のタブの唯一のペインを閉じようとした。通常のワーカー停止では発生しない (窓口/ディスパッチャー/キュレーターが残っているため) が、suspend 末端で起きた場合は該当ペインを自分自身で `exit` させる (org-suspend 参照)

### 4. 窓口への報告

知見を記録した場合のみ、`mcp__renga-peers__send_message` で窓口に報告する:
```
RETRO_RECORDED: {task_id} の委譲について {topic} の学びを記録しました。
```

### 5. curate 閾値チェックとオンデマンド curator 起動

常駐キュレーター（`/org-start` 起動 + `/loop 30m`）は廃止されている。worker クローズは
knowledge/raw/ が増える主経路なので、**CLOSE_PANE 処理の最後（Step 1〜4 完了後）に毎回**
閾値チェックを行い、超過時のみ curator を一時起動する。

> **実行コンテキスト**: 閾値チェック（5-1）〜 spawn / 起動指示（5-5）と inflight 記録（5-6）
> までが CLOSE_PANE ハンドラの**インライン処理**。CURATE_* の完了受領・timeout 管理・
> curator ペインのクローズは**ブロッキングせず** `/loop 3m` 監視サイクル側
> （[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3)）が行う。
> 「全ワーカーペインが閉じたら監視ループを停止する」判定よりも**先に** 5-1〜5-6 を完走させ、
> `curate-inflight.json` が存在する間は監視ループを停止しないこと。
>
> **starvation の既知の限界**: worker close が発生しない期間（手動 raw 追加のみ /
> skill-candidate のみ増加等）はこのチェックが走らない。補助トリガーは
> [Issue #501](https://github.com/suisya-systems/claude-org-ja/issues/501)（org-retro 末尾）/
> [Issue #502](https://github.com/suisya-systems/claude-org-ja/issues/502)（org-start バックストップ）
> としてバックログ化済み。

#### 5-1. 閾値チェックスクリプトの実行

```bash
# ディスパッチャー cwd は .dispatcher/ なので 1 段上がリポジトリルート。
py -3 ../tools/check_curate_threshold.py    # Windows
python3 ../tools/check_curate_threshold.py  # Mac/Linux
```

stdout に `status` / `reasons[]` / `counts` の JSON が 1 行出る。分岐は **exit code** で行う:

- **exit 0 (below_threshold)** → 何もしない。CLOSE_PANE フロー完了
- **exit 10 (curate_needed)** → stdout の JSON を控えて 5-2 へ
- **exit 2 (error)** → 窓口に informational として 1 行報告し、curate はスキップする
  （CLOSE_PANE フロー自体は完了扱い。閾値チェック失敗で worker クローズを止めない）

#### 5-2. single-flight 確認（coalesce）

spawn の**前に必ず** `mcp__renga-peers__list_panes` で既存 curator を確認する:

- `name == "curator"` のペインが既に存在する → **coalesce: 再 spawn しない**。CLOSE_PANE
  フロー完了。実行中のサイクルが raw 列挙（org-curate Step 2）を既に通過していた場合、
  今回の増分はそのサイクルでは拾われないが、**取りこぼしではなく遅延**である: 未処理の
  raw / pending はファイルとして残り続け、次回の worker close の閾値チェックで再評価される
  （worker close が長期間発生しないケースの保険は backlog Issue #501 / #502 の補助トリガー）
- 存在しない → 5-3 へ

worker close が短時間に連続した場合の `name_in_use` 衝突 / 別名重複起動（knowledge/ への
競合書き込み）をこの規約で防ぐ。

#### 5-3. curator ペインの spawn

```
mcp__renga-peers__spawn_claude_pane(
  target="dispatcher",
  direction="vertical",
  role="curator",
  name="curator",
  cwd="../.curator",
  permission_mode="auto",
  model="opus"
)
```

- `cwd` は caller（dispatcher、cwd=`.dispatcher/`）基準の相対解決なので `../.curator`
- `[name_in_use]` が返った場合は 5-2 とのレース（直前に別トリガーが spawn した）なので
  **coalesce 扱いで終了**してよい
- その他の `[<code>]` エラーは窓口に informational として報告し、curate をスキップする

> **state.db には書かない**: オンデマンド curator は ephemeral であり、`curator_pane_id` /
> `curator_peer_id` は **null のまま**が正常系。生存確認は `list_panes`（5-2）のみで行う。
> DB に書くと常駐前提が復活し、suspend / handover / dashboard の照合が誤る。

#### 5-4. boot 確認（Enter / list_peers poll）

`/org-start` Block D-1〜D-2 の dispatcher 分と同じ手順:

1. `mcp__renga-peers__send_keys(target="curator", enter=true)` で
   「Load development channel? (Y/n)」プロンプトを承認する
2. `mcp__renga-peers__list_peers` で `name="curator"` の peer 登録を poll する。
   未登録なら Enter を再送して再 poll（最大 3 回 retry）
3. 3 回 retry しても登録されない場合は `close_pane(target="curator")` で破棄し、
   窓口に informational として報告して curate をスキップする

#### 5-5. 起動指示の送信

5-1 で控えた JSON を**そのまま**埋め込んで送る（dispatcher 側で再解釈・再計算しない）:

```
mcp__renga-peers__send_message(to_id="curator", message="あなたはキュレーターです。/org-curate を 1 回だけ実行してください（/loop 禁止）。起動理由: {check_curate_threshold.py の stdout JSON}。完了時は改善提案（secretary 宛て）を送った後、必ず dispatcher 宛て direct send で CURATE_DONE / CURATE_SKIPPED / CURATE_ERROR のいずれかを送ってください。")
```

#### 5-6. inflight 記録と監視ループへの即時復帰（ブロッキング待ちをしない）

**ここで CURATE_* を待たない**。完了待ちで CLOSE_PANE ハンドラをブロックすると、その間
他 worker の `/loop 3m` 監視（stall / relay gap / silent dead-lock 検出）が止まり、
安全網の目的と矛盾するため、完了受領とクローズは監視ループ側
（[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3)）に委ねる。

1. 追跡状態を `.state/dispatcher/curate-inflight.json` に書く（ディスパッチャー cwd は
   `.dispatcher/` なので `../.state/dispatcher/curate-inflight.json`）:
   ```json
   {
     "started_at": "<ISO-8601 UTC、spawn 直後の現在時刻>",
     "reasons": ["<5-1 の JSON の reasons[] をそのまま>"],
     "trigger_task_id": "<本 CLOSE_PANE の対象だった task_id>",
     "extended": false
   }
   ```
   このファイルは [`.claude/skills/dispatcher-handover/SKILL.md`](../../.claude/skills/dispatcher-handover/SKILL.md) /
   `/clear` / resume で**保持される側**の内部状態ファイル（`.dispatcher/CLAUDE.md`
   「監視 gap を埋める内部状態ファイル」リスト参照）。resume 後の監視ループが
   `started_at` 起点で timeout 管理を引き継ぐ。
2. **即座に CLOSE_PANE フローを完了し、`/loop 3m` 監視ループへ復帰する**
3. 今回の worker close で全 worker ペインが閉じていても、`curate-inflight.json` が存在する
   間は監視ループを**停止しない**（curate 完了監視のため継続。
   [`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) 末尾の停止条件参照）

#### 5-7. 完了受領・timeout 管理・クローズ（監視ループ側の責務）

CURATE_DONE / CURATE_SKIPPED / CURATE_ERROR の受領、20 分 timeout の観測、
`close_pane(target="curator")`、inflight ファイルの削除は、すべて監視ループの
[`.dispatcher/references/worker-monitoring.md` Step 5.3](worker-monitoring.md#step-5-3) が通常サイクル内で行う。
本 CLOSE_PANE ハンドラ側に残る作業は無い。state.db への後始末も不要（5-3 の注記どおり
そもそも書いていない）。
