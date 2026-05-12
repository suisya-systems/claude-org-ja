---
name: secretary-handover
description: >
  窓口のコンテキストを圧迫したまま session を続けるのを避けるため、
  直近のやり取り・進行中ワーク・組織状態を handover ファイルに書き出し、
  /clear → /secretary-resume で新しい窓口セッションを開始する準備をする。
  「リフレッシュ」「窓口を引き継ぎたい」「コンテキスト整理」と言われたとき、
  または窓口自身が context が長くなったと判断したときに使う。
---

# secretary-handover: 窓口の引き継ぎ

窓口セッションを長期化させずに、組織員としての自覚と直近やり取りを次セッションへ
受け渡すための引き継ぎファイルを作る。書き出した後にユーザーへ `/clear` →
`/secretary-resume` の流れを案内する。

> **重要な前提**:
> - ディスパッチャー / キュレーター / ワーカーのペインは生かしたまま残す。
>   `/clear` は窓口の Claude コンテキストだけをリセットするので、state.db と
>   handover ファイルから復帰できれば組織は途切れない。
> - state DB (`.state/state.db`) は唯一の SoT。ペイン identity やワーク状態は
>   そちらから引ける。handover はあくまで「会話の温度感」「人間との合意」「進行中の判断」など、
>   構造化データに収まらない部分を残すために使う。

## Step 1: handover 対象を整理する

書き出す前に、窓口（自分）の context から以下を抽出する:

1. **直近の人間との合意・判断**
   - 採用した方針、却下した選択肢、保留中の検討事項
2. **進行中のワーク**
   - 派遣中のワーカー、その task_id、最新の進捗ステータス
3. **Pending Decisions（人間に投げて未回答）**
   - `.state/pending_decisions.json` があれば併読し、register と context の差分を残す
4. **次のアクション（窓口視点）**
   - 次に自分が何をすべきか、誰の返答を待っているか
5. **直近の重要なやり取り抜粋**
   - ユーザーが言った決定的な一言、自分が提案して合意された案など、3〜6 項目程度

## Step 2: state.db から構造化情報を取得する

handover に参考情報として埋め込む。書き出し先は sandbox で write 可能な `$TMPDIR`
（未設定なら `/tmp` フォールバック）に置く:

```bash
python3 -c "
from tools.state_db import connect
from tools.state_db.queries import get_org_state_summary
import json, os
conn = connect('.state/state.db')
out_path = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'secretary-handover-state.json')
with open(out_path, 'w') as f:
    json.dump(get_org_state_summary(conn), f, ensure_ascii=False, indent=2, default=str)
print(out_path)
"
```

シェルリダイレクトで `> /tmp/...` を使うと、sandbox 環境では `/tmp` が read-only で
書き込み失敗する。Python 側で `TMPDIR` を解決してから `open(..., 'w')` する形が安全。

ここから以下を取り出す:
- `session.status` / `session.objective`
- `session.dispatcher_pane_id` / `session.dispatcher_peer_id`
- `session.curator_pane_id` / `session.curator_peer_id`
- `active_runs[]`（進行中タスク）
- `active_worker_dirs[]`（生きているワーカーディレクトリ）
- 直近の `recent_events` 上位 3〜5 件

## Step 3: handover ファイルを書き出す

書き出し先: `.state/secretary-handover.md`

既存ファイルがあれば `.state/secretary-handover.prev.md` にバックアップしてから上書きする:

```bash
[ -f .state/secretary-handover.md ] && cp .state/secretary-handover.md .state/secretary-handover.prev.md
```

フォーマット（YAML frontmatter + markdown）:

```markdown
---
created_at: <UTC ISO8601>
session_status: <ACTIVE | IDLE | SUSPENDED>
session_objective: <一行サマリー or null>
dispatcher_pane: <pane_id> / peer=<peer_id>
curator_pane: <pane_id> / peer=<peer_id>
---

# Secretary Handover

## 直近の人間との合意・判断
- ...

## 進行中のワーク
- worker-<task_id> (<worker_dir>): <最新の状態 / 待ち事項>
- ...

## Pending Decisions（人間に投げて未回答）
- ...
（無ければ「なし」と明記する。空にしない）

## 次のアクション（窓口視点）
- ...

## 直近の重要なやり取り抜粋
- user: 「...」
- 窓口: 「...」
- ...

## 参考: state.db スナップショット
（Step 2 で取得した active_runs / recent_events を簡潔に転記。
 全文は `.state/state.db` に残っているので最小限でよい）
```

**書き方の注意**:
- 「過去ログ」ではなく「次の自分への申し送り」として書く。たとえば
  「ユーザーは B 案で進めたい意向」「ワーカーからの retro gate ack 待ち」のような形。
- 機密情報・トークン・パスワードは絶対に書かない。
- ファイルは人間も読むことを想定する。

## Step 4: イベントを記録する

```bash
py -3 tools/journal_append.py secretary_handover \
    --json '{"reason": "context_compaction", "active_workers": [...]}' 2>/dev/null \
    || echo "(journal_append unavailable; skipping)"
```

## Step 5: ユーザーへ案内する

以下のメッセージで完了報告する:

```
窓口の handover を `.state/secretary-handover.md` に書き出しました。
このまま /clear で context をリセットしてください。
新しい窓口セッションが始まったら /secretary-resume を実行すると、
handover を読み込んで現状把握した状態で再開できます。

ディスパッチャー・キュレーター・ワーカーペインはそのまま稼働を続けるので、
組織は中断されません。
```

**窓口がやってはいけないこと**:
- `/clear` を自分で打とうとしない（Claude Code 側のコマンド、ユーザーが入力する）
- ディスパッチャーやキュレーターに SHUTDOWN を送らない（/org-suspend ではないので、
  ペインは生かしたまま残す）
