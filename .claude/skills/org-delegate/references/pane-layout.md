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

> ※ 実際には `secretary` が左で `dispatcher/curator` が下半分を占める構成もあり、初期レイアウト詳細は org-start に委ねる。本ドキュメントで重要なのは「`dispatcher` ペインの矩形から balanced split でワーカー zone を作っていく」という点。

## 配置ルール

| 対象 | 操作 | 備考 |
|---|---|---|
| ディスパッチャー | 窓口ペインを水平分割して下半分 | `mcp__renga-peers__spawn_claude_pane(target="focused", direction="horizontal", role="dispatcher", name="dispatcher", cwd=".dispatcher", permission_mode="bypassPermissions", model="sonnet")` (org-start Step 2) |
| キュレーター | ディスパッチャーペインを垂直分割して右半分 | `mcp__renga-peers__spawn_claude_pane(target="dispatcher", direction="vertical", role="curator", name="curator", cwd=".curator", permission_mode="{default_permission_mode}")` (org-start Step 3) |
| 各ワーカー | **balanced split**: `list_panes` が返す現在の rect から target と direction を動的に選び、同一タブ内に積む | 詳細は下記「ワーカーの balanced split 戦略」セクション。`mcp__renga-peers__spawn_claude_pane(target={target}, direction={direction}, role="worker", name="worker-{task_id}", cwd="{workers_dir}/{task_id}", permission_mode="{default_permission_mode}")` (org-delegate Step 3) |

> **`spawn_claude_pane` を使う理由**: renga 0.18.0+ で追加された構造化 launch ツール。`cwd` / `permission_mode` / `model` / `args[]` を構造化フィールドで渡すと、renga が内部で `claude --permission-mode {mode} --dangerously-load-development-channels server:renga-peers ...` を合成する。旧方式（`cd`-プレフィックス付き command 文字列を `spawn_pane` に流し込む）は **禁止**（cwd 変更プレフィックスがあると renga の bare-`claude` auto-upgrade が発動せず、`send_message` の channel push が届かなくなる。窓口→ディスパッチャー / ディスパッチャー→ワーカーの指示が一切通らなくなる）。Secretary のみ `ops.toml` から bare `claude` で起動され auto-upgrade に任せる。

## ワーカーの balanced split 戦略

### なぜ balanced split が必要か

renga は各 split で対象ペインを 50/50 に分ける。`MIN_PANE_WIDTH = 20` / `MIN_PANE_HEIGHT = 5` の下限を割り込むと `[split_refused]` で拒否される (調査: `<workers_dir>/renga-split-inv/findings.md`)。

固定 target や序数 `k` ベースの lookup table では、dispatcher 幅の累積半減や、ワーカーが途中で閉じた後の再派遣で想定レイアウトと実レイアウトが乖離し、早期に `split_refused` を誘発していた。

現設計は `mcp__renga-peers__list_panes` が返す各ペインの **rect 情報 (`x / y / width / height`, cell 単位)** を使い、**現状のレイアウトから動的に target と direction を選ぶ**。ワーカー退役順の揺れや途中クローズに強く、固定的な「N 並列上限」は持たず、ターミナルサイズと MIN_PANE 制約が許す限り分割し続け、限界に達したら自動 escalate する。

### アルゴリズム

新規ワーカーを起動するディスパッチャーは、`spawn_claude_pane` を呼ぶ前に以下を実行する。判定ステップの詳細は `SKILL.md` Step 3-1 を参照（Claude が `list_panes` の結果テキストを解釈してロジックを実行する）。

1. `mcp__renga-peers__list_panes` で全ペインと属性 (id / name / role / focused / x / y / width / height) を取得する
2. **候補集合**: `role ∈ {secretary, curator, worker, dispatcher}` のペイン (4 役すべてが候補対象)
3. **候補の絞り込み**:
   - **dispatcher-curator 隣接維持**: dispatcher は curator と rect 隣接 (後述) しているときのみ候補に入れる。組織運営上 dispatcher と curator の隣接配置は前提。dispatcher を分割すると隣接が崩れ得るので、既に非隣接な dispatcher は候補から外す。curator 自体には adjacency 制約はかけない (curator が分割されても dispatcher と curator の残部は隣接維持される)
   - **secretary 保護**: secretary は分割後の新ペイン幅 `new_w >= 140` **かつ** 新ペイン高さ `new_h >= 30` を満たす場合のみ候補化 (保険条項。280 cols → 1 回だけ vertical split で 140 まで縮め、以降は自動的に候補から脱落する設計)。width だけ通っても height が足りなければ却下する
4. **direction 決定** (各候補の aspect ratio から):
   - `width > height * 2` → `vertical` (左右分割)
   - それ以外 → `horizontal` (上下分割)
   - ターミナルセルは縦長 (縦横比 ≈ 2:1) なので、文字単位で `width = 2 * height` のとき物理的にほぼ正方形。`width > height * 2` は「物理的に横長」判定として妥当
