# watcher instance 束縛と freshness gate（監視終端 cleanup の詳細）

[`.claude/skills/org-pull-request/SKILL.md`](../SKILL.md) の「監視終端で watcher ペインを窓口がイベント駆動 close する」節から移設した詳細。**本ファイルが「cleanup を watcher instance に束縛する」規則の SoT**（[`.claude/skills/pr-watch-pane/SKILL.md`](../../pr-watch-pane/SKILL.md) Step 5 が「束縛の SoT」として指す先）。裸 name allowlist の 3 条件と根拠の SoT は逆に pr-watch-pane Step 5 (b) 側にある。

## なぜ `name` で再導出してはならないか

終端イベント (`PR_MERGED` / CI 失敗確定 等) は本文に **PR 番号と head SHA** を載せるが **pane_id は載せない**。一方 watcher は同一 PR でも再起動のたびに **新しい pane_id** を持つ（CI 失敗 → 再 push で新 `pr-watch-<PR>` を spawn 等）。ここで cleanup 時に `name="pr-watch-<PR>"` で live pane を **再導出**すると、遅延 / 重複配送された**古い**終端イベントが**再起動済みの新 watcher**を解決し、その数値 pane_id を close しても **replacement monitor を誤 close** してしまう（name→id に変えても同じ罠）。

これを避けるため、窓口は **`/pr-watch-pane` 起動時に控えた pane_id（Step 3 の "Spawned pane id=N"）と監視対象 head を watcher instance の identity として保持し、cleanup はその identity に束縛する**。

## freshness gate: 監視 head を取り出すフィールドはイベント種別で異なる

終端イベントが指す **watcher の監視 head**（＝その watcher が CI 追跡していた push SHA）が**現在追跡中の watcher instance の監視 head と一致する**ことを確認する。

- CI 完了系 (`ci_completed` / `CI_COMPLETED`) / `PR_MERGE_WATCH_TIMEOUT` / `PR_MERGED` / `PR_MERGED_NO_RUN`: 本文の `head`（events DB の `head` 一致判定と同じ ground truth。SKILL.md §「CI 完了検知の正路」参照）。
- **`PR_MERGED_HEAD_UNCONFIRMED`: 本文の `head` は新たにマージされた SHA で監視 head とは異なる**ため、代わりに `last CI-confirmed head`（baseline）フィールドを監視 head として突き合わせる（`head` で判定すると必ず不一致になり、誤って superseded 扱いで cleanup を skip し、人間確認 gate 中ずっと herdr でゾンビが残る — この取り違えを避ける）。

監視 head が一致しない古い / 重複イベントだけ superseded とみなし close しない（＝再起動済みの新 watcher を殺さない）。一致すれば SKILL.md の識別子束縛 close へ進む。追跡が既に消えている（該当 instance を掃除済み）なら no-op。

## 監視 head が「照合不能」のときは superseded 扱いにしない（Issue #954）

次のいずれかに当たるイベントは監視 head を持っていない:

- **relay 経由 (B)**: `head=<missing>` + 末尾 `[head-unverifiable]`（[`tools/relay_scan.py`](../../../../tools/relay_scan.py) の `UNVERIFIABLE_RELAY_TAIL`）。
- **直 push 経由 (A)**: **マーカーは付かない**（marker を足すのは relay の compose のみ）。watcher は merged head を解決できないとき placeholder をそのまま本文に載せるため（[`tools/pr_watch.py`](../../../../tools/pr_watch.py) の `head_tag = merged_head or "unknown"`。例 `PR_MERGED_NO_RUN: PR #<n> (head=unknown)`）、**本文の head が `unknown` / `<missing>` / 空**なら marker の有無に依らず照合不能として扱う。

これは「head が違う」ではなく「**照合できない**」なので、不一致と同じ枝（黙って close skip）に流すと watcher が残留する。**照合不能は不一致とは別枝**として次の順で処理する:

1. `events` テーブルから当該 PR の canonical 行を引き直して監視 head を復元し（SKILL.md §「CI 完了検知の正路」の SQL。`pr_merged` なら `tools/run_complete_on_merge.py` が起票した `head` 付きの行が正本）、その head で改めて gate を通す。復元できたらそのまま判定を続ける。
2. **復元できない場合は黙って skip せず人間に報告する**（「PR #<n> の終端イベントが head を持たず watcher の照合ができない。watcher ペイン `pr-watch-<PR>` が残っている可能性がある」）。窓口判断で close するときは identity（spawn 時に控えた pane_id）で束縛する。

なお `[head-unverifiable]` が付いた `PR_MERGED` は、窓口が `journal_append.sh pr_merged` を手打ちして events が 2 行になった痕跡でもある（SKILL.md 2b-ii の手打ち禁止を参照）。

## 掃除タイミングを遅らせない理由

どの終端でも watcher 本体 (`tools/pr-watch.sh`) は**その終端イベントの発信時点で既に exit 済み**なので、ペイン掃除は終端イベント受領で即時行う（run.status の遷移 gate や人間確認 gate とは独立）。掃除を後段まで遅らせると herdr backend でゾンビが待機中ずっと残る。
