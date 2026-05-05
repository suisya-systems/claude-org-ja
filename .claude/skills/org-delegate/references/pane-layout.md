# Pane Layout Specification (renga-peers MCP)

renga のペイン / タブ配置ルール。`org-start` と `org-delegate` が参照する。
ペイン制御は `mcp__renga-peers__*` MCP ツール経由で行う（renga 0.18.0+ 前提。`spawn_claude_pane` / `set_pane_identity` を含む 14 ツールすべて MCP で完結）。

## 初期レイアウト (`renga --layout ops` の結果 + ディスパッチャー・キュレーター起動後)

窓口 (`secretary`) / ディスパッチャー / キュレーターが同一タブに立ち上がり、ワーカーも同一タブ内に split で積んでいく方針。

```
Tab 1: ops (ワーカー 0 人)
┌────────────────────┬────────────────────┐
│                    │                    │
│                    │     Secretary      │
│                    │     (上半分)       │
│                    │                    │
│                    ├──────────┬─────────┤
│                    │ Dispatcher  │ Curator │
│                    │          │         │
└────────────────────┴──────────┴─────────┘
```

> ※ 実際には `secretary` が左で `dispatcher/curator` が下半分を占める構成もあり、初期レイアウト詳細は org-start に委ねる。本ドキュメントで重要なのは「`secretary / curator / worker / dispatcher` の 4 役を候補とする role-priority 付き balanced split で動的にワーカー zone を作っていく」という点（詳細は下記アルゴリズム節）。

## 配置ルール

| 対象 | 操作 | 備考 |
|---|---|---|
| ディスパッチャー | 窓口ペインを水平分割して下半分 | `mcp__renga-peers__spawn_claude_pane(target="focused", direction="horizontal", role="dispatcher", name="dispatcher", cwd=".dispatcher", permission_mode="bypassPermissions", model="sonnet")` (org-start Step 2) |
| キュレーター | ディスパッチャーペインを垂直分割して右半分 | `mcp__renga-peers__spawn_claude_pane(target="dispatcher", direction="vertical", role="curator", name="curator", cwd=".curator", permission_mode="auto")` (org-start Step 3) |
| 各ワーカー | **balanced split**: `list_panes` が返す現在の rect から target と direction を動的に選び、同一タブ内に積む | 詳細は下記「ワーカーの balanced split 戦略」セクション。`mcp__renga-peers__spawn_claude_pane(target={target}, direction={direction}, role="worker", name="worker-{task_id}", cwd="{workers_dir}/{task_id}", permission_mode="auto")` (org-delegate Step 3) |

> **`spawn_claude_pane` を使う理由**: renga 0.18.0+ で追加された構造化 launch ツール。`cwd` / `permission_mode` / `model` / `args[]` を構造化フィールドで渡すと、renga が内部で `claude --permission-mode {mode} --dangerously-load-development-channels server:renga-peers ...` を合成する。旧方式（`cd`-プレフィックス付き command 文字列を `spawn_pane` に流し込む）は **禁止**（cwd 変更プレフィックスがあると renga の bare-`claude` auto-upgrade が発動せず、`send_message` の channel push が届かなくなる。窓口→ディスパッチャー / ディスパッチャー→ワーカーの指示が一切通らなくなる）。Secretary のみ `ops.toml` から bare `claude` で起動され auto-upgrade に任せる。

## ワーカーの balanced split 戦略

### なぜ balanced split が必要か

renga は各 split で対象ペインを 50/50 に分ける。`MIN_PANE_WIDTH = 20` / `MIN_PANE_HEIGHT = 5` の下限を割り込むと `[split_refused]` で拒否される (調査: `<workers_dir>/renga-split-inv/findings.md`)。

固定 target や序数 `k` ベースの lookup table では、dispatcher 幅の累積半減や、ワーカーが途中で閉じた後の再派遣で想定レイアウトと実レイアウトが乖離し、早期に `split_refused` を誘発していた。

現設計は `mcp__renga-peers__list_panes` が返す各ペインの **rect 情報 (`x / y / width / height`, cell 単位)** を使い、**現状のレイアウトから動的に target と direction を選ぶ**。ワーカー退役順の揺れや途中クローズに強く、固定的な「N 並列上限」は持たず、ターミナルサイズと MIN_PANE 制約が許す限り分割し続け、限界に達したら自動 escalate する。

### アルゴリズム

balanced split の判定ロジック (target / direction 選択、MIN_PANE 制約、secretary 保険、role priority ソート、rect 隣接判定、`split_capacity_exceeded` 検出) は **`claude-org-runtime` の helper が SoT**。dispatcher は `mcp__renga-peers__list_panes` のスナップショットと task JSON を入力にして以下のいずれかを呼び、返却された action plan (`spawn` / `after_spawn` / `escalate` / `state_writes` / `status`) に従って `spawn_claude_pane` / escalate を実行する:

- CLI (運用上の標準呼び出し): `claude-org-runtime dispatcher delegate-plan --task-json ... --panes-json ... --state-dir ... [--template-repo ...] [--locale-json ...]`。dispatcher 側の手順は `.dispatcher/CLAUDE.md` の delegate-plan helper 節を一次参照
- ライブラリ: `claude_org_runtime.dispatcher.runner` モジュールの `build_plan(...)` (action plan 全体) と `choose_split(panes)` (target / direction だけ欲しい場合の low-level helper)

