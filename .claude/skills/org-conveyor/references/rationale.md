# org-conveyor の位置づけ（他ループ系との差分）

[`.claude/skills/org-conveyor/SKILL.md`](../SKILL.md) の設計説明。手順と制約は SKILL.md 側にある。

| skill | 駆動 | 停止条件 | 人間ゲート |
|---|---|---|---|
| `/loop` | 時間駆動 | なし（間隔ごとに再実行） | なし |
| `/work-discovery` | イベント / 手動 | 候補提示で hard stop（propose-only） | 候補ごとに人間が番号選択 |
| `/org-delegate` | 単発委譲 | escalation 境界で人間に戻る | 派遣ごと / escalation ごと |
| **`/org-conveyor`** | **完了駆動ループ** | **PR ごとに merge gate で halt / 退出条件** | **起動時のスコープ承認（1 回）+ merge ごと** |

`/org-conveyor` は `/work-discovery` の per-candidate 人間選択を、**起動時に 1 回だけ取る「承認スコープ契約」**
へ畳み込んだものである。スコープ契約の内側では候補を再質問なしで自動投入し、**スコープ外の候補は scope 縁として
投入せず halt** する。これが propose-only / hard-stop の `/work-discovery` との本質的な差分。
