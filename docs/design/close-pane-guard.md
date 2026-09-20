# close_pane 相対セレクタガード（窓口ペイン保護）

> 関連 Issue: [#1018](https://github.com/suisya-systems/claude-org-ja/issues/1018)
> ステータス: フック層（下表 Layer B）実装済み。Layer A は runtime とのペア変更が必要で未着手

## 何が起きたか

2026-09-20T16:10Z、ワーカーが CLOSE_PANE 指示の処理中に `close_pane` を相対セレクタ
`"focused"` で撃ち、**窓口ペインを閉じた**。窓口セッションはその場で消え、人間との接点が
失われた（events テーブルの `anomaly_observed`、`kind=secretary_pane_closed_by_worker`、
`task=rondo-event-line-fold`）。作業の取りこぼしは無かったが、問題は結果ではなく
**ワーカーが窓口を閉じられる経路が空いていたこと**にある。

`"focused"` は「そのタブで人間がいま見ているペイン」であって caller 自身ではない。
契約 [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md)
T-§4.2 "Fail-safe consequence for Group B" は、Group B（`close_pane` / `set_pane_identity`）の
宛先を「自タブと独立に確立済みの列挙から採った**数値 pane id**」に限ると MUST で定めている。

## 3 層の防護

| Layer | 何を止めるか | 実体 | 状態 |
|---|---|---|---|
| A | ワーカーが `close_pane` を**呼ぶこと自体** | role schema の `permissions.deny`（[`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) の `worker_roles[*]`） | 未着手（runtime とのペア変更。下記） |
| B | **全ロール**の相対セレクタ指定 | PreToolUse フック [`.hooks/block-relative-close-pane.sh`](../../.hooks/block-relative-close-pane.sh) | 実装済み |
| C | 手順ドキュメントへの相対セレクタ**再混入** | 静的チェッカー [`tools/check_group_b_selectors.py`](../../tools/check_group_b_selectors.py) | 既存 |

Layer A だけでは足りない: ディスパッチャーは `bypassPermissions` で動くため
`permissions.deny` が評価されず、フック（Layer B）だけが障壁になる。
逆に Layer B だけでも足りない: 数値 pane id 経由の残余（後述）が残る。

## 決定: フックは ja の `.hooks/` に置く

Issue #1018 が「実装時に決める」としていた論点。**ja の `.hooks/` に置く**と決めた。

- 既存 guard 群（`block-adhoc-pr-watch.sh` / `block-foreground-subagent.sh` 等）と
  同じ層・同じ作法・同じ横断テスト
  （[`tests/test-hooks-payload-fail-closed.sh`](../../tests/test-hooks-payload-fail-closed.sh) が
  `.hooks/*.sh` を動的に列挙して fail-closed 不変条件を掛ける）に自動的に載る。
- Layer C の静的チェッカーと同一リポジトリに置くことで、carve-out の定義
  （下記 `pr-watch-` 例外）を**静的面と動的面で 1 か所に見比べられる**。
- runtime 側に置く案は採らなかった。ja の `tools/org_extension_schema.json` は
  runtime バンドル schema との byte 一致が CI で要求される
  （[`tools/check_runtime_schema_drift.py`](../../tools/check_runtime_schema_drift.py)）ため、
  runtime 配置はフックの一行修正ごとに runtime のペアリリースを要求する。
  フック本体は ja 固有の運用規律（Group B 手順）の表現であり、その反復速度を
  落とす取引に見合わない。

## フックの判定

許可するのは 2 形のみ:

- **数値 pane id**: `3` / `"3"` / `"%3"`
- **DD-2 stale-binding carve-out**: `pr-watch-` で始まる裸の name。登録簿に name binding だけが
  stale に残り `list_panes` に出ないため列挙から数値 id を取り直せない経路で、契約が
  transport 条件付きで認めている唯一の裸 name 経路（3 条件の SoT は
  [`.claude/skills/pr-watch-pane/SKILL.md`](../../.claude/skills/pr-watch-pane/SKILL.md) Step 5 (b)、
  静的側の対応は `check_group_b_selectors.py` の `ALLOWLIST`）。3 条件の成立は payload から
  機械判定できないので、フックは name の形だけを見て通し、条件の遵守は手順とレビューが受け持つ。

それ以外（`target` 省略 / `null` / `"focused"` / `"secretary"` / `"dispatcher"` /
`"worker-{task_id}"` 等）は exit 2 で deny する。

**正規の停止経路は壊れない**: `/org-suspend` / `/org-down` / ペインクローズ /
attention watcher 停止 / pr-watch 後片付けは、いずれも `list_panes` で照合した
**数値 pane_id** で撃つ手順になっている（[`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md)、
[`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md)、
[`.claude/skills/org-attention-stop/SKILL.md`](../../.claude/skills/org-attention-stop/SKILL.md)）。
窓口ペインは停止経路でも close されず、最後に自分自身で `exit` する。

## 残余（塞げていないもの）

**窓口ペインを数値 pane_id で撃つ経路はフックでは止まらない。** 窓口の pane_id を記録した
場所が repo にも `.state/` にも無く（[`tools/state_db/schema.sql`](../../tools/state_db/schema.sql)
の `org_sessions` が持つのは `dispatcher_pane_id` / `curator_pane_id` だけ）、broker には
renga の `RENGA_PANE_ID` に相当する caller pane id を out-of-band で供給する surface が無い
（契約 T-§4.2 の caller pane id acquisition rule）。したがってフックは「この数値 id が窓口か」を
判定する材料を持たない。

この残余は Layer A（ワーカーが `close_pane` を呼べないこと）で覆う設計とする。
ディスパッチャー・窓口自身については、数値 id が `list_panes` の `name` / `role` 照合を
通っていることを求める手順（Layer C が守る）が引き続き唯一の担保になる。

## Layer A: runtime とのペア変更

ワーカー settings の生成は ja の `tools/org_extension_schema.json` を `--schema` で
明示して行われる（[`tools/gen_delegate_payload.py`](../../tools/gen_delegate_payload.py)）ので、
deny の実効化には ja 側の編集で足りる。**ただしこのファイルは runtime バンドル schema との
byte 一致が CI で要求される**ため、ja 側だけを先に変更すると
`tools/check_runtime_schema_drift.py` が hard fail する（pin window `>=0.1.42,<0.2` の内側にいる間）。

したがって順序は: **runtime 側に同じ schema 変更を入れてリリース → ja 側で schema 同期**。
