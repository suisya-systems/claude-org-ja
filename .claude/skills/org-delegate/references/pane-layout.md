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
| 各ワーカー | **balanced split**: 既存のペイン数 `k` に応じた target と direction を `ccmux list` の結果から動的に選び、同一タブ内に積む | 詳細は下記「ワーカーの balanced split 戦略」セクション。`ccmux split --target-name {target} --direction {direction} --role worker --id worker-{task_id} --command "cd {workers_dir}/{task_id} && claude ..."` (org-delegate Step 3) |

## ワーカーの balanced split 戦略

### なぜ balanced split が必要か

ccmux は各 split で対象ペインを 50/50 に分ける。`MIN_PANE_WIDTH = 20` / `MIN_PANE_HEIGHT = 5` の下限を割り込むと `[split_refused]` で拒否される (調査: `C:/Users/iwama/working/workers/ccmux-split-inv/findings.md`)。

旧設計は全ワーカーを `--target-name foreman --direction vertical` で追加しており、`foreman` 幅が毎回半減するため、典型的なターミナル幅 (W≈200 cols) では **4 人目で `split_refused`** になっていた。

新設計 (balanced split) では「新規ワーカーの序数 `k` に応じて既存ワーカーを動的に target に選ぶ」ことで、ワーカー zone を準 balanced binary tree にする。これにより **W ≥ 160 cols で 8 並列**まで収まる。

### アルゴリズム

新規ワーカーを起動するフォアマンは、`ccmux split` を呼ぶ前に以下を計算する:

1. `ccmux list --format json` を実行し、`.panes | map(select(.role == "worker")) | sort_by(.id)` で **生きている worker ペインを作成順 (pane id 昇順) に並べた配列** `active_workers` を得る。要素は `{id, name, role, focused}` で、以降は `name` (例: `worker-foo-bar`) のみ使う。なお `PaneInfo` JSON schema には `.exited` フィールドは存在しない (`ccmux/src/ipc/mod.rs:157-167` 参照)。ccmux は `ccmux close` で撤去されたペインを list から外すので、`role == "worker"` を満たすものはすべて live。
2. 序数 `k = len(active_workers) + 1` を決める。
3. 下表から `k` に対応する `target` ペイン名と `direction` を取得する。

### 序数 `k` → target / direction テーブル

| k | target pane name | direction | 備考 |
|---|---|---|---|
| 1 | `foreman` | `vertical` | 唯一の foreman 分割。以降 foreman 幅は固定 |
| 2 | `active_workers[0]` | `horizontal` | worker zone を 2 行化 |
| 3 | `active_workers[0]` | `vertical` | 2×1 から 2×2 グリッド化 (上段) |
| 4 | `active_workers[1]` | `vertical` | 2×2 グリッド化完了 (下段) |
| 5 | `active_workers[0]` | `horizontal` | 2×2 各セルを 2 段化開始 (top-left セル) |
| 6 | `active_workers[2]` | `horizontal` | (top-right セル) |
| 7 | `active_workers[1]` | `horizontal` | (bottom-left セル) |
| 8 | `active_workers[3]` | `horizontal` | (bottom-right セル) → 2×4 の 8 セル完成 |
| 9+ | escalate to 窓口 | — | `ccmux` 本体の `MIN_PANE_WIDTH` を下げるか Phase 2 機能 (`--target-largest` 相当) が必要 |

### 幅・高さ要件

初期 foreman 領域を `W_f × H_f` とする (典型: `W_f = W/2`, `H_f = H/2`、file-tree / preview が表示中ならさらに縮む)。上表適用後の最小ペインサイズは:

| 並列数 | 最小 worker 幅 | 最小 worker 高 | 必要 `W_f` | 必要 `H_f` |
|---|---|---|---|---|
| 4 | `W_f/4` | `H_f/2` | 80 (W ≥ 160) | 10 |
| 8 | `W_f/4` | `H_f/4` | 80 (W ≥ 160) | 20 |

