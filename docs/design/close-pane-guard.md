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
| B | 相対セレクタ指定 | PreToolUse フック [`.hooks/block-relative-close-pane.sh`](../../.hooks/block-relative-close-pane.sh) | 窓口 / ディスパッチャー / キュレーターは実装済み。ワーカーは未（後述） |
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
- **DD-2 stale-binding carve-out**: `mcp__org-broker__close_pane` 宛のときだけ、
  `pr-watch-` で始まる裸の name。登録簿に name binding だけが
  stale に残り `list_panes` に出ないため列挙から数値 id を取り直せない経路で、契約が
  transport 条件付きで認めている唯一の裸 name 経路（3 条件の SoT は
  [`.claude/skills/pr-watch-pane/SKILL.md`](../../.claude/skills/pr-watch-pane/SKILL.md) Step 5 (b)、
  静的側の対応は `check_group_b_selectors.py` の `ALLOWLIST`）。3 条件のうち **backend 条件だけは
  payload から機械判定できる**: 契約は「Group B を駆動している backend が org-broker であることの
  積極的証拠」を要求し、解決済み既定からの推論を MUST NOT とする。ハーネス上のその証拠が
  「これから呼ぶ完全修飾名が `mcp__org-broker__*` であること」なので、フックはそこで判定する。
  残る 2 条件（再 spawn が `[name_taken]` / その name が `list_panes` に出ない）は payload に
  現れないので、遵守は手順とレビューが受け持つ。

それ以外（`target` 省略 / `null` / `"focused"` / `"secretary"` / `"dispatcher"` /
`"worker-{task_id}"` 等）は exit 2 で deny する。

**正規の停止経路は壊れない**: `/org-suspend` / `/org-down` / ペインクローズ /
attention watcher 停止 / pr-watch 後片付けは、いずれも `list_panes` で照合した
**数値 pane_id** で撃つ手順になっている（[`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md)、
[`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md)、
[`.claude/skills/org-attention-stop/SKILL.md`](../../.claude/skills/org-attention-stop/SKILL.md)）。
窓口ペインは停止経路でも close されず、最後に自分自身で `exit` する。

## フックはどのロールに届くか

Claude Code が読むのは **その session の project dir 配下**の `.claude/settings*.json` であって、
任意のツリーのものではない。ロールごとに project dir が違う
（[`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) の
`roles[*].settings_paths`）ので、フックは配布経路ごとに登録する必要がある。

| ロール | project dir | 配布経路 | 状態 |
|---|---|---|---|
| 窓口 | リポジトリルート | [`.claude/settings.json`](../../.claude/settings.json)（repo_shared）を直接継承 | 済 |
| ディスパッチャー | `.dispatcher/` | `.dispatcher/.claude/settings.local.json`（`/org-setup` が [`.claude/skills/org-setup/references/permissions.md`](../../.claude/skills/org-setup/references/permissions.md) から配布） | 済 |
| キュレーター | `.curator/` | 同上 | 済 |
| ワーカー | worker dir | `worker_roles[*].hooks`（`claude-org-runtime settings generate`） | **未（Layer A と同じ runtime ペア変更に乗る）** |
| ja 自身を編集するワーカー | ja の worktree | リポジトリルートの `.claude/settings.json` を継承 | 済 |

ワーカーが未カバーなのは、今回の事故を起こした当のロールが未カバーであることを意味する。
`worker_roles[*].hooks` は byte-lock された schema 側にあり、ja 単独で足すと drift CI が
hard fail するため（下記 Layer A と同じ制約）、両者は同じ runtime ペア変更で揃える。

なお `roles[*].required_hooks` は **最低要件の集合**であって網羅リストではない
（[`tools/check_role_configs.py`](../../tools/check_role_configs.py) は「required が在るか」だけを見る。
allow 側の `disallow_allow_regex` に相当する「hook のホワイトリスト」は schema に無い）。
ディスパッチャー / キュレーターへのフック追加が schema 変更なしに成立するのはこのため。

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
実効化には ja 側の編集で足りる。**ただしこのファイルは runtime バンドル schema との
byte 一致が CI で要求される**ため、ja 側だけを先に変更すると
`tools/check_runtime_schema_drift.py` が hard fail する（pin window `>=0.1.42,<0.2` の内側にいる間）。

同じ schema 変更に乗るのは 2 つ:

1. `worker_roles[*].permissions.deny` に `mcp__renga-peers__close_pane` /
   `mcp__org-broker__close_pane` を足す（Layer A 本体。ペインの破棄はディスパッチャーの
   責務であり、ワーカーが呼ぶ必要が無い）
2. `worker_roles[*].hooks.PreToolUse` に本フックを足す（上表の未カバー行。deny を
   すり抜ける経路が将来出ても Layer B が残るように、二重に掛ける）
3. `required_hook_scripts` に `block-relative-close-pane.sh` を足す（このリストは
   [`tools/org_setup_prune.py`](../../tools/org_setup_prune.py) の `_root_as_claude_org_path` が
   「そのディレクトリが本当に org チェックアウトか」を判定する台帳で、全ロールの settings が
   指すスクリプトはそこに載っているべき）

したがって順序は: **runtime 側に同じ schema 変更を入れてリリース → ja 側で schema 同期**。