5. **MIN_PANE 制約**: 分割後の新ペインサイズ `(new_w, new_h)` が `new_w >= 20` かつ `new_h >= 5` を満たさない候補は除外
   - vertical 分割: `(new_w, new_h) = (floor(width / 2), height)`
   - horizontal 分割: `(new_w, new_h) = (width, floor(height / 2))`
6. **target 選出**: 残った候補を **(role priority desc, metric desc, id asc)** で並べ、先頭を target にする。
   - role priority: `secretary = 4`, `curator = 3`, `worker = 2`, `dispatcher = 1`
   - metric は分割軸方向の新サイズ (vertical なら `new_w`、horizontal なら `new_h`)
   - id 昇順は最終 tie-break (スナップショット内で再現可能。セッション跨ぎの安定性までは保証しない)
   - secretary を最優先にすることで、初期レイアウト (secretary 280 cols 想定) では 1 回目のワーカー split が必ず secretary 側に入り、以降は SECRETARY_MIN_WIDTH=140 ガードで自動的に curator → worker → dispatcher の順に流れる。dispatcher を最後に置くのは「ワーカー監視で active な dispatcher の viewport を頻繁に半減させないため」で、curator (`/loop 30m /org-curate` で大半は idle) を分割吸収先として優先する設計
7. **候補が空なら escalate**: `SKILL.md` Step 3-1c の `SPLIT_CAPACITY_EXCEEDED` 経路で窓口に escalate (`spawn_claude_pane` は発行せず、該当ワーカー 1 件だけ派遣中止、ディスパッチャー本体は継続)

### rect 隣接判定の定義

rect `A, B` が隣接するとは以下のいずれかを満たすこと:

- **左右隣接**: `A.x + A.width == B.x` または `B.x + B.width == A.x`、かつ y 区間が overlap (`max(A.y, B.y) < min(A.y + A.height, B.y + B.height)`)
- **上下隣接**: `A.y + A.height == B.y` または `B.y + B.height == A.y`、かつ x 区間が overlap (`max(A.x, B.x) < min(A.x + A.width, B.x + B.width)`)

renga の cell 座標は整数なので tolerance なし完全一致で判定する。

### 初期状態と典型的な挙動

典型的な org-start 直後のレイアウト (secretary 280×43 上半分 / dispatcher 140×43 下半分左 / curator 140×43 下半分右) では、role priority に従って:

- **1st worker spawn**: candidate = {secretary (priority 4, vertical split で 140×43, OK), curator (priority 3), worker は不在, dispatcher (priority 1, curator 隣接 OK)}。secretary が最優先で選ばれる → vertical split。secretary は 280→140 に縮む。
- **2nd worker spawn**: secretary は今や 140 cols で `new_w = floor(140/2) = 70 < 140` のため SECRETARY_MIN_WIDTH ガードで脱落。残る最高優先度は **curator** (priority 3) → curator が target に。curator は典型 140×43 で横長なので vertical split。
- **3rd worker spawn**: secretary 脱落、curator は分割済みで metric が落ち、worker-1 (priority 2) と curator (priority 3) で curator が priority 上位。ただし curator が MIN_PANE_WIDTH 不足になっていれば worker-1 が選ばれる。実際の選出は metric / 残幅で決まる。

以降は role priority と分割後サイズに応じて自然に交替することで準 balanced な配置になる。固定的な 4 並列 / 8 並列の図は意味を持たないため割愛する (動的で決まるため)。

### Verification trace (Issue #307 シナリオ)

`secretary 280×43 / dispatcher 140×43 / curator 140×43` の直後レイアウトで本アルゴリズムを手動 trace した結果:

| spawn | candidates (Step 5 通過後) | 選出 (priority, metric) | direction | 分割後 |
|---|---|---|---|---|
| 1st | secretary (p4, m=140) / curator (p3, m=70) / dispatcher (p1, m=70) | **secretary** (p=4 最高) | vertical | secretary 140×43 / worker-1 140×43 |
| 2nd | curator (p3, m=70) / worker-1 (p2, m=70) / dispatcher (p1, m=70) — secretary は new_w=70<140 で脱落 | **curator** (p=3 最高) | vertical | curator 70×43 / worker-2 70×43 |
| 3rd | curator (p3, m=35) / worker-1 (p2, m=70) / worker-2 (p2, m=35) / dispatcher (p1, m=70) | **curator** (p=3 最高、metric が小さくても primary は role priority) | horizontal | curator 70×21 / worker-3 70×22 |

注: 3rd spawn では Issue 本文の "curator or worker-1 (whichever has larger metric)" という記述があるが、本アルゴリズムでは role priority が primary key なので curator が MIN_PANE を通る限り curator が選ばれる。curator が将来 MIN_PANE を割って脱落した時点で、初めて worker-1 が priority 2 の中で metric desc で選ばれる。priority と metric の関係は「priority が strict primary、同 priority 内でのみ metric が効く」。

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