各 split ステップで satisfying するのは「target の分割軸幅 ÷ 2 ≥ MIN」。上表の最悪ケースは k=3 の `W_f/2 → W_f/4` (必要 `W_f/2 ≥ 40`) と k=5〜8 の `H_f/2 → H_f/4` (必要 `H_f/2 ≥ 10`)。

### ペイン配置図

#### 4 並列時 (k=4 まで適用)

foreman 領域を `2×2` のワーカー zone + 左側の foreman に分ける:

```
foreman 領域 (W_f × H_f)
┌──────────┬─────────────────────┐
│          │                     │
│          │  worker-1 │ worker-3│
│          │  (W_f/4 × H_f/2)    │
│ foreman  │                     │
│ (W_f/2 × ├──────────┬──────────┤
│  H_f)    │          │          │
│          │ worker-2 │ worker-4 │
│          │ (W_f/4 × H_f/2)     │
└──────────┴─────────────────────┘
```

#### 8 並列時 (k=8 まで適用)

2×2 の各セルをさらに上下 2 段に割って `2×4` の 8 セルに:

```
foreman 領域 (W_f × H_f)
┌──────────┬──────────┬──────────┐
│          │ worker-1 │ worker-3 │
│          │ W_f/4 ×  │ W_f/4 ×  │
│          │ H_f/4    │ H_f/4    │
│          ├──────────┼──────────┤
│          │ worker-5 │ worker-6 │
│ foreman  │ W_f/4 ×  │ W_f/4 ×  │
│ (W_f/2 × │ H_f/4    │ H_f/4    │
│  H_f)    ├──────────┼──────────┤
│          │ worker-2 │ worker-4 │
│          │ W_f/4 ×  │ W_f/4 ×  │
│          │ H_f/4    │ H_f/4    │
│          ├──────────┼──────────┤
│          │ worker-7 │ worker-8 │
│          │ W_f/4 ×  │ W_f/4 ×  │
│          │ H_f/4    │ H_f/4    │
└──────────┴──────────┴──────────┘
```

図中の `worker-N` は `active_workers[N-1]` の位置関係を表す (task_id kebab-case は描画上省略)。

### Edge cases / 運用時の注意

- **ワーカーが途中で閉じた後の再派遣**: `active_workers` は「現在生きている」worker のリストなので、閉じた slot を詰めて上表を再適用すると、ccmux のレイアウト tree 実状と表の想定が乖離し得る。**この場合、`ccmux split` が `[split_refused]` / `[pane_not_found]` を返したら `references/ccmux-error-codes.md` の手順でキュレーター → 窓口にエスカレーション**する。balanced split は best-effort の配置ヒントであり、正確な木構造復元ではない。
- **9 並列以上**: 上表は k=8 までしか定義しない。k=9 以降は ccmux 本体の機能拡張 (`MIN_PANE_WIDTH` 下げ、`--target-largest` フラグ等) 待ち。フォアマンは即座に窓口へエスカレーションする。
- **レース**: `ccmux list` 実行から `ccmux split` 実行までに他ワーカーが増減した場合、target 不整合は `[pane_not_found]` として顕在化する。通常のエラーハンドリング経路で吸収する。
- **target 選出の責務**: 計算はフォアマンが `ccmux list` ベースで行う。窓口は DELEGATE メッセージに task_id だけを渡せばよく、target は指定しない。

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
- `ccmux split --target-largest` 等の自動 target 選出 (現状は balanced split table を `k` ベースで適用)
- `ccmux list` の JSON に `rect` 情報を含める拡張 (現状は rect 不明のため table-driven の近似)

> **暫定対応の位置付け**: 本ドキュメントの balanced split 戦略は、ccmux 本体で上記の `--target-largest` / rect 情報 / `MIN_PANE_WIDTH` 調整が整うまでの **暫定運用** である。upstream 追跡は happy-ryo/ccmux#78（balanced split workaround の解消 tracking issue、未作成なら作成予定）を参照。#78 がマージされ次第、本スキルの lookup table を撤去して `--target-largest --direction auto` 1 行に差し替える想定。
