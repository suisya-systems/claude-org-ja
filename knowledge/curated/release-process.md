# リリース運用

リリース昇格コミット・デプロイ・ミラー同期で踏みやすい慣行・落とし穴。

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

## ミラー PR の CI red は「翻訳クラスの未ランド修正」との結合であることが多い

CI-red の auto-mirror PR は、それ自体が壊れているのではなく、未ランドの翻訳系修正に結合していることが多い。ja PR はファイルクラスで分割される: **runtime** クラス（test + 実装）はミラー PR に乗るが、**translation** クラス（brief テンプレ等の文言）は別の translation-pending issue に分かれる。ミラー PR は「新しい test」を運ぶが「その test が assert する対象の修正」を運ばないため、テンプレ修正が en `main` に land するまで red のままになる。

診断手順: 失敗 CI ログの assertion を読む → `gh api .../contents/<template>` で en 側が修正前文字列のままか確認 → `gh pr diff <ja#> | grep -A30 <file>` で修正が translation 側に居ることを確認。red ミラー PR を「rerun して祈る」で扱わず、クロスクラス依存を探す。

解決順序（load-bearing）: テンプレ修正を先に `main` へ land → 各 red ミラー PR を `gh pr update-branch` して merge commit を新 base で再生成 → CI green。修正前に red ミラー PR を merge すると `main` 自体が red になる（test あり・修正なし）。

## `tools/templates/*.md` は ja-canonical だが classifier は translation クラスに回す

en 側 canonical-ownership では `tools/**` は runtime/ja-canonical/auto-mirror 対象に見えるが、実 classifier では `tools/templates/worker_brief_normal.md` のような brief テンプレ散文は **translation** クラスに分類される（同期スコープの正本は docs/sync-policy.md）。つまり `tools/` 配下のファイルが translation-pending issue に現れることがある。ja-canonical かつ日本語が両側なので ja diff はほぼ verbatim で適用できる（構造的な言い換えであって言語翻訳ではない）。対照的に、同じ禁止文言でも英語で書かれた `.claude/skills/.../references/worker-claude-template.md` は本物の翻訳が要る。

## stack された ja ミラー PR は ja の merge 順でコンフリクトする — 後続側へ寄せて解決

ja#490 が ja#485 の上に積まれている場合、en #277 (ja#485) を先に merge した後 en #282 (ja#490) が CONFLICTING になる。ja-canonical な解決は **後続 (ja#490/HEAD) 側を採る**。妥当性は 3 点で検証する: (a) ja `main` 自身の test が同じ形か、(b) 実装が後続の形に再アンカーされているか、(c) 解決後の test ファイルがローカルで pass するか。

## 依存 PR の内容を参照する翻訳 PR は、依存 land 後の `main` を base にする

翻訳 PR が sibling PR の内容に依存する（例: ja#497 が ja#495 の後）場合、sibling が merge される前の `main` から分岐すると、codex が「cross-file SoT contradiction」を Major で誤検出する。これらは **stale-base の false positive**。`git show origin/main:<file>` を最新 main（sibling merge 後）で確認すれば矛盾が既に解消済みと分かる。対処は最新 main で branch を作り直して codex を再実行。codex の「Major（cross-file 不整合）」は PR branch の base ではなく**実際の現 main**と突き合わせて確認する。

## ミラー repo 上の worker 権限境界と origin write の所在

en ミラーでの auto-mode classifier + hook 下のワーカー権限:

- **ブロック**: ミラー repo の clone（classifier が CLAUDE.md の ja-repo 禁止と混同）、`gh pr update-branch` / force-push（共有ブランチへの write は「peer authorization ≠ user intent」）、`git reset --hard` / `git branch -D`（block-dangerous-git hook）。
- **許可**: ローカル commit、read-only `gh`、`codex exec`（origin write は不可。merge を含む境界は下記参照）。

**origin write（push / PR 作成 / update-branch / force-push / `gh pr merge` / tag push）は全て secretary の領域**（push / PR / merge = 窓口専属。[[delegation]] / org-pull-request と一致）。ワーカーは共有 clone で commit を準備し、secretary が push する。CI-green ミラー PR の merge は人間の standing approval 下で per-merge の再確認なしに進められるが、**実行主体は worker ではなく secretary**。SHA 報告のハンドオフ規律は [[delegation]] を参照。

## ミラー作業の小技

- `gh api` raw content: `gh api -H "Accept: application/vnd.github.raw" "repos/OWNER/REPO/contents/PATH"`。Git Bash 下では先頭スラッシュを落とす（MSYS が `/repos/...` をファイルパスに書き換える）。
- CI monitor の false-terminal: push 直後 `gh pr checks` が空リストを返す（checks 未登録）ことがあり、`grep -qi pending` が「完了」と誤読する。`pass|fail` の positive 行 AND `mergeable != UNKNOWN` で gate する。
- Windows-local test noise: 実 git worktree を作る test はローカル Windows で fail し Linux CI で pass する。clean `main` で同 suite を回してベースラインを確立してから自分の変更を疑う（詳細は [[windows-worker]] / [[testing]]）。

出典: `2026-05-30-auto-mirror-p2-backlog-drain.md`

## `release.yml`（OIDC）設定済みリポジトリは tag push だけで PyPI + GitHub Release が自動生成される

claude-org-runtime v0.1.12 release で実測。`release.yml`（OIDC PyPI publish）が設定済みのリポジトリでは、バージョンタグの push だけで **PyPI publish と GitHub Release の自動作成**が走る。リリース worker 側で `twine upload` 等の手動 publish 手順は不要。

リリースタスクの最小構成は「`__about__` のバージョン文字列 bump（例 0.1.11→0.1.12）+ CHANGELOG の `[X.Y.Z] Fixed` エントリ追加」。worker が担うのはこの bump / CHANGELOG 編集 / commit 準備まで（Pattern A clone の worker_dir で完結）で、**PR 作成 → merge → tag push は origin write なので secretary 領域**（上記「ミラー repo 上の worker 権限境界」と一致）。tag push 後は PyPI publish / GitHub Release 自動作成まで一気通貫。CHANGELOG 昇格時は上段「Keep a Changelog: 空の `## [Unreleased]` を残す」の規律を守る。

CI が一度落ちて再実行が要るケースでは、worker 側ではなく secretary 側（`gh pr checks --watch` / `tools/pr-watch.*`）で監視・再依頼するフローが確立している（push / re-run は origin write なので secretary 領域、[[delegation]] 参照）。

出典: `2026-06-09-delegation-runtime-release-0-1-12.md`
