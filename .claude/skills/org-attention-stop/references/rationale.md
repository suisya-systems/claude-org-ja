# org-attention-stop の設計理由

[`.claude/skills/org-attention-stop/SKILL.md`](../SKILL.md) の設計説明。手順と制約は SKILL.md 側にある。

## なぜ identity 確認が必要か

renga 0.18+ / broker は pane id を lifecycle 跨ぎで **recycle** する。watcher がクラッシュすると、
その pane_id が別ペイン（新規 spawn された worker 等）へ再割当てされることがある。このとき sidecar
(`.state/attention_pane.json`) に残った pane_id を**無検証で `close_pane` すると、無関係なペインを
kill する**。よって本 skill は **`list_panes` の name/role で identity を確認したペインだけを close 対象**とし、
sidecar の pane_id を close の直接の根拠にしない（Issue #468）。
