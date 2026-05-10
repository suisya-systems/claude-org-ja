# リリース運用

リリース昇格コミット時に踏みやすい慣行・落とし穴。

## Keep a Changelog: リリース時に空の `## [Unreleased]` を残す

Keep a Changelog 形式の `CHANGELOG.md` をリリース昇格するとき、`## [Unreleased]` 見出しを `## [X.Y.Z] — YYYY-MM-DD` に「置換」してはならない。**空の `## [Unreleased]` プレースホルダを新リリース見出しの上にそのまま残す**。

正:

```
## [Unreleased]

## [1.1.3] — 2026-05-09
...
```

誤（リリース後に Unreleased 受け皿が消える）:

```
## [1.1.3] — 2026-05-09
...
```

### なぜ非自明か

- Keep a Changelog の例だけ見ると `[Unreleased]` は「次リリースまでの作業中エントリ溜め場」なので、リリース時に `[X.Y.Z]` に書き換えるのが直感的。
- 置換すると次リリース向けの受け皿セクションが消え、次の PR で「[Unreleased] に追記」する worker が見出しを毎回再生成する手間 / 漏れリスクを負う。
- Keep a Changelog 公式 spec はどちらの運用も明示しないので、リポジトリの過去タグを `git show vX.Y.Z:CHANGELOG.md` で確認するのが正解の決め手。

### 適用範囲

- Keep a Changelog 形式の `CHANGELOG.md` を採用しているリポジトリ全般のリリース昇格コミット。
- リリース直前 / 直後の git diff 確認時、空 `[Unreleased]` が残っているかをチェックリスト項目にする。
- renga リポジトリでは v1.1.0 / v1.1.1 / v1.1.2 すべて、リリース後タグ時点で空の `## [Unreleased]` を残してある（`git show v1.1.2:CHANGELOG.md` で確認可能）。v1.1.3 リリース worker はこの慣行を見落として置換してしまい、Codex セルフレビューで Minor 指摘を受けた。

出典: `2026-05-09-keepachangelog-empty-unreleased-placeholder.md`
