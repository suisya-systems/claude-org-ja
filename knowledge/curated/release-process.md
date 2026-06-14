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

## 昇格前チェック: `## [Unreleased]` を直前タグの CHANGELOG と照合する

リリース昇格コミットを作る**前に**、`## [Unreleased]` の下に残っているエントリが、**直前の公開済みタグ**の CHANGELOG ですでに公開済みバージョン見出しの下に載っていないか（= publish 済みエントリの巻き込み）を照合する。

```bash
# 直前の公開済みタグを確認（タグ命名は vX.Y.Z）。
# 現在のブランチから到達可能な最新タグを返すので、複数リリースライン /
# backport ブランチでも「このブランチの直前タグ」を正しく取れる
# （全タグ中の最大バージョンを返す `git tag --sort=-version:refname | head -1`
#   はブランチ文脈とズレ得るので使わない）。
git describe --tags --abbrev=0

# そのタグ時点の CHANGELOG を取り出し、今の Unreleased と突き合わせる
git show vX.Y.Z:CHANGELOG.md
```

照合手順:

- 現在の `## [Unreleased]` に列挙されているエントリを 1 件ずつ見る。
- 同じエントリが `git show vX.Y.Z:CHANGELOG.md` の出力で、`## [Unreleased]` ではなく**公開済みバージョン見出し（`## [X.Y.Z] — …`）の下**に既に載っていれば、それは前リリースで公開済みである。
- **巻き込みが見つかったら**: そのエントリは publish 済みなので、今回の `## [Unreleased]` から**除去する**（新リリース見出しへ再昇格しない）。Unreleased には未公開の差分だけを残す。

### なぜ非自明か

- `## [Unreleased]` は「次リリースまでの溜め場」なので、リリース時にそのまま `## [X.Y.Z]` へ昇格するのが直感的。だが前リリースで公開済みのエントリが何らかの理由で Unreleased に残っていると、それを再昇格して二重掲載してしまう。
- 直前タグの CHANGELOG（`git show vX.Y.Z:CHANGELOG.md`）が「どこまで公開済みか」の唯一の真実源であり、ローカルの作業ツリーだけ見ても巻き込みは判定できない。

### 適用範囲

- Keep a Changelog 形式の `CHANGELOG.md` を昇格する全リポジトリのリリース昇格コミット。昇格 diff を作る直前のチェックリスト項目にする。
- 事故事例: claude-org-runtime v0.1.13 着手時、前リリースで publish 済みの #35 / #36 のエントリが `## [Unreleased]` に居座ったまま昇格作業に入り、二重掲載しかけた。本チェックはその予防。
