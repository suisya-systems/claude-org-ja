# Pane Layout Specification (ccmux)

ccmux のペイン / タブ配置ルール。org-start と org-delegate が参照する。

## 初期レイアウト (`ccmux --layout ops` の結果)

窓口 (`secretary`) / フォアマン / キュレーターが同一タブに立ち上がり、ワーカーも同一タブ内に split で積んでいく方針。

```
Tab 1: ops
┌──────────────────────────────────┐
│                                  │
│  Secretary                       │
│                                  │
├─────────┬────────┬───────────────┤
│ Foreman │ Worker │ Curator       │
│         │ (...)  │               │
└─────────┴────────┴───────────────┘
```

## 配置ルール

| 対象 | 操作 | 備考 |
|---|---|---|
| フォアマン | 窓口ペインを水平分割して下半分 | `ccmux split --target-focused --direction horizontal --role foreman --id foreman --command "cd .foreman && claude ..."` (org-start Step 2) |
| キュレーター | フォアマンペインを垂直分割して右半分 | `ccmux split --target-name foreman --direction vertical --role curator --id curator --command "cd .curator && claude ..."` (org-start Step 3) |
| 各ワーカー | フォアマンペインを垂直分割 (同一タブ内) | `ccmux split --target-name foreman --direction vertical --role worker --id worker-{task_id} --command "cd {workers_dir}/{task_id} && claude ..."` (org-delegate Step 3) |

## 運用メモ

- **全ペインを同一タブ内に配置する**: ccmux の `list` / `focus` / `send` / `inspect` は現在フォーカス中のタブのペインしか扱えないため、フォアマン・キュレーター・全ワーカーを同一タブ内に split で積む。`ccmux new-tab` でワーカーを別タブに置くとフォアマン側から addressable でなくなる (2026-04-20 判明。ccmux 本体での解決は happy-ryo/ccmux#71)
- **命名規約**:
  - 窓口 → `secretary`
  - フォアマン → `foreman`
  - キュレーター → `curator`
  - ワーカー → `worker-{task_id}` (task_id は kebab-case の一意識別子)
- **役割ラベル (`--role`)**: `secretary` / `foreman` / `curator` / `worker` の 4 種
  - `ccmux list` の JSON 出力で `role` フィールドが取得でき、組織状態の集計に使える
- **ワーカー完了時**:
  1. 窓口がフォアマンに `CLOSE_PANE` を依頼
  2. フォアマンは `ccmux send --name worker-{task_id} --enter "exit"` でシェルを終了させる
     (シェル終了 → PTY 終了 → ccmux が exited pane として検出 → タブも自動クローズ)
- **org-suspend 時の停止順**: ワーカー → フォアマン → キュレーター (いずれも `ccmux send ... --enter "exit"`)

## ccmux split の direction 慣習

ccmux の分割方向は以下の定義:
- `--direction vertical` = 左右分割 (既存ペイン=左、新ペイン=右)
- `--direction horizontal` = 上下分割 (既存ペイン=上、新ペイン=下)

## 将来機能 (Phase 2 待ち)

- `ccmux events` によるペイン lifecycle 購読 (現在は claude-peers 経由で補完)
- `ccmux split --ratio 0.2` 等の比率指定 (現状は 50/50 固定)
- `ccmux close --name X` による明示的なペイン破棄 (現状は shell exit 経由)
