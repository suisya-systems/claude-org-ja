# org-attention-start の設計理由

[`.claude/skills/org-attention-start/SKILL.md`](../SKILL.md) の設計説明。手順と制約は SKILL.md 側にある。

## pane_id を sidecar JSON に記録する理由

pane_id 記録は `.state/state.db` の schema 拡張ではなく
sidecar JSON (`.state/attention_pane.json`) を採用する。`.state/dashboard.pid` /
`.state/attention_notified.json` と同じ「補助プロセス追跡」パターンに揃え、
importer / writer / snapshotter / converter / drift_check への波及を回避するため。
attention watcher は OS subprocess であり Claude peer ではないため peer_id は持たず、
ダッシュボードへの状況表示も現状不要（人間判断、2026-05-17）。
