# Step 4 の誤検知パターン (d-fp) と通知シーケンス (e) / (f) — 詳細

[`.dispatcher/references/worker-monitoring.md`](worker-monitoring.md) から分けた本体仕様。Step 4 で APPROVAL_BLOCKED / ERROR のパターンに該当した worker が出たサイクルだけ読む（それ以外のサイクルでは読まない）。

   #### (d-fp) 既知の誤検知パターンと見分け方 — doc / コードコメント中の status code (2026-07-29 実測)
   (d) の status-code ゲート (`(?<!#)\b(429|500|502|503|504|529)\b` + 同一行の error 文脈キーワード) は、**その行が「実際に発生したエラーの記録」か「エラーの意味を説明する文書」かを区別できない**。ワーカーが `README.md` のトラブルシューティング表と `.py` のコードコメントに「`temporarily_unavailable` (503) が返る」「retry 可能な 503 になる」と書いた委譲で、**同一サイクル内に 5 回連続の false positive** が出た。該当行はいずれもライブエラーではなく、ワーカーの文書化作業そのものだった。

   - **見分け方 (文体で判別する)**: target 行の前後文脈が「〜が返る」「〜になる」「説明」「表の 1 行」等の**解説・ドキュメント文体**なら false positive の可能性が高い。実際のコマンド出力 (`$ curl ...` の応答、pytest 失敗ログ等) に status code が含まれる場合とは文体が明確に異なる
   - **繰り返し発火は仕様どおり**: 同じ 503 系の行が複数サイクルにわたって画面内に留まり続ける (scroll で流れる速度が遅いだけ) 場合、毎サイクル再検出されるのは (d) の設計どおりの動作。都度 journal に理由を書いて抑制すればよい
   - **起きやすい委譲**: API のエラーハンドリングを実装・文書化するタスク。この種の説明文が大量に書かれるため、**同一ワーカーに対して同じ false positive が繰り返し発生しやすい**
   - **当座の運用**: 発生ごとに `anomaly_observed` へ理由付きで記録し、**通知は送らない** ((e) の step 2 で dispatcher が抑制判断する)。実測時は誤通知として窓口へ一度も送っていない
   - **恒久対応案 (未実装、別 Issue)**: `tools/inspect_anomaly_scan.py` 側に「diff の追加行 (`+` prefix) やコメント記号 (`#`, `--`, docstring 内) にある status code は除外する」ヒューリスティックを足せば自動的に減らせる。現行の `(?<!#)` は `#529` のような **issue ref に直接隣接する `#` のみ**を除外するもので、`# retry 可能な 503 になる` のような**コメント行**は除外できない

   #### (e) 実行シーケンス (journal + de-dup + notify)
   以下の順番で厳密に実行する:

   1. **観測記録** (confidence に関わらず常に): `tools/journal_append.py` 経由で追記（`cursor` ネスト object のため Python wrapper を使う。`ts` は helper が自動付与）:
      ```bash
      # PowerShell 環境は py -3。
      python3 ../tools/journal_append.py anomaly_observed \
          source=inspect worker=worker-{task_id} \
          kind=approval_blocked confidence=high matched='<該当行>' \
          --json '{"cursor": {"row": <r>, "col": <c>, "visible": <bool>}}'
      ```
   2. **通知するかの判定** — 以下を **すべて** 満たす場合のみ通知に進む:
      - APPROVAL_BLOCKED なら confidence == high (low-confidence は journal のみで終了)
      - ERROR は常に通知対象 (cursor 補強なし)
      - **de-dup チェック**: 直近 30 秒以内の journal に **`event == "notify_sent"`** かつ `(worker, kind)` 一致のエントリが存在しない
        - `anomaly_observed` エントリは de-dup キーに **含めない** (低 confidence や observation-only record が将来の通知を抑制しないため)
        - 今サイクルの step (1) で書いた `anomaly_observed` も de-dup 対象にならない
   3. **通知送信** (step 2 を通過した場合): `mcp__org-broker__send_message` で窓口に通知 (フォーマットは (f) 参照)
   4. **notify_sent 記録** (通知送信成功時): `confidence` は kind と source に一致させる (APPROVAL_BLOCKED かつ source=inspect のみ `"high"`、それ以外は `"n/a"`)。helper 経由で append:
      ```bash
      # APPROVAL_BLOCKED + source=inspect
      bash ../tools/journal_append.sh notify_sent source=inspect worker=worker-{task_id} kind=approval_blocked confidence=high
      # ERROR + source=inspect
      bash ../tools/journal_append.sh notify_sent source=inspect worker=worker-{task_id} kind=error confidence=n/a
      # APPROVAL_BLOCKED / ERROR + source=self_report (Step 2 から発行)
      bash ../tools/journal_append.sh notify_sent source=self_report worker=worker-{task_id} kind=approval_blocked confidence=n/a
      ```
   通知失敗時は `notify_sent` を書かない。次サイクルで再検出されれば de-dup が抜けて再通知が試行される (at-least-once)。
   Journal 書き込み自体が失敗した場合はそのサイクルの通知を断念、次サイクルで再試行。

   #### (f) 通知フォーマット
   (e) の step 3 に到達した場合のみ、`mcp__org-broker__send_message` で窓口に通知。既存 `APPROVAL_BLOCKED` / `ERROR_DETECTED` フォーマットに `source=inspect` + `confidence=<high|n/a>` を付与:
   ```
   APPROVAL_BLOCKED: worker-{task_id} の承認プロンプトを検出 (source=inspect, confidence=high): {該当行}
   ERROR_DETECTED: worker-{task_id} にエラーを検出 (source=inspect, confidence=n/a): {該当行}
   ```
   ERROR は cursor 補強を使わないため confidence は便宜上 `n/a`。
