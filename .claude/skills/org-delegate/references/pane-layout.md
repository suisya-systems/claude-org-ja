# Pane Layout Specification (ccmux)

ccmux のペイン / タブ配置ルール。org-start と org-delegate が参照する。

## 初期レイアウト (`ccmux --layout ops` の結果 + フォアマン・キュレーター起動後)

窓口 (`secretary`) / フォアマン / キュレーターが同一タブに立ち上がり、ワーカーも同一タブ内に split で積んでいく方針。

```
Tab 1: ops (ワーカー 0 人)
┌────────────────────┬────────────────────┐
│                    │                    │
│                    │     Secretary      │
│                    │     (上半分)       │
│                    │                    │
│                    ├──────────┬─────────┤
│                    │ Foreman  │ Curator │
│                    │          │         │
└────────────────────┴──────────┴─────────┘
```

> ※ 実際には `secretary` が左で `foreman/curator` が下半分を占める構成もあり、初期レイアウト詳細は org-start に委ねる。本ドキュメントで重要なのは「`foreman` ペインの矩形から balanced split でワーカー zone を作っていく」という点。

## 配置ルール

| 対象 | 操作 | 備考 |
|---|---|---|
| フォアマン | 窓口ペインを水平分割して下半分 | `ccmux split --target-focused --direction horizontal --role foreman --id foreman --command "cd .foreman && claude ..."` (org-start Step 2) |
| キュレーター | フォアマンペインを垂直分割して右半分 | `ccmux split --target-name foreman --direction vertical --role curator --id curator --command "cd .curator && claude ..."` (org-start Step 3) |
| 各ワーカー | **balanced split**: `ccmux list` が返す現在の rect から target と direction を動的に選び、同一タブ内に積む | 詳細は下記「ワーカーの balanced split 戦略」セクション。`ccmux split --target-name {target} --direction {direction} --role worker --id worker-{task_id} --command "cd {workers_dir}/{task_id} && claude ..."` (org-delegate Step 3) |

## ワーカーの balanced split 戦略

### なぜ balanced split が必要か

ccmux は各 split で対象ペインを 50/50 に分ける。`MIN_PANE_WIDTH = 20` / `MIN_PANE_HEIGHT = 5` の下限を割り込むと `[split_refused]` で拒否される (調査: `C:/Users/iwama/working/workers/ccmux-split-inv/findings.md`)。

固定 target (`--target-name foreman --direction vertical`) や序数 `k` ベースの lookup table では、foreman 幅の累積半減や、ワーカーが途中で閉じた後の再派遣で想定レイアウトと実レイアウトが乖離し、早期に `split_refused` を誘発していた。

現設計は ccmux v0.5.x から `ccmux list --format json` が返す各ペインの **rect 情報 (`x / y / width / height`, u16, cell 単位)** を使い、**現状のレイアウトから動的に target と direction を選ぶ**。ワーカー退役順の揺れや途中クローズに強く、固定的な「N 並列上限」は持たず、ターミナルサイズと MIN_PANE 制約が許す限り分割し続け、限界に達したら自動 escalate する。

### アルゴリズム

新規ワーカーを起動するフォアマンは、`ccmux split` を呼ぶ前に以下を実行する。bash + jq 実装は `SKILL.md` Step 3-1 を参照。

1. `ccmux list --format json` で全ペインと rect を取得する
2. **候補集合**: `role ∈ {worker, foreman, secretary}` のペイン (curator は常に除外)
3. **候補の絞り込み**:
   - **foreman-curator 隣接維持**: foreman は curator と rect 隣接 (後述) しているときのみ候補に入れる。組織運営上 foreman と curator の隣接配置は前提。foreman を分割すると隣接が崩れ得るので、既に非隣接な foreman は候補から外す
   - **secretary 保護**: secretary は分割後の新ペイン幅 `new_w >= 100` を満たす場合のみ候補化 (保険条項、実運用では通常発動しない)
4. **direction 決定** (各候補の aspect ratio から):
   - `width > height * 2` → `vertical` (左右分割)
   - それ以外 → `horizontal` (上下分割)
   - ターミナルセルは縦長 (縦横比 ≈ 2:1) なので、文字単位で `width = 2 * height` のとき物理的にほぼ正方形。`width > height * 2` は「物理的に横長」判定として妥当
5. **MIN_PANE 制約**: 分割後の新ペインサイズ `(new_w, new_h)` が `new_w >= 20` かつ `new_h >= 5` を満たさない候補は除外
   - vertical 分割: `(new_w, new_h) = (floor(width / 2), height)`
   - horizontal 分割: `(new_w, new_h) = (width, floor(height / 2))`
