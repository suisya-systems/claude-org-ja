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
  - mcp__renga-peers__list_panes
  - mcp__renga-peers__close_pane
---

# org-attention-stop: attention watcher の停止

[`/org-attention-start`](../org-attention-start/SKILL.md) で起動した watcher ペインを閉じ、
sidecar (`.state/attention_pane.json`) をクリアする。

## Step 1: sidecar 読み込み

1. `.state/attention_pane.json` を `Read` で開く
2. **存在しない場合**: ユーザーに「attention watcher は起動していません（sidecar が無い）」と
   報告して終了。念のため `mcp__renga-peers__list_panes` で `name="attention"` / `role="attention"`
   のペインが残っていないか確認し、見つかれば Step 2-b に進む（人為的に手動起動された孤児ペインの掃除）
3. `pane_id` フィールドを読み取る

## Step 2: ペインを閉じる

### 2-a: 記録された pane_id で close

```
mcp__renga-peers__close_pane(target="<pane_id>")
```

- 成功時: `"Closed pane id=N."` テキストが返る
- `[pane_not_found]` / `[pane_vanished]`: 既に閉じている。sidecar が stale。Step 3 へ進む
- `[last_pane]`: タブの唯一のペインが attention だった（通常発生しない、dispatcher / secretary が残っているはず）。
  状況をユーザーに報告して abort（手動対応に委ねる）

### 2-b: sidecar 不在で孤児ペインのみある場合

Step 1 で sidecar 不在だが `list_panes` に `name="attention"` ペインが残っていた場合:

```
mcp__renga-peers__close_pane(target="attention")
```

同じく `[pane_not_found]` / `[pane_vanished]` は skip 扱い。

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
