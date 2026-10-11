# org-suspend: 理由と経緯

[`.claude/skills/org-suspend/SKILL.md`](../SKILL.md) の手順が「なぜそうなっているか」の説明。手順そのものは SKILL.md 側にある。

## 窓口は自分自身を close_pane しない

根拠は次の 2 つで、どちらも「最後の 1 ペインだから backend が閉じさせてくれない」ことには
依存しない**構造的な理由**である:

- **窓口は人間との唯一の接点である**: 自分を閉じると、この後の「人間に報告」ステップで suspend 完了を
  渡す相手の画面が消える。報告前に自分を殺すと、人間から見て「中断できたのか分からないまま画面が
  消えた」になる
- **自己終了は人間の責務境界にある**: 窓口プロセスの終了は人間の `exit` / `/exit` / 端末クローズに
  委ねる。org-suspend が担うのは「状態保存 + ja 管理下の補助プロセスとペインの停止」までで、窓口
  ペインを閉じる責任は負わない（SKILL.md 冒頭の「責務境界（/org-suspend と /org-down）」で引いた線と同じ）

## [last_pane] を自己 close の安全網として当てにしない理由

`[last_pane]` の意味は
[`.claude/skills/org-delegate/references/renga-error-codes.md`](../../org-delegate/references/renga-error-codes.md)
のとおり「**唯一のタブの**唯一のペイン」であり、他のタブが存在する状況で窓口タブの最後のペインが
保護されるかは 2.0 系では未確認である（保護されない読みが素直だが、いずれにせよ当てにしない）。
自己 close をしない根拠は上の 2 つだけである。

## Phase 3.5 のダッシュボード停止が blind kill のままである理由

この blind kill は「/org-start で再開する前提の中断」なので簡素なまま残す。daemon ごと
落とす [`.claude/skills/org-down/SKILL.md`](../../org-down/SKILL.md) では、pid recycle による誤 kill を避ける stale-pid-safe な
停止（`/proc` / `Get-CimInstance` の CommandLine 照合）に差し替える。