定数値 (MIN_PANE_WIDTH / MIN_PANE_HEIGHT / SECRETARY_MIN_WIDTH / SECRETARY_MIN_HEIGHT / role priority マップ) と判定順序、rect 隣接の厳密な定義は **`claude_org_runtime.dispatcher.runner` モジュール本体** (`_ROLE_PRIORITY` / `MIN_PANE_*` / `SECRETARY_MIN_*` / `choose_split()` / `rect_adjacent()`) を一次参照とする。本ドキュメントから定数値の prose を削除したのは、runtime と doc の drift が `[split_refused]` 等の不可解な失敗を生む原因になるため (Issue #307 cleanup)。

候補が空のときは helper が `status="split_capacity_exceeded"` と `escalate.send_message(to_id="secretary", ...)` を返す。dispatcher は `spawn_claude_pane` を発行せず、該当ワーカー 1 件だけ派遣を中止、本体監視ループは継続する (`SKILL.md` Step 3-1c 参照)。

### Verification trace (Issue #307 シナリオ、参考)

`secretary 280×43 / dispatcher 140×43 / curator 140×43` の直後レイアウト (ターミナル ≈ 280×86、org-start で secretary horizontal split → dispatcher vertical split 直後を想定) を入力にした場合の `choose_split` の挙動を手動 trace した参考表。**正準値は runtime SoT**。doc 上の値と runtime の挙動が食い違ったら runtime を信じる。

| spawn | 選出 role | direction | 直感的な根拠 |
|---|---|---|---|
| 1st | secretary | vertical | secretary が分割可能サイズを満たすうちは role priority で最優先 |
| 2nd | curator | vertical | secretary が SECRETARY_MIN_WIDTH ガードで脱落、次点 priority の curator が選ばれる |
| 3rd | curator | horizontal | role priority が strict primary なので curator が MIN_PANE を割るまで curator 連続 |

curator が MIN_PANE を割って脱落した後は priority 2 の worker 群に流れる。dispatcher を最後に置く設計意図 (active 監視ペインの viewport を頻繁に半減させない、curator の方が `/loop 30m /org-curate` で大半 idle) は runner.py の `_ROLE_PRIORITY` コメントを参照。

### Edge cases / 運用時の注意

- **ワーカーが途中で閉じた後の再派遣**: 旧 k-table 方式で問題になった「閉じた slot を詰めるとテーブル前提と乖離」は rect ベースでは発生しない。常に実レイアウトから target を選ぶため、renga のレイアウト tree と判断が一致する
- **`spawn_claude_pane` エラー**: `[split_refused]` / `[pane_not_found]` が MCP 結果テキストで返る。`references/renga-error-codes.md` の手順でキュレーター → 窓口にエスカレーション (方針は旧設計と同じ)
- **レース**: `list_panes` 実行から `spawn_claude_pane` 実行までに他ワーカーが増減した場合、target 不整合は `[pane_not_found]` として顕在化する。既存のエラーハンドリング経路で吸収する
- **target 選出の責務**: 計算はディスパッチャーが `list_panes` の rect ベースで行う。窓口は DELEGATE メッセージに task_id だけを渡せばよく、target は指定しない

## 運用メモ

- **全ペインを同一タブ内に配置する**: renga の `list_panes` / `focus_pane` / `send_message` / `inspect`（CLI） は現在フォーカス中のタブのペインしか扱えないため、ディスパッチャー・キュレーター・全ワーカーを同一タブ内に split で積む。`new_tab` でワーカーを別タブに置くとディスパッチャー側から addressable でなくなる (2026-04-20 判明。renga 本体での解決は suisya-systems/renga#71)
- **命名規約**:
  - 窓口 → `secretary`
  - ディスパッチャー → `dispatcher`
  - キュレーター → `curator`
  - ワーカー → `worker-{task_id}` (task_id は kebab-case の一意識別子)
  - **renga-peers の target 解決ルール**: 全桁数字の name は id として解釈されるため、name には英字を必ず含める (`worker-1` は OK、`1` は id 扱いになるので NG)
- **役割ラベル (`role`)**: `secretary` / `dispatcher` / `curator` / `worker` の 4 種
  - `list_panes` の出力で `role` フィールドが取得でき、組織状態の集計や balanced split の target 選出に使える
- **ワーカー完了時**:
  1. 窓口がディスパッチャーに `CLOSE_PANE` を依頼
  2. ディスパッチャーは `mcp__renga-peers__close_pane(target="worker-{task_id}")` でペインを明示破棄する
     (renga が pane を撤去 → `Event::PaneExited` を 1 回 emit → `list_panes` からも消える。
     `[pane_not_found]` / `[pane_vanished]` は「既に閉じた扱い」として skip する)
- **org-suspend 時の停止順**: ワーカー → ディスパッチャー → キュレーター (いずれも `mcp__renga-peers__close_pane` で破棄。最後の 1 ペインを閉じるときだけ `[last_pane]` が返るので、そのペインは自分自身で `exit` させる)

## split direction 慣習

renga の分割方向は以下の定義（`spawn_pane` / `spawn_claude_pane` 共通）:
- `direction="vertical"` = 左右分割 (既存ペイン=左、新ペイン=右)
- `direction="horizontal"` = 上下分割 (既存ペイン=上、新ペイン=下)

## 将来機能 / upstream 追跡

- `spawn_pane` / `spawn_claude_pane` の `--ratio 0.2` 等の比率指定 (現状は 50/50 固定)
- `--target-largest` / `--direction auto` 等の renga 側自動 target 選出 (現状はディスパッチャー側で `list_panes` rect から算出。upstream に移譲できれば balanced split ロジックを MCP 側に畳める)