6. **target 選出**: 残った候補から **「分割軸方向の新サイズ」** (vertical なら `new_w`、horizontal なら `new_h`) が最大のペインを target にする。tie-break はその時点の pane id 昇順 (スナップショット内で再現可能。セッション跨ぎの安定性までは保証しない)
7. **候補が空なら escalate**: `SKILL.md` Step 3-1 末尾の `SPLIT_CAPACITY_EXCEEDED` 経路で窓口に escalate (`ccmux split` は発行せず、該当ワーカー 1 件だけ派遣中止、フォアマン本体は継続)

### rect 隣接判定の定義

rect `A, B` が隣接するとは以下のいずれかを満たすこと:

- **左右隣接**: `A.x + A.width == B.x` または `B.x + B.width == A.x`、かつ y 区間が overlap (`max(A.y, B.y) < min(A.y + A.height, B.y + B.height)`)
- **上下隣接**: `A.y + A.height == B.y` または `B.y + B.height == A.y`、かつ x 区間が overlap (`max(A.x, B.x) < min(A.x + A.width, B.x + B.width)`)

ccmux の cell 座標は整数なので tolerance なし完全一致で判定する。

### 初期状態と典型的な挙動

ワーカー 0 人の時点では、候補は `foreman` のみ (secretary は `new_w >= 100` 条件または隣接条件で除外されるのが通常。curator は常に除外)。foreman は典型的に横長なので vertical 分割され、最初のワーカー zone が foreman の右側に作られる。

以降は既存ペインの中で「分割後サイズが最大」のものが選ばれ、direction が rect に応じて自然に交替することで準 balanced な配置になる。固定的な 4 並列 / 8 並列の図は意味を持たないため割愛する (動的で決まるため)。

### Edge cases / 運用時の注意

- **ワーカーが途中で閉じた後の再派遣**: 旧 k-table 方式で問題になった「閉じた slot を詰めるとテーブル前提と乖離」は rect ベースでは発生しない。常に実レイアウトから target を選ぶため、ccmux のレイアウト tree と判断が一致する
- **`ccmux split` エラー**: `[split_refused]` / `[pane_not_found]` が返った場合は `references/ccmux-error-codes.md` の手順でキュレーター → 窓口にエスカレーション (方針は旧設計と同じ)
- **レース**: `ccmux list` 実行から `ccmux split` 実行までに他ワーカーが増減した場合、target 不整合は `[pane_not_found]` として顕在化する。既存のエラーハンドリング経路で吸収する
- **target 選出の責務**: 計算はフォアマンが `ccmux list` の rect ベースで行う。窓口は DELEGATE メッセージに task_id だけを渡せばよく、target は指定しない

## 運用メモ

- **全ペインを同一タブ内に配置する**: ccmux の `list` / `focus` / `send` / `inspect` は現在フォーカス中のタブのペインしか扱えないため、フォアマン・キュレーター・全ワーカーを同一タブ内に split で積む。`ccmux new-tab` でワーカーを別タブに置くとフォアマン側から addressable でなくなる (2026-04-20 判明。ccmux 本体での解決は happy-ryo/ccmux#71)
- **命名規約**:
  - 窓口 → `secretary`
  - フォアマン → `foreman`
  - キュレーター → `curator`
  - ワーカー → `worker-{task_id}` (task_id は kebab-case の一意識別子)
- **役割ラベル (`--role`)**: `secretary` / `foreman` / `curator` / `worker` の 4 種
  - `ccmux list` の JSON 出力で `role` フィールドが取得でき、組織状態の集計や balanced split の target 選出に使える
- **ワーカー完了時**:
  1. 窓口がフォアマンに `CLOSE_PANE` を依頼
  2. フォアマンは `ccmux close --name worker-{task_id}` でペインを明示破棄する (ccmux v0.5.8+)
     (ccmux が pane を撤去 → `Event::PaneExited` を 1 回 emit → `ccmux list` からも消える。
     `[pane_not_found]` / `[pane_vanished]` は「既に閉じた扱い」として skip する)
- **org-suspend 時の停止順**: ワーカー → フォアマン → キュレーター (いずれも `ccmux close --name ...` で破棄。最後の 1 ペインを閉じるときだけ `[last_pane]` が返るので、そのペインは自分自身で `exit` させる)

## ccmux split の direction 慣習

ccmux の分割方向は以下の定義:
- `--direction vertical` = 左右分割 (既存ペイン=左、新ペイン=右)
- `--direction horizontal` = 上下分割 (既存ペイン=上、新ペイン=下)

## 将来機能 (Phase 2 待ち)

- `ccmux events` によるペイン lifecycle 購読 (現在は claude-peers 経由で補完)
- `ccmux split --ratio 0.2` 等の比率指定 (現状は 50/50 固定)
- `ccmux split --target-largest` / `--direction auto` 等の ccmux 側自動 target 選出 (現状はフォアマン側で `ccmux list` rect から算出。upstream に移譲できれば lookup ロジックを ccmux CLI 1 行に畳める)
