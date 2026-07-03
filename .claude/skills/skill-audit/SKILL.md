---
name: skill-audit
description: >
  skill の棚卸し（廃止候補 / 重複統合 / owner 明記チェック）。
  状態ベースで発火する: 候補キュー knowledge/skill-candidates.md の pending が 5 件以上、
  または .claude/skills/ 配下の work-skill 数（org-* を除く）が 20 以上になった場合のみ実行。
  時間ベースの /loop では起動しない（変化の無い日に raw ログを汚す副作用を避けるため）。
effort: medium
allowed-tools:
  - Read
  - Bash(grep:*)
  - Bash(find:*)
  - Bash(wc:*)
  - mcp__org-broker__send_message
---

# skill-audit: skill 棚卸し

skill 数の増加に伴って `org-delegate` の work-skill 検索にノイズが増えるのを防ぐため、
定期的ではなく **状態ベース**で棚卸しを行う。

skill 数増加そのものよりも「検索面のノイズ」が本丸。
本スキルは 3 つの観点（廃止 / 重複統合 / owner 明記）を機械的にチェックし、
変更提案を窓口 Claude にまとめて送る。自動で skill を削除・変更することはしない。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../CLAUDE.md)「輸送層（transport）両系」節。）

## Step 1: 発火条件チェック（状態ベース）

以下をいずれも満たさない場合は **即終了**（ログも残さない）。

```bash
# 候補キュー pending エントリ数（コードフェンス内のテンプレ例文は数えない）
cand_count=$(awk '/^(```|~~~)/ { fence = !fence; next }
  !fence && /^- \*\*status\*\*: pending[ \t]*$/ { n++ }
  END { print n + 0 }' knowledge/skill-candidates.md 2>/dev/null || echo 0)

# work-skill 数（org-* は除外。ノイズ源となる work-skill 検索対象に合わせる）
work_skill_count=$(find .claude/skills -maxdepth 2 -name SKILL.md \
  | grep -v '/org-' | wc -l)
```

- `cand_count >= 5` **または** `work_skill_count >= 20` なら続行
- どちらも満たさなければ終了（このとき報告は不要）

数値の根拠: N=5 / M=20 をデフォルトとする。実運用で重くなれば PR で調整。
`org-*` を除外する理由: ノイズ源は `org-delegate` の work-skill 検索であり、
`org-*` の増減は検索ノイズに直接影響しない。

> **カウント定義の同期（重要）**: 上記 2 つのカウント定義（pending は
> `^- \*\*status\*\*: pending` の行一致 **かつコードフェンス外**（行頭 `` ``` `` /
> `~~~` で開閉するブロック内は除外）、work-skill は `find .claude/skills -maxdepth 2
> -name SKILL.md | grep -v '/org-'`）は、オンデマンド curator の起動判定
> [`tools/check_curate_threshold.py`](../../../tools/check_curate_threshold.py) と
> **完全一致**させること。pending カウントのセマンティクスはさらに
> [`knowledge/skill-candidates.md`](../../../knowledge/skill-candidates.md)
> 冒頭の運用ルール記載と合わせた **3 者同期**（本 Step 1 / check_curate_threshold.py /
> skill-candidates.md 冒頭）。いずれかを変更する場合は 3 箇所同時に更新する
> （`tools/test_check_curate_threshold.py` の parity テストが drift を検出する）。

## Step 2: 廃止候補の洗い出し

各 skill について以下を評価する。**現時点で観測可能な項目のみ**を機械判定に使い、
観測不能な項目は「要確認」として人間判定に委ねる。詳細は `references/audit-checklist.md` を参照。

観測可能（機械判定に使える）:
- description と `SKILL.md` 本文 Step 群の内容に明らかな乖離がある（本文内で完結）
- `knowledge/curated/` / `knowledge/raw/` / `.state/workers/` を `{skill-name}` で grep し、
  直近 90 日の言及が 0 件（本プロジェクト内の観測範囲に限る）

観測不能（「要確認」扱い、廃止判定には使えない）:
- `org-delegate` は work-skill を指示に埋め込むだけで「実際に採用されたか」を永続化しない
  → 言及検索は「検索で引っかかった」程度の情報にしかならない
- 既存 skill の多くが `origin.task_id` を持たず、再利用判定の起点がない
  → origin 付き skill のみ「再利用なし」判定に使い、origin 無しは除外

**廃止決定はしない。提案リストに載せるだけ**で、最終判断は人間に委ねる。
audit-checklist.md の 1.1 / 1.2 / 1.3 も同方針で詳細化済み。

## Step 3: 重複統合候補の洗い出し

skill ペアを総当たりして以下を確認する:

- description の主題語（動詞・目的語）が重複している
- triggers（または description 中の発動条件）が重なる
- 片方がもう一方の特殊化であり、パラメータ差し替えで兼用できる

重複の疑いがあるペアは「統合候補」としてリストアップする。
実際の統合判断は人間が行うので、ここでは候補提示のみ。

## Step 4: owner 未明記の洗い出し

全 skill の SKILL.md frontmatter を読み、以下を確認する:

- `owner:` または `maintainer:` フィールドが無い skill
- あっても空文字列のもの

これらは「owner 未明記」としてリストアップ。
本プロジェクトの既存 skill 全てが現時点で owner 未記載である点は想定内で、
最初の監査実行では一括提案になる見込み。

## Step 5: 報告

`org-broker` の `send_message(to_id="secretary", ...)` で窓口 Claude に送る。

```
[skill-audit] 棚卸し結果
- 廃止候補: {n} 件 ({skill-name} 一覧)
- 統合候補: {m} ペア ({skill-a} × {skill-b} 一覧)
- owner 未明記: {k} 件 ({skill-name} 一覧)

発火条件: cand_count={cand_count} / skill_count={skill_count}
詳細: 判定根拠は本メッセージ末尾の一覧を参照。

人間承認後に削除・統合・owner 追記を実施してください。自動変更はしていません。
```

候補が 0 件（クリーンな状態）だった場合も報告する: 「棚卸し実行、変更提案なし」。
次回は次の閾値超過まで実行されないので、0 件でも実行した事実を残す意味がある。

## トリガー経路

このスキルは自律的に走らない。以下のいずれかで起動する:

1. `org-curate` Step 6 で呼び出される（推奨経路）。オンデマンド化後の流れ:
   ディスパッチャーが worker クローズ時に `tools/check_curate_threshold.py` を実行し、
   `reasons[]` に `skill_candidates_pending` / `work_skill_count` が立つと curator が
   一時起動され、org-curate Step 6 が本スキルを発火する
2. 窓口が `skill-candidates.md` を見て手動で起動する
3. 人間が「棚卸しして」と依頼

`/loop` などの時間ベース起動はしない。

## 自動変更を避ける理由

- 廃止の誤判断は `org-delegate` 側で「使える skill が無い」状態を生む（委譲精度の低下）
- 統合は description・triggers・手順の擦り合わせを要し、機械的にはできない
- owner 追記は人間確認が最も軽い運用（ここだけ自動化しても効果小）

したがって本スキルは**提案までに留め**、変更は人間承認を経て窓口が手動で行う。
