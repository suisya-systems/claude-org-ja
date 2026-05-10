# codex CLI 利用の運用知見

codex CLI（セルフレビュー用）でハマりがちなハング挙動と対処。

## codex exec は長文日本語プロンプトで数十分ハングし得る → 出力 0 bytes ベースで kill タイムアウトを設ける

`codex exec --skip-git-repo-check "<長文プロンプト>"` は、4000 文字超 / 多数の指示観点 / 階層的分類指示などプロンプトが大きいと、応答開始までに数十分かかるか、永遠に応答しないことがある（codex-cli 0.129.0 で観測）。

観測例（renga `feat/spawn-claude-pane-soft-validation` ワーカー、2026-05-10）:

- プロセス自体は alive（`STAT=Sl`）
- stdout output ファイルは 0 bytes のまま 165 分（`etimes=9935s`）経過
- 既知の `codex:rescue` skill ハング（CLAUDE.md 記載: 18 分超）と異なり、`codex exec` 直打ちでも長時間ハングが発生

これは codex の thinking phase が stdout 沈黙する設計に起因する。`/proc/<pid>/cmdline` で日本語が正しく届いていることは確認できるが、それと応答時間は別問題。

運用ルール:

1. 出力 0 bytes が **5–10 分継続したら kill** を作業手順に明記する。`wc -l <output_file>` で 0 が続いている間は何も来ていないと判定してよい
2. ハング検知時は `kill -9` で codex プロセスツリー全体（`zsh -c …` / `node …` / `codex` 本体）を落とす
3. レビュー観点はもっと絞る（1–2 観点 + 「指摘がなければなしでよい」）。Blocker-Major-Minor-Nit 4 段階分類 × 5 観点のような重い指示は避ける
4. codex 未導入環境および kill 後は **skip 扱いとして完了報告に進んでよい**（renga の CLAUDE.md でも codex セルフレビュー skip は許容されている）

関連: renga の CLAUDE.md（検証深度 full の codex セルフレビュー手順）、過去の `codex:rescue` skill 18 分超ハング事例（renga CLAUDE.md 記載）。

出典: `2026-05-10-codex-exec-hang-on-long-japanese-prompt.md`
