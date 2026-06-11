---
name: secretary-resume
description: >
  /secretary-handover で書き出した handover ファイルを読み込み、
  窓口を新しいセッションで復帰させる。/clear 直後の最初のターンで使う。
  「窓口を復帰」「resume」「引き継ぎから再開」と言われたときに使う。
  /org-start ではない（ディスパッチャー・キュレーターは既に生きている前提）。
effort: low
allowed-tools:
  - Read
  - Bash(py -3 tools/journal_append.py:*)
  - mcp__renga-peers__set_summary
  - mcp__renga-peers__list_panes
  - mcp__renga-peers__set_pane_identity
  - mcp__renga-peers__list_peers
  - mcp__renga-peers__check_messages
---

# secretary-resume: 窓口の復帰

`/secretary-handover` で書き出した `.state/secretary-handover.md` を読み込み、
窓口として最低限の自覚（組織員としての立ち位置・直近の人間とのやり取り・進行中ワーク）
を復元する。

> **前提**:
> - ディスパッチャー / ワーカーのペインは前セッションから生きたまま
>   残っている。新たに spawn しない（/org-start ではない）。
> - キュレーターは常駐しない（オンデマンド化）。`curator_pane_id` / `curator_peer_id` が
>   null であること・ペインリストに curator が見えないことは**正常系**。
> - state DB (`.state/state.db`) はそのまま使う。ペイン identity の再記録も不要。
> - handover ファイルが存在しないか古すぎる場合は、/org-start もしくは
>   /org-resume の使用を案内する。

> **輸送層（transport）両系 — 既定 `renga` / opt-in `broker`**: 本スキルの `mcp__renga-peers__*` 呼び出しは **既定 `renga`**（`ORG_TRANSPORT` 無設定）で書いてあり、そのまま従えばよい（既定挙動は不変）。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では MCP サーバー名が `org-broker` になり、ツールの **完全修飾名が `mcp__renga-peers__*` → `mcp__org-broker__*`** に機械置換される（引数形・セマンティクスは同一）。輸送依存で手順が変わる点だけ broker 併記する:
>
> - **受信モデル（push → pull）**: renga は worker / dispatcher の peer message が in-band で push される。broker は pane-local ナッジが出るだけで、本文は `check_messages`（broker では `mcp__org-broker__check_messages`）で pull する（resume 後の窓口がメッセージを受ける際は「ナッジを見たら `check_messages`」に変わるだけ）。
> - **spawn 儀式（dev-channel 承認 → folder-trust 承認）**: resume は spawn しないので承認手順は使わないが、broker では spawn 時（org-start / org-delegate 側）の承認が dev-channel ではなく Claude Code の **folder-trust プロンプト**になる。
> - **エラー分岐（broker 追加コード）**: renga コードに加え broker は `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` を返しうる（未知コードは default-branch で扱う）。一覧は [`.claude/skills/org-delegate/references/renga-error-codes.md`](../org-delegate/references/renga-error-codes.md) の broker 節を参照。
>
> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（提案・批准待ち）、設計 SoT は transport-lab `docs/design/ja-migration-plan.md` §5.2(ii)。broker 実走（dogfood）は Epic #6 Issue G スコープで本スキルの既定経路ではない。

## Step 0: 自分の identity を確認する

1. `mcp__renga-peers__set_summary` で「Secretary: 窓口（resumed）」をセット
2. `mcp__renga-peers__list_panes` でフォーカスペインの name/role を確認:
   - 期待値: `name == "secretary"` かつ `role == "secretary"`
   - 不一致なら `mcp__renga-peers__set_pane_identity(target="focused", name="secretary", role="secretary")` で修復

## Step 1: handover ファイルを読み込む

1. `.state/secretary-handover.md` が存在するか確認:
   ```bash
   ls -la .state/secretary-handover.md 2>&1
   ```
   - 存在しない → ユーザーに案内して停止:
     「handover ファイルがありません。/org-start で組織を起動するか、
     /org-resume で suspend 状態から再開してください。」
2. フロントマター `created_at` を見て鮮度を判定:
   - 24 時間以内 → そのまま採用
   - 24 時間超〜7 日以内 → ユーザーに警告（「handover が古いです、続行しますか？」）
   - 7 日超 → 採用せず、`/org-start` への切り替えを推奨する
3. ファイル本文を Read で取り込む。**書かれている内容は次セッションの自分にとっての
   「事実」として扱う**（後の Step 3 で state.db と照合する）。

## Step 2: state.db で現状を再取得する

```bash
python -c "
from tools.state_db import connect
from tools.state_db.queries import get_org_state_summary
import json
conn = connect('.state/state.db')
print(json.dumps(get_org_state_summary(conn), ensure_ascii=False, indent=2, default=str))
"
```

確認項目:
- `session.status` が handover フロントマターと一致するか
- `dispatcher_pane_id` が handover に書いた値と一致するか
- `curator_pane_id` / `curator_peer_id` が null であるか（**null が正常**。値が残っている
  場合は旧仕様からの stale 値の可能性があるので人間に報告する）
- `active_runs[]` が handover の「進行中のワーク」セクションと整合するか

## Step 3: ペイン生存確認

```
mcp__renga-peers__list_peers
```

- ディスパッチャーの name が見えること
- curator は**通常見えないのが正常**（オンデマンド化）。見えている場合は
  オンデマンド curate 実行中なので、そのまま放置してよい（dispatcher が閉じる）
- handover に記載のワーカーが現存するか（消えていれば後述）

**差分があれば人間に報告する**（例:「handover ではワーカー X が進行中とありますが、
現在のペインリストには見当たりません」）。勝手に再 spawn しない。

## Step 4: ブリーフィングを人間に返す

handover の情報と state.db の現状を統合した上で、以下の構造で簡潔に報告:

```
窓口を復帰しました。

【セッション】
- 目的: <session.objective>
- 状態: <session.status>

【ペイン構成】
- dispatcher (pane=N, peer=M)
- curator: 常駐なし（オンデマンド起動）
- workers: <task_id list>

【直近の合意・判断】
- ...

【Pending Decisions】
- ...（無ければ「なし」）

【次のアクション】
- ...

ご指示をお願いします。
```

## Step 5: handover ファイルを保持する

- 削除しない（次回トラブル時の参照用に残す）
- `.state/secretary-handover.prev.md` は前回のもの。読み込み済みであっても消さない

## イベント記録

```bash
py -3 tools/journal_append.py secretary_resumed \
    --json '{"handover_age_hours": <数値>}' 2>/dev/null \
    || echo "(journal_append unavailable; skipping)"
```

## やってはいけないこと

- 新規にディスパッチャー / キュレーターを spawn する（既に生きている）
- ワーカーに勝手に SUSPEND / SHUTDOWN を送る
- handover の内容と state.db の現状が食い違うときに、勝手にどちらかへ寄せる
  （必ず人間に報告して判断を仰ぐ）
