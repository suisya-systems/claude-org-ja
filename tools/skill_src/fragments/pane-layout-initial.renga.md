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
