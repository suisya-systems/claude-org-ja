# notes/

このディレクトリは **claude-org-ja の自由記述置き場** である。
構造化された組織状態 (Status / Active Work Items / Worker Directory Registry 等)
は `.state/state.db` が SoT であり、`.state/org-state.md` はそれの DB 由来 dump
（人手編集禁止）。**自由記述は全部ここ `notes/` に書く**。

## 経緯

Issue #267 / M4 の markdown freeze で、`.state/org-state.md` は generator-only な
dump に降格した。`tools/state_db/snapshotter.py` が DB から markdown を再生成し、
`tools/state_db/drift_check.py` が DB と markdown の一致を検証する。drift_check
は**全体一致**で動くので、自由記述セクションを `org-state.md` に直接書くと CI で
即座に検出される。

それまで `org-state.md` に書いていた以下のような自由記述は notes/ に移す:

- セッションサマリー (`## 2026-05-04 セッション #11 主要成果` など)
- 学び・振り返り (`## 本セッションの学び`)
- Pending Lead アクション (`## Pending Lead`)
- ad-hoc メモ・運用ノート

## ディレクトリ構造

```
notes/
├── README.md                       # このファイル
├── .extraction-manifest.json        # extract_freetext.py が書く（commit 必須・人手編集禁止）
├── pending-leads.md                 # Pending Lead アクション（時系列）
├── sessions/                        # セッション成果
│   └── YYYY-MM-DD-session-NN.md
├── learnings/                       # 学び・振り返り
│   └── YYYY-MM-DD.md
└── misc/                            # その他自由記述
    └── <slug>.md
```

サブディレクトリは必須ではない (好きに追加していい)。`extract_freetext.py` が
頭数の見出しから自動振り分けする経路がこの構造を前提にしている、というだけ。

## 編集ルール

- **自由記述**: markdown で何を書いてもよい。文体・章立て自由
- **commit 必須**: 状態は git で残す。`.gitignore` 対象ではない
- **`.state/org-state.md` は触らない**: 構造化状態を変えたいときは
  `tools.state_db.writer.StateWriter` 経由で DB に書き、post-commit hook に
  markdown を再生成させる
- **`.state/journal.jsonl` は M4 で廃止**: events は DB の `events` テーブル
  だけが SoT。`tools/journal_append.py` 経由で追記する

## 既存の `org-state.md` 自由記述を移行する

旧来の `org-state.md` に自由記述が残っている場合は、以下で `notes/` に
切り出してから snapshotter で `org-state.md` を再生成する:

```bash
# 1. 何が動くか先に確認
python -m tools.state_db.extract_freetext \
    --org-state .state/org-state.md \
    --notes-dir notes/ \
    --plan

# 2. 実行 (notes/ にファイルが生まれ、org-state.md から該当セクションが消える)
python -m tools.state_db.extract_freetext \
    --org-state .state/org-state.md \
    --notes-dir notes/ \
    --apply

# 3. snapshotter で DB ベースの dump に置き換える
python -c "from pathlib import Path; from tools.state_db import connect; \
  from tools.state_db.snapshotter import post_commit_regenerate; \
  conn = connect('.state/state.db'); post_commit_regenerate(conn, Path('.'))"

# 4. drift_check で 0 を確認
python -m tools.state_db.drift_check --db .state/state.db --markdown .state/org-state.md
```

`extract_freetext` は冪等なので、二度走らせても何も起きない。
