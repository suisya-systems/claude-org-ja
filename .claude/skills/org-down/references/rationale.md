# org-down の設計理由

[`.claude/skills/org-down/SKILL.md`](../SKILL.md) の設計説明。手順と制約は SKILL.md 側にある。

## 1. runtime 側 pre-flight との分担

runtime 側の pre-flight 検出（`org down` 前に生存ワーカー / 未 drain キューを警告する機能）は
[runtime#142](https://github.com/suisya-systems/claude-org-runtime/issues/142) の担当で、本スキルは
触れない。本スキルは runtime の `org down` を **呼ぶだけ**。

## 2. 判定不能は「継続」ではなく STOP に倒す

ディスパッチャーの worker 監視
（[`.dispatcher/references/worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md)）は
観測不能なサイクルでも監視対象を保持して次サイクルで再評価する（人間が居ないので無停止継続が安全側）が、
本スキルの後段は `claude-org-runtime org down` = broker daemon の kill であり向きが逆になる。生死を
判定できないピアを抱えたまま daemon を落とすと、そのピアが走り続けていた場合に止める手段も、生死を
後から確かめる経路（SKILL.md 冒頭の責務境界で挙げた `check_messages` での drain 等）も同時に失われる。
判定不能のまま不可逆操作へ進まない側が安全側である。
