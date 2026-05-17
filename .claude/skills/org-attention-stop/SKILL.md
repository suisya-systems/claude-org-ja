---
name: org-attention-stop
description: >
  `/org-attention-start` で起動した attention watcher ペインを停止する。
  `.state/attention_pane.json` に記録された pane_id を参照して
  `mcp__renga-peers__close_pane` で破棄し、sidecar を削除する。
  「attention 止めて」「通知監視を停止」「watcher 落として」等で発動。
effort: low
allowed-tools:
  - Read
  - Bash(rm:*)
  - Bash(del:*)
  - Bash(bash tools/journal_append.sh:*)
  - Bash(py -3 tools/journal_append.py:*)
  - mcp__renga-peers__*
---

# org-attention-stop: attention watcher の停止

[`/org-attention-start`](../org-attention-start/SKILL.md) で起動した watcher ペインを閉じ、
sidecar (`.state/attention_pane.json`) をクリアする。

## Step 1: sidecar と live pane の状態確認

1. `mcp__renga-peers__list_panes` を呼び、`name="attention"` または `role="attention"` の
   live pane があれば pane_id を控える（**name と role の両方を見る**: 手動起動の孤児ペインは
   name を付けずに role だけ持っている可能性がある）
2. `.state/attention_pane.json` を `Read` で開けたら `pane_id` を読み取る。存在しなければ skip
3. 分岐:
   - **sidecar あり** → 2-a へ
   - **sidecar 無し + 孤児 pane 検出** → 2-b へ
   - **sidecar 無し + 孤児 pane 無し** → 「attention watcher は既に停止しています」と報告して終了

## Step 2: ペインを閉じる

### 2-a: 記録された pane_id で close

```
mcp__renga-peers__close_pane(target="<sidecar の pane_id>")
```

- 成功時: `"Closed pane id=N."` テキストが返る
- `[pane_not_found]` / `[pane_vanished]`: 既に閉じている。sidecar が stale。Step 3 へ進む
- `[last_pane]`: タブの唯一のペインが attention だった（通常発生しない、dispatcher / secretary が
  残っているはず）。状況をユーザーに報告して abort（手動対応に委ねる）

Step 1 で得た「list_panes 上の attention ペイン」と sidecar の pane_id が一致しない場合は、
**sidecar の id を優先して close した後**、`list_panes` を再取得して残っていれば 2-b に進む
（drift / orphan の追加掃除）。

### 2-b: sidecar 無し / drift で孤児ペインを掃除する場合

Step 1 で `list_panes` から得た **pane_id（name ではなく数値 id）** で close する:

```
mcp__renga-peers__close_pane(target="<list_panes から取得した数値 pane_id>")
```

`target="attention"` のような name 指定は、role だけ持って name を持たない孤児ペインに当たらない
ため使用しない。`[pane_not_found]` / `[pane_vanished]` は skip 扱い。

## Step 3: sidecar の削除

```bash
rm -f .state/attention_pane.json
```

Windows native: `del .state\attention_pane.json` （既に削除済みでも無害化のため `2>nul` 等で抑制）。

journal event を 1 行追記する:

```bash
bash tools/journal_append.sh attention_watch_stopped pane_id=<N>
```

Windows native では `py -3 tools/journal_append.py attention_watch_stopped pane_id=<N>`。

## Step 4: 報告

```
attention watcher を停止しました（pane id={N}）。
再開するときは /org-attention-start を実行してください。
```

sidecar が無く孤児ペインも無かった場合は:

```
attention watcher は既に停止しています。
```
